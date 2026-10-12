---
name: game-analysis
description: Use when locally studying a user-authorized game project, build, or unpacked directory, including game analysis, asset extraction, engine identification, code/resource export, reverse engineering for learning, game art study, game deconstruction, or local game resource reports.
---

# GameAnalysis

## 核心原则

只在用户明确授权的本地路径内工作。允许为本机学习做全量导出、分类整理、代码提取和分析报告；不要上传、发布、出售、嵌入、复用第三方原始资源，或分发反编译代码。输出必须区分"观察到的事实""我的判断""待验证假设"，事实性内容标注来源。

**前置工具策略**：本 Skill 默认允许联网恢复和安装可信工具。只要来源可信、许可证允许且不涉及破解、绕过 DRM/反作弊/访问控制，遇到缺失工具或新格式工具链时直接自动下载、校验并沉淀到 Skill 的 `tools/<tool-name>_<version>/`，不要因为“未单独确认联网下载工具”而停止流程。

**未知资源自扩展门禁**：遇到没见过、现有流程无法导出的模型、图片、动画、特效、音频、地图、代码、脚本、配置或其他资源时，不能直接跳过或只写“无法导出”。必须先在授权目录内只读识别格式、抽取小样本、查找可信工具或编写可审计解析流程；需要工具时按前置工具策略自动下载并沉淀到 `tools/`，随后实际执行小样本验证和可续跑全量导出。若成功，按目录规范输出到对应分类资源/代码/报告/临时索引，再把新增流程、脚本、工具或阻塞判定回补到本 Skill 的 `scripts/`、`references/`、脚本表、`tooling-manifest.json` 和使用后迭代记录。若确实无法完成，也必须写明已研究路线、已尝试工具/脚本、失败阶段、下一步，并把阻塞记录沉淀到 Skill，避免下次重复卡住。

## 工具获取

公开发布包可能不含 `tools/`。使用本地工具前，先读取 `references/tooling-manifest.json` 并检查 `local_path` 是否存在；若缺失且 `auto_download=true`，直接从 manifest 或官方/可信开源来源自动恢复工具到 Skill `tools/`。

- Python 依赖通过 `scripts/setup_python_env.ps1` 恢复。
- 其他第三方工具默认自动从 manifest、官方来源、可信开源 GitHub Release 或可信包管理器获取，解压到 `tools/<tool-name>_<version>/`。
- 下载后必须记录 URL、版本、许可证、哈希、用途和版本输出；可用工具必须沉淀到 Skill `tools/` 并更新 `references/tooling-manifest.json`。
- 只有来源不可信、许可证不允许、本地安全策略拦截、需要账号/付费/人工许可、或工具会绕过 DRM/反作弊/访问控制时，才停止该工具路线并写入阻塞；不要把“尚未询问是否联网下载工具”作为阻塞理由。
- 不要使用来源不明、破解或绕过 DRM/反作弊/访问控制的工具。
- Python 脚本和测试默认使用 Skill 自带环境：运行 `scripts/run_skill_tests.ps1`，它会优先使用 `tools/python-env/Scripts/python.exe`、注入本地 `site-packages`，并检查 UnityPy/pytest；缺 pytest 时仅在显式传 `-InstallMissing` 时联网安装。
- Windows 下从 PowerShell 调用 Python 且参数或输出可能含中文时，默认通过 `scripts/invoke_python_utf8.ps1 -Script "<script.py>" <args>`；包装器使用 Skill 自带 Python、保持参数数组边界并透传 stdout/stderr/退出码。不要用 `rtk` 直接代理 Python 的中文 JSON 输出；如需压缩外层输出，代理 `pwsh -File scripts/invoke_python_utf8.ps1`。传给 Python 的常规选项使用 `--flag value`，避免与包装器自身的 `-Script/-PythonExe` 单横线参数同名。
- `GameModelViewer` 是 Skill `built-in-tools` 中维护的固定 EXE。使用前先读取 `references/tooling-manifest.json`，查找 `GameModelViewer.local_path` 并检查文件存在；只有工具缺失或升级时才运行 `scripts/build_model_viewer_tool.ps1` 构建到 `built-in-tools/GameModelViewer_1.0.0/GameModelViewer.exe`。不要在每次分析中重新 build，也不要复制 EXE 到 study-root；每个输出项目只在 `2.报告/` 生成一个模型预览 BAT 入口：`打开模型预览.bat`，由 BAT 传入本次项目的模型目录、manifest、thumbs 和 preview-cache。
- GameModelViewer GUI 验证默认运行 `scripts/verify_model_viewer_gui.ps1 -StudyRoot "<study-root>" -SkillRoot "<skill>"`。该脚本用 Electron CDP 直连检查模型列表、canvas 和动作控件，并用单个已引号化 ArgumentList 启动 EXE，适配中文路径和空格路径；需要验证服装/套装入口的 viewer 层全身组合按钮时追加 `-ExpectFullBodyPreview`。
- `GameVfxViewer` 是 Skill `built-in-tools` 中维护的固定 EXE。使用前先读取 `references/tooling-manifest.json`，查找 `GameVfxViewer.local_path` 并检查 `built-in-tools/GameVfxViewer_1.0.0/GameVfxViewer.exe` 是否存在；只有工具缺失或升级时才运行 `scripts/build_vfx_viewer_tool.ps1` 构建或替换。可通过 `GAME_ANALYSIS_VFX_VIEWER` 指向固定工具 EXE，或通过 `GAME_ANALYSIS_SKILL_ROOT` 指向包含 `built-in-tools/GameVfxViewer_1.0.0/GameVfxViewer.exe` 的 Skill 根目录。不要在每次分析中重新 build，也不要复制 EXE 到 study-root；每个输出项目只在 `2.报告/` 生成 `打开特效预览.bat`，由 BAT 传入本次项目的 study-root、manifest、reconstructed-dir、preview-cache-dir 和 unity-export-dir。
- DST 的“角色 → 全部动作 → 右侧即时播放 → 保持动作切换角色”默认走 AssetArchive 派生的 `GameCharacterAnimationViewer`。先读 `references/game-character-animation-viewer-workflow.md`；其版本、来源、EXE 名和固定目录只读 `references/game-character-animation-viewer-config.json`，由 `scripts/install_game_character_animation_viewer_tool.ps1` 安装并保留 MIT LICENSE、来源 commit 和 SHA-256。运行 `scripts/create_game_character_animation_viewer_launcher.py --study-root "<study-root>" --skill-root "<skill>" --game-root "<DST安装根>"` 生成唯一 `2.报告/打开动画预览.bat`；BAT 通过 `GAME_ANALYSIS_DST_ROOT` 让 Viewer 直接读已安装 DST，不依赖旧 Bank manifest，且启动失败必须可见并写日志。
- 用户明确要求制作可套 DST 共用人物动画的新角色时，继续读 `references/game-character-animation-viewer-workflow.md` 的“内置自定义角色”节。可复用的是经至少两个官方角色 Build 验证的 Bank→Symbol/Index 契约，不是 Wilson 或其他单一角色的 Build 几何；每个角色 Build 必须拥有自己的部件尺寸、pivot、region mesh、atlas page 和 UV。现代角色优先从当前 Build 提取完整 Symbol/Index/pivot 合同，把真正的逐 Symbol RGBA PNG 交给 Klei 官方 `buildanimation.py` 重建 BILD v6 的 alpha-region mesh、Atlas 和 UV；需要 Spriter 编辑时再生成 SCML。只重写 UV 而保留基础角色顶点 XY/region mesh 仅可作为显式 bootstrap，不能作为轮廓变化后的发行产物。
- `GameAnimationViewer` 继续作为 **Klei/DST BILD + ANIM + atlas AnimState v1** 的高级 Bank/Build/Symbol 诊断与素材导出后端，路径固定为 `built-in-tools/GameAnimationViewer_1.0.1/GameAnimationViewer.exe`。其 manifest 必须符合 `references/game-animation-manifest-schema.json`，并以非空 `source_roots` 声明安全相对根；`scripts/export_game_animation_asset.py` 只声明 `current-frame-png`、`png-sequence`、`apng`、`gif`、`source-bundle` 五种真实素材导出。除非用户明确要 Bank/XML/atlas 诊断或素材导出，不得让旧 launcher 覆盖角色 Viewer 的唯一 BAT。
- Klei 人物 Bank 的动画常引用装备、胡须、皮肤等运行时 OverrideSymbol。只有命中 `game-animation-viewer-config.json` 中明确的 `render_policies`、Build 身份一致、Atlas/结构完整且覆盖率达到策略下限时，才允许选用基础 Build 并跳过缺失覆盖层；条目仍标 `partial`，manifest 必须列出 `missing_symbols` 和 `missing_symbol_policy`。Klei Symbol 哈希大小写不敏感，adapter 与 exporter 必须按 `casefold` 匹配。人物身份分类优先使用 Bank/package，不得因某个动作名包含 `ocean`、`water` 或 `fx` 就改成水面/特效。

