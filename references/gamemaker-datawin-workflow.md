# GameMaker `data.win` 本地学习流程

## 适用边界

仅在用户明确授权本机 GameMaker 游戏目录时使用。典型输入是包含 `data.win`、`audiogroup*.dat`、外部 `.ogg` 和游戏 exe 的安装目录；输出必须写入独立学习目录，不修改游戏安装目录。导出的原始 sprite、texture、sound、strings 只保留在本机学习目录，报告只输出索引、统计、缩略图、抽象视觉规律、原创规范和待验证项。

来源：UnderminersTeam/UndertaleModTool README 说明它可打开 `data.win`、`game.ios`、`game.unx` 等 GameMaker 数据文件，CLI 可用于自动化；官方 SCRIPTS.md 列出 `ExportAllSprites.csx`、`ExportAllTextures.csx`、`ExportAllEmbeddedTextures.csx`、`ExportAllSounds.csx`、`ExportAllStringsJSON.csx` 等导出脚本。工具许可为 GPL-3.0；工具许可不授权分发商业游戏原始素材。

## 推荐目录

```text
<project-root>/                # 游戏项目名根目录，例如 Katana_ZERO
  0.原始导出/
    UTMT/
  1.分类资源/
    图片/
      角色资源/
      场景资源/
      道具/
      UI/
      特效/
      纹理页/
      序列帧/
    音频/
  2.报告/
    打开图片画廊.bat
    00_研究总览.md
    01_美术资源规格表.md
    02_美术风格与色彩速查.md
    03_动画与手感帧数据.md
    04_场景关卡搭建.md
    05_技术架构拆解.md
    06_玩法系统拆解.md
    07_渲染管线拆解.md
    08_命名规范汇总.md
    09_可迁移技术清单.md
  3.代码/
    脚本/
  4.临时目录/
    _ascii_work/               # 仅当完整绝对路径为 ASCII-compatible 时使用
      GameMaker_UTMT_Exports/
        EmbeddedTextures/
        Sprites/
        TextureItems/
        Sounds/
        strings.json
      Normalized/
        gamemaker_image_index.csv
        gamemaker_sprite_frame_index.csv
        gamemaker_animation_index.csv
        gamemaker_audio_index.csv
        gamemaker_summary.json
    中间索引/
      图片画廊/
        manifest.json
        errors.jsonl
        thumbnails/
      GameMaker组织/
        主角动画序列帧索引.csv
```

## Windows 路径兼容性

空格不等于非 ASCII。`game-root`、`study-root` 或工具目录的完整绝对路径全为 ASCII 时，即使含空格也直接传参；使用真实绝对路径、参数数组或正确引号保持参数边界，不需要改写路径。

下文 `<gm-work-root>` 表示 UTMT 的兼容工作目录：若 `<project-root>\4.临时目录\_ascii_work` 的完整绝对路径为 ASCII-compatible，就直接使用它；否则只能使用主 agent 明确配置的、位于现有卷上的普通目录 `<compat-root>`，并派生 `<compat-root>/<owner-id>/<task-id>/gamemaker`。`owner-id` 必须对一次分析运行唯一并由主 agent 在建图时统一下发；`task-id` 在该运行内唯一，子 agent 不得自选或改写这些标识，确保并行写入无冲突。不得创建、删除或复用 `SUBST`、`New-PSDrive`、`DefineDosDevice`、`net use` 等虚拟盘符绕过。没有合格兼容根或标识时状态为 `BLOCKED`，按 fail closed 停止 UTMT 步骤。兼容目录中的正式结果必须回填到 `0.原始导出` 至 `4.临时目录` 的对应位置，不能作为第六个交付目录。

## 执行步骤

1. 只读识别：

```powershell
python <skill>\scripts\detect_engine.py "<game-root>" --format markdown
python <skill>\scripts\asset_inventory.py "<game-root>" --format json
```

2. 确认 `data.win` 存在，并用 Skill 内 UTMT CLI 读取基本信息：

```powershell
<skill>\tools\UTMT_CLI_v0.9.0.0-Windows\UndertaleModCli.exe info "<game-root>\data.win" --verbose
```

3. 用封装脚本执行本地导出。`StudyRoot` 传 `<gm-work-root>`；它的完整绝对路径必须为 ASCII-compatible，因为 UTMT CLI 的交互提示在部分 Windows 控制台中会把非 ASCII 路径或 PowerShell BOM 误读成无效路径。路径中的空格允许保留。

```powershell
powershell -ExecutionPolicy Bypass -File <skill>\scripts\export_gamemaker_datawin_utmt.ps1 `
  -GameRoot "<game-root>" `
  -StudyRoot "<gm-work-root>" `
  -SkillRoot "<skill>"
