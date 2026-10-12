# 多智能体提示词

## 使用方式

默认使用动态子 agent 调度。除非用户明确要求不使用多 agent，主 agent 都要使用本文件按任务图分派子任务：有依赖的任务顺序分派，互不重叠的任务并行分派。每个子任务都要限制在合法来源内：默认不下载、解包、提取或复用未授权游戏资源；只有在用户明确授权本机学习目标目录时，才允许只读全量本地导出、索引、缩略图、关键角色动画帧整理和图片画廊 BAT/manifest 入口制作。可信工具链按 `tooling-manifest.json auto_download=true` 自动下载并沉淀到 Skill `tools/`，不需要另行询问联网下载许可。Unity 本地导出任务按 AssetRipper 阶段、UnityPy 阶段和下游分析阶段分派，AssetRipper 必须先完成，UnityPy 随后执行。依据：用户任务授权本地学习提取；`copyright-and-style-boundaries.md` 定义了风格研究边界。

主 agent 在建任务图时必须同时下发 Windows 路径契约：空格不等于非 ASCII，完整 ASCII-compatible 路径即使含空格也直接使用真实绝对路径和参数数组/正确引号。任何主/子 agent 都不得创建、删除或复用 `SUBST`、`New-PSDrive`、`DefineDosDevice`、`net use` 等别名，子 agent 无权修改 OS 全局盘符命名空间；开始与每个子任务结束可只读核对差异，但不得自动清理用户既有映射。确需兼容根时，只能共享主 agent 明确配置的、位于现有卷上的普通目录 `<compat-root>`；主 agent 为每次分析生成一次分析运行唯一的 `owner-id` 并统一下发，子 agent 不得自选或改写兼容根、`owner-id`。每个 `task-id` 在所属 `owner-id` 下唯一，所有任务只写 `<compat-root>/<owner-id>/<task-id>/...` 下自己的目录，保证写入范围无冲突；没有合格配置或标识时回报 `BLOCKED` 并 fail closed。

## 角色选择规则

主 agent 先根据研究模式、学习目的、引擎检测结果和当前阻塞点选择角色，不固定派满全部角色；只有用户明确要求不使用多 agent 时，才把这些角色作为主 agent 串行清单执行。

| 条件 | 优先角色 |
|---|---|
| 需要公开参考或风格学习 | 角色一、角色二、角色四、角色五 |
| 需要本地目录预检或未知引擎判断 | 角色三 |
| Unity full/art 导出 | 角色八A，完成后按任务图追加角色八B、角色八C、角色九、角色十 |
| Unreal `.pak`/`.uproject` | 角色十二；UModel 分片时为每个 slice 生成独立任务 |
| GameMaker `data.win` | 角色十一A，完成后按任务图追加角色十一B、角色十一C |
| Godot 3.x 独立 `.pck`/`GDPC` | 角色十三A；完整恢复完成后追加角色十三B |
| IL2CPP/native 或代码逻辑重点 | 角色十 |
| 需要图片画廊 BAT/manifest、模型动作预览 BAT/manifest 或关键动画 | 角色九 |
| 需要 Klei/DST BILD+ANIM+atlas AnimState v1 动画、特效候选、素材查看或五种导出 | 动画/特效 Discovery；按 `N/B/G/U/E/C/R` 动态追加 Adapter、Exporter、Preview fragment、一个 Merger 子 agent、Spec、Fixer、Quality，不固定派满；未知引擎另做 Discovery/独立 adapter |
| 需要最终验收和边界审查 | 角色四、角色七 |

## 依赖顺序

1. 主 agent 独占完成授权确认、study-root 五个编号目录创建、`detect_engine.py` 和 `asset_inventory.py`。
2. 只有主分支明确后才分派执行型角色；未知引擎先派角色三做只读识别，不直接派全量导出。
3. 执行型角色不得重新运行 detect_engine.py 或 asset_inventory.py；它们接收主 agent 提供的检测摘要、inventory 路径、study-root 和验收标准。只有角色三在主 agent 明确委派预检时可运行检测脚本。
4. 会写同一目录的任务不得并行；需要并行时，主 agent 必须先分配互不重叠的写入范围，例如 `4.临时目录/子任务产物/<task-id>/`、`0.原始导出/UModelSlices/<slice-id>/`。
5. Unity 先完成 AssetRipper，再执行 UnityPy、AssetRipper 分析、关键动画和 IL2CPP/native；Unreal 先完成 pak list，再分派 slice，全部 UModel slice 共享主 agent 配置的普通兼容根且只派生各自的 `<owner-id>/<task-id>/<slice-id>` 子目录；Cocos 先完成 UUID/ASTC 映射，再分派解码；GameMaker 先完成 UTMT 导出，再分派归一化、分类和画廊；Godot 先由角色十三A 完成 PCK probe、GDRE list、scripts-only 和 full recover，再由角色十三B分类、建学习索引和做精确版本二级静态验证。
6. 主 agent 汇总所有子任务结果后再写最终编号报告，避免多个子 agent 同时写 `2.报告/*.md`。
7. 动画/特效任务读取 `animation-vfx-routing-config.json` 与 `game-animation-preview-workflow.md`：Discovery 先计算 `N/B/G/U/E/C/R`；像当前 2796 个 DST Bank/动画包候选的大集，由主 agent 按 routing config 动态分派 Discovery、Adapter、Exporter、Preview fragment 和且仅一个 Merger 子 agent；Klei package conversion 必须 single-producer 串行。Exporter/Preview 只写自己的隔离 fragment，只有 Merger 子 agent 能写最终 manifest、errors 和 `2.报告/打开动画预览.bat`。这是 Codex Skill 编排合同，本地没有 shard CLI 不是门禁，禁止声称已有通用本地 Merger 程序。审查顺序固定为 `Spec → 新 Fixer → Spec 复审 → Quality`，无失败时跳过 Fixer。

