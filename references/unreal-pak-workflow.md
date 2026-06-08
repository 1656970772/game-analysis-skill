# Unreal PAK 只读索引流程

## 适用边界

仅在用户明确授权本机学习目标目录时使用。输入是用户指定的 Unreal/UE4/UE5 游戏安装目录；输出写入独立学习目录。默认先做 `info/list/get` 级别的包清单和小文本配置抽取，不默认批量解包或导出原始素材。报告只总结路径结构、数量、工具状态、形状/轮廓线索和抽象美术制作规律，并把观察结果转译为原创方向建议。

## 识别信号

- 顶层或嵌套存在 `Engine/`、`<Project>/Content/Paks/*.pak`、`<Project>/Binaries/Win64/*-Win64-Shipping.exe`。
- 包内或目录中存在 `<Project>.uproject`、`Config/DefaultEngine.ini`、`.uasset`、`.uexp`、`.ubulk`、`.umap`。
- `detect_engine.py` 若未命中但 `asset_inventory.py` 显示大型 `.pak`，用 `repak info` 复核；不要把 “No engine markers found” 当作最终结论。
- 先区分游戏主包和引擎附带包：默认主分析对象是 `<Project>/Content/Paks/<Project>-WindowsNoEditor.pak`；`Engine/Programs/CrashReportClient/.../CrashReportClient.pak` 只作为引擎附带信号。

## 推荐目录

```text
<study-root>/
  0.原始导出/
  1.分类资源/
  2.报告/
  3.代码/
  4.临时目录/
```

## 基础命令

```powershell
$skill = Join-Path $env:USERPROFILE ".codex\skills\GameAnalysis"
$repak = "$skill\tools\repak_0.2.3\repak.exe"
$pak = "<game-root>\<Project>\Content\Paks\<Project>-WindowsNoEditor.pak"
$study = "<study-root>"

& $repak info $pak | Out-File -Encoding utf8 "$study\4.临时目录\_ascii_work\pak.info.txt"
& $repak list $pak | Out-File -Encoding utf8 "$study\4.临时目录\_ascii_work\pak.list.txt"
& $repak get $pak "<Project>/<Project>.uproject" | Out-File -Encoding utf8 "$study\4.临时目录\_ascii_work\<Project>.uproject.txt"
& $repak get $pak "<Project>/Config/DefaultEngine.ini" | Out-File -Encoding utf8 "$study\4.临时目录\_ascii_work\DefaultEngine.ini"
& $repak get $pak "<Project>/Config/DefaultGame.ini" | Out-File -Encoding utf8 "$study\4.临时目录\_ascii_work\DefaultGame.ini"
```

若 `repak info` 显示 `encrypted index: true`，停止自动化清单流程并记录需要用户提供合法 AES key；不要寻找或下载密钥。

在执行 `repak get` 前，必须先从 `pak.list.txt` 确认真实包内路径。不同项目可能把 `.uproject` 和 `Config/*.ini` 放在不同挂载前缀下，示例路径不能替代实际清单。

## 用户明确要求全量导出时

默认不解包 5GB+ 主包；只有用户明确说“全量导出”“全量素材级本机学习导出”或等价要求时，才在 `info/list/get` 完成后执行完整本机展开。展开前记录目标路径和磁盘空间，展开后记录文件数、总大小、耗时和源 `.pak` 前后哈希一致性。

```powershell
$out = "<study-root>\0.原始导出\PakUnpacked"
& $repak unpack -o $out $pak | Tee-Object -FilePath "<study-root>\4.临时目录\repak-unpack.log"
Get-ChildItem $out -Recurse -File | Measure-Object -Property Length -Sum
Get-FileHash -Algorithm SHA256 $pak
```

完整展开产物仍是第三方原始包文件，只能留在本机学习目录；最终交付文本只总结路径结构、数量、样本验证和抽象美术规律。若需要可视化图片，先从完整展开目录统计直接图片；项目纹理大多仍是 `.uasset/.uexp/.ubulk`，应使用 UModel 做少量样本转换验证，而不是把全量转换结果作为可复用素材包交付。

## 路径级分析

从 `pak.list.txt` 生成这些表：

- 扩展名统计：`.uasset`、`.uexp`、`.ubulk`、`.umap`、`.png`、`.locres`、`.ttf`。
- 内容一级目录统计：角色、地图、UI、技能、特效、音频、表格、核心蓝图。
- 地图区域统计：按 `Content/<Project>/Maps/<Region>` 分组。
- 角色候选：按 `Content/<Project>/Characters/<Group>/<Character>` 分组；先输出候选表，用户确认目标后再考虑导出。
- UI 候选：按 `UI`、`UMG`、`UIAssets`、`Widget`、`HUD`、`Icon`、`Portrait` 等路径关键词筛选。

输出事实只引用路径和数量。不要把角色、UI 图标、Sprite、tileset、音频作为可复用资产交付。

## UModel/UEViewer 备选

