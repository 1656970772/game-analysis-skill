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
    全量图片画廊.html
    <游戏名>_素材浏览器.exe
    交付摘要.md
    整体美术素材分析文档.md
    场景美术分析.md
    角色美术分析.md
    主角美术分析.md
  3.代码/
    代码导出说明.md
  4.临时目录/
    _ascii_work/               # UTMT 暂存，避免中文路径和 stdin 编码问题
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
      GameMaker组织/
        主角动画序列帧索引.csv
```

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

3. 用封装脚本执行本地导出。`StudyRoot` 必须是 ASCII-only 路径，因为 UTMT CLI 的交互提示在部分 Windows 控制台中会把中文路径或 PowerShell BOM 误读成无效路径。

```powershell
powershell -ExecutionPolicy Bypass -File <skill>\scripts\export_gamemaker_datawin_utmt.ps1 `
  -GameRoot "<game-root>" `
  -StudyRoot "<project-root>\4.临时目录\_ascii_work" `
  -SkillRoot "<skill>"
```

封装脚本会使用 ASCII stdin 文件重定向，避免 PowerShell 管道给 UTMT 提示输入加 BOM；不要把 `echo <path> | UndertaleModCli` 当作可靠方式，因为 `cmd echo` 会在路径尾部追加空格，可能触发 UTMT 的路径越界校验。

4. 归一化 UTMT 导出：

```powershell
python <skill>\scripts\normalize_gamemaker_exports.py `
  --exports-root "<project-root>\4.临时目录\_ascii_work\GameMaker_UTMT_Exports" `
  --output-dir "<project-root>\4.临时目录\_ascii_work\Normalized" `
  --title "<游戏名> GameMaker Art Study"
```

`normalize_gamemaker_exports.py` 会生成图片索引、sprite 帧索引、动画聚合索引和音频索引。后续报告应把索引用于调色板、形状/轮廓、UI/HUD、动画节奏和制作约束分析，而不是把导出的原始图像当成可复用素材。

5. 整理成标准交付目录，并生成规范命名主角序列帧。最终帧名使用游戏实际动作名加 1-based 两位序号，例如 `attack_01.png`、`attack_02.png`；当 `spr_fall` 和 `spr_dragon_fall` 这类动作名冲突时，输出为 `fall_01.png` 与 `dragon_fall_01.png`，索引保留原始 sprite 名：

```powershell
python <skill>\scripts\organize_gamemaker_project_export.py `
  --project-root "<project-root>" `
  --exports-root "<project-root>\4.临时目录\_ascii_work\GameMaker_UTMT_Exports" `
  --normalized-root "<project-root>\4.临时目录\_ascii_work\Normalized" `
  --game-name "<游戏名>" `
  --datawin-info "<project-root>\4.临时目录\_ascii_work\gamemaker_datawin_info.txt"
```

6. 生成全量图片画廊：

```powershell
python <skill>\scripts\build_full_image_gallery.py `
  --project-root "<project-root>\4.临时目录\_ascii_work\GameMaker_UTMT_Exports" `
  --image-index "<project-root>\4.临时目录\_ascii_work\Normalized\gamemaker_image_index.csv" `
  --gallery-file "<project-root>\2.报告\全量图片画廊.html" `
  --manifest "<project-root>\4.临时目录\中间索引\全量图片画廊_manifest.json" `
  --original-prefix "../4.临时目录/_ascii_work/GameMaker_UTMT_Exports/" `
  --title "<游戏名> GameMaker 全量图片资源画廊"
```

7. 打包本地 EXE：

```powershell
powershell -ExecutionPolicy Bypass -File <skill>\scripts\build_gallery_exe.ps1 `
  -StudyRoot "<project-root>" `
  -SkillRoot "<skill>" `
  -OutputExeName "<游戏名>_素材浏览器.exe"
```

Electron 模板会查找 `2.报告/全量图片画廊.html` 与 `4.临时目录/_ascii_work/GameMaker_UTMT_Exports/`，右键只打开系统文件管理器定位本机素材，不执行脚本、不联网、不提升权限。

## YYC 与代码边界

如果 `UndertaleModCli info` 显示 `Is YYC - True`，`dump --code` 通常会提示代码已编译进 exe，因此没有可导出的 GML code。不要写成“没有逻辑”；应写为“GML 不在 data.win 中以可导出字节码形式存在，native 行为需另行逆向验证”。本 Skill 默认不自动逆向 GameMaker YYC exe，只做美术/动画/资源索引与原创规范。

## 验证清单

- `engine_detection.md` 显示 GameMaker 命中 `data.win`。
- `asset_inventory.json` 把 `data.win` 与 `audiogroup*.dat` 归入 `game-engine-packages`。
- `gamemaker_datawin_info.txt` 记录 GMS2、YYC、Sprites、Rooms、Objects、Texture Page Items、Strings、Embedded Textures 和 Audio 数量。
- `GameMaker_UTMT_Exports` 包含 `EmbeddedTextures`、`Sprites`、`TextureItems`、`Sounds` 和 `strings.json`；若跳过声音或 strings，报告中说明。
- `Normalized/gamemaker_summary.json` 与 CSV 索引生成成功。
- `4.临时目录/中间索引/全量图片画廊_manifest.json` 中 `failed_count` 为 0，或报告列出失败项。
- `4.临时目录/中间索引/GameMaker组织/主角动画序列帧索引.csv` 没有重复 `output_relative_path`，动作帧命名为 `action_01.png`、`action_02.png`。
- Electron self-test 找到 `2.报告/全量图片画廊.html` 与 `4.临时目录/_ascii_work/GameMaker_UTMT_Exports`。
- 覆盖层预览经浏览器或 Electron 自动化验证可打开；右键定位仅访问本机导出目录。
- 最终报告分开写“观察到的事实”“我的判断”“待验证假设”，并标注本地授权路径、工具来源、不可复制元素。