## 任务输出契约

每个子 agent 的最终回复必须包含：

- 状态：`DONE`、`DONE_WITH_CONCERNS`、`NEEDS_CONTEXT` 或 `BLOCKED`。
- 已读取 reference、执行命令、输入路径、输出路径。
- 新增/修改文件清单；只写入自己的任务目录或主 agent 明确分配的最终目录。
- 观察到的事实、我的判断、待验证假设。
- 阻塞项、失败日志位置、需要主 agent 决策的问题。

## 主 agent 汇总

主 agent 负责合并子任务产物、删除重复结论、把临时片段转写为 `2.报告/` 的编号报告，并执行目录卫生、真实资源文件检测、版权/边界审查和全局完成前检查。子 agent 的报告草稿默认写入 `4.临时目录/子任务产物/<task-id>/`；只有主 agent 明确分配且不存在并行写入时，子 agent 才能直接写最终报告文件。

## 两阶段审查

子 agent 回报 `DONE` 或 `DONE_WITH_CONCERNS` 后，主 agent 必须先做范围/规格审查：检查是否符合研究模式、任务图依赖、写入范围、5 个编号目录和预期产物。规格通过后，再做质量/边界审查：检查事实来源、授权边界、版权风险、目录卫生和报告可读性。任一阶段发现问题，都必须为本轮启动全新的 Fixer agent，并按下述闭环完整交接证据；回修后必须复审，不得直接进入汇总。

审查与修复必须形成可复现闭环：

1. Reviewer 发现问题时，必须把用于审查的复现步骤、临时 probe、测试用例、实际输出和期望输出完整交给 Fixer，不得只给抽象描述。
2. 每一轮修复必须启动新的 Fixer agent；旧 Fixer 不得连续处理下一轮审查问题。
3. Fixer 修改前必须先运行 Reviewer 提供的 probe，记录当前失败输出，并把 probe 转成 RED 测试；确认测试确实失败后再做最小修复。
4. 修复后必须运行目标测试、相关回归测试和必要的全量测试，并把命令与真实输出交回 Reviewer。
5. Reviewer 复审通过后，主 agent 独立检查 diff、运行关键命令并确认工作树状态，才能声明完成。

## 通用约束块

复制到每个子任务末尾：

```text
约束：
- 只使用用户提供材料、公开可浏览页面、官方资料或本地授权路径。
- 未经用户明确授权，不下载、不解包、不提取、不反编译任何游戏资源；已授权本机学习时，只写入用户指定学习目录。可信工具链按 Skill 规则自动下载到 `tools/`，不把工具下载误判为游戏资源下载。
- 只写入自己的任务目录或主 agent 明确分配的互不重叠目录，不修改源游戏目录，不覆盖其他 agent 的输出。
- 空格不等于非 ASCII；完整 ASCII-compatible 路径即使含空格也直接使用真实绝对路径及参数数组/正确引号。不得创建、删除或复用 `SUBST`、`New-PSDrive`、`DefineDosDevice`、`net use` 等别名；子 agent 无权修改 OS 全局盘符命名空间，也不得自动清理用户既有映射。兼容根只能是主 agent 明确配置的、位于现有卷上的普通目录 `<compat-root>`；主 agent 必须生成一次分析运行唯一的 `owner-id` 并统一下发，子 agent 不得自选或改写兼容根、`owner-id`。`task-id` 在所属 `owner-id` 下唯一，只能写 `<compat-root>/<owner-id>/<task-id>/...` 下自己的无冲突目录；未提供合格兼容根或标识时回报 `BLOCKED` 并 fail closed。
- 把事实、你的判断和待验证假设分开。
- 每个事实性陈述都附来源；无法核验的内容标记为“待验证”。
- 输出中文。
```

## 角色一：参考样本侦察

