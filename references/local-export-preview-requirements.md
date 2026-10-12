# 本地导出与预览需求

## 使用方式

当用户要求“全量本地导出、全量图片画廊、动画预览/素材导出、模型预览、Unity 动态特效预览/回导、覆盖层预览、EXE/Electron 本地壳、打开目录按钮”时，按本文件写规格或实现计划。默认本地预览架构固定为 Skill 级 EXE + 每项目 BAT + manifest：图片预览每个 study-root 生成 `2.报告/打开图片画廊.bat`、`4.临时目录/中间索引/图片画廊/manifest.json`、`4.临时目录/中间索引/图片画廊/thumbnails/` 和 `4.临时目录/中间索引/图片画廊/errors.jsonl`；BAT 调用 `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe`，传入 `--study-root`、`--manifest` 和 manifest `source_roots` 派生的重复 `--images-root`。当前动画闭环仅适用于 Klei/DST BILD+ANIM+atlas AnimState v1，每个 study-root 只生成 `2.报告/打开动画预览.bat`；`build_game_animation_manifest.py` 生成 `4.临时目录/中间索引/游戏动画预览/manifest.json`，`GameAnimationViewer` 调用 `export_game_animation_asset.py` 生成真实 PNG sequence、查看 atlas/XML 素材并提供五种导出，preview-cache 留在同目录，用户导出留在 `4.临时目录/动画预览导出/`。模型预览每个 study-root 只生成 `2.报告/打开模型预览.bat`，并把 `4.临时目录/中间索引/模型预览/manifest.json`、`thumbs/`、`preview-cache/` 和 `errors.jsonl` 传给 `built-in-tools/GameModelViewer_1.0.0/GameModelViewer.exe`；不得额外生成 `2.报告/模型预览.bat` 等重复入口。特效预览每个 study-root 生成 `2.报告/打开特效预览.bat`；BAT 调用 Skill 固定 EXE `built-in-tools/GameVfxViewer_1.0.0/GameVfxViewer.exe`，只传入 `--study-root`、`--manifest`、`--reconstructed-dir`、`--preview-cache-dir` 和 `--unity-export-dir`。`4.临时目录/中间索引/VFX预览/errors.jsonl` 位于 VFX preview index 下，供状态和排错记录使用，不作为 viewer CLI 参数直接传入。边界：只处理用户授权的本地路径；未授权资源不扫描、不导出、不上传；授权后的原始导出只留在本机学习目录；不修改游戏安装目录。

## 功能规格

