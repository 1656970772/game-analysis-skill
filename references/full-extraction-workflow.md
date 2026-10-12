# 全量素材与关键角色动画流程

## 适用边界

仅在用户明确授权本机学习目标目录时使用。输入是用户指定的游戏安装目录或已导出的 Unity 工程；输出写入单独学习目录。若用户未指定输出目录，学习目录必须创建在当前 Codex 项目工作区目录下，并以游戏项目名命名；不要默认放在源游戏目录旁边、SteamLibrary/common 或游戏安装目录内。不要上传、发布、出售或嵌入第三方原始资源。报告里把“观察到的事实”“我的判断”“待验证假设”分开。

依据：本流程来自用户授权的已验证本地学习任务；工具来源见 `references/tooling-manifest.json`；AssetRipper 本地 Web 应用可用 `--headless` 启动，避免自动打开浏览器；接口定义可从本地 `/openapi.json` 读取，常用 HTTP 端点包括 `/LoadFile`、`/LoadFolder`、`/Export/UnityProject`、`/Export/PrimaryContent`。

## 推荐目录

```text
<workspace>/<游戏项目名>/       # 用户未指定输出目录时的默认 study-root；不要放到源游戏目录旁边
  0.原始导出/                  # 工具全量导出的原始目录，保持工具结构
  1.分类资源/                  # 从原始导出解析整理出的图片、模型、特效等最终分类资源
  2.报告/                      # 编号报告、图片/模型/特效预览 BAT 入口、可选 PDF
  3.代码/                      # 可导出代码，或不可导出的具体原因
  4.临时目录/                  # 工具验证记录和中间文件
```

根目录卫生是强约束：AssetRipper 工程、UnityPy 原始导出、脚本直接生成的 CSV/JSON/Markdown、验证日志、manifest、分类摘要、缩略图缓存和工具构建目录都必须放入 `4.临时目录/工具产物/`、`4.临时目录/中间索引/`、`4.临时目录/工具日志/` 或对应 0-4 交付目录。每次 study-root 的图片画廊入口只在 `2.报告/打开图片画廊.bat`；manifest、PNG 缩略图和错误日志固定在 `4.临时目录/中间索引/图片画廊/`。模型预览启动入口只允许进入 `2.报告/打开模型预览.bat`，不得额外生成 `模型预览.bat` 等重复别名；模型 manifest、thumbs、preview-cache 和 errors 固定在 `4.临时目录/中间索引/模型预览/`。特效预览启动入口只允许进入 `2.报告/打开特效预览.bat`；VFX manifest、errors、reconstructed、preview-cache 和 unity-export 固定在 `4.临时目录/中间索引/VFX预览/`。若旧脚本或 GUI 临时写到根目录，阶段结束后立即移动并记录到 `4.临时目录/工具日志/路径归档记录.md`。CSV/JSON、验证日志、manifest、缩略图、分类摘要统一写入 `4.临时目录`。

GameMaker 项目使用同样的项目名根目录，但把 UTMT 原始导出放在 `<project-root>/4.临时目录/_ascii_work`，再整理回 0-4 交付目录；详见 `references/gamemaker-datawin-workflow.md`。

Unreal `.pak` 项目也使用同样的项目名根目录，但默认先把 `repak info/list/get` 结果写入 `<project-root>/4.临时目录/_ascii_work`，只做包信息、路径清单和小文本配置抽取；详见 `references/unreal-pak-workflow.md`。若用户明确要求全量素材级本机导出，完整展开写入 `<project-root>/0.原始导出/PakUnpacked`，并记录源 `.pak` 哈希、文件数、总大小和耗时。不要用 Unity 的 AssetRipper/UnityPy 路线替代 Unreal 路线。

Godot 3.x 独立 `.pck` 项目先读取 `references/godot-pck-workflow.md`，按 `detect→PCK probe→GDRE list→scripts-only→full recover→organize` 门禁执行。GDRETools 完整恢复固定写入 `<project-root>/0.原始导出/GDRETools恢复`，分类/失败索引固定写入 `<project-root>/4.临时目录/中间索引/Godot`；不得执行游戏 EXE，也不得用 Unity/Unreal 工具链替代 Godot 分支。

## Unity 全量提取

1. 先运行只读识别：

```powershell
pwsh -NoLogo -NoProfile -File <skill>\scripts\invoke_python_utf8.ps1 -Script <skill>\scripts\detect_engine.py "<game-root>" --format markdown
pwsh -NoLogo -NoProfile -File <skill>\scripts\invoke_python_utf8.ps1 -Script <skill>\scripts\asset_inventory.py "<game-root>" --format json
```

如果识别为 Unity 构建，先运行 Unity 预检，再决定重工具顺序：

```powershell
pwsh -NoLogo -NoProfile -File <skill>\scripts\invoke_python_utf8.ps1 -Script <skill>\scripts\unity_preflight.py "<game-root>" --format markdown
pwsh -NoLogo -NoProfile -File <skill>\scripts\invoke_python_utf8.ps1 -Script <skill>\scripts\unity_preflight.py "<game-root>" --format json
```

预检只读读取 `app.info`、`boot.config`、`ScriptingAssemblies.json`、IL2CPP metadata 头部和文件名/大小，不解包素材。它用于提前标记脚本后端、metadata 版本、`.resS/.resource` 外部载荷、`StreamingAssets`、native plugins 和下一步 workflow flags。我的判断：Forestrike 这类 Unity IL2CPP / metadata 31 项目应在 AssetRipper 前就排入 Il2CppDumper 与外部载荷核验任务，避免等 C# stub 或画廊缺资源时才回头定位原因。

若 `*_Data/Res` 或 `*_Data/StreamingAssets` 中存在可直接读取的 loose JSON、图片、文本、视频或其他配置化类型，先用独立流程归档，避免这些非容器资源被 UnityPy/AssetRipper 完成状态掩盖：

```powershell
pwsh -NoLogo -NoProfile -File <skill>\scripts\invoke_python_utf8.ps1 `
  -Script <skill>\scripts\export_unity_loose_resources.py `
  --source "<game-root>" `
  --study-root "<study-root>" `
  --config <skill>\references\unity-loose-resource-config.json
```

该脚本把原始文件保持相对路径归档到 `0.原始导出/UnityLooseResources/`，把索引写入 `4.临时目录/中间索引/UnityLooseResources/`；只有图片可进入 `1.分类资源/图片/场景|角色立绘|UI|道具|未分类_待复核`，JSON/文本/视频/二进制不得混入图片分类。重复运行应复用同 SHA-256 文件，不同内容的目标冲突必须记录且不得覆盖；源扫描拒绝符号链接、Windows reparse point、绝对配置路径和 `..`。分类覆盖率不是目标，不能为了消灭 `未分类_待复核` 而强行归类。

归档发现成批 loose JSON 时，再建立不复制字段值的数据结构地图：

```powershell
pwsh -NoLogo -NoProfile -File <skill>\scripts\invoke_python_utf8.ps1 `
  -Script <skill>\scripts\analyze_loose_json_tables.py `
  --source "<授权 JSON 共同根>" `
  --output "<study-root>\4.临时目录\中间索引\LooseJSON分析" `
  --config <skill>\references\loose-json-analysis-config.json `
  --expected-file-count <归档索引中的 JSON 数> `
  --expected-total-bytes <归档索引中的 JSON 总字节>
```

默认配置适用于 `d_<module>.py.<table>.json` 一类数据表；其他命名约定应复制为项目配置并调整正则、扫描根与词法候选，不在脚本里写死项目名或当前数量。完成状态要求严格 JSON 成功/失败集合、CSV 行数/字节、预期基线、源前后快照和确定性 SHA-256 抽样都能对账。`record_count` 只是顶层结构成员数，文件名/字段名的系统候选只是检索信号；进入玩法报告前必须与加载代码、字段消费或运行时证据交叉验证。

2. 优先用 Skill 内 AssetRipper 本地 Web/API 自动导出：

```powershell
<skill>\scripts\export_unity_assetripper_project.ps1 `
  -SourcePath "<game-root>" `
  -StudyRoot "<study-root>" `
  -SkillRoot "<skill>" `
  -Port 8177 `
  -PrimaryContentFallback
