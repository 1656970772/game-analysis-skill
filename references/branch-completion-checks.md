# 引擎/工具分支流程与完成前检查

分支规则：只有命中的分支才执行对应检查；未命中的在 `2.报告/00_研究总览.md` 标为"不适用"。

**所有分支共用的目录规则**：
- 工具原始输出 → `0.原始导出/<工具名>/`
- 分类后的资源 → `1.分类资源/图片/`、`1.分类资源/模型/`、`1.分类资源/特效/`、`1.分类资源/音频/`、`1.分类资源/渲染/`
- 代码 → `3.代码/`
- 中间文件 → `4.临时目录/`
- 报告 → `2.报告/`
- 学习索引 → `4.临时目录/中间索引/学习输出/`
- `2.报告/` 只放编号报告、图片画廊 BAT 入口 `打开图片画廊.bat`、动画预览 BAT 入口 `打开动画预览.bat`、模型预览 BAT 入口 `打开模型预览.bat`、特效预览 BAT 入口 `打开特效预览.bat` 和可选 PDF；不允许 CSV/JSON、验证日志、manifest、缩略图、错误日志、分类摘要或重复 BAT 别名，这些统一写入 `4.临时目录`
- 资源分类完成后，若 `1.分类资源/图片` 已有图片或分支要求画廊，先运行 `scripts/build_image_gallery_manifest.py --study-root <study-root> --skill-root <skill-root>`，再运行 `scripts/create_image_gallery_launcher.py --study-root <study-root> --skill-root <skill-root>`；默认只生成 `2.报告/打开图片画廊.bat`，调用固定 EXE `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe`，不得运行每项目 EXE 构建
- 空目录不放 `_empty_reason.md`
- **不允许创建 0-4 以外的任何目录**

## Unity 分支

AssetRipper 自动化：优先 `--headless` Web/API，GUI 作为备选。

