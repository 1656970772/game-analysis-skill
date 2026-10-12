# 常见陷阱与防御性规则

## 目录结构违规

- **只允许 5 个编号目录**：`0.原始导出/`、`1.分类资源/`、`2.报告/`、`3.代码/`、`4.临时目录/`。不允许创建 `__ascii_work`、`0.完整资源导出目录`、`1.最终产出`、`2.主角资源`、`3.代码导出`、`主角资源`、`交付状态清单.md` 等旧版目录。
- **`_ascii_work` 不是天然 ASCII**：`4.临时目录/_ascii_work/` 只有在其完整绝对路径本身为 ASCII-compatible 时才可供旧工具使用；目录名是 ASCII 不代表父路径也兼容。
- **根目录不允许散落文件**：不允许在 study-root 根目录放置任何文件（包括 `.md`、`.csv`、`.json`）。

## Windows 路径兼容性

- **空格与非 ASCII 分开判断**：空格不等于非 ASCII。完整绝对路径全为 ASCII 时，即使包含空格也属于 ASCII-compatible；直接传入真实绝对路径，并使用参数数组或正确引号，不要改写路径。
- **兼容根由主 agent 配置并隔离所有权**：若项目内 `4.临时目录/_ascii_work/` 的完整绝对路径含非 ASCII，只能使用主 agent 明确配置的、位于现有卷上的普通目录 `<compat-root>`。主 agent 为每次分析生成一次分析运行唯一的 `owner-id` 并统一下发，子 agent 不得自选或改写兼容根、`owner-id`；每个 `task-id` 在所属 `owner-id` 下唯一，所有临时写入都派生自 `<compat-root>/<owner-id>/<task-id>/...`，使不同运行和任务无冲突。工作完成后把正式产物回填到 `0.原始导出`、`1.分类资源`、`2.报告`、`3.代码`、`4.临时目录`；外部兼容目录不是第六个交付目录。
- **不映射盘符**：不得创建、删除或复用任何盘符映射或虚拟盘符来绕过工具限制，也不得自动清理用户既有映射。没有合格兼容根时回报 `BLOCKED`，按 fail closed 停止该工具步骤。
- **保留事实与推断边界**：本次修复对当前 `scripts/` 的静态扫描未发现映射 mutator，这是事实；“历史映射来自 agent 即时命令”只能作为推断，不能证明过去不存在其他外部命令，也不得归责到当前脚本。静态扫描同样不能授权自动清理用户或其他进程创建的映射。

## 分类纯净性

- **图片目录只能有图片**：不允许出现 CSV、JSON、MD、TXT 等非图片文件。索引写入 `4.临时目录/中间索引/`；面向用户的摘要汇总到 `2.报告/`。
- **代码目录只能有代码**：不允许出现图片、模型、音频或索引文件。唯一例外是 `代码可读性判定.md`。
- **模型目录必须能用**：每个模型子文件夹必须能直接导入 Blender 且正常显示网格体和贴图。不要放只有 metadata 没有实际模型文件的空壳目录。

## 资源导出类