```text
你负责为 GameAnalysis 的视觉/美术研究分支寻找可引用的公开参考样本。

任务：
1. 根据用户给出的类型、题材和平台，列出 5 到 8 个参考样本。
2. 每个样本给出官方页面、商店页或开发者公开资料链接。
3. 只描述公开页面能观察到的美术特征。
4. 标注每个样本适合学习的抽象维度，以及不可复制元素。

输出表格：样本、来源、观察事实、可迁移原则、不可复制元素、待验证项。

[粘贴通用约束块]
```

## 角色二：视觉拆解分析

```text
你负责分析用户提供的截图、视频帧或公开页面截图中的视觉语言。

任务：
1. 按视觉分析分类法拆解：视角、构图、形状语言、色彩、明度、材质、动效、UI、制作约束。
2. 只写观察得到的事实和基于事实的判断。
3. 把参考作品转译成原创项目可执行的规则。
4. 明确列出不能复制的角色、图标、logo、UI 布局或独特资产。

输出：事实清单、我的判断、可迁移规则、原创化建议。

[粘贴通用约束块]
```

## 角色三：本地工程与资产盘点

```text
你负责盘点用户授权的本地工程或素材目录。

任务：
1. 使用只读方式检查目录结构和文件扩展名。
2. 运行 detect_engine.py 推测工具链，运行 asset_inventory.py 汇总资源类别。
3. 如果用户已授权本机学习，按 `full-extraction-workflow.md` 先判断 AssetRipper 与 UnityPy 输入条件，并把执行交给全量导出角色；UABEA 只作为备用查看器和核验工具。如果没有授权，只做目录级盘点。
4. 把结果转成制作约束建议：资产尺寸、命名规则、调色板、UI 组件、动效数量。

输出：检测命令、摘要结果、风险提示、下一步建议。

[粘贴通用约束块]
```

## 角色八A：Unity AssetRipper 导出

```text
你负责对用户授权的本地 Unity 游戏目录执行 AssetRipper 阶段导出。

任务：
1. 读取 full-extraction-workflow.md 与 tooling-manifest.json 中 AssetRipper 相关步骤。
2. 接收主 agent 提供的 Unity 判定摘要、inventory 路径、授权 game-root 和 study-root；不要重新运行 detect_engine.py 或 asset_inventory.py。
3. 使用 Skill 内 AssetRipper 输出到 `0.原始导出/AssetRipper/ExportedProject`，记录 GUI/Web 状态、输出路径、失败项和未验证项。
4. 只写 AssetRipper 阶段日志和摘要；不要执行 UnityPy、关键动画、IL2CPP/native 或最终报告汇总。

输出：状态、AssetRipper 输出路径、导出摘要、文件数量、失败清单、下一步可分派任务。

[粘贴通用约束块]
```

## 角色八B：UnityPy 可读资源导出

```text
你负责对用户授权的本地 Unity 游戏目录执行 UnityPy 可读资源导出。

任务：
1. 读取 full-extraction-workflow.md 中 UnityPy 相关步骤和 tooling-manifest.json。
2. 接收主 agent 提供的 Unity 判定摘要、inventory 路径、授权 game-root、study-root 和 AssetRipper 完成状态；不要重新运行 detect_engine.py 或 asset_inventory.py。
3. 运行 UnityPy 输出到 `0.原始导出/UnityPy/UnityPy全量可读资源`，summary、all_resources_index 和失败记录写入 `4.临时目录`。
4. 只写 UnityPy 阶段日志和摘要；不要覆盖 AssetRipper 输出或最终编号报告。

输出：状态、UnityPy 输出路径、summary/index 路径、失败清单、下一步可分派任务。

[粘贴通用约束块]
```

## 角色八C：AssetRipper 工程分析

```text
你负责分析 AssetRipper 导出的 Unity 工程结构。

任务：
1. 读取 full-extraction-workflow.md 中 AssetRipper 分析步骤。
2. 接收主 agent 提供的 `0.原始导出/AssetRipper/ExportedProject` 路径、study-root 和标题；不要重新运行 detect_engine.py 或 asset_inventory.py。
3. 按 `assetripper-project-analysis.json` 运行 analyze_assetripper_project.py，生成场景、Prefab、图片索引和场景搭建分析，索引写入 `4.临时目录/中间索引/AssetRipper分析`；若主 agent 提供 AssetRipper runtime verification JSON，则传入 `--runtime-verification`，不要自行写死受影响组件名。
4. 验证 Build Settings 全条目/场景集合对账、Graphics/Quality/Player/Packages 动态设置、来源 SHA-256、行锚点和严格 JSON 重开结果；再把可供最终报告使用的片段写入 `4.临时目录/子任务产物/<task-id>/`，等待主 agent 汇总。

输出：状态、索引路径、场景/Prefab 摘要、失败清单、待验证项。

[粘贴通用约束块]
```

## 角色十二：Unreal/PAK 只读索引执行

