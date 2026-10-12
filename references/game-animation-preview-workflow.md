# Klei/DST 游戏动画与特效预览工作流

## 目标与已实现边界

当前 `references/game-animation-manifest-schema.json`、`scripts/build_game_animation_manifest.py`、`scripts/export_game_animation_asset.py` 和固定 `GameAnimationViewer` 只实现 **Klei/DST BILD + ANIM + atlas 驱动的 AnimState v1**。Unity、未知引擎或其他格式使用各自合同，不能伪装成 Klei Bank/Build/Symbol 数据。

当前闭环支持：解析 Bank/Build/Clip/Symbol/多 atlas，播放真实 RGBA PNG sequence，查看 atlas PNG、Build XML、Anim XML 和匹配证据，并导出且只声明以下五种格式：

- `current-frame-png`
- `png-sequence`
- `apng`
- `gif`
- `source-bundle`

只处理用户授权的本地来源。扫描、转换、预览和导出不得修改源游戏目录。缓存与用户导出都是可重建工作产物；`source-bundle` 只收集 manifest 已授权且属于所选 Bank/Build/Clip 依赖闭包的已提取素材，不回读或打包未索引源文件。

## Codex 动态多 Agent 编排

动态多 Agent 是 Codex Skill 的编排能力，不是一个已存在的本地 shard CLI 或通用 Merger 程序。主 Agent 读取 `references/animation-vfx-routing-config.json`，从只读 Discovery 记录 `N/B/G/U/E/C/R`（候选数、候选字节数、格式签名组、未知签名组、依赖边、写冲突边、运行时依赖组），再按配置建立任务图并动态派发角色。

对 DST 当前约 2796 个 Bank/动画包候选的大集，主 Agent 按 routing config 分派 Discovery、Adapter、Exporter、Preview fragment 任务，并且只派一个 Merger 子 Agent。各 Maker 只写 `4.临时目录/子任务产物/<task-id>/` 或主 Agent 明确分配的互不重叠 fragment；单一 Merger 子 Agent 是最终 manifest、errors、分类发布和 `2.报告/打开动画预览.bat` 的唯一写者。Review 顺序为 `Spec → 新 Fixer → Spec 复审 → Quality`。

- Klei 原始 package conversion、共享转换索引和转换输出固定由 single-producer 串行完成，不能按包并发运行转换器。
- 转换提交后，可并行做 Prefab/`fx.lua` 语义发现、Adapter 索引、Exporter/Preview fragment 和运行时边界盘点。
- `C>0` 的 ready 节点只有在写入范围已隔离时才能并发。
- 本地没有 shard CLI 不是完成门禁；主 Agent 直接通过子 Agent 任务契约、精确输入集和隔离写范围完成分片与合并。
- 不得声称已有通用本地 Merger 程序。Merger 在本工作流中是一次动态调度的 Codex 子 Agent 角色。

## Klei/DST AnimState v1 manifest 合同

manifest 固定写入：

```text
4.临时目录/中间索引/游戏动画预览/manifest.json
```

`source_roots` 必须是非空对象 `{name: relativePath}`。key 必须是非空安全名称，value 必须是去重、精确的 study-root 相对目录；禁止绝对路径、盘符相对路径、空段、`.`、`..`，也不得通过 symlink/junction/reparse 越过 study-root。Launcher 不扫描或推导额外根，只按对象 value 的确定性顺序解析并重复传入 exact `--asset-root`。

身份键必须保留逻辑名与物理包名：

- `Bank.name`：`anim.xml` 的 `anim/@root`。
- `Bank.package`：该 `anim.xml` 所在动画包。
- `Build.name`：`build.xml` 的 `Build/@name`。
- `Build.package`：该 `build.xml` 所在动画包。

同名 Bank/Build 跨 package 不得合并。Bank→Build 没有可靠的格式内唯一外键；匹配必须保存同包、名称、Symbol 覆盖率以及 Prefab、`fx.lua`、assets/dependencies、皮肤或装备覆盖等证据。多候选时保留全部候选并标记 `partial`，Viewer 要求用户显式选择，禁止默认选第一项。

人物 AnimState 经常在基础 Build 之外由游戏运行时挂载装备、胡须、皮肤或角色变体 Symbol。adapter 仅可对配置 `matching.render_policies` 明确命中的身份组合使用 `missing_symbol_policy=skip`：Build 名称、Bank、package、最低覆盖率、Atlas 与结构预检必须全部满足策略；缺失 Symbol 必须进入 `missing_symbols`，候选和 Clip 保持 `partial`，但允许生成不含覆盖层的基础人物预览。未命中策略时继续严格失败。Klei Symbol 二进制哈希大小写不敏感，因此 XML 名称匹配使用 `casefold`。人物分类优先使用 Bank/package 身份，Clip 名中的 `ocean`、`water`、`fx` 不得覆盖人物身份。

Build 的 `<Texture filename="atlas-N.tex">` 顺序决定 atlas page。manifest 必须列出全部 atlas PNG 相对路径并验证页数，不能只加载 `atlas-0.png`。帧播放必须保持 affine 变换、pivot、层级顺序、symbol 显隐与覆盖、朝向、tint、alpha 和已识别的混合模式。