```

脚本会 headless 启动本地 Web/API，读取并保存 `/openapi.json`，再按目录/文件自动调用 `/LoadFolder` 或 `/LoadFile`，随后调用 `/Export/UnityProject`，必要时可回退 `/Export/PrimaryContent`。命令日志必须记录端口、进程 ID、加载路径、导出路径、HTTP 状态和失败响应，固定写入 `<study-root>\4.临时目录\工具日志\AssetRipper\`。这是“全部素材/场景/Prefab/ProjectSettings”学习的主路线；不要把工程主体长期放在 study-root 根目录。只有本地 API 启动或端点调用失败时，才打开 GUI/Web 页面手工选择用户授权的 `<game-root>` 并记录失败原因。

Windows 注意：AssetRipper 1.3.14 的 `--log-path` 指向带中文或空格的路径时可能直接退出且不生成日志；脚本默认把 AssetRipper runtime log 写入 ASCII-only `C:\CodexTemp\GameAnalysis_AssetRipper\...`，阶段结束再复制回 study-root。不要把这个 runtime log 目录当作交付产物。

3. AssetRipper 完成后，安装 UnityPy/Pillow 到 Skill 内并定义 Python 解释器：

```powershell
powershell -ExecutionPolicy Bypass -File <skill>\scripts\setup_python_env.ps1
$py = "<skill>\tools\python-env\Scripts\python.exe"
```

若 AssetRipper `Assets` 已含真实音频或渲染文件，先用配置化分类器保持来源相对路径归档；不要把 `.meta`、AudioClip metadata 或分类索引混入 `1.分类资源/`：

```powershell
pwsh -NoLogo -NoProfile -File <skill>\scripts\invoke_python_utf8.ps1 -Script <skill>\scripts\organize_assetripper_audio_resources.py --study-root "<study-root>" --config <skill>\references\assetripper-audio-classification.json
pwsh -NoLogo -NoProfile -File <skill>\scripts\invoke_python_utf8.ps1 -Script <skill>\scripts\organize_assetripper_render_resources.py --study-root "<study-root>" --config <skill>\references\assetripper-render-classification.json
```

音频完成状态要求候选数、分类数、总字节和确定性 manifest SHA-256 一致，missing/extra/impure/conflict/failed 为 0，源前后快照一致；索引固定留在 `4.临时目录/中间索引/AssetRipper音频分类/`。分类器优先同卷硬链接并在失败时回退复制。硬链接分类副本与 AssetRipper 原始导出共享文件实体，后续一律只读；需要编辑、转码或加工时先复制到独立工作副本。

4. 再用 UnityPy 扫描目标目录中所有可读 Unity 容器，生成补充索引和可读对象导出：

```powershell
pwsh -NoLogo -NoProfile -File <skill>\scripts\invoke_python_utf8.ps1 -Script <skill>\scripts\extract_all_readable_resources.py --source "<game-root>" --output "<study-root>\0.原始导出\UnityPy\UnityPy全量可读资源" --audio-mode metadata
```

UnityFS bounded-memory 路由有两个独立门槛：`--large-bundle-threshold-mib` 按单个解压 block 大小判断，`--large-bundle-node-threshold-mib` 按单个 node 的未压缩大小判断；两者默认均为 512 MiB，任一达到即进入 spool + 短生命周期 worker。大量 128 KiB 小 block 也可能共同组成几十 MiB 或更大的 serialized node，不能只按物理 bundle 大小或最大 block 判断内存风险。只读 `probe_unityfs_layout` 证据显示“大 node + 小 blocks”且普通 UnityPy 路径工作集持续增长时，可在 source 事务安全中断并确认无 partial 后，用配置化 node threshold（例如按机器内存预算选择 16 MiB）和较小 `--large-bundle-batch-size` no-reprocess resume；阈值是运行参数，必须记录到 checkpoint `last_run.large_bundle_options` 与 summary `large_bundle`，不得把旧进程产物冒充为新路由结果。

若 checkpoint 中少数 source 已完成、但因 exporter 修复或定向核验需要重做，只能通过可重复的精确 source action 重处理，不能手改 checkpoint：

```powershell
pwsh -NoLogo -NoProfile -File <skill>\scripts\invoke_python_utf8.ps1 -Script <skill>\scripts\extract_all_readable_resources.py --source "<game-root>" --output "<existing-unitypy-output>" --audio-mode metadata --reprocess-source "<exact/relative/source-label>"
```

**protocol 1 v3 历史规则（取代下文旧的“失败可留下未引用历史记录”表述）**：每个新 sidecar 都必须绑定前一 head transaction id、前一 sidecar SHA-256、前一 count/chain SHA-256，并声明新的 head/count/chain SHA-256；summary 与 checkpoint 必须共同锚定同一历史值。当前控制面没有 `group_transactions` 引用且目录没有合法 sidecar 时，只允许建立唯一 genesis；已经由控制面锚定的旧 v2 sidecar 只能作为显式 singleton legacy boundary，不能按“局部 schema 合法”自动吸收其它旧文件。validator/snapshot 从控制面 head 反向重建唯一 ordered chain，并把完整链纳入 immutable exact set；extra、missing predecessor、fork、cycle、duplicate transaction、前驱摘要篡改、head/count/chain 不一致、nested/illegal/reparse 一律 fail closed。sidecar 先行发布而控制面未提交时，只有严格 v3 schema、前驱正好等于旧 head/count/chain、计划 payload 与两个旧控制文件精确匹配的唯一 next tail 才视为 interrupted tail；下一轮须在相同精确旧代租约内恢复提交或清理，否则拒绝，不能永久当作任意 extra。

`--reprocess-source` 可重复传入。脚本先完成 source 发现，再在任何 checkpoint 状态变化前校验每个值非空且与发现到的 POSIX 风格相对 label 精确一致；空值或未知 label 会 fail closed，不改变 checkpoint 或 fragment。protocol 2 下，被点名且已 `completed` 的 source 复用既有 source fragment 的 `.partial` + 原子替换路径，其余 completed source 仍验证 fragment 哈希并跳过；本轮 requested/reprocessed 集合与计数写入 `summary.json` 的 `resume` 和 checkpoint 的 `last_run`。protocol 1 不允许 reprocess、pending 或 retry，必须改用新的 protocol 2 输出目录。

source 授权根必须使用调用方给出的 lexical 路径固定，不能先 `resolve()` 后丢失“根本身就是 junction”的证据。发现前拒绝 source root 祖先链、根内每一级目录和最终文件的 symlink/junction/reparse；每个候选的 canonical 路径必须仍在 canonical source root 内。脚本先枚举并授权整棵 source 文件树，再为所有授权文件取得稳定读租约，发现、catalog/payload 解析、UnityPy 读取和成功提交前均复核同一文件代；Windows 下拒绝 write/delete 的候选文件租约同时阻止其父链被换成 junction。独立验收器必须自行执行同样的 lexical/canonical/reparse 全树门禁并持有自己的稳定租约，不能把 exporter 的发现结果当作授权证明。

新建输出默认使用 resume protocol 2：`asset_id` 保留短可读前缀，并追加由完整原始 `source_label + serialized_file + path_id` 计算的 64 位 SHA-256；checkpoint identity 与顶层 `asset_identity` 必须同时持久化 scheme/version。`--audio-mode samples` 下，每个 AudioClip 子样本文件名还要包含未经清洗的完整 sample key 64 位 SHA-256，保留受限可读 stem/扩展名，拒绝 Windows 保留名、大小写碰撞和超长组件，并在首个样本写入前完成同一 AudioClip 内的路径唯一性检查；所有样本先 stage，再以可回滚集合提交，同内容目标复用且保留 mtime，任一 stage/commit 失败不得留下新增或部分更新的无索引子文件。已有 protocol 1 checkpoint 不迁移、不改写历史 identity 或 asset path；只允许全部发现源已经 `completed`、全部 canonical fragment 哈希匹配、全局 `asset_id` 与每个非空物理 `export_path` 按 Windows 大小写语义唯一、且没有 reprocess 请求的纯 no-work。物理路径必须已经是严格 canonical POSIX 相对路径；`.`、重复分隔符、反斜杠、父级与 Windows alias 均拒绝而非用不同字符串重复计数。门禁在 `resume_count`、attempts、fragment、index、summary 或资源变化前流式检查全部 fragment，并持有 checkpoint/fragments 稳定读租约直到本轮 index/summary/checkpoint 提交使用完同一快照；预检后不能重读并接受漂移字节。checkpoint 租约释放后，把预检目标的存在性、稳定 identity、byte size 与 SHA-256 传入组事务；组事务在 stage 前、每个目标首次改变前、每次提交间、所有 stage/backup/retired cleanup 完成后和成功返回前验证 reuse/published 每个目标的精确代，并验证实际移入 backup 的就是授权代。目标移入 backup、stage 发布、rollback 恢复和自有 cleanup 均用 no-replace rename；晚到硬链接、同尺寸异内容、提交间或 cleanup 窗口的可观察换代必须保留外部代并 fail closed。实际移入 backup 的代不匹配且原 source 名已被晚到目标占据时，不能只抛原始代际错误：机器状态必须是 `rollback_incomplete`，同时保留并列出 `recovery_files`，不得覆盖晚到目标或删除 backup。最终又回到完全相同 inode/文件 ID、size、mtime 与 SHA-256 的非合作 ABA 没有可观察差异，仍属于平台文件系统门禁边界。最终 summary 与 checkpoint 先全部 stage，再用覆盖两个稳定父目录的可回滚组事务发布；任一提交失败必须恢复旧代，不能留下 summary/checkpoint 代际不一致的 evidence。每轮 protocol 1 在任何控制文件提交前先以 no-replace 发布 `.unitypy_resume/group_transactions/<transaction-id>.json` 不可变预提交记录，并保持其稳定读租约直到 summary/checkpoint 事务完成；记录绑定两个 canonical 目标路径及其计划 payload 的 byte size/SHA-256，summary/checkpoint 只能共同引用已经发布且仍被租约固定的记录。失败可留下未引用的不可变历史记录，但不能让已提交控制文件引用缺失 sidecar。组事务从 cleanup、恢复 partial 删除、结果证据读取直到真正调用边界都必须持有 committed targets 的精确 generation leases，并由返回对象显式 handoff/close；cleanup rename/unlink 必须以精确文件 handle/fd 或等价 descriptor-relative syscall 执行，包装层在底层成功后抛出 `BaseException` 时按真实 basename/存在性协调，不能丢失 retired/removed 事实。恢复只能递归发现严格 token-owned partial，并且仅当 sidecar、两个 committed targets 与该 partial 自身的精确代都仍匹配时删除；cleanup 窗口目标漂移必须 fail closed、保留实际仍存在的恢复证据。下一轮先协调合法 staged sidecar，再验证完整不可变历史 exact set；独立 validator/snapshot 必须递归扫描 fragments 和 sidecar 历史，拒绝嵌套目录、stale/额外文件、非法命名、reparse，以及任何未闭环的引用。pending/retry/reprocess 必须使用新的 protocol 2 输出目录。缺 checkpoint 但已有 resume fragments、protocol/scheme/version 不匹配，均 fail closed，禁止旧新 fragments 静默混用。

任何 checkpoint、fragment、目录或资源写入前，source 与 output root 必须在 lexical 与 canonical 语义下完全分离；output root 的既有父/子组件不得是 symlink、junction 或其他 reparse point。嵌套父目录逐级创建时，每一级只能相对已验证且仍被租约固定的父目录 handle/fd 创建；不能对可变 lexical path 调用 `mkdir` 后再依赖写后检查。Windows 的目录创建、临时内容写入、最终 `replace` 与 partial cleanup 在完整父目录链的稳定句柄租约内执行；仅省略目录句柄的 delete sharing 不能作为现代 Windows 的 rename 门禁，脚本还须经已验证的最终目录句柄相对创建 delete-on-close 子锚点，并在整个操作窗口保持锚点不共享 delete 权限，从内核层阻止父目录/祖先目录 rename/remove 后替换为 junction。最终操作失败时，导出根外不得发生创建、覆盖、改名或删除；仅靠写前/写后 `resolve()` 不算完整门禁。POSIX 目录创建与最终 replace/remove 使用稳定目录 fd 的 descriptor-relative API；path-based writer callback 的 cooperative-writer 能力边界必须如实保留。完成校验用单次递归 inventory 覆盖整个输出树，任何层级残留的 `*.partial`（包括资源目录中的 `.ga-export-*.partial`）都使完成门禁失败。

补充规则：部分 Unity 项目可能把 DLC 或模块资源放在 `Data/<模块>/AssetBundles/resources_*` 这种无扩展 AssetBundle 中；`extract_all_readable_resources.py` 应把 `AssetBundles` 目录下以 `resources_` 开头且不是 `.manifest/.json/.txt` 的文件视为 Unity 容器。播放器构建中的无扩展场景序列化文件常命名为 `level0`、`level1` 等；发现规则必须在 `*_Data` 范围内按 `^level\d+$` 收录，并同时覆盖标准的 `unity default resources`、`unity_builtin_extra`。对未知后缀文件只用 `UnityFS`、`UnityWeb`、`UnityRaw` 可信魔数补漏，不能把全部无扩展文件宽泛纳入。`.resS/.resource` 是 serialized file 的外部载荷，应单独记录到 summary 的 `external_payloads`，由 companion 引用按需读取，不能作为独立对象容器重复展开。

4A. 全量索引生成并验收后，流式建立 MonoBehaviour 证据边界，不重读或改写源容器：

```powershell
pwsh -NoLogo -NoProfile -File <skill>\scripts\invoke_python_utf8.ps1 `
  -Script <skill>\scripts\analyze_unitypy_monobehaviour_boundary.py `
  --index "<study-root>\0.原始导出\UnityPy\UnityPy全量可读资源\all_resources_index.csv" `
  --output-dir "<study-root>\4.临时目录\中间索引\UnityPy分析\MonoBehaviour边界" `
  --config <skill>\references\unity-monobehaviour-boundary-config.json `
  --contract <skill>\references\unity-monobehaviour-boundary-contract.json
```

