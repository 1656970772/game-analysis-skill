# 全量素材与关键角色动画流程

## 适用边界

仅在用户明确授权本机学习目标目录时使用。输入是用户指定的游戏安装目录或已导出的 Unity 工程；输出写入单独学习目录。若用户未指定输出目录，学习目录必须创建在当前 Codex 项目工作区目录下，并以游戏项目名命名；不要默认放在源游戏目录旁边、SteamLibrary/common 或游戏安装目录内。不要上传、发布、出售或嵌入第三方原始资源。报告里把“观察到的事实”“我的判断”“待验证假设”分开。

依据：本流程来自用户授权的 Karma Exorcist 本地学习任务；工具来源见 `references/tooling-manifest.json`；AssetRipper 本地 Web 应用可用 `--headless` 启动，避免自动打开浏览器；接口定义可从本地 `/openapi.json` 读取，常用 HTTP 端点包括 `/LoadFile`、`/LoadFolder`、`/Export/UnityProject`、`/Export/PrimaryContent`。

## 推荐目录

```text
<workspace>/<游戏项目名>/       # 用户未指定输出目录时的默认 study-root；不要放到源游戏目录旁边
  0.原始导出/                  # 工具全量导出的原始目录，保持工具结构
  1.分类资源/                  # 从原始导出解析整理出的图片、模型、特效等最终分类资源
  2.报告/                      # 编号报告、HTML 画廊入口、可选 PDF
  3.代码/                      # 可导出代码，或不可导出的具体原因
  4.临时目录/                  # 工具验证记录和中间文件
```

根目录卫生是强约束：AssetRipper 工程、UnityPy 原始导出、脚本直接生成的 CSV/JSON/Markdown、验证日志、manifest、分类摘要、临时全量画廊、EXE 构建目录都必须放入 `4.临时目录/工具产物/`、`4.临时目录/中间索引/`、`4.临时目录/工具日志/` 或对应 0-4 交付目录。若旧脚本或 GUI 临时写到根目录，阶段结束后立即移动并记录到 `4.临时目录/工具日志/路径归档记录.md`。CSV/JSON、验证日志、manifest、分类摘要统一写入 `4.临时目录`。

GameMaker 项目使用同样的项目名根目录，但把 UTMT 原始导出放在 `<project-root>/4.临时目录/_ascii_work`，再整理回 0-4 交付目录；详见 `references/gamemaker-datawin-workflow.md`。

Unreal `.pak` 项目也使用同样的项目名根目录，但默认先把 `repak info/list/get` 结果写入 `<project-root>/4.临时目录/_ascii_work`，只做包信息、路径清单和小文本配置抽取；详见 `references/unreal-pak-workflow.md`。若用户明确要求全量素材级本机导出，完整展开写入 `<project-root>/0.原始导出/PakUnpacked`，并记录源 `.pak` 哈希、文件数、总大小和耗时。不要用 Unity 的 AssetRipper/UnityPy 路线替代 Unreal 路线。

## Unity 全量提取

1. 先运行只读识别：

```powershell
python <skill>\scripts\detect_engine.py "<game-root>" --format markdown
python <skill>\scripts\asset_inventory.py "<game-root>" --format json
```

如果识别为 Unity 构建，先运行 Unity 预检，再决定重工具顺序：

```powershell
python <skill>\scripts\unity_preflight.py "<game-root>" --format markdown
python <skill>\scripts\unity_preflight.py "<game-root>" --format json
```

预检只读读取 `app.info`、`boot.config`、`ScriptingAssemblies.json`、IL2CPP metadata 头部和文件名/大小，不解包素材。它用于提前标记脚本后端、metadata 版本、`.resS/.resource` 外部载荷、`StreamingAssets`、native plugins 和下一步 workflow flags。我的判断：Forestrike 这类 Unity IL2CPP / metadata 31 项目应在 AssetRipper 前就排入 Il2CppDumper 与外部载荷核验任务，避免等 C# stub 或画廊缺资源时才回头定位原因。

2. 优先用 Skill 内 AssetRipper 本地 Web/API 自动导出：

