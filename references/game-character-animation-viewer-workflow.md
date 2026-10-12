# DST 角色优先动画 Viewer

当用户要按“角色 → 该角色全部动作 → 右侧自动播放”，或要在保持当前动作的同时切换角色时，默认使用 `GameCharacterAnimationViewer`。它派生自本机 `AssetArchive-Dev` 的 Tauri/Pixi/Klei 资源加载链，直接读取用户授权的已安装 DST 根；不读取、也不要求先构建旧 `GameAnimationViewer` 的 Bank manifest。

## 固定工具与来源

- 唯一配置：`references/game-character-animation-viewer-config.json`。工具版本、固定目录、EXE 名、AssetArchive 源路径、候选构建产物、环境变量、BAT 和日志路径只在这里维护。
- 构建/安装：运行 `scripts/install_game_character_animation_viewer_tool.ps1 -Build` 可按配置调用 Tauri build 后安装；已有构建产物时省略 `-Build` 或显式传 `-ArtifactPath`。CMake 4 构建 `clippers` 需要的 `CMAKE_POLICY_VERSION_MINIMUM=3.5` 只在构建进程期间设置并在结束后恢复，不写用户环境；安装器把该兼容项写入 provenance。安装结果复制到版本化 `built-in-tools/GameCharacterAnimationViewer_<version>/`，同时复制 AssetArchive MIT `LICENSE`，生成 `ORIGIN.json`/`VERSION.txt`，并用真实 EXE SHA-256、来源 commit 和 dirty 状态更新 `references/tooling-manifest.json`。
- 启动：运行 `scripts/create_game_character_animation_viewer_launcher.py --study-root <study-root> --skill-root <skill-root> --game-root <DST安装根>`。生成物仍只有 `2.报告/打开动画预览.bat`；本机游戏根写入该 BAT，不写死在全局脚本。这个 BAT 是用户可双击的唯一入口；不要让用户直接双击固定 EXE，因为 EXE 本身拿不到 `GAME_ANALYSIS_DST_ROOT`，会停在索引等待或报缺少游戏根。也可在配置指定的项目 JSON 中保存 `schema_version: 1` 和绝对 `game_root`，生成器在未给 `--game-root` 时读取它。
- 运行协议：BAT 设置 `GAME_ANALYSIS_DST_ROOT=<DST安装根>` 后启动固定 EXE。Rust/Lua 端必须复用 AssetArchive 的 `DST_DataRoot` 校验并直接索引安装目录；不得把旧 36MB manifest 伪装成角色数据源。
- 启动可观测性：BAT 检查 EXE、辅助脚本和 `data/anim`，显示启动提示，把结果写到 `4.临时目录/中间索引/游戏动画预览/viewer-launch.log`；失败时 `pause` 保留窗口。辅助脚本按固定 EXE 路径查找已运行进程并请求前置窗口，避免双击后静默无响应。

## 内置自定义角色

用户要求制作可复用原人物骨骼的 AI 角色时，基础素材必须来自用户授权的 DST 安装目录，并遵守以下边界：