如已有隔离、只读的 TypeTree 可行性 probe JSON，可追加 `--probe-evidence "<probe.json>"`。该参数只把定向/分层/控制样本作为独立证据分层统计，不会启用 generator、加载 Managed DLL 或安装 native 依赖；输出中的 `overall_recovery_estimate` 必须保持 `null`。分析器只从索引声明列接受 `byte_size`/`raw_sha256`，列缺失或值为空时标 `unavailable`，不得用 Expected-vs-actual 的 expected 值或其他推测补齐。证据候选不得与结构列或另一证据角色重叠；多个候选列同时存在时，按配置顺序选择首个有效值，等价有效别名可共存，有效值冲突必须 fail closed，非法别名与有效值并存时显式标 `partial`。`byte_size` 只接受规范 ASCII 非负十进制：`0` 有效，正数不得有前导零，正负号、分隔符、全角数字和首尾空白均非法。来源分组、状态、通用头字段、错误正则和未匹配 remark 预览上限都由 reference 配置驱动；项目专属规则应复制为项目配置，不能回写成脚本硬编码。

该分析器用第一遍流式扫描统计全量状态，用第二遍流式扫描即时处理 exported metadata，不保留随成功行数线性增长的 row dict 列表；未匹配错误只累计总数并保留配置上限内的确定性最低 SHA-256 样本，不驻留全部唯一长 remark。两遍都必须在首个数据行前拒绝空表头、空列名和按大小写折叠重复的任意列，第二遍表头还必须与第一遍完全一致；不能让 `csv.DictReader` 用后列静默覆盖重复的 byte-size/raw-SHA 或结构字段。config、contract、probe 和 metadata 必须从与 provenance SHA-256 相同的稳定字节快照解析；大索引在两遍扫描前、扫描间和提交前后以文件身份与 SHA-256 门禁。输出目录拒绝 symlink/junction/reparse 组件，并在首次 artifact 创建前固定一个 no-reparse 目录身份；两个 final、同目录密码学随机且排他/no-follow 的 stage、既有输出 backup 必须全部与 index/config/contract/probe/任何现有被引用 metadata 及彼此做 canonical/hard-link alias 门禁。stage/backup 的创建、写入、重开、commit/restore rename、rollback 删除和 finally cleanup 全部只能使用该稳定目录下的 basename 相对操作：Windows 使用稳定 Win32/NT 目录 handle 与 `RootDirectory` 相对 API，POSIX 使用 `O_DIRECTORY|O_NOFOLLOW` 的 `dir_fd` API，不得在操作时重新解析可变 lexical parent。lexical 输出目录身份变化后不得开始新 commit，但 rollback/cleanup 仍绑定原目录；即使检查后父路径被换成 junction/symlink，外部目录也不得发生创建、覆盖、改名或删除。两份 artifact 先完整 stage/reopen，在提交点复核全部输入和旧输出，再以可回滚集合提交。Windows 对两个计划时已存在的目标从快照一直持有仅 `FILE_SHARE_READ`、拒绝其他 write/delete/replace open 的稳定句柄，同路径和 hard-link 别名写入及 pathname 替换都必须由内核拒绝；事务仍可通过自身带 DELETE 权限的精确句柄执行 `RootDirectory` 相对 `NtSetInformationFile`，把旧对象以 `ReplaceIfExists=0` 退休到密码学随机恢复名，再以 `ReplaceIfExists=0` 发布新代，commit/rollback 全链不得出现 `ReplaceIfExists=1`。每一次 Windows Nt rename 无论正常返回还是 wrapper/异步异常退出，都必须执行 syscall 后置状态协调：优先从精确打开的源句柄查询当前 basename，并相对固定目录重开该 basename 核对同一 identity；若 `GetFinalPathNameByHandleW`、普通 basename-relative reopen/identity query 或二者连续故障，则保留源/目标候选 basename、预期 identity 与协调证据，并直接用固定 `RootDirectory` 的 `NtCreateFile` 加 `fstat` 探测候选，只在唯一精确代收敛时更新 `StableOpenFile.name/temporary`、旧目标的 `retired/backup` 及账本，歧义时 fail closed 且继续持有精确对象，不能依赖 syscall 前缓存猜测位置。该协调必须覆盖 reservation→private、old-final→backup、stage→final、transaction-final→private-stage、backup→final，不能只修正常 commit。rollback 先把精确事务 final 句柄无覆盖退休回私有 stage 名，再无覆盖恢复 backup；restore 系统调用入口晚到的 final 保持不动，机器状态标 `rollback_incomplete` 并列出 `recovery_files`。rollback 报告 complete 前还要逐项证明每个计划旧代已回到 final；final 缺失或唯一旧代只在 backup 时必须转为 `rollback_incomplete`。`recovery_files` 与 `cleanup_required_paths` 只能列出当前真实存在且 identity 与预期代匹配的路径，不能保留 syscall 后的旧 basename，也不能把幸存 backup/private stage 或抢占私有名的外部代漏报/误报为自有清理对象。POSIX 若没有等价的 mandatory target-generation primitive，不得回退为 path guard 后 `replace`：计划时已有 final 必须在 stage/backup 创建前 fail closed，并要求改用新的空输出目录；计划时不存在的 fresh 输出仍使用稳定 `dir_fd` 的 no-replace 发布。每次 commit 的整组存在性/身份/字节门禁仍作为补充校验，计划不存在目标晚到、计划存在目标持久漂移或已提交目标在下一 commit 前改变都必须 fail closed。cleanup 尝试全部 delete/close，稳定目录 close 独立执行；已提交代的 cleanup 故障通过 `cleanup_state/cleanup_warnings/cleanup_required_paths` 明确报告，不用不透明异常把新代伪装成未发布。两份 final 成功发布后，其精确句柄必须从普通 cleanup 排除，并由 dict-compatible `ArtifactCommitResult`/`AnalysisRunResult` owner 把 mandatory generation lease 覆盖到 `commit_artifact_set`/`run` 调用者真正消费结果的边界；直接调用者显式 `close()` 或使用 context，CLI 在租约内完成 JSON 序列化验证，随后以双 guardian 无内部空窗地释放 primary/final leases 和目录句柄。调用方异常、序列化失败、close wrapper 后置故障或释放空窗换代都必须穷尽关闭并留下 `handoff_state`、`published_lease_state`、`release_errors`、`release_required_paths` 等机器态；最后一次 pathname check 不能代替消费期 lease。任一提交/重开失败，或提交后发现持久输入漂移，都回滚本事务已发布的目标并 fail closed；独立写者已经改变的未提交目标保持不动。输入门禁不是内核变更日志：门禁间完整改写又恢复的 ABA，及成功的提交后门禁完成之后才发生的变化，不在输入合同内；Windows destination replacement 另由 mandatory share lease、精确对象退休和 no-replace 发布提供底层约束。

