# IL2CPP 与空 C# 方法处理流程

## 适用边界

仅在用户明确授权的本地游戏目录内使用。目标是理解与美术研究相关的行为事实，例如动画状态切换、武器/道具挂点、特效触发、镜头震动、UI 反馈和音画节奏；不要发布、出售、嵌入、复用或分发反编译代码。

触发条件：

- AssetRipper 导出的 `.cs` 只有类、字段、方法签名，方法体为空或只返回 `false/null/default`。
- 游戏根目录存在 `GameAssembly.dll`，且存在 `*_Data/il2cpp_data/Metadata/global-metadata.dat`。
- `0.原始导出/AssetRipper/ExportedProject/AuxiliaryFiles/GameAssemblies/Assembly-CSharp.dll` 用 ILSpy/dnSpy/ilspycmd 反编译后仍是空方法。

## 目录建议

```text
<study-root>/
  3.代码/
    IL2CPP_Dump/
      dump.cs
      script.json
      il2cpp.h
      stringliteral.json
      DummyDll/
      导出说明.md
    Native映射/
      方法映射.csv
      行为观察.md
      未解析方法清单.md
      Ghidra伪代码批次/
  4.临时目录/
    GhidraProjects/
    工具脚本/IL2CPP_Ghidra/
    工具日志/
    中间索引/Native分析/
```

## 判定 Mono 还是 IL2CPP

1. 查找关键文件：

```powershell
python <skill>\scripts\unity_preflight.py "<game-root>" --format markdown
Get-ChildItem "<game-root>" -Force
Get-ChildItem "<game-root>\*_Data\il2cpp_data\Metadata" -Filter "global-metadata.dat" -Recurse
```

2. 先看 `unity_preflight.py` 的 `Scripting backend`、`Metadata version` 和 workflow flags；它只读检查 metadata 头部，不解包资源。
3. 若有 `Managed/Assembly-CSharp.dll` 且 ILSpy 能看到真实方法体，可以走 managed C# 反编译路线。
4. 若有 `GameAssembly.dll + global-metadata.dat`，即使尚未运行 AssetRipper，也先标记为 `IL2CPP native code required`。不要把后续空 stub 当作真实逻辑；metadata 版本高于 29 时，把 Cpp2IL 2022 降级为备用兼容性尝试。

## ILSpy/dnSpy 验证

使用 ILSpy/dnSpy/ilspycmd 只做“是否有真实 IL 方法体”的验证：

```powershell
& "<skill>\tools\ilspycmd_10.0.1.8346\ilspycmd.exe" `
  -t "命名空间.类名" `
  "<study-root>\0.原始导出\AssetRipper\ExportedProject\AuxiliaryFiles\GameAssemblies\Assembly-CSharp.dll"
```

如果输出仍是空方法，记录为“当前 DLL 是 dummy/stub，不是完整业务逻辑程序集”。来源是本地反编译结果。

## Il2CppDumper 映射

输入：

- `<game-root>\GameAssembly.dll`
- `<game-root>\*_Data\il2cpp_data\Metadata\global-metadata.dat`

命令：

```powershell
& "<skill>\tools\Il2CppDumper_v6.7.46\Il2CppDumper.exe" `
  "<game-root>\GameAssembly.dll" `
  "<game-root>\<game-name>_Data\il2cpp_data\Metadata\global-metadata.dat" `
  "<study-root>\3.代码\IL2CPP_Dump"
```

若工具在非交互终端因 `Press any key` 报错，把 `config.json` 的 `RequireAnyKey` 改为 `false` 后重跑。

期望产物：

- `dump.cs`：类型、字段、方法签名、RVA/VA。
- `script.json`：Ghidra/IDA 标注需要的地址与签名。
- `il2cpp.h`：结构体定义。
- `DummyDll/`：给 IDE/反编译器引用的壳 DLL，不含完整方法体。

## Ghidra 主线

Ghidra 适合自动化主线；它可以 headless 导入 `GameAssembly.dll`，再运行 Il2CppDumper 生成的脚本或改写脚本进行符号标注。

Windows 注意事项：

- 优先把 Ghidra、JDK、`GameAssembly.dll`、`script.json` 和 Ghidra project 放到纯 ASCII 路径。`.bat` 对中文路径、`Program Files (x86)` 这类带括号路径容易误解析。
- Ghidra 12 headless 不一定默认启用 PyGhidra；Il2CppDumper 旧脚本可改为 `# @runtime Jython`，并把弹窗 `askFile()` 改成读取 `getScriptArgs()[0]`。
- 先用 `-noanalysis` 导入并套符号，确认脚本成功；再对少量关键方法定点 decompile，最后才考虑全量自动分析。
- 定点 decompile 的 targets CSV 使用 ASCII 或 UTF-8 without BOM；带 BOM 的 CSV 会让 Jython `csv.DictReader` 把首列读成 `\ufeffname`，导致所有目标被跳过。
- `ghidra_decompile_targets_csv.py` 只导出 targets CSV 中列出的函数；全量 auto-analysis 是为了补充函数边界、交叉引用、常量引用和反编译质量，不会自动把所有函数导出成 `.c`。若用户要求 full/code 或“全部代码导出”，必须先从 `dump.cs` 批量生成 targets CSV，再分批 decompile 并输出覆盖率。