| 功能 | 要求 | 边界 |
|---|---|---|
| 全量本地导出 | 对授权路径生成完整本地导出、索引、统计、调色板摘要、缩略图路径、原创分析报告；Unity 项目先生成 AssetRipper `ExportedProject`，再生成 UnityPy 可读对象索引 | 原始导出只在本机学习目录使用；对外交付只给索引、缩略图和原创总结 |
| 关键角色动画帧 | 根据 AnimatorController 与 `.anim` 的 `m_Sprite` 引用，先从 AssetRipper 工程切片，再用 UnityPy 对照可读原始 bundle，按 `attack01_1.png`、`idle_1.png` 这类规则整理 | 仅用于学习动作拆分和节奏；原创项目必须重新绘制角色与动作表达 |
| Klei/DST AnimState v1 动画预览与素材导出 | `build_game_animation_manifest.py` 从 BILD+ANIM+atlas 生成 manifest；Klei adapter 明确区分 `bank.name/package` 与 `build.name/package`，索引全部 atlas page、clip/FPS/帧数、Symbol 覆盖率和匹配证据；`create_game_animation_viewer_launcher.py` 生成唯一 `2.报告/打开动画预览.bat`，调用固定 `GameAnimationViewer` 查看 atlas PNG、Build XML、Anim XML，播放 `export_game_animation_asset.py` 生成的真实 PNG sequence，并只导出 `current-frame-png`、`png-sequence`、`apng`、`gif`、`source-bundle` | 当前 schema/Viewer 的适用范围仅为 Klei/DST AnimState v1；Build 歧义、缺 atlas 或缺 Symbol 必须为 `partial` 并要求显式选择；不使用占位帧冒充原动画 |
| 全量图片画廊 | `build_image_gallery_manifest.py` 生成 `manifest.json`、PNG 缩略图和错误日志；`create_image_gallery_launcher.py` 生成 `2.报告/打开图片画廊.bat` | 不展示未授权内容；默认只读；manifest、缩略图、错误日志不进入 `2.报告/` |
| 模型预览画廊 | 扫描 `1.分类资源/模型/` 生成 `manifest.json`、缩略图目录和 FBX `preview-cache`；`2.报告/打开模型预览.bat` 调用 Skill `built-in-tools` 内固定 EXE `GameModelViewer`，并传入 `--study-root`、`--models-dir`、`--manifest`、`--thumbs-dir`、`--preview-cache-dir`；glTF/GLB 内置 animations 使用右侧动作列表和底部播放栏预览；manifest 可按 `full_body_preview` 配置为服装/套装入口挂接基础身体、头部和同前缀分件，GUI 信息卡提供“全身预览”按钮并在同一预览层加载多个 part | 固定 EXE 不复制到 study-root，不在每次分析中构建；BAT 只传本次项目参数；预览只读、离线；Blender 不可用时保留 manifest 并记录缺缩略图/转换失败原因；`preview-cache` 只放 FBX 临时预览副本，不回写原模型；全身预览只是 viewer 层组合，不合并或改写原模型文件；程序化 AnimationClip metadata 预览必须与真实导出动作模型分开标注 |
| Unity 动态特效预览/回导 | `build_vfx_preview_manifest.py` 生成 `4.临时目录/中间索引/VFX预览/manifest.json`、`errors.jsonl`，把粒子贴图等真实资源分类到 `1.分类资源/特效/`，把 Material/Mesh/AnimationClip 等配置/metadata 卡写入 `1.分类资源/特效/配置/`；`export_unity_vfx_reconstruction.py` 动态还原 ParticleSystem、TrailRenderer、LineRenderer 基础模块，记录依赖贴图/材质/mesh/animation 引用，输出 `reconstructed/` JSON 和 `unity-export/Assets/GameAnalysisVfxPreview` 下的 Prefab、Material、metadata、bootstrap 脚本目录；`create_vfx_viewer_launcher.py` 生成 `2.报告/打开特效预览.bat`，传入 `--study-root`、`--manifest`、`--reconstructed-dir`、`--preview-cache-dir`、`--unity-export-dir` 给 `GameVfxViewer` | `VFX材质/`、`VFX模型/`、`VFX动画/` 只放真实/可用资源；动态还原是 best-effort；不复制未解析 texture；不修改源游戏或源导出；无法解析、部分还原或 Unity 回导失败必须写入 `errors.jsonl`、manifest `warnings` 或 `preview_status` / `reconstruction_status` / `unity_export_status`，不得声称完整还原 Unity runtime |
| 当前页覆盖层预览 | 点击卡片打开预览覆盖层；鼠标滚轮缩放图片；按住鼠标左键拖拽平移图片；预览仅通过右上角 X/关闭按钮或 Esc 关闭；可扩展网格、取色、明度、构图辅助线、注释图层 | 覆盖层不改原图；注释另存到工具工作区 |
| 固定 EXE/Electron 本地壳 | 使用 Skill 维护的 `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe` 打开 manifest；完成前必须运行 `--self-test --study-root ... --manifest ... --images-root ...` | 默认禁用外网；敏感路径不写入日志；不要在每个 study-root 构建或复制项目专属 EXE |
| 打开目录按钮 | 在 EXE/Electron 壳内通过 `打开目录` 按钮，用系统文件管理器打开并定位当前图片；右键不触发操作，不属于默认工作流 | 仅调用系统文件管理器打开本地目录，不执行脚本或提升权限 |

## 推荐实现约束