## 研究模式

只有三种模式。默认使用full。用户已明确要求时直接执行，只有缺少授权路径或输出目录时才追问。

| 模式 | 范围 | 产出 |
|---|---|---|
| `full` | 全量资源 + 全量代码 + 全部报告 | 原始导出 + 分类资源 + 代码 + 报告 |
| `art` | 仅资源（图片/模型/特效） | 原始导出 + 分类资源 + 美术相关报告 |
| `code` | 仅代码/脚本/逻辑 | 原始导出 + 代码 + 技术相关报告 |

## 学习目的

默认学习目的为 `全面学习`。如果用户明确侧重，可从 `美术规格`、`代码逻辑`、`美术风格`、`动画手感`、`全面学习` 中选择一个或多个；执行范围仍由研究模式决定，报告深度按学习目的加权：

| 学习目的 | 报告加深重点 |
|---|---|
| `美术规格` | 分辨率分布、图集规格、序列帧尺寸、UI 图标统一尺寸、资源命名 |
| `代码逻辑` | 命名空间/模块地图、核心类职责、IL2CPP/native 可读性边界 |
| `美术风格` | 主色卡、明度/饱和度关系、角色/场景/UI 的视觉语法 |
| `动画手感` | AnimationClip、序列帧、帧事件、前摇/有效帧/后摇待验证表 |
| `全面学习` | 以上全部，默认生成完整编号报告 |

## 需求确认

开始前收集或推断以下信息；不能推断且影响安全或产出范围时才询问：

- 授权输入路径（游戏安装/导出/源码目录）
- 输出路径（默认在工作区下以游戏项目名命名；不得写入源游戏目录）
- 研究模式（`full` / `art` / `code`）
- 学习目的（`美术规格` / `代码逻辑` / `美术风格` / `动画手感` / `全面学习`；默认 `全面学习`）
- 引擎和平台线索
- 磁盘空间预算；联网工具恢复默认自动进行，只有来源/许可证/安全边界异常时才询问

## Windows 路径兼容性与盘符命名空间

- **先区分空格与字符集**：空格不等于非 ASCII。只要完整路径为 ASCII-compatible，即使路径包含空格，也优先直接使用用户提供的真实绝对路径，并通过参数数组或正确引号保持参数边界；不要为了空格改写路径。
- **禁止修改盘符命名空间**：任何主 agent 或子 agent 都不得为 game-root、study-root 或工具临时目录创建、删除或复用 `SUBST`、`New-PSDrive`、`DefineDosDevice`、`net use` 等盘符别名。子 agent 无权修改 OS 全局盘符命名空间。开始与每个子任务结束时可只读核对映射差异，但不得自动清理用户既有映射。
- **兼容目录 fail closed**：工具确实不支持非 ASCII 路径时，只能使用主 agent 明确配置的、位于现有卷上的普通目录 `<compat-root>`。主 agent 必须为每次分析生成一次分析运行唯一的 `owner-id`，在建任务图时统一下发；子 agent 不得自选或改写兼容根、`owner-id`。所有任务在 `<compat-root>/<owner-id>/<task-id>/...` 下派生目录，`task-id` 在该 `owner-id` 下唯一，确保不同运行和任务的写入范围无冲突。不得用虚拟盘符绕过；没有已配置且满足条件的兼容根或标识时，状态必须为 `BLOCKED`，按 fail closed 停止该工具步骤。
- **归因证据边界**：当前 `scripts/` 的静态扫描结果是事实；历史映射是否来自 agent 即时命令只能标为推断，不得归责到当前脚本。静态扫描不能证明过去没有执行过外部命令，也不构成自动清理任何既有映射的授权。

## 动态子 agent 调度

默认使用动态子 agent 调度。除非用户明确要求不使用多 agent，主 agent 必须先建立任务图，再按依赖顺序动态分派子 agent；有依赖的任务顺序分派，互不重叠的任务并行分派。若用户点名 `subagent-excute`，或任务需要 Maker/Reviewer 分离、可交付物反复审查修正，主 agent 必须把 `subagent-excute` 作为编排契约；`superpowers:subagent-driven-development` 只作为实现辅助参考。

`subagent-excute` 门禁必须保留：先写任务契约；建立带互不重叠写入范围的任务图；由 Maker/Fixer 产出或修正 artifacts；先做 Spec Review，通过后才能做 Quality Review；任何 review failure 都转成 Fixer 工作；修正后重新审查，直到 approved 或 genuinely blocked。若当前平台确实没有子 agent 工具，记录为能力降级，并按同一任务图由主 agent 执行带标签的串行 Maker/Fixer/Spec Review/Quality Review passes。

动画与特效任务还必须读取 `references/animation-vfx-routing-config.json` 和 `references/game-animation-preview-workflow.md`，从只读 Discovery 结果动态记录 `N/B/G/U/E/C/R`（候选数、字节数、格式组、未知组、依赖边、写冲突边、运行时依赖组），再由主 agent 建立 `Discovery → Adapter → Exporter → Preview → Merger → Spec → Quality` 的 Codex 子 agent 任务图。对于当前 2796 个 DST Bank/动画包候选的大集，按 routing config 分派 Discovery、Adapter、Exporter、Preview fragment 和且仅一个 Merger 子 agent；任何审查失败都走新的 `Fixer → Spec 复审 → Quality`。Merger 子 agent 是最终 manifest、errors、分类发布和 `打开动画预览.bat` 的唯一写者，Maker 只能写主 agent 分配的依赖闭合输入集或 `4.临时目录/子任务产物/<task-id>/` fragment。本地没有 shard CLI 不是门禁；不得声称已有通用本地 Merger 程序。

### 调度判定

- 适合分派：多引擎/多目录盘点、Unreal 分片导出、Cocos ASTC 批量解码、IL2CPP/native 与美术索引可并行、报告草稿与边界审查可并行。
- 降级串行：仅当用户明确要求不使用多 agent，或当前平台确实没有子 agent 工具时，主 agent 才按任务图串行执行并记录原因。
- 依赖等待：授权路径未确认、study-root 未创建、主分支未检测、同一工具会写同一目录、下一步依赖当前命令结果、需要人工 GUI 操作且无法隔离时，不取消多 agent；等待前置条件完成后再按任务图分派。
- 分派前读取 `references/multi-agent-prompts.md`，按角色选择规则生成提示词；不要让子 agent 自行重读整套 Skill，只提供本任务需要的 reference、输入路径、输出路径和验收标准。

### 任务图规则

主 agent 在执行前写出简短任务图，至少包含：`task-id`、角色、依赖、输入路径、互不重叠的写入范围、预期产物、验收命令。所有子 agent 都必须知道自己不是唯一协作者，不得回退或覆盖其他 agent 的成果。

依赖顺序：

- 通用：授权确认、5 个编号目录创建、`detect_engine.py` 与 `asset_inventory.py` 完成后，才能分派引擎分支任务。
- Unity：AssetRipper 原始导出完成后，再分派 UnityPy、AssetRipper 分析、关键动画、IL2CPP/native；报告片段写到 `4.临时目录/子任务产物/<task-id>/`，最终编号报告由主 agent 汇总。
- 动画/特效编排：按 `N/B/G/U/E/C/R` 和配置化 `shard_items/shard_bytes` 计算 ready nodes 与并发数；未知格式每个签名组只允许一个 Discovery/Adapter 小样本任务，未适配时走 `material-only/unsupported` 和既有图片、模型、VFX 流程，不强迫未知引擎符合 Klei schema；存在写冲突的节点必须隔离 fragment 或串行。
- Klei 动画：package conversion、共享转换索引和转换输出固定由 single-producer 串行完成；转换提交后，Prefab/`fx.lua` 语义发现、`.dyn` boundary 与无写冲突的 preview fragments 才可并行。最终由一个 Merger 子 agent 按 `Bank.name/package`、`Build.name/package` 保留歧义并发布 manifest。
- Unreal：pak info/list 完成后，可把 UModel slice manifest 拆给多个子 agent；每个子 agent 只写自己的 slice 输出目录，最后由主 agent 合并。
- GameMaker：UTMT 原始导出完成后，再分派 normalize、分类组织、画廊和报告。
- Cocos：cc.config/UUID/ASTC 映射完成后，再按 bundle 或语义目录分派解码与回填。
- Godot 3.x：PCK probe、GDRE list、scripts-only、full recover 由同一执行角色顺序完成；`0.原始导出/GDRETools恢复/` 完成后，再分派分类、学习索引和精确 Godot 二级静态验证。

### 子 agent 状态处理

子 agent 最终必须回报 `DONE`、`DONE_WITH_CONCERNS`、`NEEDS_CONTEXT` 或 `BLOCKED`：

