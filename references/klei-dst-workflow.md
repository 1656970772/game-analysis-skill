# Klei / Don't Starve Together 工作流

适用范围：检测到 `dontstarve_steam*.exe`、`data/databundles/scripts.zip`、`data/anim/*.zip`、`KTEX`、`.dyn`、FMOD `.fsb/.fev` 的本地授权 Don't Starve Together 或同类 Klei 自研引擎目录。

## 识别事实

- 典型目录包含 `bin`、`bin64`、`data`、`mods`。
- 核心脚本通常在 `data/databundles/scripts.zip`，内部为 `scripts/**/*.lua`。
- 动画 ZIP 常见三件套为 `anim.bin`、`build.bin`、`atlas-0.tex`。
- `KTEX` 是 Klei 纹理容器；默认使用 Stex 批量转换为 PNG，无法转换时才只索引和复制原始文件。
- `.dyn` 可能是动态动画/皮肤资源封装；样本未必是普通 ZIP，未确认算法前只能记录阻塞。
- 音频通常使用 FMOD `.fsb/.fev`；`.fsb` 是样本 bank，`.fev` 是事件层数据。

## 默认导出路线

1. 运行 `detect_engine.py` 和 `asset_inventory.py`。若未命中通用引擎但出现以上 Klei 线索，按未知引擎自扩展处理。
2. 运行：

```powershell
python "<skill>\scripts\export_klei_dst_project.py" `
  --source "<game-root>" `
  --study-root "<study-root>" `
  --project-name "Don't Starve Together"
```

3. 原始导出脚本输出：
   - 原始 ZIP、解包内容、KTEX、DYN 保留在 `0.原始导出/KleiDST/`。
   - Lua 代码写入 `3.代码/脚本/`。
   - `.wav/.fsb/.fev` 复制到 `1.分类资源/音频/`。
   - CSV/JSON 索引写入 `4.临时目录/中间索引/KleiDST/`。

4. 运行资源转换：

```powershell
python "<skill>\scripts\convert_klei_dst_resources.py" `
  --study-root "<study-root>" `
  --game-root "<game-root>"
```

`convert_klei_dst_resources.py` 是 Klei 原始转换的 single-producer：同一个 study-root 只能由一个任务串行写入 `Klei动画XML`、KleiTEX、音频转换目录和转换索引。动态子 agent 可以在转换完成后并行做动画 manifest、分类验收和边界审查，但不得并发重跑该转换器或共同写这些目录。

5. 转换脚本输出：
   - KTEX -> PNG 写入 `1.分类资源/图片/图集/KleiTEX/`。
   - FSB -> OGG/WAV 写入 `1.分类资源/音频/KleiFSB/`；`python-fsb5` 失败时自动降级到 `vgmstream-cli`。
   - `anim.bin/build.bin` -> XML 写入 `4.临时目录/中间索引/Klei动画XML/`。
   - 转换 CSV 索引写入 `4.临时目录/中间索引/学习输出/`，失败 JSONL 写入 `4.临时目录/失败记录/`。

### Klei 动画预览、素材查看与导出

动画 XML 与 atlas PNG 真实生成后，运行当前仅适用于 Klei/DST BILD + ANIM + atlas 的 AnimState v1 闭环：

```powershell
python "<skill>\scripts\build_game_animation_manifest.py" `
  --study-root "<study-root>"

python "<skill>\scripts\create_game_animation_viewer_launcher.py" `
  --study-root "<study-root>" `
  --skill-root "<skill>" `
  --manifest "<study-root>\4.临时目录\中间索引\游戏动画预览\manifest.json" `
  --renderer-script "<skill>\scripts\export_game_animation_asset.py"
```