- 工作区与项目输出目录分离：manifest、缩略图、错误日志写入 `4.临时目录/中间索引/图片画廊/`，报告与 BAT 入口写入 `2.报告/`。
- `1.分类资源/图片` 已有图片或用户要求画廊时，默认运行 `scripts/build_image_gallery_manifest.py --study-root <study-root> --skill-root <skill-root>`，再运行 `scripts/create_image_gallery_launcher.py --study-root <study-root> --skill-root <skill-root>`；旧 HTML 画廊和每项目 EXE 构建只作兼容历史输出。
- Klei 的 `convert_klei_dst_resources.py` 是同一 study-root 的 single-producer，必须先串行完成，再并行生成动画索引或做审查。动画闭环依次运行 `scripts/build_game_animation_manifest.py --study-root <study-root>` 和 `scripts/create_game_animation_viewer_launcher.py --study-root <study-root> --skill-root <skill-root> --manifest <study-root>/4.临时目录/中间索引/游戏动画预览/manifest.json --renderer-script <skill-root>/scripts/export_game_animation_asset.py`。manifest 与真实帧缓存位于 `4.临时目录/中间索引/游戏动画预览/manifest.json`、`preview-cache/`，用户五种导出位于 `4.临时目录/动画预览导出/`；固定 EXE 不复制到 study-root。
- `.dyn`、KSH shader、粒子和多 pass 海洋/水面候选必须由 `scripts/analyze_klei_dyn_boundary.py`、Klei 编号报告和图片素材清单共同保留 candidate exact set。已提取的 atlas/纹理/XML/脚本可查看或安全导出，但 runtime-only 候选不得进入 AnimState `ready` manifest；仍未恢复时最终状态至少为 `DONE_WITH_CONCERNS`。未知引擎先 Discovery/独立 adapter；未适配时走 `material-only/unsupported` 并继续既有图片、模型、VFX 流程，不要求符合 Klei schema。
- 模型预览画廊使用 Skill `built-in-tools` 中的固定 EXE；每个 study-root 在 `2.报告/` 只生成 `打开模型预览.bat`，通过参数绑定本次 `manifest`、`thumbs` 和 `preview-cache`。
- `1.分类资源/模型/` 已有 `.gltf/.glb/.fbx` 时，如果 UnityPy 原始索引存在 `AnimationClip` metadata，先运行 `scripts/build_unity_animation_preview_models.py --study-root <study-root>` 生成 `Unity动作预览_AnimationClips` glTF 学习预览模型；它只表达动作名、时长和程序化姿态，不代表完整还原原始 skin/Avatar/bone weights。随后默认运行 `scripts/build_model_gallery_manifest.py --study-root <study-root>`，可选检测 Blender 后运行 `scripts/render_model_thumbnails_blender.py --manifest <manifest> --blender <blender.exe>`，随后始终运行 `scripts/create_model_viewer_launcher.py --study-root <study-root> --skill-root <skill-root>` 和 `scripts/update_model_gallery_summary.py --study-root <study-root> --manifest <manifest> --viewer-exe <skill-root>/built-in-tools/GameModelViewer_1.0.0/GameModelViewer.exe`。
- 模型分件组合预览只通过 manifest 的 `full_body_preview` 字段表达；组合规则来自 `references/model-viewer-gallery-config.json`，不要在 GUI 中硬编码某个角色编号或套装编号。组合预览按钮只打开 viewer 层的虚拟组合，不在 `1.分类资源/模型/` 新增合并模型。
- 所有写入动作先显示目标路径；批量导出前生成清单。
- 对第三方参考样本保留来源链接和观察记录，不生成可复用资产包。
- 图片预览优先生成尺寸受限的缩略图；原图仅在用户授权目录内按需查看。
- 固定图片画廊 Electron/EXE 壳保留本地模式边界：离线、只读、允许打开目录；图片画廊 BAT 必须只负责传 `--study-root`、`--manifest` 和重复 `--images-root`。
- 模型预览 BAT 必须只负责传 `--study-root`、`--models-dir`、`--manifest`、`--thumbs-dir`、`--preview-cache-dir`。
- 模型预览 GUI 验证优先使用 `scripts/verify_model_viewer_gui.ps1`；该脚本直接连接 Electron CDP，避免依赖外部浏览器自动化状态，并固定处理中文/空格路径启动参数。
- Unity VFX 预览使用三步命令：先运行 `scripts/build_vfx_preview_manifest.py --study-root <study-root>`，再运行 `scripts/export_unity_vfx_reconstruction.py --study-root <study-root>`，最后运行 `scripts/create_vfx_viewer_launcher.py --study-root <study-root> --skill-root <skill-root>`。真实资源保留在 `1.分类资源/特效/`，配置/metadata 卡保留在 `1.分类资源/特效/配置/`，状态和动态还原产物必须保留在 `4.临时目录/中间索引/VFX预览/manifest.json`、`reconstructed/`、`preview-cache/`、`unity-export/Assets/GameAnalysisVfxPreview`，报告入口为 `2.报告/打开特效预览.bat`。
- Unity VFX 回导目录只表达可重新导入 Unity 的项目相对片段；Prefab、Material、metadata、bootstrap 脚本和依赖引用来自 manifest/reconstructed 数据，不修改源游戏，也不把未解析 texture 伪装成已恢复资源。
- 关键角色动画输出必须包含逐帧 CSV、动画级 CSV、manifest 和命名规则说明。
- 我的判断：覆盖层除了查看大图，还应支持记录色彩、形状语言、角色轮廓、UI/HUD 可读性和制作约束，便于把观察转译成原创项目规范。

## 待验证