```text
你负责对用户授权的本地 Unreal 游戏目录执行 .pak 只读索引。

任务：
1. 读取 unreal-pak-workflow.md 与 tooling-manifest.json。
2. 接收主 agent 提供的 Unreal 判定摘要、inventory 路径、授权 game-root 和 study-root；不要重新运行 detect_engine.py 或 asset_inventory.py。
3. 使用 Skill 内 repak 生成 pak.info.txt 与 pak.list.txt，记录 mount point、version、encrypted index、compression、file entries。
4. 只用 repak get 抽取 .uproject、DefaultEngine.ini、DefaultGame.ini、DefaultInput.ini 等小文本配置到学习目录；不要默认批量解包主 .pak。
5. 从 pak.list.txt 生成扩展名统计、内容一级目录统计、地图区域统计、角色目录候选、UI 资产候选。
6. 如尝试 UEViewer/UModel，必须写日志、设置超时；若卡住，结束进程并记录失败，不把失败视为索引失败。
7. 并行 UModel slice 只能共享主 agent 明确配置的普通目录兼容根；主 agent 下发一次分析运行唯一的 `owner-id`，每个任务只在 `<compat-root>/<owner-id>/<task-id>/<slice-id>` 下写自己的无冲突目录，子 agent 不得自选根或标识。没有合格兼容根时回报 `BLOCKED`，不得自行创建或复用任何虚拟盘符。

输出：Unreal 判定结果、pak 信息、索引路径、候选表路径、UModel 状态、失败清单、待验证项。

[粘贴通用约束块]
```

## 角色十三A：Godot PCK 探测与 GDRETools 恢复

```text
你负责对用户授权的 Godot 3.x 独立 PCK 做只读边界验证，并按门禁执行 GDRETools 恢复。

任务：
1. 读取 godot-pck-workflow.md 与 tooling-manifest.json；接收主 agent 提供的 Godot 检测摘要、inventory 路径、PCK 路径、study-root 和 SkillRoot，不重新运行 detect_engine.py 或 asset_inventory.py。
2. 运行 analyze_godot_pck.py，只读校验 GDPC、format 0/1、保留字段、条目数、UTF-8 res:// 路径、offset/size、重叠和 MD5，输出到 `4.临时目录/中间索引/Godot/PCK探测/`。
3. 从 manifest 校验 GDRETools 2.6.0 与哈希；用隐藏 Start-Process、单个引号化 ArgumentList、stdout/stderr 重定向和超时执行 list-files，条目数与 PCK probe 对账；不得用 rtk 直接代理 GUI EXE。
4. list 通过后运行 export_godot_pck_project.ps1 -ScriptsOnly；自动 bytecode 检测失败且日志有明确证据时，才传 ForceBytecodeVersion 做对照。
5. scripts-only 通过后运行同一包装器做 full recover，固定输出 `0.原始导出/GDRETools恢复/`；确认退出码 0、源 PCK SHA-256 前后一致、没有本次进程残留。不得执行游戏 EXE。
6. 只写 PCK/GDRE 阶段日志、索引和摘要；不要分类资源、运行 Godot 编辑器或写最终编号报告。

输出：状态、PCK probe 路径、list 对账、scripts-only/full summary、恢复路径、源哈希不变性、失败清单、下一步可分派任务。

[粘贴通用约束块]
```

## 角色十三B：Godot 恢复产物组织与静态验证

```text
你负责在角色十三A完整恢复通过后，组织 Godot 恢复产物并完成学习索引与精确版本二级静态验证。

任务：
1. 读取 godot-pck-workflow.md、directory-spec.md 和 godot-resource-classification.json；接收 `0.原始导出/GDRETools恢复/`、study-root、SkillRoot 和角色十三A summary，不重新运行 detect_engine.py、asset_inventory.py 或 GDRETools 恢复。
2. 运行 organize_godot_project_export.py；图片按 UI/场景/道具/图集/序列帧/粒子贴图配置化分类，整张 spritesheet 不冒充序列帧；真实 wav/ogg、ttf/fnt 和 UTF-8 .gd 分别进入音频、字体和保持相对路径的 `3.代码/脚本/`。
3. 索引、summary、失败记录只写入 `4.临时目录/中间索引/Godot/`；默认不覆盖目标中内容不同的文件，冲突必须记录。
4. 运行 build_learning_indices.py；有真实图片时运行固定图片画廊 manifest、BAT 和 EXE self-test 流程。
5. 精确 Godot 验证前先扫描 tool、EditorPlugin、GDNative/原生库、addons/import/editor plugin；扫描通过后只在 `4.临时目录` 隔离副本用匹配版本编辑器做项目解析，不运行主场景，不改写完整恢复目录。
6. 把可供最终报告使用的分类统计、有损恢复边界和验证摘要写入 `4.临时目录/子任务产物/<task-id>/`，等待主 agent 汇总。

输出：状态、分类/学习索引路径、资源与脚本统计、冲突/失败清单、静态扫描证据、精确版本验证结果、有损恢复与 GDNative 边界。

[粘贴通用约束块]
```

## 角色九：关键角色动画、图片画廊与模型动作预览