5. 把 UnityPy 产物整理回项目名根目录的 0-4 交付结构：

```powershell
& $py <skill>\scripts\organize_unitypy_project_export.py `
  --index "<study-root>\0.原始导出\UnityPy\UnityPy全量可读资源\all_resources_index.csv" `
  --unitypy-root "<study-root>\0.原始导出\UnityPy\UnityPy全量可读资源" `
  --study-root "<study-root>" `
  --project-name "<游戏名>" `
  --config "<skill>\references\unitypy-resource-organization.json" `
  --contract "<skill>\references\unitypy-resource-organization-contract.json" `
  --requirements-output "<study-root>\4.临时目录\中间索引\UnityPy组织\模型工具需求.md"
```

序列候选只允许由当前物理 `export_path` 的真实可交付项占用：状态必须为 `exported`，类型/扩展名必须允许，安全相对路径必须存在为非 reparse 普通文件，并且序列目标分类根必须通过 include/exclude；空路径、metadata、`read_error`、不存在文件和被过滤路径不得确认序列或改变真实帧 ordinal。每个分号分隔项必须先通过配置声明的严格 canonical POSIX 相对路径门禁：拒绝首尾空白、`.`/`..` segment、重复分隔符、反斜杠、绝对路径、NUL、冒号、非 NFC 文本及 Windows 保留名/尾点尾空格 alias。完全相同的 canonical 项在整遍索引中只计数和组织一次；按 Windows 大小写语义指向同一物理 spelling key、但文本不同的 alias 必须 fail closed。同一 CSV 行含多个图片路径时，必须针对每个去重后的物理路径分别重算 context、classification、main-candidate 与 sequence scope，不能让另一物理路径的 UI/VFX/场景/道具语义污染当前路径。

UnityPy 源根到每个资源父目录的整条目录链及最终文件必须绑定同一次稳定、no-follow 的 handle/fd 遍历；资源 SHA-256、hardlink 和 copy 必须消费同一已打开文件对象，不能在检查后重新按 lexical path 打开。Windows 用根/父目录稳定 handle 和 handle-relative 最终文件 open，并拒绝会允许父链或文件被 rename/replace 的共享模式；POSIX 用 `O_DIRECTORY|O_NOFOLLOW` 的逐级 `openat` 和最终文件 fd。父目录在检查后尝试换成 junction/symlink 时，必须由内核阻止或 fail closed，绝不能读取授权根外字节。

分类根和每一级嵌套目录的创建、hardlink/copy partial、无覆盖发布、hash 复核、冲突目标与失败清理必须绑定同一稳定目录代：Windows 使用已验证目录 handle 与 handle-relative delete-on-close 子锚点阻止父/祖先 rename 后替换为 junction，POSIX 使用 `O_DIRECTORY|O_NOFOLLOW` 的稳定 `dir_fd` 和 descriptor-relative create/link/publish/unlink。每次本事务成功创建分类目标时必须记录该精确文件代，并以稳定读取 handle/fd 对该代完成 hash/身份验证；验证失败或后到替换时，只允许清理仍与本事务代匹配的对象，固定 basename 已属于外部新代时必须保留且 fail closed。父路径在检查后换代时，计划根外不得发生创建、覆盖或删除。异常路径必须逐项尝试所有 CSV/JSONL/text writer abort、partial 删除、SQLite close 和 database/journal/WAL/SHM 清理；首个 close/unlink 错误不得跳过后续对象，主错误与全部清理错误要聚合报告。

该步骤按 reference 配置两遍流式读取索引：第一遍只统计序列候选的不同源帧号，达到 `sequence.candidate_store.spill_threshold` 后把候选切换到 `4.临时目录/中间索引/UnityPy组织/` 下的隔离 SQLite 临时库，第二遍直接组织真实资源；每一遍都对 CSV 解析器实际消费的字节流计算 SHA-256，并绑定该遍前后稳定文件身份，两个 pass digest 还必须彼此相同且等于运行初始快照，A→B→A 换代不得发布 `ok=true`。成功或异常都必须在 `finally` 路径尝试关闭 SQLite 并清理 database、journal/WAL/SHM 和 partial，close 与清理的多重错误要聚合报告。它不把全量 CSV row dict 驻留内存，但阈值内候选仍随帧数线性增长，因此不得称为严格常量内存。项目名、角色名和领域关键词不得写入脚本；如需项目专属分类，只复制默认 config 为项目配置并传 `--config`。默认同卷优先硬链接，失败时用同目录临时文件原子复制；已存在同内容文件直接复用且不触碰 mtime，不同内容先使用来源身份 SHA-256 的稳定后缀，稳定路径仍冲突则 fail closed，禁止 `__02/__03` 递增碰撞。序列候选默认需要同组至少两个不同帧号，或来源/容器/导出路径明确命中 animation/sequence/frames 语义；默认 group scope 同时包含 `source_file`、`container_path` 与规范化 `export_parent`，不同 bundle/context 不得仅凭相邻尾号合并。只有明确动画路径命中 `explicit_context_patterns`，或项目配置显式把 `group_scope.mode` 设为 `global`，才允许跨范围合并。

输出包括 `资源组织索引.csv`、`动画序列帧索引.csv`、`资源组织摘要.json`、`资源组织错误.jsonl`、`主角资源候选索引.csv` 和模型工具需求说明。默认统一位于 `4.临时目录/中间索引/UnityPy组织/`；并行或专项补充分支可复制默认 config，把 `paths.index_root` 改为 `4.临时目录/中间索引/<非空安全子路径>` 以隔离索引。该路径必须是规范 POSIX 相对路径，至少包含一个 `中间索引` 下的子目录；绝对路径、`.`/`..`、反斜杠、Windows 保留名/尾点尾空格别名和直接指向 `4.临时目录/中间索引` 均拒绝。`--requirements-output` 可把模型工具需求说明隔离到调用方分配的 `4.临时目录` 路径，省略时兼容旧路径 `4.临时目录/模型工具需求.md`。任何目录、partial 或分类资源写入前，脚本必须解析并校验全部 contract 输出和 requirements target：按 Windows 大小写语义拒绝互相 alias、文件/父路径冲突，以及与 index/config/contract 输入 alias；任何 `stat/samefile` 身份查询异常都必须 fail closed。CSV fields 必须为非空唯一字符串，各 artifact filename 后缀必须与类型一致。config/contract 的解析与 provenance SHA-256 必须来自同一字节快照，并在首次写入、六文件 artifact 组提交前、每次提交之间及提交后复核当前身份；漂移时非零退出。所有 CSV/JSON/JSONL 使用 contract 声明的字段和确定性序列化，先全部 stage、重开校验，再以 summary 最后提交的可回滚组事务发布；事务绑定每个 destination 的计划存在性/身份/SHA-256 和每个 staged artifact 的精确代，计划不存在的文件晚到时必须在首个覆盖前 fail closed。旧 success summary 在首个索引替换前退出固定路径，并且只在全部恢复成功后最后恢复；无法完整恢复时，固定 summary 必须是 `ok=false, transaction_state=rollback_incomplete` 的恢复标记或不存在，不得留下“新/缺失索引 + 旧 `ok=true` 摘要”。成功代使用 `transaction_state=committed`；backup 清理异常以 `cleanup_state=warnings` 和 `cleanup_warnings` 记录，严格命名且仍为账本精确代的 cleanup orphan 可在下次运行前重试。若 cleanup 状态本身无法权威写回，则固定 summary 必须转为 `ok=false, transaction_state=committed_cleanup_status_unknown` 的恢复标记或不存在。同内容组重复运行不替换文件并保留 mtime。include/exclude 根过滤必须作用于每一个实际输出目标，包括主角候选副本。摘要记录源 index/config/contract 与每个已组织源文件的 SHA-256，以及 created/reused/hardlinked/copied/conflict/filtered/skipped/failed 计数，并记录 canonical export-path 策略、精确重复去重数及两遍一致性。默认保留游戏内实际资源名；只有确认的序列帧才在动作目录内归一化为 `idle_01.png`、`attack_01.png`、`wall_run_01.png`。分类目录不得混入 JSON/CSV/TXT/MD。

六个 artifact 的 writer/pending 对象从 stage 创建起直到 commit、rollback、backup cleanup 和 abort 完成，必须持续持有其输出父目录的稳定 handle/fd；不得在 stage 后重新按可变 lexical parent 打开、改名或删除。old-final→backup、stage→final、transaction-final→private、backup→final、backup→cleanup-private 的每次 no-replace rename，无论包装层正常返回还是 syscall 后抛错，都必须查询精确源句柄的实际 basename、相对稳定目录重开并核对同一代，再更新 rollback/cleanup 账本。清理任何自有 partial/backup/分类目标时，必须先把仍匹配账本的精确代无覆盖退休到密码学随机私有名，再通过该文件 handle/fd 删除；固定 basename 在 ownership check 后被外部代占用时必须保留。不能用最后一次 pathname 检查猜测 syscall 是否完成，也不能先 unlink 固定目标再恢复 backup。

cleanup-warning summary 的二次发布必须先把已验证旧代退休到同目录恢复名，再以无覆盖语义发布新代；发布后必须持有新 summary 的稳定文件 handle/fd，以精确 identity 与预期 SHA-256 完成结构验证，并把该 lease 作为成功交接所有权传到真正的外层消费边界。CLI 必须在 JSON 序列化和退出码计算完成后显式 `close()`；直接 Python 调用 `organize(...)` 返回的是 dict-compatible `OrganizationResult`，调用者必须在读取成功结果后显式 `close()`，或使用 `with organize(...) as result:`。只有交接完成时才能 exact-delete 旧恢复代并释放 handle；普通 `dict` 的最后一次 pathname check 不构成成功边界。若固定路径被后到的任意结构有效但非本事务代 summary 替换，运行必须 fail closed、保留该外部字节，并保留旧恢复代；不能把“包含 required keys”等同于本代身份，也不能为了写 `committed_cleanup_status_unknown` 恢复标记再次覆盖外部代。Windows 应以不共享 write/delete 的稳定 handle 让此窗口内换代由内核拒绝；POSIX 必须用目录 fd 路径代与已开对象代双重验证。hardlink 与 copy no-replace publish 的包装层若在底层成功后抛错，只能在目标仍为本次 exact source/staged generation 时协调为成功并登记；否则 fail closed，禁止留下未入账 ghost 目标。

整理规则：UI、角色立绘、场景、道具、图集、序列帧统一位于 `1.分类资源/图片/` 下；真实音频样本进入 `1.分类资源/音频/`；AssetRipper 导出的 Shader、Material、RenderTexture 等进入 `1.分类资源/渲染/`。UnityPy 的 `metadata/*.json`、CSV、TXT、MD 不复制到分类资源，只进入原始导出或学习索引；组织摘要必须记录 `skipped_metadata_count`。空目录不放 `_empty_reason.md`，缺失原因写入 `2.报告/00_研究总览.md` 和临时验证日志。

如果在 `1.分类资源/` 发现 `.json/.csv/.txt/.md`，视为组织步骤失败并优先修复/重跑组织脚本，不用宽泛的 PowerShell `Get-ChildItem -Include` 事后搬移。我的判断：这类清理命令在递归、`-LiteralPath` 和通配符组合下容易产生过滤误判，流程上应把元数据挡在复制入口，而不是复制后再清理。

6. 若 UnityPy 索引中存在 Mesh 或 Renderer，再导出 UnityPy-derived glTF 模型。该步骤必须放在 UnityPy 图片/贴图组织之后，因为脚本会从 `1.分类资源/图片` 查找 `_MainTex` 或同名前缀候选贴图：

```powershell
& $py <skill>\scripts\export_unitypy_meshes_to_gltf.py `
  --source "<game-root>" `
  --study-root "<study-root>" `
  --expected-index "<study-root>\0.原始导出\UnityPy\UnityPy全量可读资源\all_resources_index.csv" `
  --material-config "<skill>\references\unity-material-binding-config.json"
```

该脚本把模型写入 `1.分类资源/模型/<模型名>/`，索引写入 `4.临时目录/中间索引/UnityPy模型导出/模型导出索引.csv`，并按 `references/unity-material-binding-config.json` 的材质属性优先级从 Renderer -> Material -> Texture2D 复制可恢复贴图到模型局部 `textures/<贴图名>.png`。`--expected-index` 把每个 Mesh 的 `source_file + path_id` 绑定到全量索引，missing/source_failed/skipped/exported 必须逐项对账；MeshRenderer 通过同 GameObject 的 MeshFilter 找到 Mesh，外部 Material/Texture 可从索引 metadata 补证。并行分支使用 `--model-output-subdir` 与 `--index-subdir` 隔离安全相对子根，重复运行同内容时 glTF/BIN/sidecar/index/summary 均复用且不改 mtime。glTF 必须写入 `images`、`textures`、`materials`、`pbrMetallicRoughness.baseColorTexture` 和 primitive `material`，使 Blender 导入后能看到 Image Texture 接到 Base Color。Unity 批次常含 VFX 薄片与两面可见的 Plane/Trail 形状，离线学习材质应写入 `doubleSided=true`，避免固定相机从背面观察时产生空白缩略图；该标志只服务本地预览，不等同于恢复原 Shader culling 设置。索引字段 `material_texture_name`、`material_texture_uri`、`material_binding_source` 必须保留；`unity_material_main_texture` 或 `unity_material_configured_property` 表示真实 Renderer -> Material 绑定，`same_name_candidate` 表示只按同名前缀复制到 `textures/待验证_同名前缀/` 的候选，报告中需标为待复核且不计为已验证材质。详细规则见 `references/unity-model-material-binding-workflow.md`。

完成后用 Blender CLI 抽样验证材质，而不是只验证 glTF 能导入：

```powershell
& "<blender.exe>" --background --python "<study-root>\4.临时目录\工具日志\Blender验证\check_gltf_materials_in_blender.py"
```

验证脚本应导入样本 glTF，检查材质节点中存在 Image Texture，且其 Color 输出连接到 Principled BSDF 的 Base Color；同时检查 `images[*].uri` 或 Blender image filepath 指向的贴图文件存在。若没有 Mesh、没有 `_MainTex` 或模型确实无贴图，在 `2.报告/00_研究总览.md` 和模型导出摘要中写明原因。

我的判断：AssetRipper 更适合还原场景搭建、Prefab 和工程结构；UnityPy 更适合脚本化导出 Sprite/Texture2D/TextAsset、AudioClip 元数据、Mesh glTF 和动画帧。两条产物互为补充，不能用其中一条替代另一条。AudioClip 样本导出需显式改为 `--audio-mode samples`；Forestrike 验证中，UnityPy 经 FMOD native 解码音频样本会触发进程崩溃，因此默认不触碰 `AudioClip.samples`。若 UnityPy 或 AssetRipper 得到 `.fbx`、`.obj`、`.gltf`、`.glb`，需要的查看/转换工具下载到本 Skill 的 `tools/` 目录并记录来源、版本和用途；若只能看到 Renderer/Material metadata、无法读取 Mesh 几何或贴图链，才在 `4.临时目录` 记录为待验证边界。

## IL2CPP 与空 C# 方法

如果 `ExportedProject` 中的 C# 只有字段、属性和方法签名，方法体为空或只返回默认值，不要直接判断“没有逻辑”或“导出失败”。先按 `references/il2cpp-native-code-workflow.md` 判定 Mono/IL2CPP 分支：

```powershell
Get-ChildItem "<game-root>" -Filter "GameAssembly.dll" -Recurse
Get-ChildItem "<game-root>" -Filter "global-metadata.dat" -Recurse
```

若预检或文件检查找到 `GameAssembly.dll + global-metadata.dat`，用 Il2CppDumper 生成 `IL2CPP_Dump/dump.cs`、`script.json`、`il2cpp.h` 和 `DummyDll/`，再用 Ghidra 或用户许可下的 IDA 查看 native 方法体。`DummyDll` 只用于 IDE/反编译器引用，不是完整源码。报告必须写明 IL2CPP 分析是否完成、失败原因和待验证方法清单。metadata 版本高于 29 时，不把 Cpp2IL 2022 放在第一路线；若尝试并失败，只记录为备用工具兼容性边界。

`full` 与 `code` 模式下，若用户没有明确接受“关键样本分析”，IL2CPP native 部分必须按 `references/il2cpp-native-code-workflow.md` 的 Full Code 批量导出路线执行：从 `dump.cs` 生成全量或模块化 targets，Ghidra 用 `-noanalysis` 分批反编译，并输出 `ghidra_decompile_coverage.csv/json`。Ghidra 全量 auto-analysis 只作为调用图、交叉引用、常量引用质量提升任务，不是批量代码导出的默认前置步骤。

## 场景搭建与图片索引

资源分类完成后，如果 `1.分类资源/图片` 已有图片，或用户/分支要求图片画廊，必须先生成 manifest，再生成报告目录 BAT，之后才进入学习索引和最终报告汇总：

```powershell
pwsh -NoLogo -NoProfile -File <skill>\scripts\invoke_python_utf8.ps1 `
  -Script <skill>\scripts\analyze_assetripper_project.py `
  --project-root "<study-root>\0.原始导出\AssetRipper\ExportedProject" `
  --output-dir "<study-root>\4.临时目录\中间索引\AssetRipper分析" `
  --title "<游戏名>" `
  --config <skill>\references\assetripper-project-analysis.json
python <skill-root>\scripts\build_image_gallery_manifest.py --study-root "<study-root>" --skill-root "<skill-root>"
python <skill-root>\scripts\create_image_gallery_launcher.py --study-root "<study-root>" --skill-root "<skill-root>"
```

`analyze_assetripper_project.py` 会直接生成 `导出资源文件清单.csv`、`场景索引.csv`、`Prefab索引.csv`、`图片资源尺寸索引.csv`、`场景搭建分析.md` 和 `导出分析摘要.json`；这些直接输出必须留在 `4.临时目录/中间索引/AssetRipper分析/`。脚本以新的 `- enabled` 为 Build Settings 硬记录边界，保存全部 `order/enabled/path/guid` 并与场景索引集合对账；Graphics、Quality、Player 与 Packages 参数、质量档名/数量和平台默认档均从文件动态派生。若 AssetRipper runtime review 已产出严格 JSON verification，追加 `--runtime-verification "<verification.json>"`，脚本会按配置中的 JSON pointer 候选动态生成 `safe_usage` / `forbidden_inference`，不得把特定项目组件名写进通用规则。机器摘要保存设置来源 SHA-256、动态行锚点和重开验证结果，使用 `sort_keys=True`、`allow_nan=False` 的确定性 JSON；需要给用户验收的版本再复制或汇总到 `2.报告/`。

图片画廊要求：`build_image_gallery_manifest.py` 生成 `4.临时目录/中间索引/图片画廊/manifest.json`、PNG 缩略图和 `errors.jsonl`；`create_image_gallery_launcher.py` 在 `2.报告/打开图片画廊.bat` 生成固定 EXE 启动入口。BAT 必须调用 `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe`，传入本次 `--study-root`、`--manifest`，并把 manifest `source_roots` 中的图片根重复写成 `--images-root`。固定 EXE 中点击卡片打开预览覆盖层；鼠标滚轮缩放图片；按住鼠标左键拖拽平移图片；预览仅通过右上角 X/关闭按钮或 Esc 关闭；`打开目录` 按钮在系统文件管理器中打开并定位当前图片；右键不触发操作，不属于默认工作流。

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

若同一研究目录存在多次 UnityPy 输出，学习索引默认优先读取名称含 `含DLC` 的主索引，并排除 `音频样本` 这类专门补充音频样本的重复索引，避免尺寸、命名和音频统计被重复计数。

交付前审计临时目录：

```powershell
python <skill>\scripts\audit_study_root_hygiene.py --study-root "<study-root>"
python <skill>\scripts\audit_study_root_hygiene.py --study-root "<study-root>" --clean-browser-profiles
```

`audit_study_root_hygiene.py` 默认只检查并写入 `4.临时目录/工具日志/临时目录卫生审计.json`；只有用户要求交付清理或最终打包前，才使用 `--clean-browser-profiles` 删除 `_ascii_work` 中 Chrome/Edge 浏览器 profile。不得删除 Ghidra 项目、工具输入、失败记录或学习索引。

## 图片画廊固定 EXE

默认不在每个 study-root 内构建或复制 EXE。固定 EXE 由 Skill 维护在 `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe`；每个 study-root 只生成 `2.报告/打开图片画廊.bat`，BAT 传入本次 `study-root`、`4.临时目录/中间索引/图片画廊/manifest.json`，并把 manifest `source_roots` 中的图片根重复写成 `--images-root`。

固定 EXE 是必达交付。若 `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe` 已存在则验证现有 EXE；否则在 Skill 目录运行构建脚本生成。若 Node/npm 不可用且没有可验证的固定 EXE，本流程为 BLOCKED：

```powershell
powershell -ExecutionPolicy Bypass -File <skill>\scripts\build_image_gallery_viewer_exe.ps1 -SkillRoot "<skill>"
```

固定 EXE 必须通过 `--self-test --study-root ... --manifest ... --images-root ...` 烟测；BAT 只负责调用固定 EXE，不得要求每项目运行 `npm install`、`npm run dist` 或 `build_gallery_exe.ps1`。旧 `build_full_image_gallery.py` 与 `build_gallery_exe.ps1` 仅作为兼容工具，用于打开历史 HTML 画廊或临时回退，不作为默认流程。

## VFX 动态特效预览与 Unity 回导

UnityPy 资源组织后，如果 `all_resources_index.csv` 中存在 `ParticleSystem`、`ParticleSystemRenderer`、`TrailRenderer`、`LineRenderer` 或 VFX 命名材质/贴图/动画，运行：

```powershell
python <skill>\scripts\build_vfx_preview_manifest.py --study-root "<study-root>"
python <skill>\scripts\export_unity_vfx_reconstruction.py --study-root "<study-root>"
python <skill>\scripts\create_vfx_viewer_launcher.py --study-root "<study-root>" --skill-root "<skill>"
```

`build_vfx_preview_manifest.py` 按 `references/vfx-preview-config.json` 分类 VFX 类型和关键词，把粒子贴图等真实资源输出到 `1.分类资源/特效/粒子贴图/` 等资源目录，把 ParticleSystem、Material、Mesh、AnimationClip 等配置/metadata 卡输出到 `1.分类资源/特效/配置/<类型>/`，并生成 `4.临时目录/中间索引/VFX预览/manifest.json` 和 `errors.jsonl`。`VFX材质/`、`VFX模型/`、`VFX动画/` 只放真实/可用资源；现阶段如没有复制真实资源，不用 JSON 卡片填充。`export_unity_vfx_reconstruction.py` 读取 manifest，动态还原 ParticleSystem、TrailRenderer、LineRenderer 基础模块，输出 `4.临时目录/中间索引/VFX预览/reconstructed/` JSON，并生成可重新导入 Unity 的项目相对目录 `4.临时目录/中间索引/VFX预览/unity-export/Assets/GameAnalysisVfxPreview`，其中包含 Prefab、Material、metadata、bootstrap 脚本目录和可解析的 texture/mesh/animation 引用。存在多套 AssetRipper 工程时，在项目 VFX 配置中把 `paths.assetripper_prefab_index` 与 `paths.assetripper_export_root` 指向对象覆盖最多的已复核工程；若只需更换该 Prefab 来源，用 `--original-prefabs-only` 重试补充阶段，不重跑 manifest 的全部条目。`create_vfx_viewer_launcher.py` 只在 `2.报告/` 生成 `打开特效预览.bat`，BAT 调用固定 GameVfxViewer 并传入 manifest、reconstructed-dir、preview-cache-dir 和 unity-export-dir。

根对象超过 `limits.max_items`、用户要求全量，或同源依赖扫描出现规模退化时，第一步改用：

```powershell
python <skill>\scripts\build_unity_vfx_full_index.py `
  --study-root "<study-root>" `
  --config "<skill>\references\vfx-preview-config.json" `
  --source-root "<readonly-source-root>"
```

该路径不截断 manifest 根对象，按索引派生组件总数并生成 `4.临时目录/中间索引/VFX结构/` 下的全量结构 CSV、依赖错误 JSONL、AssetRipper VFX YAML 对账和事实/判断/假设摘要。`--source-root` 只用于前后文件数/字节观察，不授权修改源目录。

与全量 manifest 配套时，第二步改用：

```powershell
python <skill>\scripts\export_unity_vfx_reconstruction_full.py `
  --study-root "<study-root>" `
  --skill-root "<skill>" `
  --config "<skill>\references\vfx-preview-config.json" `
  --source-root "<readonly-source-root>"
```

该路径解包 UnityPy `type_tree`、归一化非有限曲线值、复用适配器并避免复制文件名碰撞；完成重建后流式扫描全部选中 AssetRipper VFX YAML（包括大文件）的 GUID，以 `deque` 和入队集合生成依赖闭包。若只需在修复扫描器或配置后刷新 YAML/Scene 依赖，使用 `--supplement-only`，不得重跑已经完整验收的 reconstructed 集合。

动态还原是 best-effort：只对可从 UnityPy metadata 和依赖关系中解析出的模块、材质、贴图、mesh、animation 建立预览和回导片段；不复制未解析 texture，不修改源游戏或源导出。失败、引用缺失、模块不支持或部分回导时，必须写入 `errors.jsonl`、manifest `warnings`，或条目级 `preview_status`、`reconstruction_status`、`unity_export_status`；报告只能称为部分还原或待复核，不得假装完整复现 Unity runtime。

## 模型预览画廊

UnityPy/模型整理后，只要 `1.分类资源/模型/` 下存在 `.gltf`、`.glb`、`.fbx` 等模型文件，就生成本地模型预览画廊索引。若 UnityPy 原始索引中存在 `SkinnedMeshRenderer` 与 direct `AnimationClip` 曲线，先运行 `export_unitypy_real_animation_samples_to_gltf.py --auto` 自动选择可匹配 skeleton path 的真实蒙皮动作样例；若 UnityPy 原始索引中存在 `AnimationClip` metadata，再生成一个可播放的 glTF 动作学习预览模型，用于在 GameModelViewer 中切换动作名和观察时长/节奏；该模型是程序化预览，不代表已经完整还原原始 Unity skin、Avatar、bone weights 或 AnimationClip 曲线。若 Candide/Romestead `.cdx/.cmx` 等文件内含 BinaryWriter `i-mesh`/`IndexedMesh`，运行 `export_candide_cdx_meshes_to_gltf.py --scan-all-embedded-meshes` 导出 glTF，并把 `media/textures`、`media/sprites` 等贴图路径解析到已分类图片；这一步用于补足车、轮子、建筑、机关、doodad、地图/POI 场景等不在 `Content/mesh/*.xnb` ModelReader 清单中的模型。对 `Content/maps/**/*.cmx` 来源且同名 `.tmx` 存在的无贴图网格，`build_model_gallery_manifest.py` 必须标记 `asset_role=map_structure_mesh`、`visual_source=tmx_tileset_layers`，不能把灰色体块默认解释为美术模型导出失败；实际视觉应回看 TMX 图层和 tileset。manifest、thumbs、preview-cache 和 errors 固定写入 `4.临时目录/中间索引/模型预览/`；用户入口只放在 `2.报告/打开模型预览.bat`，不得额外生成 `模型预览.bat` 等重复入口。固定 EXE 位于 Skill `built-in-tools/GameModelViewer_1.0.0/GameModelViewer.exe`，不复制到 study-root，也不在每次分析时重新构建。

```powershell
$manifest = "<study-root>\4.临时目录\中间索引\模型预览\manifest.json"
$viewerExe = "<skill>\built-in-tools\GameModelViewer_1.0.0\GameModelViewer.exe"