- `DONE`：主 agent 进行规格审查和质量/边界审查。
- `DONE_WITH_CONCERNS`：先处理疑虑；如果影响正确性或范围，补充上下文后要求修正。
- `NEEDS_CONTEXT`：主 agent 提供缺失 reference、路径、命令输出或验收标准后重新分派。
- `BLOCKED`：判断是上下文不足、工具能力不足、任务过大还是 Skill 规则缺口；能拆分就拆分，不能继续就写入阻塞项和使用后迭代。

主 agent 始终负责授权边界、目录卫生、最终合并、最终报告和全局完成前检查；子 agent 不得直接写最终编号报告，除非主 agent 明确分配该报告文件且不存在并行写入。

## 交付目录（严格执行）

study-root 下**有且只有以下 5 个编号目录**，不允许创建任何其他目录：

```text
<study-root>/
  0.原始导出/           # 工具全量导出的原始目录，保持原始结构
  1.分类资源/           # 从原始导出解析整理出的最终分类目录
  2.报告/               # 研究总览和分析报告
  3.代码/               # 导出的可识别代码（含混淆代码）
  4.临时目录/           # 工具日志、中间文件、失败记录（结束后可删）
```

**禁止创建上述 5 个目录以外的任何目录**，包括但不限于：`__ascii_work`、`0.完整资源导出目录`、`1.最终产出`、`2.主角资源`、`3.代码导出`、`主角资源`、`交付状态清单.md`。`4.临时目录/_ascii_work/` 只有在其完整绝对路径本身为 ASCII-compatible 时才能作为工具兼容目录；否则按“Windows 路径兼容性与盘符命名空间”使用主 agent 配置的普通兼容根及 `<owner-id>/<task-id>/...` 隔离目录，并把正式产物回填到 5 个编号目录。

目录结构和分类标准的完整定义见 **`references/directory-spec.md`**。

### 核心分类规则

**1.分类资源/** 只包含最终可用资源，不包含索引文件、CSV、JSON 等元数据：

```text
1.分类资源/
  图片/
    UI/             # 界面元素、图标、按钮、状态条等
    角色立绘/       # 角色头像、叙事立绘、大尺寸角色 UI 图
    场景/           # 背景、关卡、环境装饰图
    道具/           # 武器、装备、掉落物、交互物图
    序列帧/         # 动画序列帧，按角色/动作分子文件夹
    图集/           # Atlas/SpriteSheet 切片后的完整图集
  模型/
    <模型名>/       # 每个模型一个文件夹
      *.fbx 或 *.gltf   # 网格体+骨骼+动画
      textures/          # 对应贴图和材质
  特效/
    粒子贴图/       # 粒子系统使用的贴图
    VFX材质/        # VFX 专用材质和 Shader
    VFX模型/        # 特效专用 Mesh
    VFX动画/        # 特效动画片段
    配置/           # 粒子系统配置文件
  音频/             # 仅放真实导出的 wav/ogg/mp3/bank 等音频样本；metadata 留在临时索引
  渲染/             # Shader、Material、RenderTexture 等可学习渲染资源
```

**模型标准**：每个模型文件夹必须能直接导入 Blender 且正常显示模型和贴图。FBX/glTF + 对应贴图放在一起。UnityPy 导出的 glTF 不能只放几何和外部贴图候选；必须在 glTF JSON 中写入 `images/textures/materials/baseColorTexture` 或在索引中明确无贴图原因。Unity Mesh 批次含 VFX 薄片、Trail/Plane 或其他两面可见网格时，学习预览材质写入 `doubleSided=true`，并明确这是防止离线预览背面剔除的展示标志，不是对原 Shader culling 状态的还原结论；详见 `references/unity-model-material-binding-workflow.md`。

**地图内嵌网格判定**：全量扫描 `.cdx/.cmx` 的 `IndexedMesh` 时，不得把所有无贴图网格都解释为美术模型导出失败。若来源是 `Content/maps/**/*.cmx`，同名 `.tmx` 存在，且网格自身没有材质/贴图引用，应在 manifest 与 sidecar 中标记为 `asset_role=map_structure_mesh`、`visual_source=tmx_tileset_layers`；这类灰色盒体通常是地图结构、体块、碰撞或 greybox 辅助网格，实际视觉需要回看 TMX 图层和 tileset。只有存在已验证材质绑定的条目才标记为 `textured_art_model`。

**3.代码/** 只包含代码文件，不包含其他资源：

```text
3.代码/
  脚本/            # GML、Lua、JS、Python、GDScript 等可读脚本
  IL导出/          # ILSpy/ilspycmd 导出的 C# 反编译结果
  IL2CPP_Dump/     # dump.cs、script.json、il2cpp.h
  Native映射/      # Ghidra/IDA 地址映射和行为摘要
  代码可读性判定.md