```powershell
$assetRipper = "<skill>\tools\AssetRipper_1.3.14\AssetRipper.GUI.Free.exe"
$port = 8177
$logPath = "<study-root>\4.临时目录\命令日志\assetripper.log"
$exportPath = "<study-root>\0.原始导出\AssetRipper\ExportedProject"

New-Item -ItemType Directory -Force -Path (Split-Path $logPath), $exportPath | Out-Null
$proc = Start-Process -FilePath $assetRipper -ArgumentList @(
  "--headless=true",
  "--port=$port",
  "--log=true",
  "--log-path=$logPath"
) -WindowStyle Hidden -PassThru

$baseUrl = "http://127.0.0.1:$port"
Invoke-WebRequest -Uri "$baseUrl/openapi.json" -OutFile "<study-root>\4.临时目录\命令日志\assetripper-openapi.json"
Invoke-WebRequest -Uri "$baseUrl/LoadFolder" -Method Post -Body @{ path = "<game-root>" }
Invoke-WebRequest -Uri "$baseUrl/Export/UnityProject" -Method Post -Body @{ path = $exportPath }
```

如果输入是单个 Unity 文件，用 `/LoadFile` 代替 `/LoadFolder`；如果只需要主内容或 UnityProject 导出失败后要保留可用内容，用 `/Export/PrimaryContent` 作为补充导出。命令日志必须记录端口、进程 ID、加载路径、导出路径、HTTP 状态和失败响应。这是“全部素材/场景/Prefab/ProjectSettings”学习的主路线；不要把工程主体长期放在 study-root 根目录。只有本地 API 启动或端点调用失败时，才打开 GUI/Web 页面手工选择用户授权的 `<game-root>` 并记录失败原因。

3. AssetRipper 完成后，安装 UnityPy/Pillow 到 Skill 内并定义 Python 解释器：

```powershell
powershell -ExecutionPolicy Bypass -File <skill>\scripts\setup_python_env.ps1
$py = "<skill>\tools\python-env\Scripts\python.exe"
```

4. 再用 UnityPy 扫描目标目录中所有可读 Unity 容器，生成补充索引和可读对象导出：

```powershell
& $py <skill>\scripts\extract_all_readable_resources.py --source "<game-root>" --output "<study-root>\0.原始导出\UnityPy\UnityPy全量可读资源" --audio-mode metadata
```

5. 把 UnityPy 产物整理回项目名根目录的 0-4 交付结构：

```powershell
& $py <skill>\scripts\organize_unitypy_project_export.py `
  --index "<study-root>\0.原始导出\UnityPy\UnityPy全量可读资源\all_resources_index.csv" `
  --unitypy-root "<study-root>\0.原始导出\UnityPy\UnityPy全量可读资源" `
  --study-root "<study-root>" `
  --project-name "<游戏名>"
```

该步骤会把真实资源复制到 `1.分类资源/`，并把 `资源组织索引.csv`、`动画序列帧索引.csv`、`资源组织摘要.json` 和 `主角资源候选索引.csv` 写入 `4.临时目录/中间索引/UnityPy组织/`。默认保留游戏内实际资源名；只有检测为序列帧时，才在动作目录内归一化为 `idle_01.png`、`attack_01.png`、`wall_run_01.png` 这类命名。索引必须保留原始 asset 名、原始容器路径和导出路径。若 UnityPy/AssetRipper 已经导出图片、贴图、模型、材质、Shader、音频样本、字体等真实资源文件，必须直接位于 `1.分类资源/` 对应分类目录中。能判断角色/场景/道具/特效用途的资源，要进入对应用途子目录；用途无法判断时才进入 `未分类_待复核/`。不能只生成 CSV/JSON，也不要另建图片/模型汇总入口。

整理规则：UI、角色立绘、场景、道具、图集、序列帧统一位于 `1.分类资源/图片/` 下；真实音频样本进入 `1.分类资源/音频/`；AssetRipper 导出的 Shader、Material、RenderTexture 等进入 `1.分类资源/渲染/`。UnityPy 的 `metadata/*.json`、CSV、TXT、MD 不复制到分类资源，只进入原始导出或学习索引；组织摘要必须记录 `skipped_metadata_count`。空目录不放 `_empty_reason.md`，缺失原因写入 `2.报告/00_研究总览.md` 和临时验证日志。

