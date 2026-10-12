# 交付目录结构规范

## study-root 位置

- 默认在当前 Codex 项目工作区目录下，以游戏项目名命名。
- 不得放在源游戏目录旁边、SteamLibrary/common 或游戏安装目录内。
- 不得写入源游戏目录。

## 根目录结构（严格执行）

study-root 下**有且只有以下 5 个编号目录**：

```text
<study-root>/
  0.原始导出/           # 工具全量导出的原始目录
  1.分类资源/           # 从原始导出解析整理出的最终分类目录
  2.报告/               # 研究总览和分析报告
  3.代码/               # 导出的可识别代码
  4.临时目录/           # 工具日志、中间文件、失败记录
```

**禁止创建上述 5 个目录以外的任何目录或文件**，包括但不限于：
- `__ascii_work`（如需 ASCII-only 暂存，用 `4.临时目录/_ascii_work/`）
- `0.完整资源导出目录`、`1.最终产出`、`2.主角资源`、`3.代码导出`
- `主角资源`、`交付状态清单.md`、`ExportedProject/`
- 任何散落的 `.csv`、`.json`、`.md`、`.html`、`.png`

## 0.原始导出/

工具（AssetRipper、UnityPy、repak、UModel、UTMT 等）全量导出的原始结构，保持工具输出的原始目录层级和文件名。用于后续学习搜查，不做任何重组。

每个工具的输出放在独立子目录中：

```text
0.原始导出/
  AssetRipper/          # AssetRipper 导出的 ExportedProject
  UnityPy/              # UnityPy 导出的可读资源
  UModel/               # UModel 导出的 raw 资源
  PakUnpacked/          # repak unpack 的原始解包
  UTMT/                 # UTMT 导出的 GameMaker 资源
  GDRETools恢复/        # GDRETools 完整恢复的 Godot 项目，保持原始层级
  <其他工具名>/         # 其他工具的原始输出
```

## 1.分类资源/

从原始导出中解析、转换、整理出的最终可用资源。

**分类目录中只放资源文件本身，不放散落索引（CSV/JSON/MD）。**
例外：每个模型目录必须包含与该模型同生命周期的 `metadata.json`、`export-log.json`、`dependencies.json`、`missing-resources.json`；Unity VFX 的粒子贴图、材质、模型、动画与配置按 `特效/粒子贴图`、`特效/VFX材质`、`特效/VFX模型`、`特效/VFX动画`、`特效/配置` 归档。VFX 动态还原产物不进入分类资源目录，manifest/errors、`reconstructed/`、`unity-export/` 统一保留在 `4.临时目录/中间索引/VFX预览/`。当前 Klei/DST AnimState v1 Viewer 的 manifest、真实 PNG sequence 预览缓存和五种用户导出同样属于可重建的本地工作产物：manifest/cache 留在 `4.临时目录/中间索引/游戏动画预览/{manifest.json,preview-cache/}`，用户导出留在 `4.临时目录/动画预览导出/`，不混入分类资源目录。
空目录不放 `_empty_reason.md`；缺失原因写入 `2.报告/00_研究总览.md` 和 `4.临时目录/工具日志/验证日志.md`。

### 图片/

```text
1.分类资源/图片/
  UI/                    # 界面元素：图标、按钮、状态条、HUD 等
  角色立绘/              # 角色头像、叙事立绘、uichar* 大图等
  场景/                  # 背景、关卡、环境装饰图
  道具/                  # 武器、装备、掉落物、交互物图
  序列帧/                # 动画序列帧
    <角色或动作名>/
      idle_01.png
      attack_01.png
      ...
  图集/                  # Atlas/SpriteSheet 切片后的完整图集
    <图集名>/
```

验收：打开图片目录，看到的全部是图片文件（PNG/JPG/TGA/PSD/BMP/WEBP），不允许出现 CSV、JSON、MD、TXT。

### 模型/

```text
1.分类资源/模型/
  <模型名>/
    <模型名>.fbx         # 或 .gltf/.glb
    metadata.json         # 模型来源、分类、材质/动画状态与可追踪 id
    export-log.json       # 单模型导出与修补步骤日志
    dependencies.json     # 材质、贴图、骨骼、动画、原始资源依赖清单
    missing-resources.json # 无法定位的依赖与原因
    textures/
      diffuse.png
      normal.png
      ...
```