| 阶段 | 执行流程 | 完成前检查 |
|---|---|---|
| 预检 | 运行 `unity_preflight.py` | `2.报告/00_研究总览.md` 已记录引擎信息 |
| Unity loose 资源 | 若 `*_Data/Res` 或 `*_Data/StreamingAssets` 存在直接可读资源，按 `references/unity-loose-resource-config.json` 运行 `export_unity_loose_resources.py` | `0.原始导出/UnityLooseResources/` 保持来源相对路径；`4.临时目录/中间索引/UnityLooseResources/summary.json` 的发现数、索引行数、状态数对账；重复运行同哈希全部复用；只有图片进入 `1.分类资源/图片/`，JSON/文本/视频/二进制不混入分类图片；冲突不覆盖，源前后基线不变 |
| Unity loose JSON 结构地图 | loose 归档发现成批 JSON 时，按项目配置运行 `analyze_loose_json_tables.py`，把预期文件数/字节绑定到归档索引 | `4.临时目录/中间索引/LooseJSON分析/` 的 CSV/JSON/Markdown 可回读；扫描数、字节、解析状态、结构分桶、预期基线、源前后快照和固定种子 SHA-256 抽样全部对账；输出不含字段值；系统候选与 `record_count` 不冒充运行时边界或实体数 |
| Game Framework 容器恢复 | 若 `StreamingAssets/GameFrameworkVersion.dat` 为 `GFP` 且资源容器为 `GFF`，读取 `references/unity-gameframework-gff-workflow.md`，运行 `export_unity_gameframework_resources.py` 的 probe，再全量 extract | 版本列表声明数、probe/extract 状态数、解码魔数、错误数、输出长度/SHA-256 和源目录前后基线已对账；恢复目录继续交给 UnityPy 与 AssetRipper，不能只停在解密层 |
| AssetRipper 导出 | headless 导出到 `0.原始导出/AssetRipper/` | 目录存在或阻塞已记录 |
| UnityPy 补充 | 导出到 `0.原始导出/UnityPy/` | 资源文件、summary、checkpoint 存在且可对账；source lexical root 的祖先、根内每级目录与最终文件均无 symlink/junction/reparse，canonical 候选不出根，exporter/独立 validator 分别绑定全量授权文件稳定代；protocol 1 仅为纯 no-work，summary/checkpoint 同时引用已先行 no-replace 发布且在控制面提交期间保持租约的不可变 group transaction sidecar，sidecar 精确绑定两个计划控制 payload 的路径/byte size/SHA-256；完整 sidecar 历史与 fragments 递归 canonical exact set 闭环，嵌套、额外、非法命名、reparse 全部拒绝；cleanup/recovery/结果消费到显式 close 期间两个 committed targets 保持 exact-generation leases，cleanup rename/unlink 以精确 handle/fd 协调底层成功后的异常，恢复只删除自身代匹配的 token-owned partial，任一目标或 partial 漂移 fail closed 且只列真实存在的恢复路径；输出树无任何 `*.partial`；rollback 无法完整恢复时明确保留 `rollback_incomplete/recovery_files` |
| UnityPy MonoBehaviour 边界 | UnityPy 全量索引生成后，按 `references/unity-monobehaviour-boundary-config.json` 与输出契约运行 `analyze_unitypy_monobehaviour_boundary.py`；可选传入独立 TypeTree probe evidence，但不引入该 probe 的 native 依赖 | `4.临时目录/中间索引/UnityPy分析/MonoBehaviour边界/` 中严格 JSON 与中文 Markdown 可重开；两遍流式索引行数、非空全局唯一且完全相同的表头、全量状态、unknown 状态、generic-header/custom-fields/parse-error/unavailable、Expected-vs-actual、配置化来源组动态对账；index/config/contract/probe/metadata 与 final/stage/backup 无 canonical/hard-link alias，输出路径无 reparse，解析字节和 provenance SHA 属于同一稳定快照且提交前后无持久漂移；两份 artifact 完整 stage/reopen，Windows 既有目标从计划到 rollback 持有拒绝 write 的稳定句柄，精确旧对象先以 `ReplaceIfExists=0` 退休，stage 与 backup 也只以 no-replace 发布，第一/第二目标的同尺寸、hard-link 和 rollback 晚到写入均不被覆盖；POSIX 无等价 mandatory primitive 时对既有目标在 stage/backup 前 fail closed，仅 fresh 空目录走 descriptor-relative no-replace；rollback 二次失败保留唯一 backup 并标 `rollback_incomplete/recovery_files`；cleanup 全项尝试且稳定目录 close 独立执行，已提交代的 cleanup warning/待清理路径机器可读；所有 create/write/reopen/commit/restore/delete/cleanup 均相对首次固定的同一输出目录 handle/fd，父路径换成 junction/symlink 时外部目录基线不变；缺失 byte_size/raw SHA 标 unavailable，候选角色不冲突，byte size 是规范 ASCII 非负十进制，未匹配 remark 统计有界，多候选按配置优先且有效冲突 fail closed；probe 只报告样本内统计且总体恢复估计为 null |
| UnityPy glTF 模型 | 在 UnityPy 图片/贴图组织后运行 `export_unitypy_meshes_to_gltf.py --expected-index <all_resources_index.csv>`；按 `references/unity-material-binding-config.json` 与 `references/unity-model-material-binding-workflow.md` 从 MeshFilter/MeshRenderer -> Material -> 贴图属性优先级写入材质贴图绑定；专项分支用安全 model/index 子根隔离 | `4.临时目录/中间索引/UnityPy模型导出/模型导出索引.csv` 存在；expected Mesh 的 source/path id exact set 与 exported/skipped/failed 精确对账；相同运行复用 glTF/BIN/sidecar/index/summary 且 mtime 不变；`material_binding_source` 统计已记录；可恢复材质的 glTF 含 `baseColorTexture`；`same_name_candidate` 不计为已验证材质 |
| 真实动作样例 | 若存在 `SkinnedMeshRenderer` 与 direct `AnimationClip` 曲线，运行 `export_unitypy_real_animation_samples_to_gltf.py --auto` | `4.临时目录/中间索引/UnityPy真实动作导出/真实动作导出索引.csv` 记录候选覆盖率、导出状态或失败原因；至少一个可匹配样例应进入模型预览 |
| 资源分类 | UnityPy 图片/真实音频先按 `references/unitypy-resource-organization.json` 与 contract 运行 `organize_unitypy_project_export.py`；图片→`1.分类资源/图片/`，模型→`1.分类资源/模型/`，特效→`1.分类资源/特效/`；AssetRipper 原始工程含真实音频时运行 `organize_assetripper_audio_resources.py`，按 `references/assetripper-audio-classification.json` 保持 `Assets` 相对路径归档到 `1.分类资源/音频/AssetRipper/`；含 `.mat/.shader/.rendertexture` 时运行 `organize_assetripper_render_resources.py`，按 `references/assetripper-render-classification.json` 归档到 `1.分类资源/渲染/`；VFX 依次运行 `build_vfx_preview_manifest.py`、`export_unity_vfx_reconstruction.py`、`create_vfx_viewer_launcher.py` 生成 manifest、reconstructed JSON、Unity export 和报告入口 | UnityPy 组织为两遍流式，索引/config/contract/资源 SHA-256 可追溯，created/reused/hardlinked/copied/conflict/filtered/skipped/failed 对账；序列候选内存达到配置阈值后切换隔离 SQLite 且结束无 spill 残留，默认 scope 隔离 source/container/export parent，只有明确动画上下文或显式 global 配置可跨范围；重复运行同内容不改 mtime、不生成递增后缀，路径穿越/reparse、`stat/samefile` 身份查询异常与稳定冲突二次不一致 fail closed，include/exclude 覆盖主分类和主角候选副本，分类目录无 JSON/CSV/TXT/MD；六个组织 artifact 全部 stage/reopen 后以 summary 最后提交的组事务发布，任意第 N 个提交失败或控制输入漂移都恢复旧集合。图片目录只有图片；模型能导入 Blender，且可恢复贴图的 glTF 在 Blender 中显示 Image Texture -> Base Color；每个模型目录含 `metadata.json`、`export-log.json`、`dependencies.json`、`missing-resources.json`；音频候选数/分类数/总字节/manifest SHA-256 一致，missing/extra/impure/conflict/failed 为 0，源前后快照一致，索引留在 `4.临时目录/中间索引/AssetRipper音频分类/`；硬链接分类只读，编辑前复制；通用渲染分类保持 AssetRipper `Assets` 相对路径，候选数与分类资源数一致，缺失/额外/冲突为 0，索引留在 `4.临时目录/中间索引/渲染分类/`；VFX manifest/errors 位于 `4.临时目录/中间索引/VFX预览/`，`reconstructed/` 已生成，`unity-export/Assets/GameAnalysisVfxPreview` 包含 Prefab、Material、metadata、bootstrap 脚本目录，`2.报告/打开特效预览.bat` 已生成；best-effort 动态还原的失败或部分状态写入 `preview_status`、`reconstruction_status`、`unity_export_status`、warnings 或 errors |
| Wwise 音频 | 若存在 `SoundbanksInfo.xml`、`.bnk` 或 `.wem`，读取 `references/wwise-soundbank-workflow.md`，运行 `export_wwise_audio.py` 分别恢复外置媒体和 bank 内嵌子流 | 实际 WEM 数与有效外置 WAV 数一致；各可解码 bank 的 vgmstream `stream count` 与有效内嵌 WAV 数一致；XML 的 `Location=Memory/Loose` 数作为语义覆盖证据单独统计，和运行时文件/子流不一致时记录边界而非丢弃额外资源；不能只看退出码；事件/媒体映射与日志留在 `4.临时目录` |
| 场景分析 | 按 `references/assetripper-project-analysis.json` 运行 `analyze_assetripper_project.py`；有 runtime review JSON 时传 `--runtime-verification`；直接输出写入 `4.临时目录/中间索引/AssetRipper分析/`，结论再汇总或引用到 `2.报告/04_场景关卡搭建.md` / `2.报告/00_研究总览.md` | Build Settings 全条目以新 `- enabled` 为硬边界，`order/enabled/path/guid` 与场景索引集合已对账；Graphics/Quality/Player/Packages 的数量、档名、参数和值均动态提取；来源 SHA-256、行锚点、严格 JSON 重开验证通过；提供 layout mismatch 输入时摘要存在动态 `safe_usage/forbidden_inference`，未从错位组件 YAML 推断自定义字段 |
| 代码提取 | ILSpy→`3.代码/IL导出/`；IL2CPP→`3.代码/IL2CPP_Dump/` | 代码目录只有代码 |
| 学习索引 | 运行 `build_learning_indices.py`，输出到 `4.临时目录/中间索引/学习输出/` | 尺寸、命名、渲染、音频、动画、namespace 索引存在 |
| 临时卫生 | 运行 `audit_study_root_hygiene.py`；交付清理时才加 `--clean-browser-profiles` | 浏览器 profile 已记录或清理，Ghidra/工具中间文件未误删 |