`tools/UEViewer_umodel/umodel_64.exe` 可用于资源查看或小样本导出，但大型商业 `.pak` 可能需要明确 Unreal 版本参数，或在包扫描/对象加载阶段超时。使用时必须写日志到 `<study-root>/4.临时目录`，设置明确超时；若卡住，结束进程并记录失败，回到 `repak` 路径清单分析。

```powershell
$umodel = "$skill\tools\UEViewer_umodel\umodel_64.exe"
& $umodel -help
```

对 `repak info` 显示 `version: V11`、`version major: Fnv64BugFix` 的 UE4 包，可先试 `'-game=ue4.25+'`。在 PowerShell 中必须把 `-game=ue4.25+` 整个参数加引号，否则 UModel 可能把它解析成未知的 `ue4` 标签。

```powershell
$out = "<study-root>\4.临时目录\_ascii_work\umodel_sample_export"
& $umodel -pkginfo '-game=ue4.25+' "-path=<game-root>" "JH/JHNeoUI/UIAssets/Alchemy/Alchemy00_BG"
& $umodel -export '-game=ue4.25+' -png "-out=$out" "-path=<game-root>" "JH/JHNeoUI/UIAssets/Alchemy/Alchemy00_BG"
```

UModel 是较老的 Windows 工具，输出目录使用 ASCII-only 路径；中文目录可能在日志和实际输出路径中出现乱码。

只有在用户明确要求素材级本机学习导出、目标包不加密、且工具能稳定读取时，才对少量已确认候选做导出验证。导出的原始素材只能留在本机学习目录，最终报告转译为抽象原则。

## UModel 全量转换与分片导出

当用户明确要求“全量导出图片和模型”时，先说明 `repak unpack` 的产物仍是 Cooked Unreal 项目结构，不等于 PNG/FBX 这类通用文件。要得到可查看图片和模型，必须在已解包目录上再跑 UModel/UEViewer 或等价本机工具；输出仍只用于用户授权范围内的本机学习。

全量转换前必须先做小样本版本验证：至少选择一个纹理包和一个 StaticMesh/SkeletalMesh 候选，依次尝试 `-game=ue4.25+`、`-game=ue4.26`、`-game=ue4.27` 或检测到的具体版本，并把成功/失败命令写入 `4.临时目录/工具验证记录/`。有些项目会出现“纹理能用一个标签导出、模型必须换另一个标签”的情况；不要只凭纹理样本就开始长任务。PowerShell 中包含 `+` 的参数保持整串传入，例如 `'-game=ue4.25+'`。

若 UModel 对 `JH/.../*` 这类限定通配符扫描失败，但对 `*` 可以在受限 `-path` 下工作，使用 ASCII-only 暂存根和 Windows junction 分片：把每个候选目录按原始相对路径链接到 `4.临时目录/_ascii_work/umodel-slices/inputs/<slice-id>/`，再用 `-path=<slice-input-root> *` 导出。这样既避免全包扫描过大，也能保留原始目录和资源名。不要在 `.ps1` 里硬编码中文路径；脚本用参数、`$PSScriptRoot` 或自动发现路径，避免 Windows PowerShell 5.1 把非 UTF-8 脚本内容解析成乱码。

推荐使用 `scripts/export_unreal_umodel_slices.ps1` 按 JSON manifest 分片导出。每个 slice 至少包含：

```json
{
  "slice_id": "ui_001",
  "name": "UI sample",
  "includes": [
    { "root": "Content", "rel": "Game/UI", "dest": "Game/UI" }
  ]
}
```

脚本默认执行两轮 UModel：第一轮用 `-nomesh -nostat -noanim -novert` 导出纹理和材质相关输出，第二轮做完整 `-gltf` 模型导出并使用 `-nooverwrite` 避免覆盖。若完整模型 pass 非零退出，脚本用 `rg -a -l --glob "*.uasset" "StaticMesh|SkeletalMesh|AnimSequence|AnimMontage|Skeleton"` 生成候选包列表，并逐包恢复导出。分片可以交给多个 agent 并行，但每个 agent 必须拥有互不重叠的 slice manifest 和输出 slice 目录。

输出目录采用两层组织：`raw_umodel/` 保留 UModel 原始相对路径和文件名，作为事实来源；`by_type/` 用硬链接或复制整理为 `Textures_PNG/`、`Models_GLTF/`、`Materials_Metadata/`、`Animations/` 等便于浏览的分类。正式统计以 `raw_umodel/` 与索引 CSV 为准，不把 `by_type/` 硬链接重复计入“新增资源数”。

分片完成后运行 `scripts/merge_unreal_umodel_slices.ps1` 合并 `converted_assets_index.csv`，生成 `_merged_index/all_converted_assets_index.csv`、类型统计和校验摘要。完成前检查包括：索引中的 raw 文件全部存在、大小与索引一致、零字节为 0、PNG 文件头有效、glTF JSON 可解析且 `.bin` buffer 引用存在。抽样校验可以用于中途巡检；最终交付前优先扩大到全量或记录抽样范围。