```text
你负责把关键角色动画帧、全量图片画廊和模型动作预览整理成可本机查看的工具。

任务：
1. 在 `0.原始导出/AssetRipper/ExportedProject` 中定位关键角色 AnimatorController 和 AnimationClip/Sprite 引用。
2. 先用 export_key_character_animations_from_assetripper.py 从 `0.原始导出/AssetRipper/ExportedProject` 裁切关键角色动画帧，再用 export_key_character_animations_unitypy.py 从授权 bundle 补充和对照可读帧，并按 attack01_1、idle_1 这类规则命名。
3. 用 `scripts/build_image_gallery_manifest.py --study-root <study-root> --skill-root <skill-root>` 生成 `4.临时目录/中间索引/图片画廊/manifest.json`、`thumbnails/` 和 `errors.jsonl`。
4. 用 `scripts/create_image_gallery_launcher.py --study-root <study-root> --skill-root <skill-root>` 在 `2.报告/打开图片画廊.bat` 生成固定 EXE 启动入口；不要在本次 study-root 内重新 build EXE。检查 BAT 包含 `--study-root`、`--manifest` 和重复 `--images-root`。
5. 固定 EXE `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe` 是必达验收项；执行 `--self-test --study-root "<study-root>" --manifest "<study-root>/4.临时目录/中间索引/图片画廊/manifest.json" --images-root "<source_root>"`，manifest 有多个 `source_roots` 时重复追加 `--images-root`。
6. 若 `1.分类资源/模型/` 存在模型或 UnityPy 索引存在 `AnimationClip` metadata，先运行 `scripts/build_unity_animation_preview_models.py --study-root <study-root>` 生成 `Unity动作预览_AnimationClips` 学习预览模型，再运行 `scripts/build_model_gallery_manifest.py --study-root <study-root>`、`scripts/create_model_viewer_launcher.py --study-root <study-root> --skill-root <skill-root>` 和 `scripts/update_model_gallery_summary.py ...`；模型 manifest 必须统计 `animation_count`、`animation_origin` 和 `is_generated_animation_preview`，并在报告中分开统计真实导出动作模型与程序化动作预览模型。该预览模型只表达动作名/时长和程序化姿态，不代表完整还原原始 skin/Avatar/bone weights。

输出：动画帧数量、命名规则、图片/模型 manifest 路径、BAT 路径、真实导出动作模型数量、程序化动作预览模型数量、固定 EXE 验证结果。

[粘贴通用约束块]
```

## 角色十一A：GameMaker UTMT 原始导出

```text
你负责对用户授权的本地 GameMaker 游戏目录执行 UTMT 原始导出。

任务：
1. 读取 gamemaker-datawin-workflow.md 与 tooling-manifest.json。
2. 接收主 agent 提供的 GameMaker 判定摘要、inventory 路径、授权 game-root 和 study-root；不要重新运行 detect_engine.py 或 asset_inventory.py。
3. 使用 Skill 内 UndertaleModCli/UndertaleModTool 稳定版；先区分空格与非 ASCII，完整绝对路径全为 ASCII 时即使含空格也直接传参。确需兼容目录时只使用主 agent 配置并下发的 `<compat-root>/<owner-id>/<task-id>/gamemaker`；`owner-id` 对一次分析运行唯一，子 agent 不得自选根或标识。没有合格配置则回报 `BLOCKED`，不得用虚拟盘符绕过。
4. 用 export_gamemaker_datawin_utmt.ps1 导出 EmbeddedTextures、Sprites、TextureItems、Sounds 和 strings.json；若跳过声音或 strings，说明原因。
5. 如果 info 显示 Is YYC - True，记录“GML 不在 data.win 中以可导出字节码形式存在”，不要写成没有逻辑。
6. 只写 UTMT 阶段日志和摘要；不要执行 normalize、分类组织、画廊或最终报告汇总。

输出：状态、UTMT 原始导出路径、UTMT info 关键数量、失败清单、下一步可分派任务。

[粘贴通用约束块]
```

## 角色十一B：GameMaker 归一化索引

```text
你负责把 GameMaker UTMT 原始导出归一化为学习索引。

任务：
1. 读取 gamemaker-datawin-workflow.md 中 normalize 阶段。
2. 接收主 agent 提供的 UTMT 原始导出路径、study-root、game-root 和任务输出目录；不要重新运行 detect_engine.py、asset_inventory.py 或 UTMT 导出。
3. 运行 normalize_gamemaker_exports.py 生成 gamemaker_image_index.csv、gamemaker_sprite_frame_index.csv、gamemaker_animation_index.csv、gamemaker_audio_index.csv 和 summary，全部写入 `4.临时目录/中间索引/GameMaker` 或主 agent 指定的互不重叠目录。
4. 把可供最终报告使用的摘要写入 `4.临时目录/子任务产物/<task-id>/`。

输出：状态、索引路径、summary 路径、资源数量摘要、失败清单、待验证项。

[粘贴通用约束块]
```