默认命令草案：

```powershell
$Skill = "<skill>"
$Ghidra = "$Skill\tools\Ghidra_12.0.4_PUBLIC"
$env:JAVA_HOME = "$Skill\tools\JDK21"
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
$GameAssembly = "<game-root>\GameAssembly.dll"
$Dump = "<study-root>\3.代码\IL2CPP_Dump"
$ProjectRoot = "<study-root>\4.临时目录\GhidraProjects"
$ScriptDir = "<study-root>\4.临时目录\工具脚本\IL2CPP_Ghidra"

New-Item -ItemType Directory -Force $ProjectRoot, $ScriptDir

Copy-Item "$Skill\scripts\ghidra_apply_il2cppdumper_jython.py" "$ScriptDir\ghidra_apply_il2cppdumper_jython.py" -Force

& "$Ghidra\support\analyzeHeadless.bat" `
  $ProjectRoot "Game_IL2CPP" `
  -import $GameAssembly `
  -overwrite `
  -noanalysis `
  -scriptPath $ScriptDir `
  -postScript ghidra_apply_il2cppdumper_jython.py "$Dump\script.json" `
  -log "<study-root>\4.临时目录\工具日志\ghidra_headless.log" `
  -scriptlog "<study-root>\4.临时目录\工具日志\ghidra_script.log"
```

如果明确需要 native call graph、交叉引用或大范围反汇编，再单独跑全量自动分析，并把它视为重任务：

```powershell
& "$Ghidra\support\analyzeHeadless.bat" `
  $ProjectRoot "Game_IL2CPP" `
  -process GameAssembly.dll `
  -analysisTimeoutPerFile 1800 `
  -log "<study-root>\4.临时目录\工具日志\ghidra_headless_full_analysis.log" `
  -scriptlog "<study-root>\4.临时目录\工具日志\ghidra_headless_full_analysis_script.log"
```

若全量分析接近或超过 30 分钟，优先检查日志中的 `Total Time`、`Analysis timed out` 和各 analyzer 耗时；不要把全量自动分析失败等同于“无法进行 IL2CPP 行为观察”。保留已套用的 Il2CppDumper 符号后，用 `-process GameAssembly.dll -noanalysis` 跑定点 decompile。

一次成功的默认判断标准：

- Il2CppDumper 已生成 `script.json` 和 `dump.cs`。
- Ghidra headless 使用 `-noanalysis` 成功导入或处理 `GameAssembly.dll`，并成功执行 `ghidra_apply_il2cppdumper_jython.py`。
- targets CSV 无 BOM，列名是 `name,rva`，每一行都能在 `decompile_summary.csv` 中得到 `OK` 或明确失败原因。
- 若用户要求的是玩法/美术学习，默认只导出与问题相关的目标方法；若用户明确要求全量 native 审计，再单独运行全量 auto-analysis 或全函数导出，并提前说明耗时风险。

## Full Code 批量导出路线

当研究模式是 `full` 或 `code`，且用户没有把代码范围缩小为“关键样本”时，IL2CPP 代码交付不能只给 10-20 个手工目标。默认按以下路线输出覆盖率：

1. 从 `dump.cs` 生成全量或模块化 targets。默认全量；若硬盘/时间受限，可按命名空间或类名前缀分模块生成，但必须在报告中写明过滤条件。

```powershell
python <skill>\scripts\build_il2cpp_ghidra_targets.py `
  --dump-cs "<study-root>\3.代码\IL2CPP_Dump\dump.cs" `
  --output "<study-root>\4.临时目录\中间索引\Native分析\all_targets.csv" `
  --batch-dir "<study-root>\4.临时目录\中间索引\Native分析\batches" `
  --batch-size 500 `
  --manifest "<study-root>\4.临时目录\中间索引\Native分析\all_targets_manifest.json"
```

常用缩小范围示例：

```powershell
python <skill>\scripts\build_il2cpp_ghidra_targets.py `
  --dump-cs "<study-root>\3.代码\IL2CPP_Dump\dump.cs" `
  --output "<study-root>\4.临时目录\中间索引\Native分析\combat_targets.csv" `
  --batch-dir "<study-root>\4.临时目录\中间索引\Native分析\combat_batches" `
  --include-regex "WeaponSystem|PlayerState|EnemyState|Damage|Hit|Attack|Animation|Camera|Visual"
```