```

封装脚本会使用 ASCII stdin 文件重定向，避免 PowerShell 管道给 UTMT 提示输入加 BOM；不要把 `echo <path> | UndertaleModCli` 当作可靠方式，因为 `cmd echo` 会在路径尾部追加空格，可能触发 UTMT 的路径越界校验。

4. 归一化 UTMT 导出：

```powershell
python <skill>\scripts\normalize_gamemaker_exports.py `
  --exports-root "<gm-work-root>\GameMaker_UTMT_Exports" `
  --output-dir "<gm-work-root>\Normalized" `
  --title "<游戏名> GameMaker Art Study"
```

`normalize_gamemaker_exports.py` 会生成图片索引、sprite 帧索引、动画聚合索引和音频索引。后续报告应把索引用于调色板、形状/轮廓、UI/HUD、动画节奏和制作约束分析，而不是把导出的原始图像当成可复用素材。

5. 整理成标准交付目录，并生成规范命名主角序列帧。最终帧名使用游戏实际动作名加 1-based 两位序号，例如 `attack_01.png`、`attack_02.png`；当 `spr_fall` 和 `spr_dragon_fall` 这类动作名冲突时，输出为 `fall_01.png` 与 `dragon_fall_01.png`，索引保留原始 sprite 名：

```powershell
python <skill>\scripts\organize_gamemaker_project_export.py `
  --project-root "<project-root>" `
  --exports-root "<gm-work-root>\GameMaker_UTMT_Exports" `
  --normalized-root "<gm-work-root>\Normalized" `
  --game-name "<游戏名>" `
  --datawin-info "<gm-work-root>\gamemaker_datawin_info.txt"
```

6. 生成图片画廊 manifest 与 BAT：

```powershell
python <skill>\scripts\build_image_gallery_manifest.py `
  --study-root "<project-root>" `
  --image-root "<gm-work-root>\GameMaker_UTMT_Exports" `
  --title "<游戏名> GameMaker 图片画廊"

python <skill>\scripts\create_image_gallery_launcher.py `
  --study-root "<project-root>" `
  --skill-root "<skill>"
```

固定 Electron 客户端会读取 `4.临时目录/中间索引/图片画廊/manifest.json`，并校验 BAT/CLI 传入的重复 `--images-root` 与 manifest `source_roots` 完全一致；点击卡片打开预览覆盖层，鼠标滚轮缩放图片，按住鼠标左键拖拽平移图片，预览仅通过右上角 X/关闭按钮或 Esc 关闭；`打开目录` 按钮在系统文件管理器中打开并定位当前图片，不执行脚本、不联网、不提升权限；右键不触发操作，不属于默认工作流。固定 EXE 由 Skill 目录的 `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe` 提供，不要在每个项目内构建 EXE。旧 `build_full_image_gallery.py` 与 `build_gallery_exe.ps1` 仅作为兼容工具。

## YYC 与代码边界

如果 `UndertaleModCli info` 显示 `Is YYC - True`，`dump --code` 通常会提示代码已编译进 exe，因此没有可导出的 GML code。不要写成“没有逻辑”；应写为“GML 不在 data.win 中以可导出字节码形式存在，native 行为需另行逆向验证”。本 Skill 默认不自动逆向 GameMaker YYC exe，只做美术/动画/资源索引与原创规范。

## 验证清单

- `engine_detection.md` 显示 GameMaker 命中 `data.win`。
- `asset_inventory.json` 把 `data.win` 与 `audiogroup*.dat` 归入 `game-engine-packages`。
- `gamemaker_datawin_info.txt` 记录 GMS2、YYC、Sprites、Rooms、Objects、Texture Page Items、Strings、Embedded Textures 和 Audio 数量。
- `GameMaker_UTMT_Exports` 包含 `EmbeddedTextures`、`Sprites`、`TextureItems`、`Sounds` 和 `strings.json`；若跳过声音或 strings，报告中说明。
- `Normalized/gamemaker_summary.json` 与 CSV 索引生成成功。
- `4.临时目录/中间索引/图片画廊/manifest.json` 中 `summary.failed_count` 为 0，或报告列出失败项。
- 固定图片画廊入口 `2.报告/打开图片画廊.bat` 存在且包含重复 `--images-root`；`built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe` 存在验证和 self-test 必须通过。
- `4.临时目录/中间索引/GameMaker组织/主角动画序列帧索引.csv` 没有重复 `output_relative_path`，动作帧命名为 `action_01.png`、`action_02.png`。
- 固定 EXE self-test 通过；固定客户端自动化验证覆盖层预览可由点击卡片打开、滚轮缩放、左键拖拽平移，并且仅能通过右上角 X/关闭按钮或 Esc 关闭；`打开目录` 按钮可打开/定位当前图片；右键不触发操作，不属于默认工作流。
- 最终报告分开写“观察到的事实”“我的判断”“待验证假设”，并标注本地授权路径、工具来源、不可复制元素。