```

**2.报告/** 只放学习者阅读产物：编号报告、图片画廊 BAT 入口、模型预览 BAT 入口、特效预览 BAT 入口、可选 PDF。CSV/JSON、验证日志、manifest、分类摘要统一写入 `4.临时目录`；空目录不放 `_empty_reason.md`，缺失原因写入 `2.报告/00_研究总览.md` 和临时验证日志。需要图片画廊时，默认入口文件命名为 `打开图片画廊.bat` 并直接放在本目录；它调用 Skill 固定 EXE `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe`，读取 `4.临时目录/中间索引/图片画廊/manifest.json`，并按 manifest `source_roots` 派生重复 `--images-root` 参数。BAT 契约固定为传入 `--study-root "<study-root>"`、`--manifest "<study-root>/4.临时目录/中间索引/图片画廊/manifest.json"`，以及一个或多个 `--images-root "<source_root>"`；manifest、缩略图缓存和错误日志分别位于 `4.临时目录/中间索引/图片画廊/manifest.json`、`4.临时目录/中间索引/图片画廊/thumbnails/`、`4.临时目录/中间索引/图片画廊/errors.jsonl`。需要模型预览时，模型入口只放在本目录且只生成 `打开模型预览.bat`；它调用 Skill 固定 EXE `built-in-tools/GameModelViewer_1.0.0/GameModelViewer.exe`，传入本次项目的 `--study-root`、`--models-dir`、`--manifest`、`--thumbs-dir` 和 `--preview-cache-dir`。模型 manifest、thumbs、preview-cache 和 errors 固定保留在 `4.临时目录/中间索引/模型预览/`。需要 Unity 动态特效预览时，特效入口只放在本目录，主入口是 `打开特效预览.bat`；它调用 Skill 固定 EXE `built-in-tools/GameVfxViewer_1.0.0/GameVfxViewer.exe`，传入本次项目的 `--study-root`、`--manifest`、`--reconstructed-dir`、`--preview-cache-dir` 和 `--unity-export-dir`。VFX manifest、errors、reconstructed JSON、preview-cache 和 unity-export 固定保留在 `4.临时目录/中间索引/VFX预览/`：

| 模式 | 生成的报告 |
|---|---|
| `full` | `00_研究总览.md`、`01_美术资源规格表.md`、`02_美术风格与色彩速查.md`、`03_动画与手感帧数据.md`、`04_场景关卡搭建.md`、`05_技术架构拆解.md`、`06_玩法系统拆解.md`、`07_渲染管线拆解.md`、`08_命名规范汇总.md`、`09_可迁移技术清单.md` |
| `art` | `00_研究总览.md`、`01_美术资源规格表.md`、`02_美术风格与色彩速查.md`、`03_动画与手感帧数据.md`、`04_场景关卡搭建.md`、`07_渲染管线拆解.md`、`08_命名规范汇总.md`、`09_可迁移技术清单.md` |
| `code` | `00_研究总览.md`、`05_技术架构拆解.md`、`06_玩法系统拆解.md`、`08_命名规范汇总.md`、`09_可迁移技术清单.md` |

每份报告的模板见 `references/templates/<报告名>模板.md`。

## 通用执行入口

1. 确认授权、输入目录、输出目录、研究模式；创建 study-root 下 `0.原始导出/`、`1.分类资源/`、`2.报告/`、`3.代码/`、`4.临时目录/` 共 5 个目录，不创建任何其他目录。
2. 运行 `detect_engine.py` 与 `asset_inventory.py`，结果写入 `2.报告/00_研究总览.md`。
3. 根据检测结果选择**主分支**（只执行命中的分支），并按“动态子 agent 调度”建立任务图：

   | 检测结果 | 详细流程 |
   |---|---|
   | Unity 构建 | `references/branch-completion-checks.md` → Unity 节 |
   | Unreal `.pak`/`.uproject` | `references/branch-completion-checks.md` → Unreal 节 |
   | GameMaker `data.win` | `references/branch-completion-checks.md` → GameMaker 节 |
   | Godot 3.x 独立 `.pck`/`GDPC` | `references/branch-completion-checks.md` → Godot 3.x PCK 节；同时读取 `references/godot-pck-workflow.md` |
   | Cocos Creator APK/`.astc` | `references/branch-completion-checks.md` → Cocos 节 |
   | MonoGame/FNA/XNA `.xnb` | `references/branch-completion-checks.md` → MonoGame/FNA/XNA `.xnb` 节；同时读取 `references/monogame-xnb-workflow.md` |
   | Klei / Don't Starve Together | `references/branch-completion-checks.md` → Klei / Don't Starve Together 节；同时读取 `references/klei-dst-workflow.md` |
   | 以上都不是 | `references/branch-completion-checks.md` → 未知引擎节 |

4. 根据研究模式执行对应范围：`art` 只执行资源导出和分类，`code` 只执行代码提取，`full` 全部执行；默认按任务图分派子 agent，依赖任务顺序分派，互不重叠任务并行分派。
5. 工具原始输出写入 `0.原始导出/<工具名>/`，中间文件写入 `4.临时目录/`。
6. 资源分类完成后，如果 `1.分类资源/图片` 已有图片，或用户/分支要求图片画廊，先运行 `scripts/build_image_gallery_manifest.py --study-root <study-root> --skill-root <skill-root>`，再运行 `scripts/create_image_gallery_launcher.py --study-root <study-root> --skill-root <skill-root>`。默认输出 `<study-root>/2.报告/打开图片画廊.bat`；manifest、缩略图缓存、错误日志固定为 `<study-root>/4.临时目录/中间索引/图片画廊/manifest.json`、`thumbnails/`、`errors.jsonl`。BAT 必须调用固定 EXE `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe`，传入 `--study-root`、`--manifest`，并按 manifest `source_roots` 重复传入 `--images-root`。旧 HTML 画廊和每项目 EXE 构建仅作兼容历史输出，不是默认流程。
7. 模型导出/分类后，若 UnityPy 原始索引中存在 `SkinnedMeshRenderer` 与 `AnimationClip`，先运行 `scripts/export_unitypy_real_animation_samples_to_gltf.py --study-root "<study-root>" --source "<game-root>" --auto --max-samples 1`，自动用 direct Transform 曲线与 skeleton path 覆盖率挑选可播放真实蒙皮动作样例；无法匹配时把失败原因写入 `4.临时目录/中间索引/UnityPy真实动作导出/`，不得硬编码 path id。随后如果 UnityPy 原始索引中存在 `AnimationClip` metadata，再运行 `scripts/build_unity_animation_preview_models.py --study-root "<study-root>"`，在 `1.分类资源/模型/Unity动作预览_AnimationClips/` 生成一个可播放 glTF 动作学习预览模型，并把摘要写入 `4.临时目录/中间索引/模型预览/unity-animation-preview-summary.json`；该模型只保留动作名和时长并生成程序化预览姿态，不等同于完整还原原始 Unity skin、Avatar、bone weights 或 AnimationClip 曲线。若 Candide/Romestead `.cdx/.cmx` 等文件中存在 BinaryWriter `i-mesh`/`IndexedMesh` 内嵌网格，运行 `scripts/export_candide_cdx_meshes_to_gltf.py --study-root "<study-root>" --source "<game-root>" --scan-all-embedded-meshes`，把车、轮子、建筑、机关、doodad、地图/POI 场景等不在 `Content\mesh\*.xnb` 中的内嵌网格转为 glTF，并从 `media/textures`、`media/sprites` 等资源路径绑定已导出的贴图；用户只要求特定对象时可加 `--include-regex` 缩小范围。随后如果 `1.分类资源/模型/` 下存在 `.gltf`、`.glb` 或 `.fbx`，运行模型预览链：先运行 `scripts/build_model_gallery_manifest.py --study-root "<study-root>"` 生成 `4.临时目录/中间索引/模型预览/manifest.json`、`thumbs/`、`preview-cache/` 和 `errors.jsonl`，manifest 必须解析 glTF/GLB 的 `animations`、`skins`、`meshes` 和 `materials` 统计，并为每个模型目录写入 `metadata.json`、`export-log.json`、`dependencies.json` 和 `missing-resources.json`；再用 `Get-Command blender -ErrorAction SilentlyContinue` 检测 Blender，检测到时运行 `scripts/render_model_thumbnails_blender.py --manifest "<study-root>\4.临时目录\中间索引\模型预览\manifest.json" --blender "<blender.exe>"`。模型数量大、执行时间长或渲染耗时不可作为跳过导出、跳过贴图绑定、跳过 Blender 缩略图渲染或用占位图冒充完成的理由；必须继续执行，必要时拆成可续跑批次并记录进度、失败项和下一批命令。占位缩略图只能作为执行中的临时标记，不能作为最终交付状态；若确实因工具缺失、崩溃或磁盘空间等外部阻塞无法完成，必须记录阻塞证据并标为 `DONE_WITH_CONCERNS` 或 `BLOCKED`。未检测到 Blender 时运行 `scripts/build_model_gallery_manifest.py --study-root "<study-root>" --record-blender-missing` 追加 `stage="thumbnail"`、`status="skipped"`、`reason="blender_not_found"`，再继续。随后无论 Blender 是否可用，都运行 `scripts/create_model_viewer_launcher.py --study-root "<study-root>" --skill-root "<skill>"` 生成唯一入口 `2.报告/打开模型预览.bat`，不得额外生成重复模型预览 BAT，再运行 `scripts/update_model_gallery_summary.py --study-root "<study-root>" --manifest "<study-root>\4.临时目录\中间索引\模型预览\manifest.json" --viewer-exe "<skill>\built-in-tools\GameModelViewer_1.0.0\GameModelViewer.exe"` 更新研究总览模型预览摘要。固定 EXE 始终留在 Skill `built-in-tools`，不复制到 study-root。
8. 若 UnityPy 原始索引中存在 `ParticleSystem`、`ParticleSystemRenderer`、`TrailRenderer`、`LineRenderer` 或 VFX 命名材质/贴图/动画，运行 Unity 动态特效预览链：先运行 `scripts/build_vfx_preview_manifest.py --study-root "<study-root>"`，把粒子贴图等真实资源分类到 `1.分类资源/特效/`，把配置/metadata 卡写入 `1.分类资源/特效/配置/`，并生成 `4.临时目录/中间索引/VFX预览/manifest.json` 和 `errors.jsonl`；再运行 `scripts/export_unity_vfx_reconstruction.py --study-root "<study-root>"`，生成 `4.临时目录/中间索引/VFX预览/reconstructed/` 下的 reconstructed JSON，并导出可重新导入 Unity 的项目相对目录 `4.临时目录/中间索引/VFX预览/unity-export/Assets/GameAnalysisVfxPreview`，其中包含 Prefab、Material、metadata、bootstrap 脚本目录和可解析的贴图/mesh/animation 引用。若同一研究中有多套 AssetRipper 导出，`paths.assetripper_prefab_index` 与 `paths.assetripper_export_root` 必须指向对象覆盖最多且已复核的工程，不得保留旧的较小索引；只需重试原始 Prefab 补充时使用 `--original-prefabs-only`，避免重跑全部 VFX 条目。动态还原、状态、warnings/errors、reconstructed 和 unity-export 留在 `4.临时目录/中间索引/VFX预览/`；最后运行 `scripts/create_vfx_viewer_launcher.py --study-root "<study-root>" --skill-root "<skill>"` 生成 `2.报告/打开特效预览.bat`。动态还原是 best-effort：只覆盖 ParticleSystem、TrailRenderer、LineRenderer 的基础模块和可解析依赖；失败或部分还原必须写入 manifest item 的 `preview_status`、`reconstruction_status`、`unity_export_status`、`errors` 或 `warnings`，不得假装完整还原 Unity runtime。
   若根对象超过 `limits.max_items`、用户明确要求全量结构追溯，或常规适配器的同源依赖扫描出现规模退化，第一步改用 `scripts/build_unity_vfx_full_index.py --study-root "<study-root>" --config "<skill>/references/vfx-preview-config.json" --source-root "<readonly-source-root>"`。交互预览上限不是全量导出上限；全量路径不截断 manifest 根对象，按同 GameObject 建立 ParticleSystem/Renderer/Trail/Line 配对并输出 AssetRipper YAML 对账，所有数量从输入索引派生，不写死项目计数。
9. 用户要求按 DST 角色浏览和即时播放动作时，按 `references/game-character-animation-viewer-workflow.md` 运行角色 Viewer，并用明确的已安装 DST 根生成唯一 `2.报告/打开动画预览.bat`。用户要求 Bank/Build/Symbol、XML/atlas、逐帧或 source-bundle 等高级素材诊断/导出时，再按 `references/game-animation-preview-workflow.md` 运行旧 AnimState v1 任务图：先用 `scripts/build_game_animation_manifest.py` 建索引，再由 `scripts/export_game_animation_asset.py` 导出；manifest/cache 固定在 `4.临时目录/中间索引/游戏动画预览/{manifest.json,preview-cache/}`，用户导出固定在 `4.临时目录/动画预览导出/`，但不得覆盖角色 Viewer 的 BAT。未知引擎先 Discovery 并建立独立 adapter；未适配时走 `material-only/unsupported`，继续使用既有图片、模型和 VFX 流程，不要求符合 Klei schema。
10. 运行 `scripts/build_learning_indices.py` 汇总学习索引到 `4.临时目录/中间索引/学习输出/`，再据此撰写编号报告。
11. 运行 `audit_study_root_hygiene.py` 检查 `_ascii_work` 临时目录和 study-root 合同；需要交付清理时显式加 `--clean-browser-profiles`。
12. 汇总所有子 agent 产物，完成规格审查、质量/边界审查和全局完成前检查；每个分支遇到阻塞时记录到 `2.报告/00_研究总览.md` 的阻塞项节。

详细命令参考 `references/full-extraction-workflow.md`。

## 未知引擎与自扩展

遇到本 Skill 未覆盖的引擎/格式时：

1. 只读识别：文件树、扩展名、魔数、manifest、版本线索。
2. 查找可信工具：优先官方/开源/长期维护工具；找到可用工具后自动下载到 Skill 的 `tools/`，记录来源、许可证、哈希和版本输出。
3. 若无现成工具，编写或接入可审计解析流程，并沉淀到 `scripts/` 或 `references/`。
4. 小样本验证后再全量执行；不要停在“已识别但未转换”。
5. 若来源/许可证/安全边界或格式复杂度确实无法继续，写入阻塞项、已尝试路线和下一步。

未知动画/特效格式先做只读 Discovery，并为确认的引擎/格式建立独立 adapter 和数据合同；当前 Klei/DST AnimState v1 的 `references/game-animation-manifest-schema.json` 不适用于未知引擎。没有 adapter 时标记 `material-only/unsupported`，继续走既有图片画廊、模型预览和 VFX 预览/导出流程；禁止因为存在 AnimationClip、粒子命名或贴图就默认按 Unity 或 Klei 解释。

详细规则见 `references/tool-extension-and-iteration.md`。

## 使用后迭代（每次必须执行）

每次使用结束前**必须**执行以下步骤：

1. **新工具沉淀**：本次如果使用了之前没用过的工具，必须把工具本体放入 Skill 的 `tools/<tool-name>_<version>/` 目录，并更新 `references/tooling-manifest.json`（记录名称、版本、路径、来源 URL、许可证、SHA256、用途）。
2. **新流程沉淀**：如果发现了新的引擎/格式/工具链/命令模式，更新对应的 reference 文件或新增 reference。
3. **新脚本沉淀**：如果编写了新的自动化脚本，放入 `scripts/` 并更新 SKILL.md 脚本表。
4. **记录到研究总览**：在 `2.报告/00_研究总览.md` 的「Skill 迭代」节写明本次新增了什么、未能沉淀的原因。

只有本次没有任何新工具/新流程/新脚本时才可跳过。跳过时也必须在研究总览中注明"本次无新增沉淀"。

## 全局完成前检查

- [ ] 源游戏目录未修改
- [ ] 若 YooAsset manifest `.bundle` 为可审计的全零前缀 + 明文 UnityFS offset 包装，已按 `references/yooasset-offset-recovery-workflow.md` 恢复全部合格条目；恢复数与 manifest `.bundle` 数或精确阻塞集合对账，embedded declared size/后缀 SHA-256/源快照通过，恢复后 UnityPy 的 bundle source 对象覆盖、fragment exact set、no-work resume 和递归 `partial=0` 通过
- [ ] study-root 下有且只有 `0.原始导出/`、`1.分类资源/`、`2.报告/`、`3.代码/`、`4.临时目录/`，无其他目录或散落文件
- [ ] `0.原始导出/` 有工具全量导出的完整原始结构
- [ ] `1.分类资源/图片/` 里只有图片文件（PNG/JPG/TGA/PSD），无 CSV/JSON/MD
- [ ] `1.分类资源/模型/` 里每个子文件夹能直接导入 Blender 并正常显示模型+贴图；可恢复贴图的 UnityPy-derived glTF 已抽样同时验证 `images/textures/materials/pbrMetallicRoughness.baseColorTexture`、primitive `material`、相对贴图 URI 文件存在，以及 Blender CLI 中 Image Texture Color -> Principled BSDF Base Color 连接；`same_name_candidate` 仅标为待复核，不计为已验证材质
- [ ] `1.分类资源/特效/` 按粒子贴图/VFX材质/VFX模型/VFX动画/配置分类
- [ ] 若检测到 Unity VFX，`4.临时目录/中间索引/VFX预览/manifest.json`、`errors.jsonl`、`reconstructed/`、`unity-export/` 和 `2.报告/打开特效预览.bat` 均已生成；`unity-export/Assets/GameAnalysisVfxPreview` 是可重新导入 Unity 的项目相对目录，包含 Prefab、Material、metadata、bootstrap 脚本和可解析依赖引用
- [ ] VFX 动态还原按 best-effort 标注：ParticleSystem、TrailRenderer、LineRenderer 基础模块和依赖贴图/材质/mesh/animation 可解析时才标为 ready/full；失败或部分还原写入 `preview_status`、`reconstruction_status`、`unity_export_status`、`errors` 或 `warnings`，不得声称完整还原 Unity runtime 粒子模拟
- [ ] `1.分类资源/音频/` 只放真实音频样本，AudioClip metadata 留在 `4.临时目录/中间索引/学习输出/音频资源索引.csv`；AssetRipper 已导出真实音频时运行 `scripts/organize_assetripper_audio_resources.py`，候选/分类/总字节/manifest SHA-256 对账且错误集合为空；硬链接分类只读，编辑前复制
- [ ] `1.分类资源/渲染/` 只放可学习渲染资源；shader/material 索引写入临时目录
- [ ] `3.代码/` 里只有代码文件，无图片/模型/索引（`code` 和 `full` 模式时）
- [ ] `2.报告/00_研究总览.md` 存在且记录了引擎、工具、导出统计、阻塞项
- [ ] `2.报告/` 没有 CSV、JSON、验证日志、manifest 或分类摘要；图片画廊入口仅保留 `打开图片画廊.bat`；模型预览入口仅保留 `打开模型预览.bat`，不得再生成 `模型预览.bat` 等重复别名；特效预览入口仅保留 `打开特效预览.bat`
- [ ] 图片画廊生成时，`4.临时目录/中间索引/图片画廊/manifest.json`、`4.临时目录/中间索引/图片画廊/thumbnails/` 和 `4.临时目录/中间索引/图片画廊/errors.jsonl` 均位于临时目录；manifest 能列出授权图片根和缩略图路径，失败项只写入 `errors.jsonl`
- [ ] `2.报告/打开图片画廊.bat` 调用固定 EXE `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe`，并包含 `--study-root`、`--manifest` 和 manifest `source_roots` 派生的重复 `--images-root`
- [ ] 固定 EXE `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe` 存在；完成前必须执行 `--self-test --study-root ... --manifest ... --images-root ...` 并通过，不能用旧 HTML 画廊或每项目构建代替
- [ ] 若 `1.分类资源/模型/` 有 `.gltf/.glb/.fbx`，`4.临时目录/中间索引/模型预览/manifest.json`、`thumbs/`、`preview-cache/`、`errors.jsonl`、`2.报告/打开模型预览.bat` 和 `2.报告/00_研究总览.md` 的模型预览摘要均存在；manifest 已统计 glTF/GLB 的 `animation_count`、`animation_origin` 和 `is_generated_animation_preview`，研究总览已分开统计真实导出动作模型与程序化动作预览模型；Blender 不可用、缩略图缺失或 FBX preview-cache 转换失败时，只作为 warning 记录到 `errors.jsonl` 与研究总览，不阻塞图片、代码、报告等其他交付
- [ ] 模型预览交付前，固定 EXE `--self-test` 通过；含真实或程序化动作条目时运行 `scripts/verify_model_viewer_gui.ps1`，验证目标模型、右侧动作列表、底部播放控件、拖动时间轴后暂停、canvas 和 `viewportError` 均符合预期；含 `full_body_preview` 的服装/套装入口追加 `-ExpectFullBodyPreview` 验证按钮点击和全身组合视图
- [ ] 若 `1.分类资源/特效/` 有 Unity VFX 条目，`4.临时目录/中间索引/VFX预览/manifest.json`、`errors.jsonl`、`reconstructed/`、`preview-cache/`、`unity-export/Assets/GameAnalysisVfxPreview` 和 `2.报告/打开特效预览.bat` 均按 `references/vfx-preview-config.json` 生成；ParticleSystem 的 Prefab/Material/metadata/bootstrap 脚本、贴图 `.meta` 和 GUID 引用可被 Unity 项目重新导入；TrailRenderer/LineRenderer 或缺失依赖时必须标为 partial/failed，不得标 ready
- [ ] 若 XNB reader 索引中存在 `ModelReader`，必须满足其一：`1.分类资源/模型/` 中存在 Blender 可导入的 `.gltf/.glb/.fbx` 并已完成模型预览/验证链；或存在 `4.临时目录/失败记录/MonoGame模型转换阻塞.md` 且 `2.报告/00_研究总览.md` 记录阻塞项，最终状态标为 `DONE_WITH_CONCERNS` 或 `BLOCKED`。不能只生成边界索引后声称 full 完成
- [ ] 若命中 Godot 3.x PCK，PCK probe 已验证 format 0/1、安全 `res://` 路径、offset/size、重叠和全部 MD5；GDRE list、scripts-only、full recover 逐级通过，完整恢复位于 `0.原始导出/GDRETools恢复/` 且源 PCK SHA-256 前后一致；分类索引只在 `4.临时目录/中间索引/Godot/`；精确 Godot 二级验证前已扫描 `tool`、`EditorPlugin`、GDNative/原生库且不运行主场景
- [ ] `4.临时目录/中间索引/学习输出/` 已生成学习索引
- [ ] `audit_study_root_hygiene.py` 已检查临时目录卫生；浏览器 profile 清理只在用户要求交付清理时执行
- [ ] 使用后迭代已执行：新工具已放入 `tools/`，manifest 已更新
- [ ] 最终回复说明哪些完成、哪些阻塞

