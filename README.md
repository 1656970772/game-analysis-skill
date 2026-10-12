# GameAnalysis Skill

用于研究用户明确授权的本地游戏项目、安装目录或导出目录。支持资源盘点、引擎识别、资源与代码导出、分类、学习报告，以及图片、模型、动画和 Unity 特效预览。具体授权边界、执行流程和验收标准以 [SKILL.md](SKILL.md) 为准。

## 仓库内容

- `SKILL.md`：完整技能说明。
- `scripts/`：导出、分析、组织、校验、预览器构建与启动脚本。
- `references/`：引擎工作流、配置、数据契约、工具来源和报告模板。
- `assets/`：图片、模型、动画和特效预览器源码。
- `tests/`：自动化测试；动画 XML fixture 是合成数据，不包含游戏原始素材。
- `docs/`：现有设计与实施文档。

第三方工具下载、Python 环境、依赖缓存和预编译程序不进入 Git 源码历史。可分发的 Windows 预览器通过 [GitHub Releases](https://github.com/1656970772/game-analysis-skill/releases) 提供。

## 安装

在 PowerShell 中将仓库克隆到 Codex 的 skill 目录；如果已经存在该目录，先检查其中的本地修改。

```powershell
git clone https://github.com/1656970772/game-analysis-skill.git "$env:USERPROFILE\.codex\skills\game-analysis"
Set-Location "$env:USERPROFILE\.codex\skills\game-analysis"
pwsh -NoLogo -NoProfile -File scripts/setup_python_env.ps1
```

如需使用现成预览器，从 Release 下载 `game-analysis-built-in-tools-windows-x64.zip` 和 `SHA256SUMS.txt`，核对 ZIP 的 SHA-256 后，将 ZIP 解压到 skill 根目录。解压后程序位于 `built-in-tools/<工具名>_<版本>/`，保留随包许可证。Release 中的 `release-manifest.json` 列出版本、文件数量及程序和压缩包哈希。

也可以使用 `scripts/build_image_gallery_viewer_exe.ps1`、`scripts/build_model_viewer_tool.ps1`、`scripts/build_game_animation_viewer_tool.ps1` 和 `scripts/build_vfx_viewer_tool.ps1` 在本机构建；各脚本的依赖和参数见脚本及相关 reference。

其他游戏资源工具按 `references/tooling-manifest.json` 的来源和版本恢复到 `tools/`。不随仓库分发游戏安装文件、原始图集、反编译游戏代码或本地研究项目。

## 角色动画预览器边界

`GameCharacterAnimationViewer` 来自外部 AssetArchive 派生项目，配置、安装脚本和工作流已包含在仓库中。当前本地预编译程序内嵌 FMOD 组件，资源目录还包含 Wilson 图集模板等项目素材；本次 Release 不分发该程序及其 `custom-characters/` 目录，也不分发已被 1.0.1 替代的 GameAnimationViewer 1.0.0。

需要角色优先预览时，按 `references/game-character-animation-viewer-workflow.md`，使用本机合法取得的 AssetArchive 源码或构建产物运行 `scripts/install_game_character_animation_viewer_tool.ps1`。此安装流程不等同于提供该外部项目的完整派生源码。

## 验证

完整测试需要先恢复 Python 环境及 Release 中的预览器；其中模型预览器哈希测试和默认 Python 包装器测试会检查实际安装文件。

```powershell
pwsh -NoLogo -NoProfile -File scripts/run_skill_tests.ps1 -InstallMissing
```

若系统旧的 pytest 临时目录存在权限问题，可给 `-PytestArgs` 传入一个新的、可写的 `--basetemp` 路径，并通过 `-p no:cacheprovider` 禁用旧缓存。测试使用合成输入，不需要读取或运行真实游戏。

模型预览器源码中 vendored Three.js addons 遵循 Three.js MIT 许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。各第三方工具和运行时继续遵循各自许可证；本仓库的授权边界不改变其许可条件。