验收：每个模型子文件夹能直接导入 Blender 且正常显示网格体和贴图。FBX/glTF 中的贴图引用使用相对路径。如果原始导出的 glTF 材质缺贴图，必须先用资源索引、材质依赖、引用表、缓存或路径反查尝试修补，再写入缺失资源报告。包含网格体、骨骼、动画片段、材质定义、贴图文件和四个模型 sidecar。

### 特效/

```text
1.分类资源/特效/
  粒子贴图/              # 火焰、烟雾、光效、尾迹等贴图
  VFX材质/               # VFX 专用材质和 Shader
  VFX模型/               # 已分类的真实/可用 VFX Mesh 或模型资源；无法还原的中间状态不放入本目录
  VFX动画/               # 特效动画片段
  配置/                  # 粒子系统配置文件
    Niagara/
    Cascade/
    ParticleSystem/
    其他/
```

验收：粒子贴图只有图片；`VFX材质/`、`VFX模型/`、`VFX动画/` 只放已分类的真实/可用 VFX 资源。配置按引擎类型或资源类型分子目录，例如 `ParticleSystem`、`Material`、`Mesh`、`AnimationClip`。无法还原为模型文件、配置/metadata 卡、预览模式、warnings/status 等中间状态统一留在 `1.分类资源/特效/配置/`、`4.临时目录/中间索引/VFX预览/manifest.json`、`reconstructed/` 或 `errors.jsonl`，不放进 `VFX材质/`、`VFX模型/`、`VFX动画/` 冒充资源。若检测到 Unity VFX，必须生成 `4.临时目录/中间索引/VFX预览/manifest.json`、`errors.jsonl`、`reconstructed/` 和 `unity-export/Assets/GameAnalysisVfxPreview`；`unity-export/Assets/GameAnalysisVfxPreview` 是可重新导入 Unity 的项目相对目录。动态还原是 best-effort：无法解析、部分还原或 Unity 回导失败时，必须在 manifest/errors 中记录 `preview_status`、`reconstruction_status`、`unity_export_status`、warnings 或 errors，不得伪装为完整 Unity runtime 复现。

### 音频/

```text
1.分类资源/音频/
  <用途或资源名>/
    *.wav
    *.ogg
    *.mp3
    *.bank
```

验收：只放真实导出的音频样本。Unity AudioClip metadata、采样失败记录和命名索引写入 `4.临时目录/中间索引/学习输出/音频资源索引.csv`。

### 渲染/

```text
1.分类资源/渲染/
  Shader/
  Material/
  RenderTexture/
  其他/
```

验收：只放可学习的 shader/material/render texture 等渲染资源；渲染清单写入 `4.临时目录/中间索引/学习输出/渲染资源索引.csv`。

## 2.报告/

`2.报告/` 只放学习者阅读产物：编号报告、图片画廊 BAT 入口 `打开图片画廊.bat`、动画预览 BAT 入口 `打开动画预览.bat`、模型预览 BAT 入口 `打开模型预览.bat`、特效预览 BAT 入口 `打开特效预览.bat`、可选 PDF。不允许放 CSV/JSON、验证日志、manifest、缩略图、分类摘要或重复 BAT 别名，这些统一写入 `4.临时目录`。每类预览入口只保留一个固定名称；动画预览不得额外生成 `动画预览.bat`、`打开Klei动画.bat` 等重复别名。

按模式生成对应报告：

- `full`：`00_研究总览.md`、`01_美术资源规格表.md`、`02_美术风格与色彩速查.md`、`03_动画与手感帧数据.md`、`04_场景关卡搭建.md`、`05_技术架构拆解.md`、`06_玩法系统拆解.md`、`07_渲染管线拆解.md`、`08_命名规范汇总.md`、`09_可迁移技术清单.md`
- `art`：`00_研究总览.md`、`01_美术资源规格表.md`、`02_美术风格与色彩速查.md`、`03_动画与手感帧数据.md`、`04_场景关卡搭建.md`、`07_渲染管线拆解.md`、`08_命名规范汇总.md`、`09_可迁移技术清单.md`
- `code`：`00_研究总览.md`、`05_技术架构拆解.md`、`06_玩法系统拆解.md`、`08_命名规范汇总.md`、`09_可迁移技术清单.md`