- **不要用文本索引代替真实资源**：分类目录中必须有真实资源文件。
- **不要另建快速入口**：不要另建"图片快速入口""模型快速入口"等汇总目录。
- **不要把批量解包结果包装为可复用素材包**：导出仅用于本机学习。
- **不要把 Unity VFX 的 `limits.max_items` 当成全量范围**：该值只控制常规交互预览规模；根对象超过上限或用户要求全量时，使用 `build_unity_vfx_full_index.py`，并对账 manifest 根数、全组件结构 CSV 与源索引。
- **不要压缩或截断 VFX manifest 顶层 `items`**：配置卡可限制嵌套深度和列表长度，但 `manifest.item_count` 必须与实际 `items` 长度一致。UnityPy metadata 真实字段可能位于顶层 `type_tree`，重建前先解包，并拒绝把 `NaN`/`Infinity` 写入 JSON。
- **不要按文件大小静默跳过 Unity YAML GUID**：Prefab/Scene 可能超过 25 MB；使用逐行流式扫描。闭包队列使用 `deque` 和 queued/copied 集合，避免 `list.pop(0)` 与重复入队在大工程中退化。
- **UnityPy 不得漏扫无扩展场景容器或重复扫外部载荷**：Unity 播放器构建常把场景序列化文件命名为 `level0`、`level1` 等，必须在 `*_Data` 范围内按 `^level\d+$` 发现，并覆盖 `unity default resources`、`unity_builtin_extra`；未知后缀只用 `UnityFS/UnityWeb/UnityRaw` 魔数补漏。`.resS/.resource` 只记录为 external payload，由 companion serialized file 读取，不能作为独立对象 source。
- **UnityPy 不得对空 Texture2D stream path 发起依赖加载**：`image_data` 为空且 `m_StreamData.path` 为空或 `m_StreamData.size <= 0` 时，这个 Texture2D 没有可读图像载荷。在调用 `data.image` 前先标记 `indexed_no_image_data`；否则 UnityPy 1.25.0 会把空路径解析为 environment 目录并尝试将目录当文件打开，产生误导性 `PermissionError`。不得将该错误解释为需要给源游戏目录写权限。
- **UnityPy MonoBehaviour read_error 不是源截断结论**：`Expected to read N bytes, but only read M bytes` 表示当前类型树只消费了 M 字节，不证明源对象只有 M 字节。用 `analyze_unitypy_monobehaviour_boundary.py` 区分 read_error、generic header、实际 custom fields 和证据 unavailable；不能把 exported 全算作自定义字段恢复，也不能把定向/分层 TypeTree probe 的成功率外推为总体。
- **MonoBehaviour 边界分析不得把解析、provenance 与双产物提交拆成无门禁步骤**：config/contract/probe/metadata 从同一稳定字节快照解析和哈希；大索引用两遍流式扫描前后身份与 SHA-256 门禁，第二遍即时处理 exported metadata，不保留完整成功行列表；两遍表头必须非空、所有列按大小写折叠全局唯一且彼此完全相同，不能让 `DictReader` 的后列覆盖吞掉冲突证据。final/stage/backup 与任一输入存在 canonical/hard-link alias、输出目录含 symlink/junction/reparse、提交前后输入持久漂移时都必须 fail closed；stage/backup 使用同目录随机排他/no-follow 文件，两份 artifact 全部 stage/reopen 后再提交。仅在 rename 前追加 path guard 不能绑定 destination：Windows 必须从快照开始同时持有两个既有 final 的拒写句柄，把精确旧文件对象以 `ReplaceIfExists=0` 退休到恢复名，再用 no-replace 发布 stage；commit/rollback 不得使用 `ReplaceIfExists=1`。这样同路径或 hard-link 的同尺寸写入由内核拒绝，pathname 替换或 rollback 系统调用入口晚到的 final 由 no-replace 保留。POSIX 若没有等价 mandatory target-generation primitive，必须在 stage/backup 前拒绝覆盖既有 final 并要求 fresh 输出目录，不能退化为 path guard + replace。rollback 二次失败或 rollback 期外部漂移要保留唯一 backup，并报告 `rollback_incomplete/recovery_files`。cleanup 要尝试全部自有 delete/close，稳定输出目录 close 不得被首错跳过；已发布新代的 cleanup 故障必须保留 `transaction_state=committed`，并以 `cleanup_state/cleanup_warnings/cleanup_required_paths` 区分可恢复告警和待清理项。输出目录只固定一次稳定身份，stage/backup create、write/reopen、commit/restore rename、rollback delete 与 finally cleanup 全部使用 basename 相对的稳定 handle/fd；不得在每次操作时重新解析 lexical parent，也不能先写到可能已换成 junction 的外部目录再事后删除。多个显式证据候选按配置顺序选首个有效值，且不得与结构列或彼此角色冲突；等价别名可共存、有效冲突拒绝、非法别名不得被后续有效列静默遮蔽。byte size 只接受 `0` 或无前导零的 ASCII 正十进制；未匹配 remark 只能保留配置上限内的有界确定性样本。
- **UnityPy 全量导出不能把文件名清洗结果当作完整 canonical identity**：截断、Unicode/非法字符归一化都可能让不同 source label、serialized file 或 AudioClip sample key 落到同一 `asset_id`/输出路径。fresh 输出使用 protocol 2 的短可读前缀 + 完整原始身份 SHA-256，并在 checkpoint 持久化 scheme/version；AudioClip 子样本文件名也必须包含未经清洗的完整 sample key SHA-256，并预先按 Windows 大小写语义检查唯一性。多样本先全部 stage，再以可回滚集合提交；任一样本 stage/commit 失败都恢复旧集合并删除本轮新增，不能留下无索引子文件。protocol 1 只能运行“全部发现源已 completed、全部 canonical fragment 哈希匹配、全局 `asset_id` 与每个严格 canonical 非空物理 `export_path` 唯一、没有 reprocess 请求”的纯 no-work；`.`、重复分隔符、反斜杠等物理路径 alias 一律拒绝。预检后必须持有 checkpoint/fragments 的稳定读租约并从该快照生成本轮证据，不能重新信任漂移后的路径字节；释放 checkpoint 租约后，要把预检时的存在性、稳定 identity、byte size 与 SHA-256 作为跨目录组事务的目标代条件，每个目标第一次改变前重验，实际移入 backup 的代也必须再次匹配。旧目标、stage、rollback 和 cleanup 只用 no-replace rename 移动；晚到硬链接、同尺寸异内容或提交间可观察换代必须保持外部代不动并 fail closed。若非合作写者最终恢复了完全相同 inode/文件 ID、size、mtime 与 SHA-256，则没有可观察代差，不能声称已检测该 ABA。最终 summary/checkpoint 必须在同一跨目录可回滚组事务中发布，任一提交失败都恢复旧代，不能留下互相矛盾的新旧 evidence；pending/retry/reprocess 一律使用新的 protocol 2 输出目录，不能静默迁移或混用 fragments。
- **导出根目录“看起来在目标路径下”不代表写入安全**：source/output 等同或互相嵌套、output 父/子组件中的 symlink/junction/reparse point 都必须在首次目录/checkpoint/resource 写入前拒绝；每一级新目录必须经已验证父目录 handle/fd 相对创建，不能对可变 lexical path 调用 `mkdir` 后再事后复核。Windows 的新目录创建、最终临时写入、`replace` 和 cleanup 必须在完整父目录链的稳定句柄租约内执行；仅让目录句柄省略 `FILE_SHARE_DELETE` 在现代 Windows 上不足以阻止 rename，必须再经已验证的最终目录句柄相对创建一个 delete-on-close 子锚点，并让该锚点在整个创建/写入/提交/清理窗口省略 delete sharing，从内核层阻止父目录 rename/remove/junction-swap。写后 `resolve()` 只能做补充校验，不能代替该门禁。POSIX 目录创建和最终 replace/remove 使用 `O_DIRECTORY|O_NOFOLLOW` 的 descriptor-relative API，并明确 path-based writer callback 仍属于 cooperative-writer 边界。完成门禁必须把 partial 检测合并进单次递归 output inventory，不能只检查 resume 目录和根目录而漏掉资源子目录中的 `.ga-export-*.partial`。
- **source root 当前没有 reparse 不代表读取边界已经成立**：调用方给出的 source lexical 根本身、祖先、所有嵌套目录和最终候选文件都要在发现前拒绝 symlink/junction/reparse，canonical 候选必须位于 canonical 根内；发现后还要为整棵授权文件集持有稳定读租约并在读取/提交窗口复核。独立 validator 必须重新执行同一全树门禁并持有自己的租约，不能复用 exporter 的“已发现”结论。组事务也不能在 backup/stage cleanup 后直接返回：reuse/published 的每个目标从 cleanup、恢复 partial 删除、证据消费直到调用方真正成功边界都要由 exact-generation lease 固定并显式交接。protocol 1 sidecar 不是提交后补写的可变状态：必须先发布不可变预提交记录并持有租约，记录绑定计划 summary/checkpoint payload 的路径、byte size 与 SHA-256，控制文件才能引用它；失败可以留下未引用历史，绝不能留下引用缺失 sidecar 的已提交控制面。恢复只删除递归发现且严格 token-owned、自身代精确匹配的 partial，并在 sidecar 与两个 committed targets 代全窗口匹配时执行；精确 handle/fd 的 rename/unlink 底层成功后即使 wrapper 抛 `BaseException`，账本也必须按当前真实 basename/存在性协调，报告中只列真实存在且仍 owned 的路径。validator/snapshot 要递归建立 fragments 与全部 sidecar 历史 exact set，嵌套、额外、非法命名、reparse 或未闭环引用都拒绝。backup 实际代不匹配且 source 名已被晚到目标占据时，必须保留两者并报告 `rollback_incomplete/recovery_files`。
- **protocol 1 sidecar 文件名合法不等于历史合法**：v3 sidecar 必须通过 `previous head + previous sidecar SHA-256 + previous count/chain SHA-256 -> new head/count/chain SHA-256` 形成唯一有序链，summary/checkpoint 同时锚定同一 head/count/chain。控制面没有历史引用时只允许一次空目录 genesis；旧 v2 只接受控制面已显式锚定的 singleton boundary。未被 head 链引用的文件默认是 extra；仅唯一、严格 v3、前驱与计划 payload/旧控制代都精确匹配的 next tail 可按 interrupted publish 在同一旧代租约内恢复或清理，fork、missing、cycle、duplicate、摘要篡改、nested/illegal/reparse 全部拒绝。
- **vgmstream 的 `-I` 不是只读开关**：单独使用 `-I <bank.bnk>` 虽会打印 JSON，但仍可能按默认规则在输入文件旁写出 `<bank.bnk>.wav`。任何 metadata 探测必须使用 `-m -I`；任何真实解码必须使用显式 `-o <study-root 内目标>`，并在执行前后对账源目录文件清单。