- `build_game_animation_manifest.py` 使用 `references/game-animation-viewer-config.json` 和 Klei adapter，默认生成 `4.临时目录/中间索引/游戏动画预览/manifest.json`。manifest 必须把逻辑名和物理包名分开：`bank.name` 是 `anim.xml` 的 `anim/@root`，`bank.package` 是 `anim.xml` 所在动画包；`build.name` 是 `build.xml` 的 `Build/@name`，`build.package` 是 `build.xml` 所在动画包。不要把四者折叠成一个目录名。
- Build 的 `<Texture filename="atlas-N.tex">` 顺序决定 atlas page；manifest 的 Build candidate 必须保留全部 atlas PNG 相对路径并验证页数完整。多 atlas 动画不能只加载 `atlas-0.png`。
- Bank→Build 没有可靠的格式内外键。adapter 应保留同包、名称和 Symbol 覆盖率的匹配证据及多个候选；候选不唯一时 `preview_status` 必须为 `partial`，Viewer 要求用户显式选择 Build，不得静默取第一项或标记完整成功。
- 人物 Bank 可引用游戏运行时才挂载的装备、胡须、皮肤和变体 Symbol。仅当配置化 `render_policies` 命中、基础 Build 身份一致、Atlas/结构完整且达到策略覆盖率时，允许 `missing_symbol_policy=skip` 生成基础人物预览；必须记录 `missing_symbols` 并保持 `partial`。未命中策略仍严格阻断。Klei Symbol 哈希按小写计算，adapter/exporter 必须大小写不敏感匹配；人物分类优先依据 Bank/package，不能被动作名中的水面或特效关键词抢占。
- `GameAnimationViewer` 固定在 `<skill>/built-in-tools/GameAnimationViewer_1.0.1/GameAnimationViewer.exe`，每个项目只生成 `2.报告/打开动画预览.bat`。Viewer 可播放 exporter 生成的真实 RGBA PNG sequence，并查看 manifest 列出的 `atlas_png`、`build_xml`、`anim_xml` 素材及匹配证据；不得用占位帧或程序化假动画冒充原动画。
- launcher 的预览缓存固定为 `4.临时目录/中间索引/游戏动画预览/preview-cache/`，用户导出固定为 `4.临时目录/动画预览导出/`。五种导出且只声明 `current-frame-png`、`png-sequence`、`apng`、`gif`、`source-bundle`；其中 `source-bundle` 包含所选 Build/Bank/Clip 的 atlas PNG、XML 和可追溯描述，GIF 只作为调色板受限的预览格式。
- 需要命令行导出时，直接运行：

```powershell
python "<skill>\scripts\export_game_animation_asset.py" `
  --study-root "<study-root>" `
  --manifest "<study-root>\4.临时目录\中间索引\游戏动画预览\manifest.json" `
  --item "<manifest-item-id>" `
  --build "<build-candidate-id>" `
  --clip "<clip-id>" `
  --output-root "<study-root>\4.临时目录\动画预览导出" `
  --format "png-sequence"
```

- 完成前至少抽样验收人物、怪物、建筑、植物、AnimState FX 和水面候选，检查播放帧数与 `numframes` 一致、FPS 来自 `anim.xml`、多 atlas 页可加载、素材/XML 可打开、五种导出均真实生成或明确报告不支持。分类来自配置化关键词和证据，只是检索标签，不把名称命中外推为运行时类型事实。
- 当前 schema、builder、exporter 和 Viewer 只还原 BILD/ANIM + atlas 驱动的 Klei/DST AnimState v1 二维层动画。未知引擎先走 Discovery/独立 adapter；未适配时标为 `material-only/unsupported`，并继续使用既有图片、模型和 VFX 流程，不要求符合 Klei schema。
- `.dyn`、KSH shader、粒子模拟和多 pass 海洋/水面是 runtime-only 候选：运行 `scripts/analyze_klei_dyn_boundary.py`，并在 Klei 编号报告与图片素材清单中保留 candidate exact set、来源和未恢复原因。已提取的 atlas/纹理/XML/脚本可以查看或按安全边界导出，但这些候选不得进入 AnimState `ready` manifest；若索引引用则只能是 `partial/unsupported`。`wave/ocean/water` 命名候选能播放 AnimState 帧，不等于完整还原游戏水面。

### 本体与 Mod 多来源图片补导

当本体已有导出、安装目录新增 Mod，或审计发现 loose TEX、直接图片、嵌套 ZIP、地形图集仍有缺口时，创建来源 manifest 并运行独立补导脚本；不要修改或重复硬编码旧单来源转换器：

```json
{
  "version": 1,
  "sources": [
    {
      "id": "base-game",
      "root": "<game-root>",
      "output_prefix": "",
      "terrain_prefix": "",
      "kind": "game",
      "display_name": "Don't Starve Together"
    },
    {
      "id": "<mod-source-id>",
      "root": "<mod-root>",
      "output_prefix": "Mods/<mod-source-id>",
      "terrain_prefix": "模组/<Mod中文名（来源ID）>",
      "kind": "mod",
      "display_name": "<mod-display-name>"
    }
  ]
}
```

```powershell
python "<skill>\scripts\export_klei_dst_images.py" `
  --study-root "<study-root>" `
  --sources-manifest "<study-root>\4.临时目录\中间索引\KleiDST\图片补全\sources-manifest.json" `
  --skill-root "<skill>" `
  --config "<skill>\references\klei-dst-image-export-config.json"