UnityPy 资源组织还必须通过控制面门禁：任何目录、partial 或分类资源写入前，全部 contract artifacts 与 requirements target 已按 Windows 大小写语义验证为互不 alias、无文件/父路径冲突，且不与 index/config/contract 输入 alias；CSV fields 为非空唯一字符串，artifact 后缀匹配类型。config/contract 的解析与 provenance 来自同一字节快照，首次写入、artifact 组提交前、每次提交之间与提交后身份复核一致；漂移或第 N 个提交失败时非零退出，并通过组 rollback 恢复旧集合或移除新集合，不能留下与旧 `ok=true` summary 冲突的新索引。第二遍 index 漂移可保留可恢复分类文件，但最终 `ok` 必须为 `false`。

protocol 1 v3 的完成门禁还必须证明：summary/checkpoint 锚定相同 history head/count/chain SHA-256；从 head 到唯一 genesis 或由控制面显式锚定的 singleton v2 legacy boundary 可重建完整 ordered chain；每条 v3 链接的前驱 sidecar SHA-256、count 与 chain digest 正确；链的 recursive exact set 无 extra/missing/fork/cycle/duplicate/nested/illegal/reparse。若存在控制面未引用的 sidecar，只能是唯一严格 next interrupted tail，且须在旧 head 与两个旧控制文件精确代仍匹配时恢复或清理，否则完成门禁失败。

