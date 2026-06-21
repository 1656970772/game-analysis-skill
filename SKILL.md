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

## 动态子 agent 调度

默认使用动态子 agent 调度。除非用户明确要求不使用多 agent，主 agent 必须先建立任务图，再按依赖顺序动态分派子 agent；有依赖的任务顺序分派，互不重叠的任务并行分派。参考 `superpowers:subagent-driven-development` 的做法：每个独立任务使用新子 agent，完成后先做范围/规格审查，再做质量/边界审查。若当前平台确实没有子 agent 工具，记录为能力降级，并按同一任务图由主 agent 串行执行。

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
- Unreal：pak info/list 完成后，可把 UModel slice manifest 拆给多个子 agent；每个子 agent 只写自己的 slice 输出目录，最后由主 agent 合并。
- GameMaker：UTMT 原始导出完成后，再分派 normalize、分类组织、画廊和报告。
- Cocos：cc.config/UUID/ASTC 映射完成后，再按 bundle 或语义目录分派解码与回填。

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

**禁止创建上述 5 个目录以外的任何目录**，包括但不限于：`__ascii_work`、`0.完整资源导出目录`、`1.最终产出`、`2.主角资源`、`3.代码导出`、`主角资源`、`交付状态清单.md`。如果工具需要 ASCII-only 暂存路径，使用 `4.临时目录/_ascii_work/`。

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

**模型标准**：每个模型文件夹必须能直接导入 Blender 且正常显示模型和贴图。FBX/glTF + 对应贴图放在一起。