```

- `output_prefix=""` 的本体复用 `0.原始导出/KleiDST/packages`、`packages_extracted`、`textures_ktex` 和 `1.分类资源/图片/图集/KleiTEX` 既有命名空间；每个 Mod 必须由 manifest 给出互不冲突的前缀，不在脚本中写死 Workshop ID。
- `terrain_prefix` 是独立于原始导出命名空间的安全相对路径。本体可配置为空以复用旧中文目录中的原路径；Mod 使用 `模组/<中文名（来源ID）>` 隔离同名文件。绝对路径、盘符、`.`、`..`、空路径段、Windows 非法字符、控制字符、尾随空格/点及保留设备名均拒绝。
- 扫描 loose TEX、配置允许的直接图片、任意安全 ZIP 成员及配置深度内的嵌套 ZIP；默认 `max_nested_archive_depth=2`。完整 `archive_chain`、冲突、不安全路径和逐来源状态写入 `4.临时目录/中间索引/KleiDST/图片补全/` 的 CSV、JSON、JSONL。
- XML 图集元数据只进入 `0.原始导出/KleiDST` 和索引，不进入分类图片。所有分类结果均为经 Pillow `verify/load` 验证的真实 PNG；无效或残缺结果会重试，源哈希变化会刷新原始副本和 PNG。
- 地形判断及中文用途分类来自 `references/klei-dst-image-export-config.json`。高可信地形在 KleiTEX 主归档之外，按 `<classified_terrain>/<terrain_category>/<terrain_prefix>/<原PNG文件名>` 优先硬链接、失败时复制到 `1.分类资源/图片/地形贴图/`。受管分类固定为 `地图Tile图集PNG`、`道路与噪声PNG`、`小地图图集与底图PNG`、`小地图地表纹理PNG`、`地表噪声与材质PNG`；既有 `地皮与特殊地面候选PNG` 动画图集不由此补导器接管。`map_icons`、地皮物品图标和 ground FX 不因单个词命中自动升为高可信地形。

6. 若生成了 PNG，运行色彩抽样和图片画廊：

```powershell
python "<skill>\scripts\image_palette_report.py" `
  "<study-root>\1.分类资源\图片" `
  --format json `
  --max-files 800 `
  > "<study-root>\4.临时目录\中间索引\学习输出\klei_palette_report.json"

python "<skill>\scripts\build_image_gallery_manifest.py" `
  --study-root "<study-root>" `
  --skill-root "<skill>" `
  --title "Don't Starve Together 图片资源画廊"

python "<skill>\scripts\create_image_gallery_launcher.py" `
  --study-root "<study-root>" `
  --skill-root "<skill>"
```

随后运行固定 EXE self-test，`--images-root` 指向 `1.分类资源/图片`。

7. 若存在 `.dyn` 且尚无可信解码路线，运行边界盘点：

```powershell
python "<skill>\scripts\analyze_klei_dyn_boundary.py" `
  --study-root "<study-root>" `
  --game-root "<game-root>"
```

输出 `4.临时目录/中间索引/KleiDST/dyn_boundary_inventory.csv`、`dyn_boundary_inventory_summary.json` 和 `4.临时目录/失败记录/KleiDST_dyn_boundary_inventory.md`。该脚本只读 `.dyn` 前 64 字节、熵、header 聚类、`anim_dynamic` 同名 ZIP 对照和 EXE 字符串线索，不尝试解码、不绕过访问控制。还必须把 `.dyn`、KSH shader、粒子和多 pass 海洋/水面候选的 exact set 汇入 Klei 报告与图片素材清单；候选不能因未解码而消失，也不能进入 AnimState `ready` manifest。

8. 若用户追问地图生成、`WorldSim`、Voronoi、tile map、连通性、道路或 native 底层入口，运行 WorldSim native 绑定分析：