UModel 导出的 glTF 可能只包含 mesh、材质槽和 `baseColorFactor`，没有 `images/textures` 节点；Blender 能导入模型和 `.bin`，但不会自动知道 Unreal 材质实例里该挂哪张贴图。遇到这种情况，不要重新导出模型，也不要手工逐个猜贴图；先检查同一 `raw_umodel/` 树下的 `.props.txt/.mat`，里面的 `TextureParameterValues` 通常记录了 `Texture2D'Package/Texture.Texture'` 引用，而对应 PNG/HDR 已在纹理导出 pass 中生成。

模型导出后应运行 `scripts/repair_umodel_gltf_materials.py --converted-root <FullConverted> --output-root <study-root>/1.分类资源/模型/FullConverted_BlenderReady_GLTF --overwrite`。该脚本不修改原始 `FullConverted`，而是生成 Blender-ready 副本：保留 glTF 与 `.bin`，按材质名匹配最近的 `.props.txt`，把可识别的 base color、normal、emissive 贴图硬链接或复制到每个模型目录的 `_textures/`，并在 glTF 中补写 `images`、`textures`、`baseColorTexture`、`normalTexture` 或 `emissiveTexture`。若某些材质使用复杂 Unreal shader、遮罩、扰动、runtime virtual texture 或无法分类的参数，脚本应在 `material_repair_index.csv` 中标为未修补/无法分类，而不是伪造材质。

UModel 对某些 VFX/shape mesh 会把 glTF 材质名写成 `dummy_material_0`，而真实材质在粒子系统、Niagara/Cascade 模块或运行时参数里绑定，mesh glTF 本身没有静态材质槽名。遇到这种情况，修补脚本可以按 mesh 文件名、同级/近路径 `.props.txt`、贴图对象名做保守回退匹配，例如 mesh 名命中 `Kunai` 且材质/贴图命中 `T_Kunai_Mesh`；但不得把 `T_Noise`、`Mask`、`Distortion`、`Roughness`、`Metallic` 等特效辅助贴图误接成 base color 或 normal。若 cooked mesh 与 `.props.txt` 都没有贴图引用，保持 `no_props` 或 `no_texture_refs`，并在报告中说明这类模型通常只是特效形状载体，需要从粒子系统材质绑定继续追踪。

修补后再做 Blender 导入验证：导入 `FullConverted_BlenderReady_GLTF/<slice-id>/.../*.gltf`，不要继续导入原始 `FullConverted/.../raw_umodel` 或 `by_type/Models_GLTF` 下的 glTF。完成前检查包括：修补版 glTF 全部 JSON 可解析、外部 `.bin` 存在、`images[*].uri` 指向的 `_textures` 文件存在、`material_repair_summary.json` 记录总 glTF 数、修补 glTF 数、修补材质数和链接贴图数。

Blender 验证要先区分“资产缺材质”和“启动参数错误”。Windows 路径常同时包含空格和中文，使用 PowerShell `Start-Process -ArgumentList @('--python', '<path>')` 时容易把带空格的目录名拆开，Blender 会把 `.py` 当成要加载的工程文件并弹出 `Unable to Load File` / `Cannot read file ... .py`。推荐流程是：先运行 `& "<blender.exe>" --background --python "<preview.py>"` 做后台导入验证；GUI 预览用 `scripts/launch_blender_python.cmd "<blender.exe>" "<preview.py>"` 或单字符串参数 `Start-Process -FilePath "<blender.exe>" -ArgumentList '--python "<preview.py>"'`。预览脚本应从 `__file__` 推导 study-root 并发现 `1.分类资源/模型` 导出目录，不要硬编码中文目录名，避免编码乱码导致指向不存在的路径。

不要承诺 UModel 能直接导出 FBX。UModel 常见直接产物是 PNG、材质文本、glTF/GLB 或 PSK/PSKX/PSA 等；若用户确实需要 FBX，先交付 glTF/PSK 系列和索引，再用 Blender、ActorX Importer 或其他本机转换链另行转换，并单独记录工具版本、失败项和命名映射。

## 报告要点

- 观察到的事实：引擎信号、`.pak` 信息、文件条目数、主目录分布、关键配置、默认地图、启用插件、候选路径数量。
- 我的判断：视角/玩法线索、2D/3D 资产组织方式、UI 信息密度、动效/特效分层、制作约束。
- 待验证假设：未打开素材时不能断言具体调色板、角色造型、帧率或材质；这些只能在合法截图、用户确认导出样本或公开资料中验证。

## 验证

- 源游戏目录没有写入或修改。
- `<study-root>/4.临时目录/_ascii_work/pak.info.txt` 与 `pak.list.txt` 已生成。
- 报告记录 `encrypted index`、mount point、version、compression 和 file entries。
- 若用户明确要求全量导出，`<study-root>/0.原始导出/PakUnpacked` 已生成，并记录文件数、体积、耗时和源 `.pak` 前后哈希。
- 若 UModel 尝试失败，记录命令、日志路径、超时和进程清理结果。
- 最终文本通过 `guideline_lint.py`，没有暗示上传、发布、复用或分发第三方原始资源。