1. 先确定动画 Bank，再从至少两个可在该 Bank 下切换的角色 Build ZIP 导出 `build.bin` 和 Atlas PNG，比较 Symbol 名、Index 集合和动作引用覆盖。共享的是 Bank 使用的 **Symbol/Index 契约**，不是某个角色的 Atlas 布局或 UV；不得把 `wilson.zip` 或任何单一角色 Build 当成所有新角色的 UV 模板。
2. 每个角色 Build 自己定义各 Symbol/Index 图像的尺寸、pivot、mesh 顶点、Atlas 放置和 UV。新角色应有自己的 Build 名和独立的这些数据；可以复用已经验证相容的 Symbol/Index 命名与附着语义，但不能仅改 Build 名后继续依赖基础角色的整张 UV。
3. 只有用户明确要求或授权上传参考素材时，才把真实拆件 Atlas 或按 Symbol/Index 导出的透明部件传给图像生成工具。它们是低细节画风、部件语义、方向变体和重复表情的参考，不是要求模型原位填色的固定 UV 蒙版。不要先生成复杂整身立绘再凭视觉硬切成骨骼部件。
4. 需要 Spriter 可编辑工程时，默认走已经实机验证的官方链，而不是自行从 BILD 网格反拼 PNG：

   - 在纯 ASCII 隔离根解出相容角色的 `build.bin + anim.bin + atlas-N.tex`，用 `krane` 生成干净的逐 Symbol PNG 和 SCML。`krane`、Klei 旧工具和 ImageMagick 2 时代组件均按 ANSI 路径处理，中文路径会出现 `MagickPNGErrorHandler/WriteBlob Failed`；不得用盘符映射掩盖来源，使用可追溯的普通 ASCII 目录。
   - 以现代游戏 Build 的 Symbol/Index/尺寸/pivot 合同为权威，把目标角色 RGBA 部件注册到 clean SCML 的逐帧画布；不得再把多 quad region mesh 当轴对齐矩形从 BILD Atlas 反拼，后者会制造看似合法但实际为横向碎条的部件图。
   - SCML 必须满足：全局 PNG basename 唯一、真 RGBA、零空帧、精确 Symbol/Index 集合、有限 pivot，并且有且仅有一个非空 `BUILD_PLAYER`。先用 Spriter 实机打开并播放至少一个组装动画，再调用 Klei 官方 `scml.exe -f <project.scml> <isolated-mod-root>`；不要把“编译退出码 0”当作视觉通过。
   - 官方原包保留 `build.bin + anim.bin + atlas-N.tex` 作为不可改写的溯源产物。角色 Viewer 使用共享 Wilson Bank 时，另生成只含 `build.bin + atlas-N.tex` 的发布副本，并逐成员验证其 SHA-256 与官方原包相同；不得让 SCML 中少量工程演示动画覆盖 Viewer 的完整共享动作 Bank。

   不需要 Spriter 工程时可继续使用 Klei 官方 `buildanimation.py`，但同样必须从干净逐部件 RGBA 输入重建 BILD v6、alpha-region mesh、Atlas 和 UV。若新 PNG 的透明轮廓发生变化，必须重建 region mesh；“只重排 Atlas/重写 UV、但保留基础角色全部顶点 XY 和 vertex_count”的产物只能标为 bootstrap，不能进入 Viewer 内置发行资源。官方工具不可用时，fallback 必须同步重建与新 alpha 覆盖闭合的几何区域并生成逐 Symbol/Index 映射清单；做不到就 fail closed。
5. 图像模型可能烘入棋盘背景、错误 Alpha、无关阴影或移动/合并部件。发行输入必须是真 RGBA，逐部件检查透明边缘、方向、尺寸、pivot 对齐和重复帧覆盖；不得把模型回包的棋盘当透明通道，也不得把包含完整头脸的图片映射到 `hair`、把整件带手臂的衣服映射到 `torso`、或把整条裤靴映射到单侧 `leg/foot`。先从动画 XML 抽样确认哪些 Symbol 会在同一帧并发合成，再决定每张素材的语义所有权。
6. 编译或 fallback 完成后必须同时通过结构、溯源和渲染验证：

   - 目标动作与方向引用的 `(symbol hash, index)` 全部能在新 Build 中解析；只有已声明的运行时 `OverrideSymbol` 可以例外，且必须列出缺失项和策略。
   - Atlas page 引用、顶点/三角形索引、pivot、mesh 坐标均合法且有限；所有 UV 落在声明页面的有效范围内，不跨页、不采样错误部件。轮廓变化后，输出 vertex_count/region 分布不得仍与基础 Build 逐帧完全相同；完全相同通常说明误走了旧 mesh 克隆路径。
   - 每个顶点 UV 可追溯到新 Build 自己的 Symbol/Index atlas rect；发生重排时，rewrite 清单的顶点集合必须与受影响 Build 顶点全集闭合。
   - 记录 SCML 编译器版本与命令，或 fallback 的映射清单，并记录输入部件、Build、Atlas 和输出 ZIP 的 SHA-256；新 Build 不得引用基础角色 Atlas。
   - 用一个故意移动 Atlas 岛位置的 canary Build 验证渲染器确实读取每个 Build 自己的 UV；在 Wilson 与新角色之间保持同一动作切换时，检查实际选中的 Build/Atlas 路径、输出像素差异、缓存隔离和无静默回退。
   - 至少验证 `idle_loop`、一个移动动作和一个表情或受击动作；覆盖可用方向，并抽查首帧、中间帧、末帧，确认无透明空帧、碎片、错位、缺层、Atlas bleed 或 pivot 跳变。

   不得修改源游戏 ZIP。

### 自定义角色的视觉身份发行门禁

“共用人物动画”只表示复用已经验证相容的 Bank、Symbol/Index 命名和附着语义，不表示复用基础角色的可见美术。验收时必须同时检查下面的身份层；任一层仍明显来自 Wilson 或其他基础角色，都不能把产物称为新的自定义角色：

- 头部轮廓与遮罩：`headbase`、`headbase_hat`。
- 五官与表情：`face` 的全部已用 Index；若角色设定需要独立面型，不能只在基础角色脸上叠眉眼。
- 发型与帽子兼容层：`hair`、`hair_hat`。
- 服装主体：`torso`、`torso_pelvis`。
- 上下肢与肤色连接层：`arm_upper`、`arm_lower`、`arm_upper_skin`、`hand`、`leg`、`foot`。
- 列表/物品图标：`SWAP_ICON`，以及 manifest 声明的其他身份图标。