如果在 `1.分类资源/` 发现 `.json/.csv/.txt/.md`，视为组织步骤失败并优先修复/重跑组织脚本，不用宽泛的 PowerShell `Get-ChildItem -Include` 事后搬移。我的判断：这类清理命令在递归、`-LiteralPath` 和通配符组合下容易产生过滤误判，流程上应把元数据挡在复制入口，而不是复制后再清理。

我的判断：AssetRipper 更适合还原场景搭建、Prefab 和工程结构；UnityPy 更适合脚本化导出 Sprite/Texture2D/TextAsset、AudioClip 元数据及动画帧。两条产物互为补充，不能用其中一条替代另一条。AudioClip 样本导出需显式改为 `--audio-mode samples`；Forestrike 验证中，UnityPy 经 FMOD native 解码音频样本会触发进程崩溃，因此默认不触碰 `AudioClip.samples`。若发现实际模型文件或 AssetRipper 后得到 `.fbx`、`.obj`、`.gltf`、`.glb`，需要的查看/转换工具下载到本 Skill 的 `tools/` 目录并记录来源、版本和用途；若只有 Mesh/MeshRenderer 元数据，先在 `4.临时目录` 记录为待验证。

## IL2CPP 与空 C# 方法

如果 `ExportedProject` 中的 C# 只有字段、属性和方法签名，方法体为空或只返回默认值，不要直接判断“没有逻辑”或“导出失败”。先按 `references/il2cpp-native-code-workflow.md` 判定 Mono/IL2CPP 分支：

```powershell
Get-ChildItem "<game-root>" -Filter "GameAssembly.dll" -Recurse
Get-ChildItem "<game-root>" -Filter "global-metadata.dat" -Recurse
```

若预检或文件检查找到 `GameAssembly.dll + global-metadata.dat`，用 Il2CppDumper 生成 `IL2CPP_Dump/dump.cs`、`script.json`、`il2cpp.h` 和 `DummyDll/`，再用 Ghidra 或用户许可下的 IDA 查看 native 方法体。`DummyDll` 只用于 IDE/反编译器引用，不是完整源码。报告必须写明 IL2CPP 分析是否完成、失败原因和待验证方法清单。metadata 版本高于 29 时，不把 Cpp2IL 2022 放在第一路线；若尝试并失败，只记录为备用工具兼容性边界。

`full` 与 `code` 模式下，若用户没有明确接受“关键样本分析”，IL2CPP native 部分必须按 `references/il2cpp-native-code-workflow.md` 的 Full Code 批量导出路线执行：从 `dump.cs` 生成全量或模块化 targets，Ghidra 用 `-noanalysis` 分批反编译，并输出 `ghidra_decompile_coverage.csv/json`。Ghidra 全量 auto-analysis 只作为调用图、交叉引用、常量引用质量提升任务，不是批量代码导出的默认前置步骤。

## 场景搭建与图片索引

```powershell
python <skill>\scripts\analyze_assetripper_project.py --project-root "<study-root>\0.原始导出\AssetRipper\ExportedProject" --output-dir "<study-root>\4.临时目录\中间索引\AssetRipper分析" --title "<游戏名>"
python <skill>\scripts\build_full_image_gallery.py --project-root "<study-root>\0.原始导出\AssetRipper\ExportedProject" --image-index "<study-root>\4.临时目录\中间索引\AssetRipper分析\图片资源尺寸索引.csv" --gallery-file "<study-root>\2.报告\全量图片画廊.html" --manifest "<study-root>\4.临时目录\中间索引\全量图片画廊_manifest.json" --original-prefix "../0.原始导出/AssetRipper/ExportedProject/" --title "<游戏名> 全量图片资源画廊"
```

