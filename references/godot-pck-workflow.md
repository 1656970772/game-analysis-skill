# Godot 3.x PCK 全量研究流程

## 导航

- [适用边界](#适用边界)
- [目录合同](#目录合同)
- [执行顺序](#执行顺序)
- [分类与学习产出](#分类与学习产出)
- [精确版本二级静态验证](#精确版本二级静态验证)
- [有损恢复边界](#有损恢复边界)
- [完成检查](#完成检查)
- [Windows GUI EXE 调用陷阱](#windows-gui-exe-调用陷阱)

## 适用边界

检测到 `project.godot`、独立 `.pck`、`GDPC` magic 或 Godot 3.x 版本字符串时读取本文件。完整顺序固定为：

`detect→PCK probe→GDRE list→scripts-only→full recover→organize→学习索引/画廊→精确 Godot 二级静态验证`

本流程只处理用户授权的本地包。不得执行游戏 EXE；不得猜测加密密钥、接入自定义解密器或绕过 DRM、反作弊、访问控制。`scripts/analyze_godot_pck.py` 只支持首部为 `GDPC` 的独立 PCK format 0/1，不负责嵌入 EXE 尾部的 PCK，也不提取内容。

## 目录合同

| 产物 | 固定位置 |
|---|---|
| 原始 PCK/ZIP 解包 | `0.原始导出/<来源工具>/` |
| GDRETools 完整恢复 | `0.原始导出/GDRETools恢复/` |
| scripts-only 小样本 | `4.临时目录/小样本/GDRETools脚本恢复/` |
| 图片/音频/字体等分类资源 | `1.分类资源/` 对应分类 |
| 可读 `.gd` | `3.代码/脚本/<恢复相对路径>` |
| PCK、分类、恢复索引 | `4.临时目录/中间索引/Godot/` |
| GDRETools/Godot stdout、stderr | `4.临时目录/工具日志/` |
| 失败记录 | `4.临时目录/中间索引/Godot/organize_failures.jsonl` 或 `4.临时目录/失败记录/` |

不得把 `project.godot`、`.tscn`、`.tres`、导入缓存或完整恢复工程直接复制到分类目录。分类目录不放 CSV、JSON、Markdown、summary 或 manifest。

## 执行顺序

### 1. detect 与 inventory

由主 agent 独占运行 `detect_engine.py` 和 `asset_inventory.py`，记录 `.pck`、`project.godot`、Godot 版本字符串、文件大小与源 SHA-256。执行型子 agent 接收检测摘要，不重复扫描。

### 2. PCK probe

先运行无第三方依赖的只读探测器：

```powershell
& "<skill>\tools\python-env\Scripts\python.exe" "<skill>\scripts\analyze_godot_pck.py" `
  "<game.pck>" `
  --output-dir "<study-root>\4.临时目录\中间索引\Godot\PCK探测"
```

必须通过：`GDPC`、format 0/1、16 个零保留字段、受限条目数、严格 UTF-8 `res://` 路径、无绝对/盘符/UNC/`..`、无 Windows 设备名/NTFS ADS/非法字符/段尾点或空格、无 Unicode NFC + 大小写折叠冲突、`offset+size` 不越界、数据区互不重叠、全部条目 MD5。任何一项失败都停止写出恢复，保留错误输出；探测器不提取资产。

### 3. GDRE list

从 `references/tooling-manifest.json` 读取 `GDRETools` 版本、`local_path` 和 `exe_sha256`，核对文件与哈希。用隐藏 `Start-Process` 调用 `--headless "--list-files=<game.pck>"`，stdout/stderr 分别写入 `4.临时目录/工具日志/GDRETools/`。要求退出码为 0、列表非空且没有 unsupported/encrypted/checksum/越界错误。列表数量要与 PCK probe 对账。

不要用 `rtk` 直接代理 `gdre_tools.exe`，不要用 `& $gdre ... 2>&1 | Tee-Object` 判断 GUI 型进程是否完成。

### 4. scripts-only 小样本

```powershell
pwsh -NoLogo -NoProfile -File "<skill>\scripts\export_godot_pck_project.ps1" `
  -SourcePath "<game.pck>" -StudyRoot "<study-root>" -SkillRoot "<skill>" `
  -TimeoutSeconds 600 -ScriptsOnly
```

验收至少一个 `.gdc` 恢复为可读 `.gd`，函数、常量、NodePath 和资源引用语法完整。首次不得传 `-ForceBytecodeVersion`；只有自动检测失败，且 PCK probe 与恢复日志提供一致的精确版本证据时，才用 `-ForceBytecodeVersion "<detected-bytecode-version>"` 做对照。参数值必须来自本次证据，不得复用其他项目版本。注释、空白、制表符和原始文件名不保证恢复。

### 5. full recover

```powershell
pwsh -NoLogo -NoProfile -File "<skill>\scripts\export_godot_pck_project.ps1" `
  -SourcePath "<game.pck>" -StudyRoot "<study-root>" -SkillRoot "<skill>" `
  -TimeoutSeconds 1800
```

完整恢复固定写入 `0.原始导出/GDRETools恢复/`。包装器要求 manifest 的 `exe_sha256` 是非空 64 位十六进制值并在启动前匹配；默认拒绝非空恢复目录，不隐式续跑或覆盖。恢复根、日志根和索引根必须先解析 Junction/symlink/reparse component 的 canonical 路径并证明仍在真实 `study-root` 内，全部通过后才能创建目录、日志或启动进程。包装器记录工具哈希、参数、超时、退出码、stdout/stderr、Job Object 清理保证和源 PCK 前后 SHA-256；恢复退出码必须为 0，源哈希必须一致。不得执行同目录的游戏 EXE。

### 6. organize

```powershell
& "<skill>\tools\python-env\Scripts\python.exe" "<skill>\scripts\organize_godot_project_export.py" `
  --recovery-root "<study-root>\0.原始导出\GDRETools恢复" `
  --study-root "<study-root>"
```

默认读取 `references/godot-resource-classification.json`。分类规则要项目无关；需要调整时复制为任务配置并显式传 `--classification-config`，不要在脚本中硬编码项目名。固定分类、代码和索引根必须在任何复制或索引写入前完成 canonical 边界验证；写索引阶段复用已验证根，不从 CLI 字符串重新拼接。重复运行时同 SHA-256 记为 unchanged；目标已有不同内容时默认不覆盖并记录 conflict，只有明确要求才传 `--overwrite`。

## 分类与学习产出

- 图片按配置进入 `图片/UI`、`图片/场景`、`图片/道具`、`图片/图集`、`图片/序列帧` 或 `特效/粒子贴图`。
- `spritesheet`、`atlas`、`strip` 整张图片优先进入图集；没有切片证据时不得冒充序列帧。
- 配置声明支持的每种图片必须由 Pillow 执行格式匹配、`verify()` 和重新打开后的完整 `load()`；`.png` 还必须通过 chunk 边界、IHDR/IDAT/IEND、非空 IDAT 和 CRC。只有可完整读取的 RIFF/WAVE 或通过 Ogg 页边界、校验和、codec header 与 EOS 的 `.wav/.ogg` 才进入 `1.分类资源/音频/`。
- TrueType/OpenType/Collection 必须通过 offset table 与 table directory 边界；BMFont 必须通过文本必需段或二进制 block 边界。通过后 `.ttf/.otf/.ttc/.fnt` 才进入 `1.分类资源/渲染/其他/字体/`。
- UTF-8 `.gd` 保持恢复目录相对路径进入 `3.代码/脚本/`。
- `organized_files.csv`、`organize_summary.json`、`organize_failures.jsonl` 固定进入 `4.临时目录/中间索引/Godot/`。

分类后运行 `build_learning_indices.py`。若 `1.分类资源/图片/` 有真实图片，再运行固定图片画廊 manifest、BAT 和 EXE self-test 流程；Godot 场景、脚本、音频、字体和图片索引分别汇总进编号报告，不把索引文件复制进分类目录。

## 精确版本二级静态验证

先按恢复日志确定精确引擎版本，并从 manifest 选择相同版本 Godot；例如 3.2.3 项目使用 `Godot Engine 3.2.3`，不先用 3.6/4.x 迁移。

在任何编辑器打开前静态扫描恢复树：

1. 扫描 GDScript 行首 `tool`、`extends EditorPlugin`、`EditorPlugin` 注册代码。
2. 扫描 `.gdnlib`、`.gdns`、`GDNative`、DLL/SO/DYLIB 与原生库引用。
3. 扫描 `addons/`、自定义 import plugin、editor plugin 与自动加载项。
4. 命中可执行编辑器代码或原生扩展时，不打开原始恢复目录；记录证据并在隔离环境决定是否继续。

静态扫描通过后，把恢复工程复制到 `4.临时目录/Godot验证副本/`，用精确版本编辑器执行 `--editor --path <副本> --quit` 一类项目解析检查。只验证项目配置、场景/资源解析和导入日志，**不运行主场景**，不调用 `--path <project>` 后的游戏启动模式，不让编辑器改写 `0.原始导出/GDRETools恢复/`。

## 有损恢复边界

- `.gdc` 反编译不能恢复注释、原始空白、精确源码文件名或构建前宏处理。
- 导入纹理、音频、模型和字体可能已经过压缩、重采样、平台转码、烘焙或裁剪；GDRETools 输出是可学习的恢复/转码产物，不等于作者原始源素材。
- `.tscn/.tres` 文本化可能丢失编辑器私有状态；无法转换项要保留包内原始字节和日志。
- GDNative/原生 DLL 的实现不由 GDScript 恢复；只能索引 ABI、配置和调用边界，不能声称源码已还原。
- 加密或自定义包没有合法密钥时标为阻塞，不尝试破解或自定义解密器。

## 完成检查

- [ ] PCK probe JSON、CSV、摘要存在，所有条目路径、范围、重叠与 MD5 通过
- [ ] GDRE list 与 PCK probe 条目数对账，日志和退出码已记录
- [ ] scripts-only 成功或 bytecode 阻塞有完整证据；没有无证据强制版本
- [ ] full recover 位于 `0.原始导出/GDRETools恢复/`，源 PCK SHA-256 前后一致
- [ ] 图片、真实音频、字体和 `.gd` 分类正确，整张 spritesheet 未冒充序列帧
- [ ] 索引、summary、失败记录只在 `4.临时目录/中间索引/Godot/`
- [ ] 学习索引和需要的固定 EXE 画廊已生成并验证
- [ ] 精确 Godot 二级验证前已扫描 tool/EditorPlugin/GDNative，验证只在副本且不运行主场景
- [ ] 报告明确有损恢复、GDNative、加密与未转换边界

## Windows GUI EXE 调用陷阱

- GDRETools 是 GUI 子系统 EXE；`rtk` 直接代理可能提前返回、留下孤儿进程或拿不到真实退出码。
- 包装器必须先创建带 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的 Windows Job Object，再以 `CREATE_SUSPENDED | CREATE_NO_WINDOW` 创建目标进程，完成 `AssignProcessToJobObject` 后才 `ResumeThread`；stdout/stderr 分离重定向并用 `WaitForExit` 超时。Job 创建、进程创建、分配、恢复或关闭任一步失败都必须硬失败并写入 summary，不能先运行目标后补分配，也不能退化为事后扫描或只杀根 PID。
- `--recover`、`--list-files` 以真实退出码 0 判成功；不要只因日志出现 `Godot RE Tools` 或 `Successfully` 就通过。
- `--gdre-version` 可能输出正确版本文本但退出码为 1；Godot 3.2.3 `--version` 可能输出正确文本但退出码为 255。版本探测要结合 manifest 哈希、版本文本和无残留进程，不得把这一特例套到恢复命令。
- PowerShell 5.1 对无 BOM UTF-8 中文字面量不稳定；复用包装器中的 codepoint 目录名，不复制手写中文路径逻辑。