## Unreal 分支

| 阶段 | 执行流程 | 完成前检查 |
|---|---|---|
| 包识别 | 检测 `.pak`、`*-Win64-Shipping.exe` | `2.报告/00_研究总览.md` 记录 Unreal 证据 |
| 只读分析 | `repak info/list/get` 路径清单写入 `4.临时目录/` | 路径清单已生成 |
| 全量解包 | `repak unpack` 到 `0.原始导出/PakUnpacked/` | 记录文件数、体积、耗时 |
| UModel 转换 | 分片导出到 `0.原始导出/UModel/` | raw 输出保留原始路径 |
| 材质修补 | 修补后 glTF 能在 Blender 正常显示 | 修补版已验证 |
| 资源分类 | 图片→`1.分类资源/图片/`，模型+贴图→`1.分类资源/模型/<名>/` | 模型目录含 FBX/glTF + textures/ |
| 路径分析 | 候选表写入 `2.报告/` | 报告有路径级结构判断 |

## GameMaker 分支

| 阶段 | 执行流程 | 完成前检查 |
|---|---|---|
| 包识别 | 检测 `data.win` | `2.报告/00_研究总览.md` 记录证据 |
| UTMT 导出 | 导出到 `0.原始导出/UTMT/`（中文路径时用 `4.临时目录/_ascii_work/` 暂存后回填） | 原始导出完整 |
| 归一化 | 运行 `normalize_gamemaker_exports.py`，索引写入 `4.临时目录/` | 索引已生成 |
| 资源分类 | sprite 帧→`1.分类资源/图片/序列帧/` | 图片目录只有图片 |
| 代码边界 | GML→`3.代码/脚本/` | `3.代码/代码可读性判定.md` 存在 |

## Godot 3.x PCK 分支

详细流程见 `references/godot-pck-workflow.md`。检测到独立 `.pck`、`GDPC`、`project.godot` 或 Godot 3.x 版本字符串时，先执行只读 PCK probe，再逐级扩大到 GDRETools 恢复；不得执行游戏 EXE。