引擎分支的专项检查在 `references/branch-completion-checks.md` 中完成。
常见 Agent 易犯错误见 `references/common-pitfalls.md`。

## 资源导航

| 文件 | 何时读取 |
|---|---|
| `references/directory-spec.md` | 创建 study-root、分类资源、验收标准 |
| `references/branch-completion-checks.md` | 执行引擎分支流程和完成前检查 |
| `references/common-pitfalls.md` | 避免已知陷阱 |
| `references/full-extraction-workflow.md` | 全量素材提取流程 |
| `references/unity-gameframework-gff-workflow.md` | Unity Game Framework `GFP/GFF` 识别、探测、XOR 恢复、边界校验与后续 UnityPy/AssetRipper 接力 |
| `references/unity-monobehaviour-boundary-config.json` | UnityPy MonoBehaviour 状态、Expected-vs-actual、metadata 完整性与来源分组的配置化边界分析 |
| `references/unity-monobehaviour-boundary-contract.json` | MonoBehaviour 边界 JSON/Markdown 输出 schema、严格序列化与禁止外推契约 |
| `references/unitypy-resource-organization.json` | UnityPy 图片/音频分类、序列帧门槛、主角候选、传输与冲突策略的通用配置；项目专属关键词只放项目配置副本；专项分支可把副本的 `paths.index_root` 配为 `4.临时目录/中间索引/<非空安全子路径>` 以隔离索引 |
| `references/unitypy-resource-organization-contract.json` | UnityPy 组织索引字段、摘要键、确定性序列化、路径与冲突输出契约 |
| `references/unity-model-material-binding-workflow.md` | UnityPy Mesh glTF 材质贴图绑定与 Blender 验证 |
| `references/yooasset-manifest-workflow.md` | YooAsset JSON manifest 的 Asset/Bundle/依赖映射、物理 payload 文件名/大小/哈希/CRC 对账、加密声明与只读边界 |
| `references/yooasset-manifest-audit-config.json` | YooAsset manifest 发现、包目录、OutputNameStyle 文件名模板、payload/metadata、哈希/CRC 表示和输出名配置 |
| `references/yooasset-offset-recovery-workflow.md` | YooAsset `.bundle` 的全零前缀 + 明文 UnityFS offset 包装识别、严格停止边界、恢复、UnityPy 接力与覆盖对账 |
| `references/yooasset-offset-recovery-config.json` | offset 扫描上限、magic、UnityFS format/header、访问控制信号、no-reparse/fresh 输出和审计文件配置 |
| `references/wwise-soundbank-workflow.md` | Wwise SoundbanksInfo 语义索引、外置 WEM 与 bank 内嵌子流的 vgmstream 全量恢复及“退出 0 但零产物”校验 |
| `references/model-viewer-gallery-config.json` | GameModelViewer 固定 EXE、BAT、manifest、thumbs 和 preview-cache 配置 |
| `references/vfx-preview-config.json` | Unity VFX 类型、关键词、状态、路径和固定 GameVfxViewer 配置 |
| `references/vfx-unity-reconstruction-schema.json` | Unity VFX reconstructed JSON 与 Unity 回导目录结构校验 |
| `references/gamemaker-datawin-workflow.md` | GameMaker `data.win` 导出 |
| `references/cocos-creator-astc-workflow.md` | Cocos Creator APK、`.astc` 解码 |
| `references/unreal-pak-workflow.md` | Unreal `.pak` 分析和导出 |
| `references/monogame-xnb-workflow.md` | MonoGame/FNA/XNA `.xnb` reader 扫描、Texture2D 导出、ModelReader 转换/阻塞门禁、Tiled/FMOD 索引 |
| `references/klei-dst-workflow.md` | Don't Starve Together / Klei 自研包体识别、Lua 导出、KTEX/DYN/FMOD 阻塞记录和后续工具链 |
| `references/godot-pck-workflow.md` | Godot 3.x 独立 PCK 只读探测、GDRETools 分阶段恢复、分类、学习索引和精确版本二级静态验证 |
| `references/godot-resource-classification.json` | Godot 恢复图片的 UI/场景/道具/图集/序列帧/粒子贴图配置化分类规则 |
| `references/il2cpp-native-code-workflow.md` | IL2CPP/native 代码分析 |
| `references/local-export-preview-requirements.md` | BAT/manifest/固定 EXE 图片画廊与模型预览规格 |
| `references/copyright-and-style-boundaries.md` | 学习边界、授权边界 |
| `references/visual-analysis-taxonomy.md` | 色彩、构图、材质分析 |
| `references/multi-agent-prompts.md` | 动态子 agent 角色选择、提示词和输出契约；补充 `subagent-excute` 编排 |
| `references/templates/*.md` | 报告模板 |
| `references/tool-extension-and-iteration.md` | 未知引擎、Skill 自扩展 |
| `references/tooling-manifest.json` | 工具版本和本地路径 |