替换清单必须由数据配置驱动，并按目标 Bank 实际引用的 `(Symbol, Index)` 集合计算覆盖率，不能在代码中写死角色名或固定帧数。默认要求上述身份层的全部被引用帧都有新素材；仅可把 `cheeks`、一次性状态效果、装备 OverrideSymbol 等不承载角色身份的层列入显式豁免，且 manifest/probe 必须逐项记录 `symbol`、`index`、原因和运行时策略。当前 Wilson 契约若替换 130/132 帧、只豁免 `cheeks:0,1`，可以作为一个合格实例，但 130/132 不是其他 Bank 的通用阈值。

以下情况一律判定为视觉身份未通过：

- 只替换 `hair`/`hair_hat`、调色、加头饰或修改角色显示名。
- 保留基础角色的头型、五官、红背心/衬衫、手脚或整体剪影，只在上面叠加“修仙”装饰。
- `Bank` 和 `Build` 都显示为基础角色，或者新 Build 加载失败后静默回退到基础角色。
- 单独的概念图看起来像新角色，但实际进入 Viewer 的逐部件 Build 仍是基础角色素材。

发行前必须在安装后的 `GameCharacterAnimationViewer` 以 100% 常用缩放做实机视觉验收：至少播放 `idle_loop` 和一个移动动作，保持动作切换基础角色与新角色，分别核对头脸、发型、服装、四肢、轮廓和图标。界面显示 `Bank: wilson` 可以表示共享动画 Bank，但必须同时显示新角色自己的 `Build`，渲染像素也必须来自新 Build；报告和 UI 不得把 `Bank` 文本误解释为角色仍是 Wilson。截图只作证据，最终判断以安装后实际播放和资源探针为准。

图像生成返回的棋盘格不等于 Alpha。生成素材进入工程前必须恢复并验证真 RGBA（Alpha 至少同时存在 0 与 255），保存版本化源图、配置化部件布局和逐 Symbol 输出；再由官方编译链重建 Build/Atlas。这样后续可以替换概念或部件来源而不重写映射逻辑。

7. 每个角色放在 AssetArchive 源仓库配置指定的 `custom_character_resources_relative/<id>/` 下，至少包含：

   ```text
   character.json
   <build>.zip
   <build>-atlas.png
   ```

   `character.json` schema 1 至少声明 `id`、`display_name`、`build`、`asset`、`atlas_preview`；ID/Build 只允许 ASCII 字母、数字、`-`、`_`，所有文件路径必须是角色目录内的安全相对文件名。可选 `ai_source_atlas`、`parts_contact_sheet`、`spriter_project_archive`、`official_export_archive`、`official_release_probe` 用于保留生成来源、可编辑工程和官方编译证据。Viewer 应为内置角色提供查看运行素材、查看部件总览、导出 Spriter 工程和导出官方原包的直接入口。
8. Viewer 启动时扫描 EXE 同级 `custom-characters/*/character.json`，把其中的 Build ZIP 走 AssetArchive 既有 Mod Build loader 注入；人物页把游戏预设与内置角色合并，并单独显示数量。React 页面不得维护角色专名或绝对路径。
9. 安装器从配置的 `custom_character_resources_relative` 复制资源到 `custom_character_install_relative`。交付验证必须同时覆盖：manifest 路径逃逸拒绝、Build ZIP 可解析、角色出现在列表、选择后使用声明的 Bank 动作播放、Atlas/Build 错误不静默回退到任何基础角色。

## 与旧 Bank Viewer 的边界

`GameAnimationViewer_1.0.1`、`build_game_animation_manifest.py`、`export_game_animation_asset.py` 和 Klei XML/atlas 素材链继续保留，适用于 Bank/Build/Symbol 诊断、歧义证据、逐帧 PNG、APNG/GIF 和 source-bundle 导出。它们不是角色浏览首页，也不应再覆盖角色 Viewer 已生成的唯一 BAT。需要高级 Bank/素材导出时由 Agent 显式调用旧后端脚本或旧 EXE，不额外生成第二个 `2.报告` 启动入口。

角色 Viewer 的完成门槛至少包括：启动后列出角色；选择 Wilson 后列出可用动作；选择动作后右侧立即循环播放；切换到另一角色时尽量保持同名当前动作，并在动作不可用时给出明确回退。具体角色组装、动作映射和 Pixi 播放实现应沿用派生 AssetArchive 代码，不能在 Skill 启动层重新维护一套角色硬编码表。