| 阶段 | 执行流程 | 完成前检查 |
|---|---|---|
| 包识别 | 主 agent 运行 `detect_engine.py`、`asset_inventory.py`，记录 PCK/EXE 哈希和 Godot 版本证据 | `2.报告/00_研究总览.md` 记录 Godot 事实、判断与待验证项 |
| PCK probe | 运行 `scripts/analyze_godot_pck.py <pck> --output-dir <study-root>/4.临时目录/中间索引/Godot/PCK探测` | JSON、CSV、摘要存在；format 0/1、保留字段、条目数、UTF-8 `res://` 路径、offset/size、重叠和全部 MD5 通过 |
| GDRE list | 从 manifest 校验 GDRETools 2.6.0 后，用隐藏 `Start-Process` 列包并保留 stdout/stderr | 退出码 0；列表条目数与 PCK probe 对账；无 unsupported/encrypted/checksum/越界错误 |
| scripts-only | 运行 `scripts/export_godot_pck_project.ps1 -ScriptsOnly`；仅在日志证明自动版本检测失败时才传 `-ForceBytecodeVersion` | `4.临时目录/小样本/GDRETools脚本恢复/` 有可读 `.gd`，或 bytecode 阻塞有日志证据 |
| 完整恢复 | 运行 `scripts/export_godot_pck_project.ps1` | 恢复/日志/索引根在创建前通过 canonical Junction/reparse 边界检查；`0.原始导出/GDRETools恢复/` 保留完整恢复层级；恢复退出码 0；源 PCK SHA-256 前后一致 |
| 分类组织 | 运行 `scripts/organize_godot_project_export.py`，默认读取 `references/godot-resource-classification.json` | 分类/代码/索引根在写入前通过 canonical 边界检查；图片通过 Pillow `verify/load` 后按 UI/场景/道具/图集/序列帧/粒子贴图分类；整张 spritesheet 不冒充序列帧；真实 wav/ogg、ttf/fnt 与 `.gd` 分别进入音频、字体和脚本目录 |
| 学习索引与画廊 | 运行 `build_learning_indices.py`；有真实图片时运行固定图片画廊链 | Godot 索引只在 `4.临时目录/中间索引/Godot/`；分类目录无索引；固定 EXE self-test 通过 |
| 精确版本二级静态验证 | 先扫描 `tool`、`EditorPlugin`、GDNative/原生库和 import/editor plugin；通过后只在 `4.临时目录` 隔离副本用精确 Godot 版本解析 | 不运行主场景，不改写 `0.原始导出/GDRETools恢复/`；有损恢复、GDNative、加密和未转换边界已写入报告 |

## Cocos Creator / Android ASTC 分支

| 阶段 | 执行流程 | 完成前检查 |
|---|---|---|
| 包识别 | 检测 `cc.config.json`、`.astc` | 引擎证据已记录 |
| UUID 映射 | 映射索引写入 `4.临时目录/` | 映射已建立 |
| ASTC 解码 | 解码 PNG 到 `4.临时目录/`，分类后复制到 `1.分类资源/图片/` | 图片目录只有图片 |
| 代码提取 | JS/TS→`3.代码/脚本/` | 代码目录只有代码 |

## MonoGame / FNA / XNA `.xnb` 分支

详细流程见 `references/monogame-xnb-workflow.md`。该分支可从“未知引擎”识别中提升为主分支；检测到 `MonoGame.Framework.dll`、`FNA.dll`、`Microsoft.Xna.Framework` 或大量 `.xnb` 时，不应只套用泛化未知流程。