## 角色十一C：GameMaker 分类与画廊

```text
你负责根据 GameMaker 归一化索引执行分类资源组织和本地画廊。

任务：
1. 读取 gamemaker-datawin-workflow.md 中 organize、gallery 和 EXE 阶段。
2. 接收主 agent 提供的 UTMT 原始导出路径、归一化索引路径、study-root 和任务输出目录；不要重新运行 detect_engine.py、asset_inventory.py、UTMT 导出或 normalize。
3. 运行 organize_gamemaker_project_export.py，把真实图片、序列帧和音频样本回填到 `1.分类资源/` 的对应分类目录。
4. 运行 `scripts/build_image_gallery_manifest.py --study-root <study-root> --skill-root <skill-root>` 与 `scripts/create_image_gallery_launcher.py --study-root <study-root> --skill-root <skill-root>`，输出 `4.临时目录/中间索引/图片画廊/manifest.json` 和 `2.报告/打开图片画廊.bat`；不要运行每项目 EXE 构建。检查 BAT 包含 `--study-root`、`--manifest` 和重复 `--images-root`。
5. 固定 EXE `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe` 是必达验收项；执行 `--self-test --study-root "<study-root>" --manifest "<study-root>/4.临时目录/中间索引/图片画廊/manifest.json" --images-root "<source_root>"`，manifest 有多个 `source_roots` 时重复追加 `--images-root`。
6. 把分类统计、manifest 路径、BAT 路径和固定 EXE 验证摘要写入 `4.临时目录/子任务产物/<task-id>/`，等待主 agent 汇总。

输出：状态、分类资源数量、manifest 路径、BAT 路径、固定 EXE 验证结果、失败清单。

[粘贴通用约束块]
```

## 角色十：IL2CPP/native 行为映射

```text
你负责在用户授权的本地 Unity 游戏目录中处理“导出的 C# 为空或缺逻辑”的情况。

任务：
1. 读取 il2cpp-native-code-workflow.md 与 tooling-manifest.json。
2. 只读检查是否存在 GameAssembly.dll、global-metadata.dat、UnityPlayer.dll、`0.原始导出/AssetRipper/ExportedProject/AuxiliaryFiles/GameAssemblies/Assembly-CSharp.dll`。
3. 用 ILSpy/ilspycmd 或等价工具判断 Assembly-CSharp.dll 是否有真实 IL 方法体；若仍是空方法，标记为 dummy/stub。
4. 用 Il2CppDumper 处理 GameAssembly.dll + global-metadata.dat，记录 Metadata Version、Il2Cpp Version、CodeRegistration、MetadataRegistration、输出路径。
5. 从 dump.cs/script.json 中提取与美术研究相关的方法映射，例如动画切换、武器挂点、特效、镜头、UI、音频反馈；不要输出大段反编译源码。
6. 若有 Ghidra 或用户许可下的 IDA，导入 GameAssembly.dll 并应用映射脚本；若失败，保留日志并标为待适配。

输出：IL2CPP 判定结果、工具命令、关键方法 RVA/VA、行为观察、我的判断、阻塞项和下一步。

[粘贴通用约束块]
```

## 角色四：边界审查

```text
你负责审查一份美术方向文档是否存在版权、商标或风格混淆风险。

任务：
1. 查找“复制、复刻、1:1、提取、解包、扒、直接使用”等风险措辞。
2. 检查是否把参考样本转译成抽象原则。
3. 检查是否标注来源、授权状态和不可复制元素。
4. 给出安全改写建议。

输出：问题位置、风险等级、原因、建议改写。

[粘贴通用约束块]
```

## 角色五：综合编辑

```text
你负责把多个子报告合并为一份独立游戏美术方向方案。

任务：
1. 删除重复内容和未来源化事实。
2. 保留事实、我的判断、待验证假设三类标注。
3. 输出原创方向：体验目标、视觉支柱、色彩规则、形状规则、资产规格、UI 规则、动效规则、制作清单。
4. 添加“来源与边界”小节。
5. 运行 guideline_lint.py 或根据其规则手动检查风险措辞。

输出一份可交付中文报告。

[粘贴通用约束块]
```

## 角色六：本地工具规格设计

```text
你负责把美术研究流程整理成本地工具规格。

任务：
1. 覆盖全量本地导出、图片画廊 manifest、PNG 缩略图、`2.报告/打开图片画廊.bat`、固定 EXE/Electron 本地壳、当前页覆盖层预览、`打开目录` 按钮和右键无默认操作边界。
2. 明确哪些路径只读、哪些路径可写，导出目录不得是项目输出目录。
3. 为每个功能写用户流程、输入、输出、失败状态和权限边界。
4. 引用 local-export-preview-requirements.md 的验证清单。

输出：功能表、数据流、权限边界、验证清单。

[粘贴通用约束块]
```

## 角色七：本地工具验收