`analyze_assetripper_project.py` 会直接生成 `导出资源文件清单.csv`、`场景索引.csv`、`Prefab索引.csv`、`图片资源尺寸索引.csv`、`场景搭建分析.md` 和 `导出分析摘要.json`；这些直接输出必须留在 `4.临时目录/中间索引/AssetRipper分析/`。需要给用户验收的版本再复制或汇总到 `2.报告/`。

画廊要求：点击卡片在当前页覆盖层预览；再次点击预览图、背景或按 Esc 关闭；在 Electron 壳里右键卡片或预览图可在资源管理器中定位素材。

## 关键角色全部动画

1. 在 `ExportedProject` 内定位关键角色 AnimatorController，例如：

```powershell
Get-ChildItem "<study-root>\0.原始导出\AssetRipper\ExportedProject\Assets" -Recurse -Filter "*.controller" |
  Select-Object FullName
```

2. 找到包含关键角色 Sprite 的原始 bundle。Unity Addressables 项目通常在：

```text
<game-root>\*_Data\StreamingAssets\aa\StandaloneWindows64\*.bundle
```

3. 如果 `ExportedProject` 里已经有 AssetRipper 导出的 `Assets/Sprite/*.asset` 与 `Assets/Texture2D/*.png`，优先从导出工程裁切 Sprite：

```powershell
python <skill>\scripts\export_key_character_animations_from_assetripper.py `
  --project-root "<study-root>\0.原始导出\AssetRipper\ExportedProject" `
  --controller "Assets/AnimatorController/HeroAC.controller" `
  --character-name "主角" `
  --output-dir "<study-root>\1.分类资源\图片\序列帧\主角\AssetRipper切片导出" `
  --overwrite
```

4. 随后用 UnityPy 从可读原始 bundle 补充和对照该控制器引用的所有 Sprite 帧：

```powershell
& $py <skill>\scripts\export_key_character_animations_unitypy.py `
  --project-root "<study-root>\0.原始导出\AssetRipper\ExportedProject" `
  --controller "Assets/AnimatorController/HeroAC.controller" `
  --source-bundle "<game-root>\Game_Data\StreamingAssets\aa\StandaloneWindows64\xxx.bundle" `
  --character-name "主角" `
  --output-dir "<study-root>\1.分类资源\图片\序列帧\主角\UnityPy补充导出" `
  --overwrite