## 工具使用类

- **不要把 UnityPy 成功当作 AssetRipper 成功**（Unity）：两个工具独立评估。
- **AssetRipper runtime log 使用 ASCII 路径**（Unity）：AssetRipper 1.3.14 的 `--log-path` 指向带中文或空格的路径时可能直接退出且没有日志。默认用 `scripts/export_unity_assetripper_project.ps1`，它会把 runtime log 写入 ASCII 临时目录，再复制回 study-root。
- **PowerShell 5.1 不可靠解析无 BOM UTF-8 脚本中的中文字面量**：`.ps1` 里需要长期复用的目录名、默认参数和验证搜索词尽量用 ASCII、配置文件或 codepoint 组装；临时命令仍需显式设置 UTF-8 输出并使用 `-LiteralPath`。
- **不要让 `rtk` 直接代理 Python 的中文 JSON 输出**：部分 Windows 链路会把中文路径转坏。使用 `scripts/invoke_python_utf8.ps1 -Script <script.py> <args>` 设置 Console/环境 UTF-8、保持参数数组边界并透传退出码；确需 `rtk` 时代理外层 `pwsh -File`。PowerShell 5.1 读取 BOM-less UTF-8 `.ps1` 仍有代码页风险，关键代码前的非 ASCII 行注释应以 ASCII 字符收尾，或由受验证的构建步骤统一写入 UTF-8 BOM。
- **pytest 不要依赖不可写的共享 AppData 临时根**：若本机 `pytest-of-<user>` 权限异常，测试 fixture 使用 Skill/项目内带 UUID 的唯一 `tests_runtime/` 子目录并在 teardown 清理；不要改写用户全局 TEMP，也不要留下运行目录。
- **不要用 `rtk` 直接代理 GDRETools/Godot 等 GUI 子系统 EXE**：代理层可能先返回、留下孤儿进程，或无法取得真实退出码。对可能派生子进程的恢复命令，应先创建带 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的 Job，再以挂起状态创建目标、分配 Job 后恢复，并分离重定向 stdout/stderr、设置超时；创建、分配、恢复或关闭失败必须硬失败并记录，不能先运行后补分配，也不能退化为事后扫描或只杀根 PID。
- **不要只按版本文本判 GUI EXE 成败**：GDRETools `--gdre-version` 可能输出正确文本但退出码为 1，Godot 3.2.3 `--version` 可能输出正确文本但退出码为 255；版本探测结合 manifest 哈希、文本和残留进程判定。该特例不得套到 `--list-files`/`--recover`，这两类实际任务仍要求退出码 0。
- **不要把 DummyDll 当完整源码**（Unity IL2CPP）：Il2CppDumper 的 `DummyDll` 只有类型签名。
- **不要把空 stub 写成"无逻辑"**：区分"代码被编译无法还原"和"项目确实没有逻辑"。
- **不要把 `value[2]` 当作纹理 UUID**（Cocos Creator）。
- **不要误用 `windows-arm64` 包**（ASTC）：Windows x64 选 `windows-x64`。