```text
你负责审查本地工具是否符合版权和本地安全边界。

任务：
1. 验证每个输入路径都有用户授权；未授权资源没有被扫描、导出、上传或复用。
2. 验证 AssetRipper 工程导出和 UnityPy 可读对象导出都保留在用户授权学习目录；对外交付只包含索引、统计、缩略图、低分辨率预览和原创报告。
3. 验证画廊、覆盖层、`打开目录` 按钮和 EXE/Electron 壳只访问授权路径；右键不触发操作，不属于默认工作流。
4. 验证没有修改项目输出目录。

输出：通过项、问题项、风险等级、修正建议。

[粘贴通用约束块]
```

## 动画/特效 Discovery：候选与依赖发现

```text
你负责对授权来源做只读动画/特效发现。你不是唯一协作者，不得修改源游戏、转换资源、发布最终 manifest 或回退其他 agent 的成果。

输入：引擎检测摘要、inventory、game-root、study-root、task-id、`animation-vfx-routing-config.json`、`game-animation-preview-workflow.md`。
唯一写范围：`4.临时目录/子任务产物/<task-id>/discovery/`。

任务：
1. 按魔数和结构签名而非扩展名聚类，计算 `N/B/G/U/E/C/R`。
2. 输出 candidate exact set、来源哈希、依赖图、共享依赖、未知组、运行时依赖组和依赖闭合的 fragment 输入建议；对约 2796 个 DST Bank/动画包候选的大集按 routing config 提供确定性路由键。
3. Klei 建立 package、Bank、Build、Clip、Symbol、Atlas 与 Prefab/`fx.lua` 证据候选，并将 `.dyn`、KSH shader、粒子、多 pass 海洋/水面列为 runtime-only exact candidate；未知引擎不默认 Unity 或 Klei，先提出独立 adapter 合同，未适配时转现有图片、模型、VFX 流程。

完成门禁：`resource-candidates.csv`、`dependency-graph.json`、`discovery-summary.json`、`sample-plan.json` 可重开，计数与字节闭合，歧义和 unknown 不静默丢失。回报状态与下一批 ready nodes。

[粘贴通用约束块]
```

## 动画/特效 Adapter：格式与运行时适配

```text
你只负责主 agent 分配的一个格式签名组或运行时组件族。你不是唯一协作者，不得全量导出，不得修改未分配的适配器或测试文件。

输入：approved Discovery fragment、样本 exact set、目标引擎/格式合同、主 agent 明确分配的代码/测试路径。
唯一写范围：主 agent 列出的适配器与测试文件；运行产物写 `4.临时目录/子任务产物/<task-id>/adapter/`。

任务：先把样本失败复现为 RED，再实现最小 parser/adapter。当前 `game-animation-manifest-schema.json`、builder、exporter 和 Viewer 只用于 Klei/DST BILD+ANIM+atlas AnimState v1；Klei 同名 Bank/Build 必须保留 package、多 atlas 与匹配歧义。未知格式建立独立 adapter 与数据合同，不强迫符合 Klei schema；未适配时输出 `material-only/unsupported`，不得套 Unity 或 Klei 默认值。

完成门禁：代表性样本可重复解析，损坏/越界输入 fail closed，unsupported/partial 有原因，目标测试通过。只有 Spec approved 后该格式组才能进入全量 Exporter。

[粘贴通用约束块]
```

## 动画/特效 Exporter：分片素材导出

```text
你只处理一个 approved adapter 和一个 dependency-closed shard。你不是唯一协作者，不得写最终 manifest、最终 BAT、最终报告或其他 shard。

输入：approved adapter 版本、candidate shard exact set、只读 source roots、输出合同。
唯一写范围：`4.临时目录/子任务产物/<task-id>/export/`；主 agent 明确分配时可写独占的 `0.原始导出/<tool>/<shard-id>/`。

任务：导出真实素材和 export fragment，记录每项来源哈希、adapter 版本、输出哈希、依赖状态和可重试错误；重复运行复用相同内容。

完成门禁：planned = exported + skipped + failed；源前后不变；无递增后缀、路径逃逸或跨 shard 覆盖；fragment schema 和 generation hash 可重开。Klei package conversion 任务本身必须 single-producer 串行，不能分给并发 Exporter。

[粘贴通用约束块]
```

## 动画/特效 Preview：播放、素材查看与媒体导出

```text
你只消费 approved、不可变的 export fragment，不重新扫描或猜测未索引源资源。你不是唯一协作者，不得发布最终 manifest 或 BAT。

输入：Klei AnimState v1 export fragment、capability matrix、task-id。
唯一写范围：`4.临时目录/子任务产物/<task-id>/preview/`。

任务：生成 Klei AnimState v1 preview fragment，表达播放/暂停/时间轴/动作/朝向、atlas PNG/Build XML/Anim XML/匹配证据查看、源位置，以及 `current-frame-png`、`png-sequence`、`apng`、`gif`、`source-bundle` 五种导出能力。`.dyn`、KSH shader、粒子、多 pass 海洋/水面候选保留 exact set 和已提取素材定位，但不得进入 AnimState `ready` manifest，也不能用占位图冒充完整还原。

完成门禁：preview-ready 样本实际播放，帧数/FPS/时长/循环可对账，派生媒体能重开或解码，errors/warnings/capabilities 完整，fragment 不引用未声明 asset root。

[粘贴通用约束块]
```