```

命名规则：优先使用游戏内实际动作名，转小写，符号转 `_`，帧号使用 1-based 两位序号，例如 `idle_01.png`、`attack_01.png`、`wall_run_01.png`。不要把帧改成日期、随机串或纯 path id；如果源资源里只有 `Hero_Flip_1` 这类名称，整理后的文件放入 `Hero/flip/flip_01.png`，索引保留原始 `Hero_Flip_1`。如果目标角色是骨骼/切片式 2D 动画，导出的通常是每段动画引用到的局部 Sprite 切片；完整动作还需要结合 `.anim` 的 Transform/Rotation/Scale 曲线学习。

## 学习索引与临时目录卫生

生成报告前必须先汇总学习型索引：

```powershell
python <skill>\scripts\build_learning_indices.py --study-root "<study-root>"
```

`build_learning_indices.py` 输出到 `4.临时目录/中间索引/学习输出/`，包括美术资源尺寸分布、色彩摘要输入、渲染资源索引、音频资源索引、动画资源索引、命名规范统计和代码命名空间索引。编号报告从这些索引提炼结论，不把 CSV/JSON 复制到 `2.报告/`。

交付前审计临时目录：

```powershell
python <skill>\scripts\audit_study_root_hygiene.py --study-root "<study-root>"
python <skill>\scripts\audit_study_root_hygiene.py --study-root "<study-root>" --clean-browser-profiles
```

`audit_study_root_hygiene.py` 默认只检查并写入 `4.临时目录/工具日志/临时目录卫生审计.json`；只有用户要求交付清理或最终打包前，才使用 `--clean-browser-profiles` 删除 `_ascii_work` 中 Chrome/Edge 浏览器 profile。不得删除 Ghidra 项目、工具输入、失败记录或学习索引。

## 打包 EXE

```powershell
powershell -ExecutionPolicy Bypass -File <skill>\scripts\build_gallery_exe.ps1 -StudyRoot "<study-root>" -OutputExeName "GameArtStudyGallery.exe"
```

EXE 构建产物写入 `2.报告/`，构建工作目录写入 `4.临时目录/工具产物/gallery-exe/`。EXE 需要和 `2.报告/全量图片画廊.html`、`0.原始导出/AssetRipper/ExportedProject/` 或 `1.分类资源/` 保持在同一个 `<study-root>` 下；可移动整个 `<study-root>`，不要只移动 EXE。Windows PowerShell 里要显式调用 `npm.cmd`，避免执行策略拦截 `npm.ps1`。

## 验证

- 源游戏目录文件数量和关键文件哈希没有变化。
- Unity 构建已保存 `unity_preflight.py` 输出；其中的 workflow flags 已被转成 AssetRipper、UnityPy、Il2CppDumper、外部载荷核验和 native plugins 边界任务。
- `<study-root>\0.原始导出\AssetRipper\ExportedProject` 已由 AssetRipper 生成；`<study-root>\0.原始导出\UnityPy\UnityPy全量可读资源\summary.json` 与 `all_resources_index.csv` 已由 UnityPy 生成。
- UnityPy 组织步骤已把真实资源写入 `1.分类资源/`，并在 `4.临时目录\中间索引\UnityPy组织\` 下生成 `资源组织索引.csv`、`动画序列帧索引.csv`、`资源组织摘要.json` 和 `主角资源候选索引.csv`；非序列资源尽量保留游戏实际命名，序列帧使用动作名加 1-based 两位序号。
- `1.分类资源/` 不包含 UnityPy 元数据 `.json/.csv/.txt/.md`；`资源组织摘要.json` 记录 `skipped_metadata_count`，元数据仍保留在 `0.原始导出/UnityPy/` 或 `4.临时目录/中间索引/`。
- `full`/`code` 模式下若存在 IL2CPP，`4.临时目录/中间索引/Native分析/` 包含 targets CSV、targets manifest、`ghidra_decompile_coverage.csv/json`；报告写明 target/OK/failed/missing/coverage。若只做关键样本，必须明确标为“样本分析”，不得称为全部代码导出。
- 真实资源文件检测已生成 `4.临时目录\自测结果\真实资源文件检测_分类目录.json`；分类目录中的图片、模型、材质/Shader、音频、字体等真实资源计数大于 0，或报告中说明为什么只能导出 metadata。
- `4.临时目录/中间索引/学习输出/` 下包含 `美术资源尺寸分布.csv`、`色彩摘要输入.csv`、`渲染资源索引.csv`、`音频资源索引.csv`、`动画资源索引.csv`、`命名规范统计.csv`、`代码命名空间索引.csv` 和 `学习输出摘要.json`。
- `4.临时目录/中间索引/AssetRipper分析/` 下包含导出清单、场景索引、Prefab 索引、图片尺寸索引；`2.报告/全量图片画廊.html` 存在，画廊 manifest 位于 `4.临时目录/中间索引/全量图片画廊_manifest.json`。
- `<study-root>` 根目录没有未归档的工具产物、临时画廊或散落索引文件。
- `2.报告/` 只包含 `00_研究总览.md` 到 `09_可迁移技术清单.md`、`全量图片画廊.html` 和可选 PDF；没有 CSV/JSON、验证日志、manifest 或分类摘要。
- 临时目录卫生审计已运行；交付清理时浏览器 profile 已清理且非浏览器工具中间文件未误删。
- 画廊能显示缩略图，覆盖层能打开和关闭，浏览器地址不跳到单张 PNG。
- EXE `npm run self-test` 或启动测试能找到 `全量图片画廊/index.html` 与 `ExportedProject`。
- 关键角色动画 CSV 中每帧都有动画名、原 Sprite 名、GUID、尺寸、状态和输出文件。
- 最终原创规范覆盖来源边界、色彩/调色、形状/轮廓、UI/HUD 可读性和制作规格/约束，并明确哪些属于“我的判断”、哪些属于“待验证假设”。
- 最终报告和规范通过 `guideline_lint.py`，没有引导公开传播或直接复用原始资源的表述。