2. 先导入一次 `GameAssembly.dll` 并套用 Il2CppDumper 符号；后续每批都用 `-process GameAssembly.dll -noanalysis`，不要为批量导出先跑 30 分钟全量 auto-analysis。

```powershell
& "$Ghidra\support\analyzeHeadless.bat" `
  $ProjectRoot "Game_IL2CPP" `
  -process GameAssembly.dll `
  -noanalysis `
  -scriptPath $ScriptDir `
  -postScript ghidra_decompile_targets_csv.py "<batch-targets.csv>" "<batch-output-dir>" `
  -log "<study-root>\4.临时目录\工具日志\ghidra_decompile_batch.log" `
  -scriptlog "<study-root>\4.临时目录\工具日志\ghidra_decompile_batch_script.log"
```

3. 合并所有批次的 `decompile_summary.csv`，生成覆盖率报告。

```powershell
python <skill>\scripts\merge_ghidra_decompile_summaries.py `
  --targets "<study-root>\4.临时目录\中间索引\Native分析\all_targets.csv" `
  --summary-root "<study-root>\3.代码\Native映射\Ghidra伪代码批次" `
  --output-csv "<study-root>\4.临时目录\中间索引\Native分析\ghidra_decompile_coverage.csv" `
  --output-json "<study-root>\4.临时目录\中间索引\Native分析\ghidra_decompile_coverage.json"
```

4. `3.代码/Native映射/` 中可放 `.c` 伪代码与批次目录；`4.临时目录/中间索引/Native分析/` 中必须保留 targets、manifest、coverage。报告必须写明 `target_count`、`ok_count`、`failed_count`、`missing_count`、`coverage_percent`。没有覆盖率时，不得声称“全部代码导出完成”。

如果脚本因 Ghidra 版本或 Python runtime 失败，仍保留 Ghidra 项目、`dump.cs`、`script.json` 和失败日志；报告中标为“符号脚本待适配”，不要写成“代码不存在”。

## IDA 边界

IDA Pro 可作为强力手工核验工具，但依赖用户许可。IDA Free 适合 x86/x64 手工查看，通常不适合作自动化主线：它不提供完整 IDAPython/API/SDK 能力。没有许可或账号时，不要尝试绕过下载、登录或授权。

## 输出规则

报告只总结与研究目标相关的行为：

- 哪些方法负责动画切换、武器绑定、特效、镜头、UI 或音频反馈。
- 方法地址、来源文件、工具输出和待验证点。
- 从 native 伪代码推断出的行为必须标为“我的判断”，并说明依据。
- 技术观察最终仍要转译为原创美术方向：色彩、形状语言、动画节奏、UI 反馈和制作约束。

不要输出大段反编译源码。若需要引用代码，只引用很短的方法名、字段名、RVA/VA 和局部行为摘要。

## Forestrike 验证样例

观察到的事实：

- `Il2CppDumper` 可处理 Forestrike 的 `GameAssembly.dll + global-metadata.dat`，识别 `Metadata Version: 31` 与 `Il2Cpp Version: 31`。
- 产物包含 `dump.cs`、`script.json`、`il2cpp.h`、`stringliteral.json` 和 `DummyDll/`。
- `Cpp2IL 2022.0.7` 对 metadata `31` 失败，错误为只支持 `24-29`。
- `WeaponSystem.TryEquipWeapon` 可定位到 VA `0x18073D600`，`Weapon.Sample` 可定位到 VA `0x180765050`。
- Ghidra 12.0.4 + JDK 21 可在 headless 中导入 `GameAssembly.dll`，Jython 版脚本可成功应用 Il2CppDumper 映射，并可定点导出关键方法伪代码。
- Forestrike 的 `GameAssembly.dll` 约 68 MB；一次全量 Ghidra auto-analysis 因 `-analysisTimeoutPerFile 1800` 在 30 分钟超时，日志显示 `Total Time 1849 secs` 和 `Analysis timed out at 1800 seconds`。主要耗时 analyzer 包括 `Non-Returning Functions - Discovered` 约 646 秒、`x86 Constant Reference Analyzer` 约 529 秒、`Disassemble Entry Points` 约 429 秒、`Decompiler Switch Analysis` 约 184 秒。
- 同一 Ghidra project 后续使用 `-process GameAssembly.dll -noanalysis` 做定点 decompile 可在十几秒量级完成；因此默认路线应为符号映射加定点分析，全量 auto-analysis 只在需要深层 native 结构时启用。

我的判断：

- 对 Unity 2022.3 / metadata 31 项目，Il2CppDumper 更适合作第一步稳定导出符号；Cpp2IL 只有在支持该 metadata 版本时再尝试。
- Ghidra 是当前自动化主线；IDA Free 只作为手工交叉查看。对类似 Forestrike 的 IL2CPP 项目，先 `-noanalysis` 定点 decompile 的性价比明显高于默认全量分析。