python <skill>\scripts\export_unitypy_real_animation_samples_to_gltf.py --study-root "<study-root>" --source "<game-root>" --auto --max-samples 1
python <skill>\scripts\build_unity_animation_preview_models.py --study-root "<study-root>"
python <skill>\scripts\build_model_gallery_manifest.py --study-root "<study-root>"

$blenderCommand = Get-Command blender -ErrorAction SilentlyContinue
if ($blenderCommand) {
  python <skill>\scripts\render_model_thumbnails_blender.py --manifest $manifest --blender $blenderCommand.Source
} else {
  python <skill>\scripts\build_model_gallery_manifest.py --study-root "<study-root>" --record-blender-missing
  Write-Warning "Blender not found; recorded thumbnail skip reason in errors.jsonl."
}

python <skill>\scripts\create_model_viewer_launcher.py --study-root "<study-root>" --skill-root "<skill>"
python <skill>\scripts\update_model_gallery_summary.py --study-root "<study-root>" --manifest $manifest --viewer-exe $viewerExe
powershell -ExecutionPolicy Bypass -File <skill>\scripts\verify_model_viewer_gui.ps1 -StudyRoot "<study-root>" -SkillRoot "<skill>" -ModelName "Unity动作预览_AnimationClips" -MinAnimationCount 1
```

`build_unity_animation_preview_models.py` 会读取 `0.原始导出/UnityPy/UnityPy全量可读资源/all_resources_index.csv` 中的 `AnimationClip` metadata，按 `references/model-viewer-gallery-config.json` 的 `animation_preview` 配置选择动作名、时长上限和输出模型名，生成 `1.分类资源/模型/Unity动作预览_AnimationClips/*.gltf` 与 `4.临时目录/中间索引/模型预览/unity-animation-preview-summary.json`；若无 AnimationClip metadata，只写 skipped 摘要，不阻塞模型预览。`export_candide_cdx_meshes_to_gltf.py` 会扫描 `.cdx/.cmx` 等二进制中的 BinaryWriter `i-mesh` 段，读取 `vertices/texture/faces/surfaces`，从 `media/textures`、`media/sprites` 等路径绑定已导出的贴图，生成每个内嵌网格的 glTF、`metadata.json`、`export-log.json`、`dependencies.json` 和 `missing-resources.json`；全量补漏使用 `--scan-all-embedded-meshes`，定向追查对象可用 `--include-regex`。`build_model_gallery_manifest.py` 会创建 `manifest.json`、`thumbs/`、`preview-cache/` 和 `errors.jsonl`，并统计 glTF/GLB 的 `mesh_count`、`material_count`、`skin_count`、`animation_count`、`animation_origin`、`is_generated_animation_preview` 和动作名；若命名符合 `full_body_preview` 配置，会在服装/套装入口条目上附加基础身体、头部和同前缀分件列表，GUI 只做 viewer 层虚拟组合，不合并或改写原模型文件。Blender 是模型预览验收链的一部分：只有 `Get-Command blender` 检测到可执行文件时才运行缩略图渲染和 FBX preview-cache 转换；`render_model_thumbnails_blender.py` 启动失败、模型导入失败或缩略图缺失时，流程 `Write-Warning` 并把失败细节写入 `errors.jsonl`。模型数量过大、执行时间长或渲染耗时不可作为跳过导出、跳过贴图绑定、跳过 Blender 缩略图渲染或用占位图冒充完成的理由；必须继续执行，必要时拆成可续跑批次并记录已完成数量、失败项和下一批命令。占位缩略图只能作为执行中的临时标记；最终无法完成时必须记录外部阻塞证据并标为 `DONE_WITH_CONCERNS` 或 `BLOCKED`。Blender 不可用时不要调用渲染脚本，no-Blender 分支必须重新调用 `build_model_gallery_manifest.py --record-blender-missing`，把 `stage="thumbnail"`、`status="skipped"`、`reason="blender_not_found"` 追加到 `errors.jsonl`，并继续执行 `create_model_viewer_launcher.py` 和 `update_model_gallery_summary.py`。`update_model_gallery_summary.py` 从 manifest、errors.jsonl、启动器和固定 EXE 状态更新 `2.报告/00_研究总览.md` 的「模型预览」节；它会分开统计真实导出动作模型与程序化动作预览模型，并统计 `thumbnail/skipped` 原因，例如 `blender_not_found`。`create_model_viewer_launcher.py` 只生成 `打开模型预览.bat`，并把 `--study-root`、`--models-dir`、`--manifest`、`--thumbs-dir`、`--preview-cache-dir` 传给固定 EXE，保持只读离线预览边界。`verify_model_viewer_gui.ps1` 使用 CDP 读取 Electron 页面状态，启动 EXE 时通过单个已引号化参数串处理中文路径和空格路径；含动作模型时必须验证右侧动作列表、底部播放栏、拖动时间轴暂停和 canvas 可达；含 `full_body_preview` 的入口可追加 `-ExpectFullBodyPreview` 验证按钮可见、点击后标题/按钮切换、canvas 可达且 `viewportError` 为空。GameModelViewer 读取 glTF/GLB 内置 `animations` 后提供右侧动作列表、底部播放/暂停、时间轴和速度控制；若条目没有 animations，则按静态模型全屏预览。

## 验证

- 源游戏目录文件数量和关键文件哈希没有变化。
- Unity 构建已保存 `unity_preflight.py` 输出；其中的 workflow flags 已被转成 AssetRipper、UnityPy、Il2CppDumper、外部载荷核验和 native plugins 边界任务。
- `<study-root>\0.原始导出\AssetRipper\ExportedProject` 已由 AssetRipper 生成；`<study-root>\0.原始导出\UnityPy\UnityPy全量可读资源\summary.json` 与 `all_resources_index.csv` 已由 UnityPy 生成。UnityPy source root/候选全链经 exporter 与独立 validator 双重 reparse/canonical 门禁并在读取期绑定稳定文件代；protocol 1 的 summary/checkpoint 引用同一先行发布的不可变事务 sidecar，sidecar 的计划路径/byte size/SHA-256 与两个当前控制 payload 精确匹配，完整 sidecar 历史和 fragments 的递归 canonical exact set 已闭环且无 nested/extra/illegal/reparse；cleanup/recovery 到调用方显式关闭的 committed target leases 已验证，输出树不存在任何事务 partial。
- 若 UnityPy 索引含 MonoBehaviour，`4.临时目录/中间索引/UnityPy分析/MonoBehaviour边界/` 的严格 JSON 与中文 Markdown 已生成；索引/metadata/config/contract/probe 来源 SHA-256、两遍索引行数与完全相同且非空唯一的表头、unknown 状态、metadata 完整性、Expected-vs-actual 和配置化来源组均对账；final/stage/backup 与输入无 canonical/hard-link alias，输出路径无 reparse，提交前后输入快照未持久漂移，两份 artifact 的 stage/reopen/rollback 集合事务已通过；每次 commit 前 destination 存在性/身份/字节仍匹配计划，晚到与 commit 间漂移不被覆盖；Windows reservation/old/stage/transaction-final/backup 的每个 Nt rename 都有 syscall 后置 basename+identity 协调，`GetFinalPathNameByHandleW`、普通相对 identity 查询和连续协调故障已由候选 basename、精确句柄及固定目录直接 NT 重开/fstat 收敛或保守失败，底层成功后 wrapper 抛错不会遗留旧缓存或隐藏幸存代；rollback 不先删除 final，报告 complete 前已证明每个旧代回到 final，失败时只列出当前存在且 identity 匹配的 recovery backup 与 `rollback_incomplete` 机器状态；cleanup 全项尝试、目录 handle 必关，已提交代的 warning/待清理路径可机读且不会把抢占同名的外部代列为自有路径；两份成功 final 的 mandatory leases 由 owner 保持到 API/CLI 在租约内消费/序列化验证，再经双 guardian 显式释放，调用方异常、序列化失败、primary/guardian/directory close 故障和释放空窗换代均有可机读 handoff/release 状态；create/commit/restore/delete/cleanup 均相对同一稳定输出目录 handle/fd，父路径被换成 junction/symlink 时外部目录全量基线不变；缺失 byte_size/raw SHA 标 unavailable，多候选角色与规范十进制语义可解释，未匹配 remark 预览有界，probe 不产生总体恢复率。
- UnityPy 组织步骤已按 config/contract 两遍流式处理，把真实资源写入 `1.分类资源/`，并在 `4.临时目录\中间索引\UnityPy组织\` 下生成 `资源组织索引.csv`、`动画序列帧索引.csv`、`资源组织摘要.json`、`资源组织错误.jsonl` 和 `主角资源候选索引.csv`；两个实际消费 pass 的 SHA-256、稳定文件身份和初始快照完全一致，A→B→A 不会发布成功；每个 `export_path` 通过 strict POSIX 与 Windows alias 门禁，精确重复只计一次且两遍去重数一致；源根到最终资源文件使用稳定 handle/fd 链，同一打开对象承担 SHA-256 与 transfer，junction/symlink 换代不能导入根外字节；分类目标验证和失败 cleanup 只绑定本事务创建的精确文件代，后到外部代不会被删除；源 index/config/contract/资源 SHA-256 与 transfer/冲突/过滤/跳过计数可对账；序列 scope 默认隔离 source/container/export parent，候选超过阈值时 SQLite spill 已触发且 database/journal/WAL/SHM/partial 无临时残留；重复运行复用同内容并保留 mtime，不产生递增后缀；六文件 artifact 组绑定计划 destination 身份，在任一提交失败/控制输入漂移时用 backup 直接原子 replace 恢复旧集合且 summary 最后恢复；恢复失败时固定 summary 为明确的 `ok=false` 恢复态或不存在；cleanup-status 二次 summary 从发布后到旧恢复代删除和成功返回都绑定本事务新代的 identity/SHA-256，结构有效的后到代不能被接受并触发旧代删除；成功代 cleanup 告警可机读且自有孤儿可在下次安全重试；include/exclude 未被主角候选副本越过；非序列资源保留实际命名，确认序列帧使用动作名加稳定的 1-based 两位序号。
- 若存在 UnityPy Mesh，`export_unitypy_meshes_to_gltf.py` 已生成 `1.分类资源/模型/<模型名>/*.gltf`、`.bin`、模型局部 `textures/` 和 `4.临时目录\中间索引\UnityPy模型导出\模型导出索引.csv`；索引统计已区分 `unity_material_main_texture`、`same_name_candidate` 与无绑定模型。
- UnityPy-derived glTF 已抽样检查 `baseColorTexture`：可恢复材质的 glTF 包含 `images/textures/materials`，贴图 URI 是相对路径且文件存在；Blender CLI 验证记录显示 Image Texture 节点连接到 Base Color。不能只以“Blender 能导入几何体”作为模型验收。
- 若 `1.分类资源/模型/` 存在模型，`4.临时目录/中间索引/模型预览/manifest.json` 已生成，`thumbs/`、`preview-cache/`、`errors.jsonl` 位于同一目录；若 UnityPy 存在 AnimationClip metadata，`Unity动作预览_AnimationClips` 条目在 manifest 中有 `animation_count > 0` 和 `animation:with` 标签；`2.报告/打开模型预览.bat` 已生成且传入本次 study-root 参数，`2.报告/模型预览.bat` 等重复入口不存在；`2.报告/00_研究总览.md` 包含模型预览摘要。GameModelViewer 固定 EXE 仍在 Skill `built-in-tools`，未复制到 study-root；Blender 不可用或缩略图/FBX preview-cache 失败时已写明原因。
- 若检测到 Unity VFX，`4.临时目录/中间索引/VFX预览/manifest.json`、`errors.jsonl`、`reconstructed/`、`unity-export/Assets/GameAnalysisVfxPreview` 和 `2.报告/打开特效预览.bat` 已生成；manifest 条目记录 `preview_status`、`reconstruction_status`、`unity_export_status`；无法解析或部分还原的 ParticleSystem、TrailRenderer、LineRenderer 依赖写入 warnings/errors，不修改源游戏，也不把 best-effort 结果标为完整 Unity runtime 复现。
- `1.分类资源/` 不包含 UnityPy 元数据 `.json/.csv/.txt/.md`；`资源组织摘要.json` 记录 `skipped_metadata_count`，元数据仍保留在 `0.原始导出/UnityPy/` 或 `4.临时目录/中间索引/`。
- `full`/`code` 模式下若存在 IL2CPP，`4.临时目录/中间索引/Native分析/` 包含 targets CSV、targets manifest、`ghidra_decompile_coverage.csv/json`；报告写明 target/OK/failed/missing/coverage。若只做关键样本，必须明确标为“样本分析”，不得称为全部代码导出。
- 真实资源文件检测已生成 `4.临时目录\自测结果\真实资源文件检测_分类目录.json`；分类目录中的图片、模型、材质/Shader、音频、字体等真实资源计数大于 0，或报告中说明为什么只能导出 metadata。
- `4.临时目录/中间索引/学习输出/` 下包含 `美术资源尺寸分布.csv`、`色彩摘要输入.csv`、`渲染资源索引.csv`、`音频资源索引.csv`、`动画资源索引.csv`、`命名规范统计.csv`、`代码命名空间索引.csv` 和 `学习输出摘要.json`。
- `4.临时目录/中间索引/AssetRipper分析/` 下包含导出清单、场景索引、Prefab 索引、图片尺寸索引；`4.临时目录/中间索引/图片画廊/manifest.json` 存在，`2.报告/打开图片画廊.bat` 存在。
- `<study-root>` 根目录没有未归档的工具产物、临时画廊或散落索引文件。
- `2.报告/` 只包含 `00_研究总览.md` 到 `09_可迁移技术清单.md`、`打开图片画廊.bat`、模型预览 BAT 入口 `打开模型预览.bat`、特效预览 BAT 入口 `打开特效预览.bat` 和可选 PDF；没有 CSV/JSON、验证日志、manifest、缩略图、分类摘要或重复 BAT 别名。
- 临时目录卫生审计已运行；交付清理时浏览器 profile 已清理且非浏览器工具中间文件未误删。
- 图片画廊 manifest 能列出图片，缩略图 PNG 已生成；固定 EXE 存在验证和 `--self-test --images-root ...` 烟测必须通过。
- `打开图片画廊.bat` 包含 `--study-root`、`--manifest` 和 manifest `source_roots` 派生的重复 `--images-root`。
- 模型预览固定 EXE `--self-test` 通过；含真实或程序化动作条目时，`verify_model_viewer_gui.ps1` 的 CDP 验证输出 `ok=true`、动作列表数量达到预期、拖动时间轴后按钮回到“播放”、`viewportError` 为空；含全身组合入口时，追加 `-ExpectFullBodyPreview` 后按钮点击、标题切换和 canvas 状态也必须通过。
- 关键角色动画 CSV 中每帧都有动画名、原 Sprite 名、GUID、尺寸、状态和输出文件。
- 最终原创规范覆盖来源边界、色彩/调色、形状/轮廓、UI/HUD 可读性和制作规格/约束，并明确哪些属于“我的判断”、哪些属于“待验证假设”。
- 最终报告和规范通过 `guideline_lint.py`，没有引导公开传播或直接复用原始资源的表述。