```powershell
python "<skill>\scripts\analyze_klei_worldsim_binary.py" `
  --study-root "<study-root>" `
  --game-root "<game-root>"
```

输出 `4.临时目录/中间索引/KleiDST/worldsim_native/worldsim_lua_bindings.csv`、`worldsim_function_summary.md`、`worldsim_binary_summary.json` 和 `ghidra_worldsim_targets_autogen.csv`。该脚本用 `pefile` 解析 PE section/image base/`.pdata`，用 `capstone` 反汇编 wrapper，恢复 Lua 注册表 `method_name_ptr -> wrapper_function_ptr -> 0`、日志字符串和候选 native call。

需要更深层学习时，使用 Skill 固定 Ghidra 与 JDK 做定点反编译，不做整库源码倾倒：

```powershell
$env:JAVA_HOME = "<skill>\tools\JDK21"
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
& "<skill>\tools\Ghidra_12.0.4_PUBLIC\support\analyzeHeadless.bat" `
  "<study-root>\4.临时目录\GhidraProjects" "DSTWorldSim" `
  -import "<game-root>\bin64\dontstarve_dedicated_server_nullrenderer_x64.exe" `
  -overwrite `
  -scriptPath "<skill>\scripts" `
  -postScript ghidra_decompile_targets_csv.py `
    "<study-root>\4.临时目录\中间索引\KleiDST\worldsim_native\ghidra_worldsim_targets_autogen.csv" `
    "<study-root>\4.临时目录\中间索引\KleiDST\worldsim_native\ghidra_decompile" `
  -analysisTimeoutPerFile 900 `
  -max-cpu 4
```

Ghidra 输出只作为本机学习材料；最终报告应写地址、调用链、伪代码和行为摘要，不要分发反编译函数体。遇到 `GetEncodedMap` 这类 x64 `.pdata` continuation 切分，优先用脚本中的 continuation 检测或手动追加边界 RVA 做第二轮 target CSV。

若用户要求“完整复原地图生成算法”而不只是入口报告，按以下还原包流程继续：

1. 在 `4.临时目录/子任务产物/WorldSim还原任务契约.md` 写任务契约，明确只读游戏目录、子任务只写自己的 `4.临时目录/子任务产物/<task-id>/`，不得写最终报告或分发反编译函数体。
2. 按互不重叠领域分派或串行执行 6 个子任务：
   - `wsim-01-voronoi`：`InitializeNodePoints`、`AddNewPositions`、`VoronoiPass`、`Commit`。
   - `wsim-02-rasterizer`：`ConvertToTileMap`、site polygon raster、`CreateNodeIdTileMap`。
   - `wsim-03-connectivity`：`SeparateIslands`、`ForceConnectivity`、`DrawCellLine`、`DrawGroundLine`。
   - `wsim-04-roads`：`SetRoadParameters`、`DrawRoads`、`GetRoad`、road path list。
   - `wsim-05-maze-sites`：`GetPointsForSite`、`GetRandomPointsForSite`、`RunMaze`、`GetPointsForMetaMaze`。
   - `wsim-06-encoding-flags`：`GetEncodedMap`、tile flags、`WORLD_TILES`、地形贴图映射、seed/RNG 线索。
3. 每个子任务输出 `reconstruction.md`、`field_map.csv`、`call_chain.csv`、`clean_pseudocode.md`、可选 `missing_targets.csv`，只写行为摘要和 clean-room 伪代码。
4. 主 agent 根据子任务缺口补少量 helper targets，例如 `0x471BE0` polygon fill、`0x45FF00` edge draw、`0x452250` room connector、`0x46D5F0/0x46D6F0` line draw、`0x46A3A0` road path extraction。补出的 `.c` 仍保留在 `4.临时目录/中间索引/KleiDST/worldsim_native/ghidra_decompile_helpers/`。
5. 最终整合到 `3.代码/Native映射/WorldSim还原/`：
   - `00_总览.md`
   - `WorldSimTypes.pseudo.hpp`
   - `WorldSim地图生成算法.pseudo.cpp`
   - `还原覆盖与缺口.md`
   - `field_map_merged.csv`
   - `call_chain_merged.csv`
   - `missing_targets_merged.csv`
   - `tile_flags.csv`
   - `texture_mapping.csv`
6. 单独生成 `2.报告/11_WorldSim完整算法还原进度.md`。该报告必须明确哪些是观察事实、哪些是判断或缺口；不得声称 100% 逐 seed 复刻，除非已确认 native RNG seed、Kamada-Kawai 实体、BoostMap 构建和 road write helper。
7. 合并 CSV 时清洗 Ghidra 临时变量名和 `FUN_*` 合成名；保留 RVA、调用关系和行为证据即可。

9. 生成 Klei/DST 编号报告：

```powershell
python "<skill>\scripts\build_klei_dst_reports.py" `
  --study-root "<study-root>"
```