- 固定 EXE 在中文路径、空格路径下通过 `--self-test --study-root ... --manifest ... --images-root ...`；BAT 双击后仍能找到 manifest 与授权素材目录。
- 动画预览 BAT 在中文路径、空格路径下能找到 `GameAnimationViewer.exe`、动画 manifest、全部授权 atlas/XML source root、`4.临时目录/中间索引/游戏动画预览/preview-cache/`、`4.临时目录/动画预览导出/`、Skill Python 和 `export_game_animation_asset.py`；人物、怪物、建筑、植物、AnimState FX、水面候选分别抽样，真实 PNG sequence 帧数/FPS 正确，多 atlas 可加载，歧义 Build 不自动消解，五种导出可生成或明确报告 `partial/unsupported`。
- 模型预览 BAT 在中文路径、空格路径下能找到 `GameModelViewer.exe`、模型目录、manifest、thumbs 和 preview-cache；Blender 不可用时仍能打开模型列表并显示缺缩略图原因；含 animations 的 glTF/GLB 条目通过 `verify_model_viewer_gui.ps1` 验证右侧动作列表、底部播放栏和拖动时间轴暂停行为；需要验证服装分件全身组合时追加 `-ExpectFullBodyPreview`。
- 特效预览 BAT 在中文路径、空格路径下能找到 `GameVfxViewer.exe`、VFX manifest、reconstructed、preview-cache 和 unity-export；`unity-export/Assets/GameAnalysisVfxPreview` 可复制进 Unity 项目检查 Prefab、Material、metadata、bootstrap 脚本；部分还原只显示 best-effort status，不升级为完整成功。
- `打开目录` 按钮只打开系统文件管理器并定位当前图片，不执行脚本、不联网、不提升权限；右键不触发操作，不属于默认工作流。
- 覆盖层关闭、翻页和筛选后不残留高亮状态或遮挡界面。

## 验证清单

- 授权：每个扫描路径都有用户授权说明。
- 只读：扫描、画廊、覆盖层不会修改原项目文件。
- 导出：导出目录不是项目输出目录，且包含导出清单。
- 画廊：`2.报告/打开图片画廊.bat` 存在；`4.临时目录/中间索引/图片画廊/manifest.json`、`thumbnails/`、`errors.jsonl` 存在；缺失/损坏图片有错误提示。
- 动画预览：`4.临时目录/中间索引/游戏动画预览/manifest.json`、同目录 `preview-cache/`、`4.临时目录/动画预览导出/` 和唯一 `2.报告/打开动画预览.bat` 存在；manifest 分开记录 Bank/Build 的逻辑名与包名、多 atlas 和匹配证据；Viewer 可查看 atlas/XML、播放真实 PNG sequence，并只导出 `current-frame-png`、`png-sequence`、`apng`、`gif`、`source-bundle`；`.dyn`、KSH shader、粒子、多 pass 海洋的 exact candidate 未消失、未进入 `ready`、未被标成完整还原。
- 模型预览画廊：`manifest.json`、`thumbs/`、`preview-cache/` 和 `errors.jsonl` 位于 `4.临时目录/中间索引/模型预览/`；manifest 统计 `animation_count`、`animation_origin` 和 `is_generated_animation_preview`；`2.报告/00_研究总览.md` 分开统计真实导出动作模型与程序化动作预览模型；`2.报告/打开模型预览.bat` 可启动固定 EXE 并打开本次模型集合，`2.报告/模型预览.bat` 等重复入口不存在；缺缩略图或 FBX 转换失败有错误说明。
- 模型 GUI：含动作条目时，`scripts/verify_model_viewer_gui.ps1 -StudyRoot <study-root> -SkillRoot <skill-root> -MinAnimationCount 1` 通过；含 `full_body_preview` 条目时可追加 `-ExpectFullBodyPreview` 验证按钮、标题切换、canvas 和 `viewportError`。
- Unity 动态特效预览：依次运行 `scripts/build_vfx_preview_manifest.py --study-root <study-root>`、`scripts/export_unity_vfx_reconstruction.py --study-root <study-root>`、`scripts/create_vfx_viewer_launcher.py --study-root <study-root> --skill-root <skill-root>`；检查 `4.临时目录/中间索引/VFX预览/manifest.json`、`errors.jsonl`、`reconstructed/`、`preview-cache/`、`unity-export/Assets/GameAnalysisVfxPreview` 和 `2.报告/打开特效预览.bat` 存在；manifest 条目含 `preview_status`、`reconstruction_status`、`unity_export_status`，失败或部分还原有原因。
- 覆盖层：打开、关闭、缩放、翻页后不遮挡核心内容。
- 打开目录按钮：只打开素材所在目录或选中文件，不执行脚本、不联网、不提升权限；右键不触发操作，不属于默认工作流。
- 本地壳：固定 EXE 离线可运行；`--self-test --study-root ... --manifest ... --images-root ...` 通过；路径、权限、中文文件名正常。
- 边界：报告中已标注来源、权限状态、不可复制元素和待验证项。

依据：本文件沉淀用户新增需求；版权边界沿用 `copyright-and-style-boundaries.md`。