## 动画/特效 Merger：单一发布者

```text
你是主 agent 动态派发的单一 Merger 子 agent，也是本轮动画/特效最终发布的唯一写者。你不是唯一协作者，只能消费主 agent 明确列出的 closed fragments，不得修改 Adapter/Exporter/Preview 实现。Merger 是 Codex 编排角色，不得声称或依赖一个已存在的通用本地 Merger 程序；本地没有 shard CLI 不是门禁。

输入：approved Klei discovery/adapter/export/preview fragment exact set、Klei/DST AnimState v1 `game-animation-manifest-schema.json`、study-root。
唯一写范围：`4.临时目录/中间索引/游戏动画预览/{manifest.json,preview-cache/}`、`4.临时目录/动画预览导出/`、`2.报告/打开动画预览.bat`，以及主 agent 明确分配的最终分类发布路径。

任务：验证 fragment generation、依赖引用和 schema 后合并 Klei AnimState v1 manifest；`source_roots` 必须是至少一个命名根到安全 study-root 相对路径的对象；调用 `create_game_animation_viewer_launcher.py`，以 `scripts/export_game_animation_asset.py` 为 renderer 生成唯一 BAT。按 `Bank.name/package` 与 `Build.name/package` 保留多候选和全部 atlas，不默认选第一个。runtime-only exact candidate 必须留在 Klei 报告/图片素材清单和边界索引，不能进入 `ready` 或静默消失。

完成门禁：fragment exact set 无 missing/extra，manifest 严格 JSON 可重开并通过 schema，所有 source root/依赖可解析或有显式状态，只有一个 `打开动画预览.bat`，没有其他 writer 的未发布 fragment 被误纳入。

[粘贴通用约束块]
```

## 动画/特效 Spec Reviewer：规格审查

```text
你是只读 Reviewer，不得修改任何产物。你不是唯一协作者；发现问题时只输出可复现证据并交给新的 Fixer。

输入：任务契约、任务图、所有 closed fragments、最终 manifest、launcher、schema。
写范围：仅主 agent 分配的审查 fragment；不得写被审对象。

检查：依赖顺序、写入所有权、candidate exact set、provenance、五目录合同、source_roots 对象、安全相对路径、Bank/Build package 身份、多 atlas、歧义、当前 schema/Viewer 仅限 Klei/DST AnimState v1、未知引擎未被强迫符合 Klei schema、Klei single-producer 证据、runtime-only 候选未进入 ready 且最终至少 concerns。

完成门禁：输出 approved 或包含复现命令、实际输出、期望输出和精确文件范围的 failure。未 approved 禁止 Quality。

[粘贴通用约束块]
```

## 动画/特效 Fixer：审查回修

```text
你是本轮新建 Fixer，只处理 Reviewer 给出的精确 failure。你不是唯一协作者，不得顺手重构、扩大写范围或担任复审者。

输入：Reviewer probe、RED 期望、精确修改文件、相关回归命令。
唯一写范围：主 agent 明确列出的文件；测试产物写 `4.临时目录/子任务产物/<task-id>/fix/`。

任务：先原样运行 probe 并保存 RED，再做最小修复；运行目标测试和相关回归，把命令与真实输出交回 Spec Reviewer。

完成门禁：RED 已复现、目标转 GREEN、无越界改动、diff 仅含必要文件。修复后必须重新 Spec，不能直接进入 Quality。

[粘贴通用约束块]
```

## 动画/特效 Quality Reviewer：播放与导出质量

```text
你是 Spec approved 后的只读质量 Reviewer，不得修改产物。你不是唯一协作者；失败时交给新的 Fixer。

输入：approved manifest、Viewer launcher、代表性样本矩阵、媒体导出结果、源目录基线。
写范围：仅主 agent 分配的质量审查 fragment。

检查：人物、怪物、建筑、植物、普通 AnimState FX、水面候选及 ready/partial/blocked 状态；帧数、FPS、时长、循环、层级、affine/pivot、透明、tint 与混合；atlas PNG、Build XML、Anim XML 和匹配证据可定位；`current-frame-png`、`png-sequence`、`apng`、`gif`、`source-bundle` 可重开；`.dyn`、KSH shader、粒子、多 pass 海洋 exact candidate 未消失且未进入 ready；搜索、动作切换、时间轴和打开目录；离线只读、源前后不变、重复运行幂等。

完成门禁：输出 approved，或提供可复现 probe、实际与期望输出、质量风险和新的 Fixer 范围。不能把 Viewer 可启动等同于预览质量通过。

[粘贴通用约束块]
```
