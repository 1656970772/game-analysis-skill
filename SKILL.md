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
- `GameModelViewer` 是 Skill `built-in-tools` 中维护的固定 EXE。使用前先读取 `references/tooling-manifest.json`，查找 `GameModelViewer.local_path` 并检查文件存在；只有工具缺失或升级时才运行 `scripts/build_model_viewer_tool.ps1` 构建到 `built-in-tools/GameModelViewer_1.0.0/GameModelViewer.exe`。不要在每次分析中重新 build，也不要复制 EXE 到 study-root；每个输出项目只在 `2.报告/` 生成一个模型预览 BAT 入口：`打开模型预览.bat`，由 BAT 传入本次项目的模型目录、manifest、thumbs 和 preview-cache。
- GameModelViewer GUI 验证默认运行 `scripts/verify_model_viewer_gui.ps1 -StudyRoot "<study-root>" -SkillRoot "<skill>"`。该脚本用 Electron CDP 直连检查模型列表、canvas 和动作控件，并用单个已引号化 ArgumentList 启动 EXE，适配中文路径和空格路径；需要验证服装/套装入口的 viewer 层全身组合按钮时追加 `-ExpectFullBodyPreview`。
- `GameVfxViewer` 是 Skill `built-in-tools` 中维护的固定 EXE。使用前先读取 `references/tooling-manifest.json`，查找 `GameVfxViewer.local_path` 并检查 `built-in-tools/GameVfxViewer_1.0.0/GameVfxViewer.exe` 是否存在；只有工具缺失或升级时才运行 `scripts/build_vfx_viewer_tool.ps1` 构建或替换。可通过 `GAME_ANALYSIS_VFX_VIEWER` 指向固定工具 EXE，或通过 `GAME_ANALYSIS_SKILL_ROOT` 指向包含 `built-in-tools/GameVfxViewer_1.0.0/GameVfxViewer.exe` 的 Skill 根目录。不要在每次分析中重新 build，也不要复制 EXE 到 study-root；每个输出项目只在 `2.报告/` 生成 `打开特效预览.bat`，由 BAT 传入本次项目的 study-root、manifest、reconstructed-dir、preview-cache-dir 和 unity-export-dir。

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

默认使用动态子 agent 调度。除非用户明确要求不使用多 agent，主 agent 必须先建立任务图，再按依赖顺序动态分派子 agent；有依赖的任务顺序分派，互不重叠的任务并行分派。若用户点名 `subagent-excute`，或任务需要 Maker/Reviewer 分离、可交付物反复审查修正，主 agent 必须把 `subagent-excute` 作为编排契约；`superpowers:subagent-driven-development` 只作为实现辅助参考。

`subagent-excute` 门禁必须保留：先写任务契约；建立带互不重叠写入范围的任务图；由 Maker/Fixer 产出或修正 artifacts；先做 Spec Review，通过后才能做 Quality Review；任何 review failure 都转成 Fixer 工作；修正后重新审查，直到 approved 或 genuinely blocked。若当前平台确实没有子 agent 工具，记录为能力降级，并按同一任务图由主 agent 执行带标签的串行 Maker/Fixer/Spec Review/Quality Review passes。

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

**模型标准**：每个模型文件夹必须能直接导入 Blender 且正常显示模型和贴图。FBX/glTF + 对应贴图放在一起。UnityPy 导出的 glTF 不能只放几何和外部贴图候选；必须在 glTF JSON 中写入 `images/textures/materials/baseColorTexture` 或在索引中明确无贴图原因，详见 `references/unity-model-material-binding-workflow.md`。

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
   | Cocos Creator APK/`.astc` | `references/branch-completion-checks.md` → Cocos 节 |
   | MonoGame/FNA/XNA `.xnb` | `references/branch-completion-checks.md` → MonoGame/FNA/XNA `.xnb` 节；同时读取 `references/monogame-xnb-workflow.md` |
   | 以上都不是 | `references/branch-completion-checks.md` → 未知引擎节 |