该脚本读取 `klei_texture_conversion_index.csv`、`klei_animation_xml_index.csv`、`klei_fsb_conversion_index.csv`、`klei_palette_report.json`、Lua 代码目录和 `.dyn` 阻塞索引，生成 `2.报告/00_研究总览.md` 到 `09_可迁移技术清单.md`，并写入 `3.代码/代码可读性判定.md` 与 `4.临时目录/失败记录/KleiDST剩余阻塞.md`。若本轮还修改了 Skill，可通过 `--skill-iteration-note` 把沉淀记录写入 00 报告；若单独生成 WorldSim 学习报告，可追加为 `2.报告/10_WorldSim地图生成底层入口还原.md`。

## 完成状态判定

- 只完成 ZIP/Lua/音频 bank 导出、KTEX/DYN/FSB 索引时，最终状态应为 `DONE_WITH_CONCERNS`。
- 只有运行 `convert_klei_dst_resources.py` 且 PNG/动画 XML/音频样本真实生成后，才能把图片、动画结构或音频拆分标为完成。
- 用户要求动画或特效预览/素材导出时，还必须生成游戏动画 manifest、真实 PNG sequence 预览、唯一 `2.报告/打开动画预览.bat`，并验证 atlas/XML 素材查看及五种导出；只生成动画 XML 不能标记动画闭环完成。
- Bank/Build 候选歧义、atlas 不完整或缺 Symbol 时可以保留可查看索引，但条目必须是 `partial`；只有选择明确且真实帧导出成功的条目才能标为 `ready`。
- `.dyn`、KSH shader、粒子和多 pass 海洋仍未恢复时，不影响已验证 BILD/ANIM 条目的局部完成，但最终总状态至少为 `DONE_WITH_CONCERNS`；报告与图片素材清单必须保留其 candidate exact set，报告不得声称完整还原全部 DST 特效或水面。
- `.dyn` 未识别时必须运行 `scripts/analyze_klei_dyn_boundary.py` 做 boundary inventory：前 64 字节聚类、熵统计、与 `anim_dynamic.zip` 条目名对照、EXE 字符串加载路径检索。
- 若转换 summary 中 KTEX、动画 XML 和 FSB 均为 0 failed，但 `.dyn` 仍未识别，最终状态可为 `DONE_WITH_CONCERNS`；报告必须明确“KTEX/FSB 阶段阻塞已由转换脚本解决，剩余阻塞是 `.dyn`”，避免沿用第一阶段的过期阻塞说明。
- full 模式必须生成 00-09 编号报告，报告事实来源优先引用 `4.临时目录/中间索引/学习输出/` 的三个 Klei 转换索引和 `4.临时目录/工具日志/klei_resource_conversion_summary.json`。
- 若用户要求 WorldSim/native 地图生成算法学习，必须至少运行 `scripts/analyze_klei_worldsim_binary.py` 并输出 `worldsim_lua_bindings.csv`；若还运行 Ghidra，只把反编译结果留在 `4.临时目录/中间索引/KleiDST/worldsim_native/`，报告写伪代码和调用链，不复制长函数体。

## 推荐工具候选

- `Stexatlaser`：默认 KTEX/TEX -> PNG 转换器，Windows 静态包沉淀到 Skill `tools/`。
- `DstAnimTool`：默认将 `anim.bin/build.bin` 反编译为 XML；本地副本带 Python 3 兼容补丁。
- `python-fsb5` + `vgmstream-cli`：默认 FSB5 样本抽取组合。`python-fsb5` 保留 OGG 原始重建，`vgmstream-cli` 作为异常 bank 的 WAV fallback。需要结合 FEV 解析恢复事件语义。
- `nsimplex/ktools`：`ktech`/`krane` 是候选替代工具；若下载的镜像包缺 DLL 或无法运行，必须记录退出码并使用上述可运行工具链。