| 阶段 | 执行流程 | 完成前检查 |
|---|---|---|
| 包识别 | 检测 `MonoGame.Framework.dll`、`FNA.dll`、`Microsoft.Xna.Framework`、`Content/*.xnb`、Tiled `.tmx/.tsx`、FMOD bank | `2.报告/00_研究总览.md` 记录 MonoGame/FNA/XNA 证据 |
| XNB reader 扫描 | 对所有 `.xnb` 做只读 reader 扫描，区分 `Texture2DReader`、`ModelReader`、`EffectReader`、字体和 custom reader | `4.临时目录/中间索引/MonoGame资源/XNBReader索引.csv` 存在，并统计各 reader 数量 |
| Texture2D 导出 | 支持格式先小样本验证，再把可导出的 Texture2D 转 PNG 并分类到 `1.分类资源/图片/` | 图片目录只有真实图片；失败项写入 `4.临时目录/中间索引/MonoGame资源/` |
| ModelReader 转换门禁 | 若存在 `ModelReader`，必须写 `4.临时目录/中间索引/MonoGame模型/ModelReader索引.csv`，查找可信转换路线，做小样本转换，并用 Blender 验证 `.gltf/.glb/.fbx`；无法转换时写阻塞记录 | 不能只索引就结束。必须满足其一：`1.分类资源/模型/` 中有 Blender 可导入模型和 sidecar；或 `4.临时目录/失败记录/MonoGame模型转换阻塞.md` 与 `2.报告/00_研究总览.md` 阻塞项存在，最终状态为 `DONE_WITH_CONCERNS` 或 `BLOCKED` |
| Tiled 地图索引 | 扫描 `.tmx/.tsx`、tileset、对象层、碰撞层、自定义属性，索引写入 `4.临时目录/中间索引/MonoGame地图/` | `2.报告/04_场景关卡搭建.md` 解释地图资源规范、生成/加载链路和摆放机制 |
| 音频/渲染/特效 | FMOD bank 归入音频；`EffectReader` 和 shader 归入渲染；粒子/发射器配置归入特效配置 | 真实资源进入 `1.分类资源/` 对应目录，索引和失败日志留在 `4.临时目录/` |
| 代码提取 | .NET 程序集用 ILSpy 导出到 `3.代码/IL导出/` | `3.代码/` 只有代码文件，代码可读性边界已记录 |
| 学习索引与画廊 | 运行学习索引和图片画廊固定 EXE 流程；若模型转换成功，再运行模型预览链 | 画廊 BAT/self-test 通过；模型预览只在存在可导入模型时生成，未转换时报告阻塞而不是伪造预览 |

## Klei / Don't Starve Together 分支

详细流程见 `references/klei-dst-workflow.md`。检测到 `dontstarve_steam*.exe`、`data/databundles/scripts.zip`、`data/anim/*.zip`、KTEX `.tex`、`.dyn` 或 FMOD `.fsb/.fev` 时，应提升为 Klei/DST 主分支，而不是停留在未知引擎。