## Klei 流程

```text
detect/inventory
  → Discovery：anim ZIP、BILD、ANIM、KTEX、DYN、scripts.zip、fx.lua
  → single-producer package conversion
  → 并行只读语义发现与边界盘点
  → Bank.name/package + Build.name/package + Clip + Symbol + Atlas catalog
  → 隔离的 export/preview fragments
  → 单一 Merger 子 Agent 发布 manifest 与唯一 BAT
  → AnimState Preview / Material View / 五种导出
  → Spec → Quality
```

人物、怪物、建筑、植物和普通 AnimState FX 的分类优先使用 Prefab/脚本/标签证据；文件名命中只能作为待验证候选。

## Runtime-only 候选边界

`.dyn`、KSH shader、运行时粒子和多 pass 海洋/水面候选不属于当前 AnimState v1 的 ready 条目。必须运行现有 `scripts/analyze_klei_dyn_boundary.py`，并通过 Klei 编号报告与图片素材清单保留这些 runtime-only 候选的 exact set、来源、匹配线索和未恢复原因；候选不能因无法播放而从清单消失。

- 已提取的纹理、图集、XML、脚本或材质可以沿用图片画廊、分类素材和 `source-bundle` 安全边界查看/导出。
- runtime-only 候选不得进入 AnimState `ready` manifest；若保留索引引用，状态只能为 `partial/unsupported` 并写明原因。
- `.dyn` 未有可信解码路线时只做 header/熵/同名 ZIP/EXE 线索盘点，不伪造动画。
- KSH、自定义 Shader、UV 滚动/扭曲、粒子模拟、RenderTarget 和多 pass 海洋只能按已恢复素材展示，不用单张循环图冒充完整特效或水面。
- 只要这些候选仍未恢复，最终总状态至少为 `DONE_WITH_CONCERNS`，Klei 报告必须保留限制与后续路线。

## 非 Klei 与未知引擎

未知格式先做只读 Discovery，再为已识别的格式建立独立 adapter 和自己的数据合同；不得要求它符合当前 Klei/DST AnimState v1 schema。未适配或证据不足时标记 `material-only/unsupported`，同时继续使用 Skill 既有图片画廊、模型预览和 VFX 预览/导出流程，不因为动画 adapter 缺失而丢弃已提取素材。

Unity 等已存在专用流程的引擎继续使用其模型和 VFX manifest/Viewer。不得根据扩展名假定 Unity，也不得把 AnimationClip、粒子命名或贴图自动解释成 Klei Bank/Build。

## 本地 Viewer 与 Launcher 合同

运行：

```powershell
python scripts/create_game_animation_viewer_launcher.py `
  --study-root "<study-root>" `
  --skill-root "<skill-root>" `
  --manifest "<study-root>/4.临时目录/中间索引/游戏动画预览/manifest.json" `
  --renderer-script "<skill-root>/scripts/export_game_animation_asset.py"
```

Launcher 只生成唯一入口：

```text
2.报告/打开动画预览.bat
```

BAT 调用固定 `built-in-tools/GameAnimationViewer_1.0.1/GameAnimationViewer.exe`，并传入 `--study-root`、`--manifest`、一个或多个 `--asset-root`、`--preview-cache-dir`、`--export-dir`、`--python-exe` 和 `--renderer-script`。固定 EXE 不复制到 study-root，也不在分析任务中重新构建。启动时写入同一中间索引目录的 `viewer-launch.log`；缺失 EXE/manifest 或进程异常退出时必须保留可见错误窗口，重复启动则前置已有 Viewer。

固定目录为：

```text
4.临时目录/中间索引/游戏动画预览/manifest.json
4.临时目录/中间索引/游戏动画预览/preview-cache/
4.临时目录/动画预览导出/
```

Launcher 内容相同时不重写 BAT。用户导出目录不得被 Merger 当成 manifest fragment 输入；重复导出需保持安全路径、明确冲突策略和可追溯清单。

## 审查门禁

Spec Reviewer 至少检查：candidate exact set 闭合、Klei single-producer 证据、fragment 写入所有权、来源哈希、Bank/Build package 身份、歧义、多 atlas、`source_roots` 对象、安全路径、固定三目录和唯一 BAT；同时确认文档与 UI 都只声明五种受支持导出。

Quality Reviewer 至少实测人物、怪物、建筑、植物、普通 AnimState FX 和水面候选各一项，覆盖 `ready`、`partial`、`unsupported/blocked`；检查帧数、FPS、时长、循环、层级、变换、透明、混合、atlas/XML 素材定位，以及 `current-frame-png`、`png-sequence`、`apng`、`gif`、`source-bundle` 五种导出。还要核对 runtime-only exact candidate 未消失、未进入 ready manifest，Viewer 离线只读、源目录不变且重复运行幂等。

Reviewer 不得修改被审产物。失败时把复现命令、实际与期望输出交给新的 Fixer；Fixer 先复现 RED，再做最小修正，随后重新执行 Spec 和 Quality。