4. 根据研究模式执行对应范围：`art` 只执行资源导出和分类，`code` 只执行代码提取，`full` 全部执行；默认按任务图分派子 agent，依赖任务顺序分派，互不重叠任务并行分派。
5. 工具原始输出写入 `0.原始导出/<工具名>/`，中间文件写入 `4.临时目录/`。
6. 资源分类完成后，如果 `1.分类资源/图片` 已有图片，或用户/分支要求图片画廊，先运行 `scripts/build_image_gallery_manifest.py --study-root <study-root> --skill-root <skill-root>`，再运行 `scripts/create_image_gallery_launcher.py --study-root <study-root> --skill-root <skill-root>`。默认输出 `<study-root>/2.报告/打开图片画廊.bat`；manifest、缩略图缓存、错误日志固定为 `<study-root>/4.临时目录/中间索引/图片画廊/manifest.json`、`thumbnails/`、`errors.jsonl`。BAT 必须调用固定 EXE `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe`，传入 `--study-root`、`--manifest`，并按 manifest `source_roots` 重复传入 `--images-root`。旧 HTML 画廊和每项目 EXE 构建仅作兼容历史输出，不是默认流程。
7. 模型导出/分类后，若 UnityPy 原始索引中存在 `SkinnedMeshRenderer` 与 `AnimationClip`，先运行 `scripts/export_unitypy_real_animation_samples_to_gltf.py --study-root "<study-root>" --source "<game-root>" --auto --max-samples 1`，自动用 direct Transform 曲线与 skeleton path 覆盖率挑选可播放真实蒙皮动作样例；无法匹配时把失败原因写入 `4.临时目录/中间索引/UnityPy真实动作导出/`，不得硬编码 path id。随后如果 UnityPy 原始索引中存在 `AnimationClip` metadata，再运行 `scripts/build_unity_animation_preview_models.py --study-root "<study-root>"`，在 `1.分类资源/模型/Unity动作预览_AnimationClips/` 生成一个可播放 glTF 动作学习预览模型，并把摘要写入 `4.临时目录/中间索引/模型预览/unity-animation-preview-summary.json`；该模型只保留动作名和时长并生成程序化预览姿态，不等同于完整还原原始 Unity skin、Avatar、bone weights 或 AnimationClip 曲线。若 Candide/Romestead `.cdx/.cmx` 等文件中存在 BinaryWriter `i-mesh`/`IndexedMesh` 内嵌网格，运行 `scripts/export_candide_cdx_meshes_to_gltf.py --study-root "<study-root>" --source "<game-root>" --scan-all-embedded-meshes`，把车、轮子、建筑、机关、doodad、地图/POI 场景等不在 `Content\mesh\*.xnb` 中的内嵌网格转为 glTF，并从 `media/textures`、`media/sprites` 等资源路径绑定已导出的贴图；用户只要求特定对象时可加 `--include-regex` 缩小范围。随后如果 `1.分类资源/模型/` 下存在 `.gltf`、`.glb` 或 `.fbx`，运行模型预览链：先运行 `scripts/build_model_gallery_manifest.py --study-root "<study-root>"` 生成 `4.临时目录/中间索引/模型预览/manifest.json`、`thumbs/`、`preview-cache/` 和 `errors.jsonl`，manifest 必须解析 glTF/GLB 的 `animations`、`skins`、`meshes` 和 `materials` 统计，并为每个模型目录写入 `metadata.json`、`export-log.json`、`dependencies.json` 和 `missing-resources.json`；再用 `Get-Command blender -ErrorAction SilentlyContinue` 检测 Blender，检测到时运行 `scripts/render_model_thumbnails_blender.py --manifest "<study-root>\4.临时目录\中间索引\模型预览\manifest.json" --blender "<blender.exe>"`。模型数量大、执行时间长或渲染耗时不可作为跳过导出、跳过贴图绑定、跳过 Blender 缩略图渲染或用占位图冒充完成的理由；必须继续执行，必要时拆成可续跑批次并记录进度、失败项和下一批命令。占位缩略图只能作为执行中的临时标记，不能作为最终交付状态；若确实因工具缺失、崩溃或磁盘空间等外部阻塞无法完成，必须记录阻塞证据并标为 `DONE_WITH_CONCERNS` 或 `BLOCKED`。未检测到 Blender 时运行 `scripts/build_model_gallery_manifest.py --study-root "<study-root>" --record-blender-missing` 追加 `stage="thumbnail"`、`status="skipped"`、`reason="blender_not_found"`，再继续。随后无论 Blender 是否可用，都运行 `scripts/create_model_viewer_launcher.py --study-root "<study-root>" --skill-root "<skill>"` 生成唯一入口 `2.报告/打开模型预览.bat`，不得额外生成重复模型预览 BAT，再运行 `scripts/update_model_gallery_summary.py --study-root "<study-root>" --manifest "<study-root>\4.临时目录\中间索引\模型预览\manifest.json" --viewer-exe "<skill>\built-in-tools\GameModelViewer_1.0.0\GameModelViewer.exe"` 更新研究总览模型预览摘要。固定 EXE 始终留在 Skill `built-in-tools`，不复制到 study-root。
8. 若 UnityPy 原始索引中存在 `ParticleSystem`、`ParticleSystemRenderer`、`TrailRenderer`、`LineRenderer` 或 VFX 命名材质/贴图/动画，运行 Unity 动态特效预览链：先运行 `scripts/build_vfx_preview_manifest.py --study-root "<study-root>"`，把粒子贴图等真实资源分类到 `1.分类资源/特效/`，把配置/metadata 卡写入 `1.分类资源/特效/配置/`，并生成 `4.临时目录/中间索引/VFX预览/manifest.json` 和 `errors.jsonl`；再运行 `scripts/export_unity_vfx_reconstruction.py --study-root "<study-root>"`，生成 `4.临时目录/中间索引/VFX预览/reconstructed/` 下的 reconstructed JSON，并导出可重新导入 Unity 的项目相对目录 `4.临时目录/中间索引/VFX预览/unity-export/Assets/GameAnalysisVfxPreview`，其中包含 Prefab、Material、metadata、bootstrap 脚本目录和可解析的贴图/mesh/animation 引用。动态还原、状态、warnings/errors、reconstructed 和 unity-export 留在 `4.临时目录/中间索引/VFX预览/`；最后运行 `scripts/create_vfx_viewer_launcher.py --study-root "<study-root>" --skill-root "<skill>"` 生成 `2.报告/打开特效预览.bat`。动态还原是 best-effort：只覆盖 ParticleSystem、TrailRenderer、LineRenderer 的基础模块和可解析依赖；失败或部分还原必须写入 manifest item 的 `preview_status`、`reconstruction_status`、`unity_export_status`、`errors` 或 `warnings`，不得假装完整还原 Unity runtime。
9. 运行 `scripts/build_learning_indices.py` 汇总学习索引到 `4.临时目录/中间索引/学习输出/`，再据此撰写编号报告。
10. 运行 `audit_study_root_hygiene.py` 检查 `_ascii_work` 临时目录和 study-root 合同；需要交付清理时显式加 `--clean-browser-profiles`。
11. 汇总所有子 agent 产物，完成规格审查、质量/边界审查和全局完成前检查；每个分支遇到阻塞时记录到 `2.报告/00_研究总览.md` 的阻塞项节。

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
- [ ] `1.分类资源/模型/` 里每个子文件夹能直接导入 Blender 并正常显示模型+贴图；可恢复贴图的 UnityPy-derived glTF 已抽样同时验证 `images/textures/materials/pbrMetallicRoughness.baseColorTexture`、primitive `material`、相对贴图 URI 文件存在，以及 Blender CLI 中 Image Texture Color -> Principled BSDF Base Color 连接；`same_name_candidate` 仅标为待复核，不计为已验证材质
- [ ] `1.分类资源/特效/` 按粒子贴图/VFX材质/VFX模型/VFX动画/配置分类
- [ ] 若检测到 Unity VFX，`4.临时目录/中间索引/VFX预览/manifest.json`、`errors.jsonl`、`reconstructed/`、`unity-export/` 和 `2.报告/打开特效预览.bat` 均已生成；`unity-export/Assets/GameAnalysisVfxPreview` 是可重新导入 Unity 的项目相对目录，包含 Prefab、Material、metadata、bootstrap 脚本和可解析依赖引用
- [ ] VFX 动态还原按 best-effort 标注：ParticleSystem、TrailRenderer、LineRenderer 基础模块和依赖贴图/材质/mesh/animation 可解析时才标为 ready/full；失败或部分还原写入 `preview_status`、`reconstruction_status`、`unity_export_status`、`errors` 或 `warnings`，不得声称完整还原 Unity runtime 粒子模拟
- [ ] `1.分类资源/音频/` 只放真实音频样本，AudioClip metadata 留在 `4.临时目录/中间索引/学习输出/音频资源索引.csv`
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
| `references/unity-model-material-binding-workflow.md` | UnityPy Mesh glTF 材质贴图绑定与 Blender 验证 |
| `references/model-viewer-gallery-config.json` | GameModelViewer 固定 EXE、BAT、manifest、thumbs 和 preview-cache 配置 |
| `references/vfx-preview-config.json` | Unity VFX 类型、关键词、状态、路径和固定 GameVfxViewer 配置 |
| `references/vfx-unity-reconstruction-schema.json` | Unity VFX reconstructed JSON 与 Unity 回导目录结构校验 |
| `references/gamemaker-datawin-workflow.md` | GameMaker `data.win` 导出 |
| `references/cocos-creator-astc-workflow.md` | Cocos Creator APK、`.astc` 解码 |
| `references/unreal-pak-workflow.md` | Unreal `.pak` 分析和导出 |
| `references/monogame-xnb-workflow.md` | MonoGame/FNA/XNA `.xnb` reader 扫描、Texture2D 导出、ModelReader 转换/阻塞门禁、Tiled/FMOD 索引 |
| `references/klei-dst-workflow.md` | Don't Starve Together / Klei 自研包体识别、Lua 导出、KTEX/DYN/FMOD 阻塞记录和后续工具链 |
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
| `scripts/export_gamemaker_datawin_utmt.ps1` | GameMaker `data.win` 导出 |
| `scripts/normalize_gamemaker_exports.py` | UTMT 导出归一化索引 |
| `scripts/organize_gamemaker_project_export.py` | GameMaker 分类资源组织 |
| `scripts/cocos_creator_astc_repair.py` | Cocos Creator ASTC 修复 |
| `scripts/extract_all_readable_resources.py` | UnityPy 可读资源导出 |
| `scripts/export_unitypy_meshes_to_gltf.py` | UnityPy Mesh 转 glTF 模型导出，并尽量写入 `_MainTex`/`baseColorTexture` 材质贴图绑定 |
| `scripts/export_candide_cdx_meshes_to_gltf.py` | 从 Candide/Romestead `.cdx/.cmx` 等文件中扫描 BinaryWriter `i-mesh`/内嵌 `IndexedMesh`，导出 glTF 并绑定已导出的贴图，补足不在 `Content/mesh/*.xnb` 中的车、轮子、建筑、机关、doodad 和地图/POI 场景网格 |
| `scripts/export_klei_dst_project.py` | 对 Don't Starve Together / Klei 自研包体做本机学习导出：解包 ZIP、导出 Lua、复制 FMOD 音频 bank、索引 KTEX/DYN/动画包 |
| `scripts/convert_klei_dst_resources.py` | 在 Klei/DST 原始导出后执行资源转换：Stex 批量 KTEX->PNG、DstAnimTool 批量 anim/build->XML、python-fsb5/vgmstream 批量 FSB->OGG/WAV，并写入转换索引与失败记录 |
| `references/unity-material-binding-config.json` | Unity 材质贴图属性优先级、同名候选过滤和绑定来源枚举配置 |
| `scripts/export_unitypy_real_animation_samples_to_gltf.py` | 从 UnityPy Mesh、SkinnedMeshRenderer、Transform 和 AnimationClip 导出少量真实蒙皮动作 glTF 样例；支持 `--auto` 按 skeleton path 覆盖率自动匹配 direct AnimationClip；Mecanim dense clip 写入受限预览并在 extras 标注来源 |
| `scripts/convert_gltf_models_to_fbx_blender.py` | Blender 批量把 glTF 模型转 FBX |
| `scripts/organize_unitypy_project_export.py` | UnityPy 分类资源组织 |
| `references/vfx-preview-config.json` | Unity VFX 类型、关键词、预览模式和输出路径配置 |
| `scripts/build_vfx_preview_manifest.py` | 从 UnityPy 全量索引生成 VFX 配置卡、粒子贴图/VFX 材质/模型/动画分类和 `4.临时目录/中间索引/VFX预览/manifest.json` |
| `scripts/export_unity_vfx_reconstruction.py` | 从 VFX manifest 生成 `reconstructed/` JSON，并导出可重新导入 Unity 的 `unity-export/Assets/GameAnalysisVfxPreview` Prefab、Material、metadata、bootstrap 脚本和依赖引用 |
| `scripts/create_vfx_viewer_launcher.py` | 在 `2.报告/` 生成 `打开特效预览.bat`，把本次 study-root、manifest、reconstructed-dir、preview-cache-dir 和 unity-export-dir 传给固定 GameVfxViewer |
| `scripts/build_vfx_viewer_tool.ps1` | 维护构建固定 `GameVfxViewer` EXE 到 `built-in-tools/GameVfxViewer_1.0.0/GameVfxViewer.exe`，只在工具缺失或升级时运行 |
| `references/vfx-unity-reconstruction-schema.json` | Unity VFX 动态还原 JSON、Prefab/Material/metadata/bootstrap 回导结构的 schema |
| `scripts/build_learning_indices.py` | 生成美术规格、命名、渲染、音频、动画、代码命名空间等学习索引 |
| `scripts/audit_study_root_hygiene.py` | 审计并可选清理临时目录中的浏览器 profile |
| `scripts/analyze_assetripper_project.py` | AssetRipper 场景/Prefab 分析 |
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