| 阶段 | 执行流程 | 完成前检查 |
|---|---|---|
| 包识别 | 运行 `detect_engine.py` 与 `asset_inventory.py`；若 Klei markers 命中，读取 `references/klei-dst-workflow.md` | `2.报告/00_研究总览.md` 记录 Klei/DST 证据 |
| 原始导出 | 运行 `scripts/export_klei_dst_project.py --source <game-root> --study-root <study-root> --project-name "Don't Starve Together"` | `0.原始导出/KleiDST/`、`3.代码/脚本/`、`4.临时目录/中间索引/KleiDST/` 存在；源目录未修改 |
| 资源转换 | 运行 `scripts/convert_klei_dst_resources.py --study-root <study-root> --game-root <game-root> --skill-root <skill>`；该步骤是同一 study-root 的 single-producer，只允许一个任务串行写 Klei 动画 XML、KleiTEX、音频与转换索引 | `klei_texture_conversion_index.csv`、`klei_animation_xml_index.csv`、`klei_fsb_conversion_index.csv` 存在；PNG、动画 XML、音频样本真实生成或失败 JSONL 有证据；无并发转换任务共写目录 |
| 动画预览与素材导出 | 转换完成后运行 `scripts/build_game_animation_manifest.py --study-root <study-root>`；再运行 `scripts/create_game_animation_viewer_launcher.py --study-root <study-root> --skill-root <skill> --manifest <study-root>/4.临时目录/中间索引/游戏动画预览/manifest.json --renderer-script <skill>/scripts/export_game_animation_asset.py`，由固定 `GameAnimationViewer` 查看 atlas/XML、播放真实 PNG sequence，并只导出 `current-frame-png`、`png-sequence`、`apng`、`gif`、`source-bundle` | 当前闭环仅为 Klei/DST BILD+ANIM+atlas AnimState v1；manifest 分开记录 `bank.name/package` 与 `build.name/package`，多 atlas 页完整；歧义 Build 为 `partial` 且需显式选择；`2.报告/打开动画预览.bat` 是唯一动画入口，manifest/cache 位于 `4.临时目录/中间索引/游戏动画预览/{manifest.json,preview-cache/}`，用户导出位于 `4.临时目录/动画预览导出/`；人物、怪物、建筑、植物、AnimState FX、水面候选均有抽样验收 |
| 本体/Mod 图片补全 | 发现新增 Mod、loose TEX、直接图片、嵌套 ZIP 或地形贴图缺口时，运行 `scripts/export_klei_dst_images.py --study-root <study-root> --sources-manifest <manifest> --skill-root <skill> --config <skill>/references/klei-dst-image-export-config.json` | manifest 明确每个来源的 `id/root/output_prefix/terrain_prefix/kind/display_name`，其中 `terrain_prefix` 为空或安全相对路径；CSV/JSON/JSONL 含来源链、哈希、尺寸、`terrain_category`、地形理由与状态；XML 不混入分类图片；分类 PNG 可由 Pillow `verify/load`；高可信地形按中文用途分类和来源前缀硬链接/复制到 `1.分类资源/图片/地形贴图/`；冲突和不安全 ZIP 路径有证据；源目录未修改 |
| 画廊与色彩 | 对 `1.分类资源/图片` 运行 `image_palette_report.py`、`build_image_gallery_manifest.py`、`create_image_gallery_launcher.py`，再执行固定 EXE self-test | `2.报告/打开图片画廊.bat` 仅此一个入口；manifest/thumbnails/errors 在 `4.临时目录/中间索引/图片画廊/`；self-test 通过 |
| Runtime-only 边界盘点 | 若 `.dyn` 未解码，运行 `scripts/analyze_klei_dyn_boundary.py --study-root <study-root> --game-root <game-root>`；同时由 Klei 报告和图片素材清单保留 `.dyn`、KSH shader、粒子、多 pass 海洋/水面候选 exact set | `dyn_boundary_inventory.csv`、`dyn_boundary_inventory_summary.json` 和失败记录存在；已提取素材可查看/导出，但 runtime-only 候选不进入 AnimState `ready` manifest，状态为 `partial/unsupported`；只要仍未恢复，最终至少 `DONE_WITH_CONCERNS`，候选不消失、不冒充完整还原 |
| 报告 | 运行 `scripts/build_klei_dst_reports.py --study-root <study-root>`，必要时追加 `--skill-iteration-note` | 00-09 编号报告存在；`3.代码/代码可读性判定.md` 存在；`.dyn` 未解码时 `4.临时目录/失败记录/KleiDST剩余阻塞.md` 存在且 00 报告记录 |
| 剩余阻塞 | 对 `.dyn` 做 boundary inventory 记录；KTEX/FSB 若已转换成功，不再沿用第一阶段阻塞描述 | 最终状态为 `DONE` 或 `DONE_WITH_CONCERNS`，不得把已转换成功的 KTEX/FSB 继续列为未解决阻塞 |

## 未知引擎分支

| 阶段 | 执行流程 | 完成前检查 |
|---|---|---|
| 只读识别 | 观察事实写入 `2.报告/00_研究总览.md` | 有事实和假设 |
| 工具发现 | 记录候选工具 | 已记录 |
| 下载验证 | 下载到 Skill `tools/`，manifest 已更新 | 工具已沉淀 |
| 导出 | 全量导出到 `0.原始导出/<工具名>/` | 原始导出完整 |
| 分类 | 按图片/模型/特效/代码分类到 `1.分类资源/` 和 `3.代码/` | 分类符合标准 |
| 学习索引 | 运行 `build_learning_indices.py` | 索引写入 `4.临时目录/中间索引/学习输出/` |