`2.报告/00_研究总览.md` 是所有模式的必填文件，包含：引擎判定、工具版本、学习目的、导出统计、推荐阅读路径、阻塞项、使用后迭代记录。

如果 `1.分类资源/图片` 已有图片，或用户/分支要求图片画廊，默认先运行 `scripts/build_image_gallery_manifest.py --study-root <study-root> --skill-root <skill-root>`，再运行 `scripts/create_image_gallery_launcher.py --study-root <study-root> --skill-root <skill-root>`。默认入口文件固定为 `2.报告/打开图片画廊.bat`。BAT 调用 Skill 固定 EXE `built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe`，必须传入 `--study-root "<study-root>"`、`--manifest "<study-root>/4.临时目录/中间索引/图片画廊/manifest.json"`，并按 manifest `source_roots` 传入一个或多个重复 `--images-root "<source_root>"`。固定 EXE 的存在性验证和 `self-test` 必须通过；不要在每个 study-root 复制、生成或构建项目专属 EXE。画廊 manifest 固定写入 `4.临时目录/中间索引/图片画廊/manifest.json`，缩略图缓存写入 `4.临时目录/中间索引/图片画廊/thumbnails/`，错误日志写入 `4.临时目录/中间索引/图片画廊/errors.jsonl`。旧 HTML 画廊和每项目 EXE 构建仅作兼容历史输出，不是默认流程。不要在 study-root 根目录创建画廊入口目录，也不要把 manifest、缩略图或日志放入 `2.报告/`。

Klei/DST BILD+ANIM+atlas adapter 已生成 AnimState v1 manifest 时，运行 `scripts/create_game_animation_viewer_launcher.py` 生成唯一 `2.报告/打开动画预览.bat`，调用 Skill 固定 `built-in-tools/GameAnimationViewer_1.0.1/GameAnimationViewer.exe` 和真实 renderer `scripts/export_game_animation_asset.py`。manifest/cache 固定在 `4.临时目录/中间索引/游戏动画预览/{manifest.json,preview-cache/,viewer-launch.log}`，必须区分 `bank.name/package` 与 `build.name/package` 并列全多 atlas；`current-frame-png`、`png-sequence`、`apng`、`gif`、`source-bundle` 五种用户导出固定在 `4.临时目录/动画预览导出/`。素材查看可打开 manifest 授权的 atlas PNG、Build XML、Anim XML；Build 歧义保持 `partial` 并要求显式选择。`.dyn`、KSH shader、粒子和多 pass 海洋由边界索引、Klei 报告及图片素材清单保留 exact candidate，已提取素材可查看/导出，但不得进入 AnimState `ready` manifest 或冒充完整还原。未知引擎另走 Discovery/独立 adapter；未适配时沿用图片、模型、VFX 流程，不要求符合 Klei schema。

## 3.代码/

只包含代码文件，不包含图片、模型、音频或索引。

```text
3.代码/
  脚本/                  # GML、Lua、JS、Python、GDScript 等可读脚本
  IL导出/                # ILSpy 反编译的 C# 代码
  IL2CPP_Dump/           # dump.cs、script.json、il2cpp.h
  Native映射/            # Ghidra/IDA 地址映射和行为摘要
  代码可读性判定.md      # 唯一允许的非代码文件
```

验收：`脚本/` 中只有脚本文件；混淆代码也保留；非代码文件绝对不出现。

## 4.临时目录/

工具日志、中间文件、失败记录。结束后可删除。

```text
4.临时目录/
  工具日志/              # 工具 stdout/stderr 摘要
  中间索引/              # 临时 CSV/JSON 等
    Godot/              # PCK probe、恢复对账、分类 summary 与失败记录
    学习输出/            # build_learning_indices.py 生成的学习型索引
    游戏动画预览/        # Klei/DST AnimState v1 manifest 与可重建预览缓存
      manifest.json      # Bank/Build/atlas/clip 统一索引
      preview-cache/     # exporter 生成的真实 RGBA PNG sequence，仅供预览
  动画预览导出/          # current-frame-png、png-sequence、apng、gif、source-bundle 用户导出
  子任务产物/            # 动态子 agent 的报告片段、审查记录和隔离输出
  失败记录/              # 阻塞项、失败对象
  _ascii_work/           # ASCII-only 暂存（替代根目录的 __ascii_work）
```