**3.代码/** 只包含代码文件，不包含其他资源：

```text
3.代码/
  脚本/            # GML、Lua、JS、Python、GDScript 等可读脚本
  IL导出/          # ILSpy/ilspycmd 导出的 C# 反编译结果
  IL2CPP_Dump/     # dump.cs、script.json、il2cpp.h
  Native映射/      # Ghidra/IDA 地址映射和行为摘要
  代码可读性判定.md
```

**2.报告/** 只放学习者阅读产物：编号报告、HTML 画廊、可选 PDF。CSV/JSON、验证日志、manifest、分类摘要统一写入 `4.临时目录`；空目录不放 `_empty_reason.md`，缺失原因写入 `2.报告/00_研究总览.md` 和临时验证日志。需要图片画廊时，HTML 入口文件命名为 `全量图片画廊.html` 并直接放在本目录：

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
   | Cocos Creator APK/`.astc` | `references/branch-completion-checks.md` → Cocos 节 |
   | 以上都不是 | `references/branch-completion-checks.md` → 未知引擎节 |

4. 根据研究模式执行对应范围：`art` 只执行资源导出和分类，`code` 只执行代码提取，`full` 全部执行；默认按任务图分派子 agent，依赖任务顺序分派，互不重叠任务并行分派。
5. 工具原始输出写入 `0.原始导出/<工具名>/`，中间文件写入 `4.临时目录/`。
6. 运行 `build_learning_indices.py` 汇总学习索引到 `4.临时目录/中间索引/学习输出/`，再据此撰写编号报告。
7. 运行 `audit_study_root_hygiene.py` 检查 `_ascii_work` 临时目录；需要交付清理时显式加 `--clean-browser-profiles`。
8. 汇总所有子 agent 产物，完成规格审查、质量/边界审查和全局完成前检查；每个分支遇到阻塞时记录到 `2.报告/00_研究总览.md` 的阻塞项节。

详细命令参考 `references/full-extraction-workflow.md`。

## 未知引擎与自扩展

遇到本 Skill 未覆盖的引擎/格式时：

1. 只读识别：文件树、扩展名、魔数、manifest、版本线索。
2. 查找可信工具：优先官方/开源/长期维护工具；找到可用工具后自动下载到 Skill 的 `tools/`，记录来源、许可证、哈希和版本输出。
3. 若无现成工具，编写或接入可审计解析流程，并沉淀到 `scripts/` 或 `references/`。
4. 小样本验证后再全量执行；不要停在“已识别但未转换”。
5. 若来源/许可证/安全边界或格式复杂度确实无法继续，写入阻塞项、已尝试路线和下一步。

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
- [ ] study-root 下有且只有 `0.原始导出/`、`1.分类资源/`、`2.报告/`、`3.代码/`、`4.临时目录/`，无其他目录或散落文件
- [ ] `0.原始导出/` 有工具全量导出的完整原始结构
- [ ] `1.分类资源/图片/` 里只有图片文件（PNG/JPG/TGA/PSD），无 CSV/JSON/MD
- [ ] `1.分类资源/模型/` 里每个子文件夹能直接导入 Blender 并正常显示模型+贴图
- [ ] `1.分类资源/特效/` 按粒子贴图/VFX材质/VFX模型/VFX动画/配置分类
- [ ] `1.分类资源/音频/` 只放真实音频样本，AudioClip metadata 留在 `4.临时目录/中间索引/学习输出/音频资源索引.csv`
- [ ] `1.分类资源/渲染/` 只放可学习渲染资源；shader/material 索引写入临时目录
- [ ] `3.代码/` 里只有代码文件，无图片/模型/索引（`code` 和 `full` 模式时）
- [ ] `2.报告/00_研究总览.md` 存在且记录了引擎、工具、导出统计、阻塞项
- [ ] `2.报告/` 没有 CSV、JSON、验证日志、manifest 或分类摘要
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
| `references/gamemaker-datawin-workflow.md` | GameMaker `data.win` 导出 |
| `references/cocos-creator-astc-workflow.md` | Cocos Creator APK、`.astc` 解码 |
| `references/unreal-pak-workflow.md` | Unreal `.pak` 分析和导出 |
| `references/klei-dst-workflow.md` | Don't Starve Together / Klei 自研包体识别、Lua 导出、KTEX/DYN/FMOD 阻塞记录和后续工具链 |
| `references/il2cpp-native-code-workflow.md` | IL2CPP/native 代码分析 |
| `references/local-export-preview-requirements.md` | HTML 画廊、EXE 壳 |
| `references/copyright-and-style-boundaries.md` | 学习边界、授权边界 |
| `references/visual-analysis-taxonomy.md` | 色彩、构图、材质分析 |
| `references/multi-agent-prompts.md` | 动态子 agent 角色选择、提示词和输出契约 |
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
| `scripts/export_gamemaker_datawin_utmt.ps1` | GameMaker `data.win` 导出 |
| `scripts/normalize_gamemaker_exports.py` | UTMT 导出归一化索引 |
| `scripts/organize_gamemaker_project_export.py` | GameMaker 分类资源组织 |
| `scripts/cocos_creator_astc_repair.py` | Cocos Creator ASTC 修复 |
| `scripts/extract_all_readable_resources.py` | UnityPy 可读资源导出 |
| `scripts/export_unitypy_meshes_to_gltf.py` | UnityPy Mesh 转 glTF 模型导出 |
| `scripts/convert_gltf_models_to_fbx_blender.py` | Blender 批量把 glTF 模型转 FBX |
| `scripts/organize_unitypy_project_export.py` | UnityPy 分类资源组织 |
| `scripts/build_learning_indices.py` | 生成美术规格、命名、渲染、音频、动画、代码命名空间等学习索引 |
| `scripts/audit_study_root_hygiene.py` | 审计并可选清理临时目录中的浏览器 profile |
| `scripts/analyze_assetripper_project.py` | AssetRipper 场景/Prefab 分析 |
| `scripts/build_full_image_gallery.py` | HTML 画廊生成 |
| `scripts/export_key_character_animations_unitypy.py` | 关键角色动画帧（UnityPy） |
| `scripts/export_key_character_animations_from_assetripper.py` | 关键角色动画帧（AssetRipper） |
| `scripts/setup_python_env.ps1` | Python 环境安装 |
| `scripts/open_assetripper.ps1` | 启动 AssetRipper Web/API |
| `scripts/export_unreal_umodel_slices.ps1` | UModel 分片导出 |
| `scripts/merge_unreal_umodel_slices.ps1` | UModel 分片合并校验 |
| `scripts/repair_umodel_gltf_materials.py` | glTF 材质修补 |
| `scripts/launch_blender_python.cmd` | Blender 安全启动 |
| `scripts/build_gallery_exe.ps1` | EXE 画廊打包 |
| `scripts/image_palette_report.py` | 主色报告 |
| `scripts/build_il2cpp_ghidra_targets.py` | IL2CPP `dump.cs` 批量生成 Ghidra `name,rva` targets 与分批清单 |
| `scripts/merge_ghidra_decompile_summaries.py` | 合并 Ghidra 批量反编译结果并生成覆盖率报告 |
| `scripts/guideline_lint.py` | 风险表述检查 |