## 模型修补类

- **UnityPy glTF 不能只导出几何**：如果 glTF 只有 geometry/UV/index，旁边即使复制了 `textures/`，Blender 也不会自动显示贴图。必须从 Renderer -> Material -> `_MainTex` 恢复 Texture2D 名称，把贴图复制到模型局部相对路径，并在 glTF 写入 `images`、`textures`、`materials`、`baseColorTexture` 和 primitive `material`；索引记录 `material_binding_source`。只按同名前缀复制的候选必须放在 `textures/待验证_同名前缀/` 并标为 `same_name_candidate`。
- **glTF 缺贴图必须修补**：UModel 导出的 glTF 如果只有材质槽没有贴图，必须运行 `repair_umodel_gltf_materials.py` 修补后再放入分类目录。
- **噪声/mask/distortion 不得误接为 base/normal**：无法判断的运行时特效材质标 `no_props` 或 `no_texture_refs`。
- **Blender 路径必须加引号**：exe、`.py`、`.blend`、glTF 路径全部有引号；出现加载失败先查空格路径问题。
- **Blender 已安装不等于 PATH 可见**：`Get-Command blender` 失败时，先查 `C:\Program Files\Blender Foundation\Blender *\blender.exe`、注册表卸载项和用户 PATH；找到后可显式传 `--blender "<blender.exe>"` 或加入当前用户 PATH，再重跑缩略图。不要直接写成 `blender_not_found`。

## 使用后迭代类

- **新工具必须放入 Skill 的 tools/ 目录**：不管体积多大，本次使用的新工具必须沉淀。
- **必须更新 tooling-manifest.json**：记录名称、版本、路径、来源 URL、许可证、SHA256、用途。
- **不要沉淀私有信息**：项目私有路径、密钥、原始素材和反编译代码不得写入 Skill。