## 脚本

所有脚本默认本地运行，不上传内容。工具版本和路径见 `references/tooling-manifest.json`。

| 脚本 | 用途 |
|---|---|
| `scripts/detect_engine.py` | 从目录结构推测引擎 |
| `scripts/asset_inventory.py` | 统计文件类型、大小、资源类别 |
| `scripts/unity_preflight.py` | Unity 构建只读预检 |
| `scripts/invoke_python_utf8.ps1` | Windows PowerShell 下以 UTF-8 安全调用 Skill Python 脚本，保持中文/空格参数边界并透传 stdout、stderr 与退出码 |
| `scripts/export_unity_gameframework_resources.py` | 解析 Unity Game Framework `GFP` v2 / `GFF` v0，先 probe 再按资源 load type 做无解密、快速 XOR 或完整 XOR 恢复，并输出完整边界/哈希索引 |
| `scripts/export_gamemaker_datawin_utmt.ps1` | GameMaker `data.win` 导出 |
| `scripts/normalize_gamemaker_exports.py` | UTMT 导出归一化索引 |
| `scripts/organize_gamemaker_project_export.py` | GameMaker 分类资源组织 |
| `scripts/cocos_creator_astc_repair.py` | Cocos Creator ASTC 修复 |
| `scripts/analyze_godot_pck.py` | 只读解析独立 Godot PCK format 0/1，校验安全路径、范围、重叠与 MD5，输出 JSON/CSV/摘要且不提取内容 |
| `scripts/export_godot_pck_project.ps1` | 从 manifest 启动 GDRETools，按 scripts-only/full 固定目录恢复，记录超时、退出码、日志和源 PCK 前后 SHA-256 |
| `scripts/organize_godot_project_export.py` | 按配置幂等组织 Godot 恢复图片、真实音频、字体和保持相对路径的 GDScript，索引写入临时目录 |
| `scripts/extract_all_readable_resources.py` | UnityPy 可读资源导出；source root 到所有候选的整链拒绝 symlink/junction/reparse 与 canonical 根外路径，并在读取窗口租约固定授权文件代；fresh protocol 2 用完整对象/AudioClip sample key SHA-256 身份，AudioClip 多样本集合 stage/commit/rollback；UnityFS bounded-memory 路由同时按单 block 与单 node 的配置化未压缩大小门槛选择 spool/短生命周期 worker，避免“小 blocks 组成大 node”绕过内存门禁，并把运行阈值记录到 checkpoint/summary；legacy protocol 1 仅允许 canonical 路径全局唯一且 checkpoint/fragments 稳定租约绑定的纯 no-work。每轮先发布并租约固定不可变事务 sidecar，sidecar 绑定计划中的 summary/checkpoint 路径、字节数与 SHA-256；v3 sidecar 还绑定 previous head、previous sidecar SHA-256、previous count/chain 与两份旧控制精确代，两个控制文件共同锚定同一新 head/count/chain 后再用 no-replace 跨目录可回滚组事务发布。无 sidecar 且双控制存在时只允许唯一 genesis；单个被双控制精确引用的 v2 可作为显式迁移边界，未引用 legacy 不自动吸收；严格匹配旧 head/旧控制代/计划 anchor 的单个未引用 v3 才可作为 interrupted tail 清理。组事务的 cleanup/recovery/真正成功返回通过精确目标代 lease 显式交接，rename/unlink 以精确句柄/fd 的 syscall 后置事实协调，恢复只删除事务 token 自有且代仍精确匹配的 partial。独立 validator/snapshot 从控制 head 重建唯一 ordered chain 并纳入不可变快照，递归拒绝 fragment/sidecar 的嵌套、extra、missing predecessor、fork、cycle、duplicate transaction、digest/anchor tamper、非法命名和 reparse；逐级目录创建与 Windows 输出提交/清理均相对稳定父目录 handle/fd |
| `scripts/analyze_unitypy_monobehaviour_boundary.py` | 流式分析 UnityPy 全量索引中的 MonoBehaviour 状态、metadata 通用头/自定义字段边界和 Expected-vs-actual 分布；两遍拒绝空/重复表头，Windows 双产物事务以目标拒写句柄、精确对象退休和全链 no-replace 绑定目标代；每个 Nt rename 都从精确源句柄做 syscall 后置 basename/identity 协调，协调器查询失败时以固定目录相对 NT 重开/fstat 和源/目标候选代收敛，只报告实际匹配的 cleanup/recovery 路径；成功结果由 dict-compatible owner 把两份 final 的 mandatory generation lease 交接到 API/CLI 真正消费边界后显式释放，POSIX 缺强制原语时对既有目标 fail closed；缺失 byte_size/raw SHA 明确标 unavailable，可选 probe 仅按样本报告且禁止总体外推 |
| `scripts/export_unity_loose_resources.py` | 配置化扫描并幂等归档 Unity `*_Data/Res` 与 `*_Data/StreamingAssets` loose 资源；验证 JSON/图片，只把图片按保守规则复制到分类目录，索引和错误写入临时目录 |
| `scripts/analyze_yooasset_manifests.py` | 只读解析 YooAsset JSON manifest，生成可追溯 Asset/Bundle CSV、summary JSON/中文 Markdown，并对账 BundleID/DependIDs、物理文件名、大小、FileHash、FileCRC、缺失/额外与 Encrypted 声明 |
| `scripts/recover_yooasset_offset_bundles.py` | 严格恢复 manifest `.bundle` 中全零前缀包裹的明文 UnityFS；校验唯一 magic、header declared size、源/后缀/恢复哈希和安全边界，非零/歧义/访问控制信号按条目阻塞 |
| `references/unity-loose-resource-config.json` | Unity loose 资源发现根、排除规则、文件类型和图片用途分类配置 |
| `scripts/analyze_loose_json_tables.py` | 对授权 loose JSON 做只读结构地图：严格解码/解析、文件名模块拆分、字段名级系统候选、源快照与确定性哈希抽样；不输出字段值 |
| `references/loose-json-analysis-config.json` | loose JSON 扫描根、预期基线、命名规则、编码顺序、结构分桶、词法候选、隐私字段名信号、抽样和输出名配置 |
| `references/assetripper-project-analysis.json` | AssetRipper Build Settings、动态渲染设置与 layout mismatch 可信度边界配置 |
| `scripts/export_wwise_audio.py` | 安全全量导出 Wwise 外置 WEM 与 BNK 子流：metadata 固定使用 `-m -I`，解码固定显式 `-o`，解析 SoundbanksInfo 语义并生成可续跑状态索引；支持硬链接归档到分类音频目录，避免复制大体积 WAV |
| `scripts/export_unitypy_meshes_to_gltf.py` | UnityPy Mesh 转 glTF 模型导出；可用 `--expected-index` 精确跟踪 all-resources Mesh 集合、用安全子根隔离并行分支，拼接 MeshFilter/MeshRenderer/Material 与外部 Texture/Material metadata 证据，内容不变时复用 glTF/BIN/sidecar/index/summary 并保留 mtime；真实配置属性与同名候选严格分栏 |
| `scripts/export_candide_cdx_meshes_to_gltf.py` | 从 Candide/Romestead `.cdx/.cmx` 等文件中扫描 BinaryWriter `i-mesh`/内嵌 `IndexedMesh`，导出 glTF 并绑定已导出的贴图，补足不在 `Content/mesh/*.xnb` 中的车、轮子、建筑、机关、doodad 和地图/POI 场景网格 |
| `scripts/export_klei_dst_project.py` | 对 Don't Starve Together / Klei 自研包体做本机学习导出：解包 ZIP、导出 Lua、复制 FMOD 音频 bank、索引 KTEX/DYN/动画包 |
| `scripts/export_klei_dst_images.py` | 按来源 manifest 对 Klei/DST 本体与 Mod 做多来源图片补导：安全扫描 loose/嵌套 ZIP 中的 TEX、直图与 XML 图集元数据，Stex 转换并用 Pillow 验证真实 PNG，高可信地形按配置化中文用途分类与安全 `terrain_prefix` 归档到 `图片/地形贴图`，输出来源链、冲突和不安全路径索引 |
| `scripts/convert_klei_dst_resources.py` | 在 Klei/DST 原始导出后执行资源转换：Stex 批量 KTEX->PNG、DstAnimTool 批量 anim/build->XML、python-fsb5/vgmstream 批量 FSB->OGG/WAV，并写入转换索引与失败记录 |
| `scripts/analyze_klei_dyn_boundary.py` | 对 Klei/DST `.dyn` 未识别载荷做边界盘点：前 64 字节、熵、header 聚类、`anim_dynamic` 同名 ZIP 对照和 EXE 字符串线索，输出 `dyn_boundary_inventory.csv`、summary JSON 与失败记录 |
| `scripts/analyze_klei_worldsim_binary.py` | 对 Klei/DST `WorldSim` native 地图生成入口做 PE/Lua 绑定分析：用 pefile/capstone 恢复 Lua 注册表、wrapper RVA、日志字符串、候选 native call，并生成 Ghidra 定点 target CSV |
| `scripts/build_klei_dst_reports.py` | 从 Klei/DST 导出 summary、转换索引、图片尺寸、色彩抽样、Lua 目录和 `.dyn` 阻塞索引生成 full 模式 00-09 编号报告、`3.代码/代码可读性判定.md` 与 `4.临时目录/失败记录/KleiDST剩余阻塞.md` |
| `references/unity-material-binding-config.json` | Unity 材质贴图属性优先级、同名候选过滤和绑定来源枚举配置 |
| `scripts/export_unitypy_real_animation_samples_to_gltf.py` | 从 UnityPy Mesh、SkinnedMeshRenderer、Transform 和 AnimationClip 导出少量真实蒙皮动作 glTF 样例；支持 `--auto` 按 skeleton path 覆盖率自动匹配 direct AnimationClip；Mecanim dense clip 写入受限预览并在 extras 标注来源 |
| `scripts/convert_gltf_models_to_fbx_blender.py` | Blender 批量把 glTF 模型转 FBX |
| `scripts/organize_unitypy_project_export.py` | 按 reference 配置两遍流式组织 UnityPy 图片/真实音频；源读取与分类目标绑定稳定 handle/fd；六个 artifact 从 stage 到 commit/rollback/cleanup 持有稳定父目录租约，所有 no-replace rename 在正常/异常返回后按 exact generation 协调并更新账本；自有文件先精确退休再以 handle/fd 删除，hardlink/copy 底层成功后包装异常只收养可证明的精确发布代；cleanup-warning summary 把成功租约交给 CLI/直接调用者，序列化或消费后显式 `close()` 才清理旧代；同内容复用、稳定冲突后缀、可回滚组事务和严格索引对账 |
| `references/animation-vfx-routing-config.json` | 动画/特效 `N/B/G/U/E/C/R` 规模、small/mid/large、并发公式、Codex 子 agent 角色、fragment 和 Klei single-producer 配置；不代表已有本地 shard/Merger CLI |
| `references/game-character-animation-viewer-workflow.md` | AssetArchive 派生的 DST 角色优先动画浏览、直接安装根启动协议，以及与旧 Bank/素材导出后端的边界 |
| `references/game-character-animation-viewer-config.json` | 角色 Viewer 版本、固定 EXE、AssetArchive 来源/候选产物、环境变量、唯一 BAT 与日志路径 |
| `references/game-animation-preview-workflow.md` | Klei/DST BILD+ANIM+atlas AnimState v1 的动态子 agent 任务图、预览/素材查看/五种导出边界及 Spec/Fixer/Quality 门禁；未知引擎另建 adapter |
| `references/game-animation-manifest-schema.json` | 当前 Klei/DST AnimState v1 的 GameAnimationViewer manifest schema；要求非空 `source_roots` 对象、安全相对路径、Bank/Build package 身份、多 atlas、候选歧义与能力状态 |
| `scripts/export_game_animation_asset.py` | 从 approved Klei AnimState manifest 与安全 asset root 导出 Viewer 请求的当前帧 PNG、PNG sequence、APNG、GIF 或 source bundle；不重新猜测未索引源文件 |
| `scripts/create_game_animation_viewer_launcher.py` | 校验 manifest `source_roots` 边界并生成唯一 `打开动画预览.bat`，把重复 asset-root、缓存、用户导出目录、Python 和 renderer 传给固定 GameAnimationViewer |
| `scripts/install_game_character_animation_viewer_tool.ps1` | 把 AssetArchive 派生构建产物和配置化 `custom-characters/` 素材安装到版本化固定目录，复制 MIT LICENSE，记录来源 commit/哈希并更新 tooling manifest |
| `scripts/create_game_character_animation_viewer_launcher.py` | 校验已安装 DST 根并生成角色优先的唯一 `打开动画预览.bat`，通过环境变量直接把游戏根交给 Viewer；不读取旧 Bank manifest |
| `scripts/launch_game_character_animation_viewer.ps1` | 启动固定角色 Viewer、记录日志，并在重复双击时按 EXE 路径前置已有窗口 |
| `references/vfx-preview-config.json` | Unity VFX 类型、关键词、预览模式和输出路径配置 |
| `scripts/build_vfx_preview_manifest.py` | 从 UnityPy 全量索引生成 VFX 配置卡、粒子贴图/VFX 材质/模型/动画分类和 `4.临时目录/中间索引/VFX预览/manifest.json` |
| `scripts/build_unity_vfx_full_index.py` | 面向大规模 Unity 项目的无根对象截断 VFX 全组件结构索引、同 GameObject 配对、依赖追溯、AssetRipper YAML 对账与完整 manifest 生成 |
| `scripts/export_unity_vfx_reconstruction.py` | 从 VFX manifest 生成 `reconstructed/` JSON，并导出可重新导入 Unity 的 `unity-export/Assets/GameAnalysisVfxPreview` Prefab、Material、metadata、bootstrap 脚本和依赖引用 |
| `scripts/export_unity_vfx_reconstruction_full.py` | 面向大规模 Unity 项目解包 `type_tree`、恢复 Renderer、完整重建 manifest，并以流式 GUID 扫描刷新全部 AssetRipper VFX YAML/Scene 依赖闭包；支持 `--supplement-only` |
| `scripts/create_vfx_viewer_launcher.py` | 在 `2.报告/` 生成 `打开特效预览.bat`，把本次 study-root、manifest、reconstructed-dir、preview-cache-dir 和 unity-export-dir 传给固定 GameVfxViewer |
| `scripts/build_vfx_viewer_tool.ps1` | 维护构建固定 `GameVfxViewer` EXE 到 `built-in-tools/GameVfxViewer_1.0.0/GameVfxViewer.exe`，只在工具缺失或升级时运行 |
| `references/vfx-unity-reconstruction-schema.json` | Unity VFX 动态还原 JSON、Prefab/Material/metadata/bootstrap 回导结构的 schema |
| `scripts/build_learning_indices.py` | 生成美术规格、命名、渲染、音频、动画、代码命名空间等学习索引 |
| `scripts/audit_study_root_hygiene.py` | 审计并可选清理临时目录中的浏览器 profile |
| `scripts/analyze_assetripper_project.py` | AssetRipper 场景/Prefab 分析；解析并对账 Build Settings，全量提取 Graphics/Quality/Player/Packages 设置锚点，可从 runtime verification 动态生成 layout mismatch 使用边界 |
| `scripts/organize_assetripper_audio_resources.py` | 按 `references/assetripper-audio-classification.json` 配置安全扫描 AssetRipper 真实音频，保持 `Assets` 相对路径归档到 `1.分类资源/音频/AssetRipper/`；拒绝路径逃逸、目录重叠与 reparse 穿越，优先硬链接并回退复制，输出源快照、确定性 manifest、CSV 与错误 JSONL |
| `references/assetripper-audio-classification.json` | AssetRipper 音频源目录、分类目录、索引目录、允许扩展名与传输方法配置 |
| `scripts/organize_assetripper_render_resources.py` | 按 `references/assetripper-render-classification.json` 的扩展名→分类映射，把 AssetRipper `Assets` 中真实 Material、Shader、RenderTexture 保持相对路径归档到 `1.分类资源/渲染/`；优先同卷硬链接，索引和对账摘要留在临时目录 |
| `scripts/build_image_gallery_manifest.py` | 生成固定图片预览客户端使用的 manifest、PNG 缩略图和错误日志 |
| `scripts/create_image_gallery_launcher.py` | 在 `2.报告/` 生成 `打开图片画廊.bat`，把本次 study-root、manifest 和 manifest `source_roots` 派生出的重复 `--images-root` 传给固定 EXE |
| `scripts/build_image_gallery_viewer_exe.ps1` | 一次性构建固定图片画廊 EXE 到 Skill `built-in-tools/GameImageGallery_1.0.0/` |
| `scripts/model_gallery_common.py` | 读取模型预览配置并解析 Skill、报告、manifest、thumbs 和 preview-cache 路径 |
| `scripts/build_unity_animation_preview_models.py` | 从 UnityPy `AnimationClip` metadata 生成可播放 glTF 动作学习预览模型；仅用于动作名/时长和程序化姿态预览，不代表完整还原原始 skin/Avatar/bone weights |
| `scripts/build_model_gallery_manifest.py` | 扫描 `1.分类资源/模型/`，生成 GameModelViewer manifest、thumbs/preview-cache 目录和模型预览错误日志，统计 glTF/GLB 的 mesh、material、skin 和 animation 信息，并按配置为服装/套装入口生成 viewer 层 `full_body_preview` 组合描述 |
| `scripts/blender_model_preview_worker.py` | Blender 后台 worker：导入模型、渲染缩略图并按需转换 FBX 预览副本 |
| `scripts/render_model_thumbnails_blender.py` | 调用 Blender 后台渲染模型缩略图，并为 FBX 生成只读 preview-cache GLB 预览副本 |
| `scripts/create_model_viewer_launcher.py` | 在 `2.报告/` 生成唯一入口 `打开模型预览.bat`，把本次 study-root 的模型目录、manifest、thumbs、preview-cache 传给固定 EXE |
| `scripts/update_model_gallery_summary.py` | 更新 `2.报告/00_研究总览.md` 的模型预览摘要，记录模型数量、失败数量、BAT 和 GameModelViewer 状态 |
| `scripts/build_model_viewer_tool.ps1` | 维护构建固定 `GameModelViewer` EXE 到 `built-in-tools/GameModelViewer_1.0.0/GameModelViewer.exe`，只在工具缺失或升级时运行 |
| `scripts/verify_model_viewer_gui.ps1` | 通过 Electron CDP 验证 GameModelViewer GUI、模型过滤、canvas、动作播放控件和可选全身组合按钮；适配中文/空格路径 |
| `scripts/run_skill_tests.ps1` | 使用 Skill 自带 Python venv 运行测试，统一处理 UnityPy、pytest 和 `PYTHONPATH` |
| `scripts/build_full_image_gallery.py` | 兼容工具：生成旧 HTML 画廊，不作为默认图片画廊流程 |
| `scripts/export_key_character_animations_unitypy.py` | 关键角色动画帧（UnityPy） |
| `scripts/export_key_character_animations_from_assetripper.py` | 关键角色动画帧（AssetRipper） |
| `scripts/setup_python_env.ps1` | Python 环境安装 |
| `scripts/export_unity_assetripper_project.ps1` | headless 启动 AssetRipper Web/API，自动 LoadFolder/LoadFile、导出 UnityProject；AssetRipper runtime log 先写 ASCII 临时目录再复制回 study-root，OpenAPI、HTTP 状态和失败响应写入 study-root 工具日志 |
| `scripts/open_assetripper.ps1` | 兼容入口：仅启动 AssetRipper Web/API |
| `scripts/export_unreal_umodel_slices.ps1` | UModel 分片导出 |
| `scripts/merge_unreal_umodel_slices.ps1` | UModel 分片合并校验 |
| `scripts/repair_umodel_gltf_materials.py` | glTF 材质修补 |
| `scripts/launch_blender_python.cmd` | Blender 安全启动 |
| `scripts/build_gallery_exe.ps1` | 兼容工具：旧 HTML 画廊 Electron 壳打包，不作为默认图片画廊流程 |
| `scripts/image_palette_report.py` | 主色报告 |
| `scripts/build_il2cpp_ghidra_targets.py` | IL2CPP `dump.cs` 批量生成 Ghidra `name,rva` targets 与分批清单 |
| `scripts/merge_ghidra_decompile_summaries.py` | 合并 Ghidra 批量反编译结果并生成覆盖率报告 |
| `scripts/guideline_lint.py` | 风险表述检查 |
