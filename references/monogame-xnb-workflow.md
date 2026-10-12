# MonoGame/FNA/XNA `.xnb` 工作流

适用范围：检测到 `MonoGame.Framework.dll`、`FNA.dll`、`Microsoft.Xna.Framework`、`Content/*.xnb`、Tiled `.tmx/.tsx` 或 FMOD bank 的本地游戏项目。

## 识别与索引

1. 扫描程序集和资源目录，记录 MonoGame/FNA/XNA 证据到 `2.报告/00_研究总览.md`。
2. 对所有 `.xnb` 做只读 reader 扫描，至少区分 `Texture2DReader`、`ModelReader`、`EffectReader`、字体 reader、自定义 reader。
3. XNB reader 索引写入 `4.临时目录/中间索引/MonoGame资源/XNBReader索引.csv`，不要写入 `1.分类资源/`。
4. 若存在 `Content/mesh/*.xnb` 或任意 `ModelReader`，必须进入“ModelReader 转换决策门”。
5. 若存在 Candide/Romestead `.cdx/.cmx` 等文件，额外扫描 BinaryWriter `i-mesh`/`IndexedMesh` 内嵌网格；这些模型不在 XNB `ModelReader` 清单里，仍必须按 glTF 导出后进入模型预览。

## Texture2D 导出

1. 优先支持 `SurfaceFormat.Color` 到 PNG 的可逆导出。
2. 导出的 PNG 分类到 `1.分类资源/图片/` 下的 UI、场景、道具、序列帧、图集等目录。
3. 分类索引、尺寸统计和失败日志写入 `4.临时目录/中间索引/MonoGame资源/`。
4. 图片画廊仍使用固定 `GameImageGallery` 流程，入口只放 `2.报告/打开图片画廊.bat`。

## ModelReader 转换决策门

`ModelReader` 不能只停留在“发现/索引”状态。只要 reader 扫描发现一个或多个 `ModelReader`，完成状态必须满足以下两种之一：

- 已导出至少一个 Blender 可导入模型，并把可交付模型放入 `1.分类资源/模型/<模型名>/`。
- 或已明确记录转换阻塞，并把全局状态标为 `DONE_WITH_CONCERNS` 或 `BLOCKED`。

执行顺序：

1. 写出模型清单：`4.临时目录/中间索引/MonoGame模型/ModelReader索引.csv`，字段至少包含原始路径、reader、文件大小、候选贴图路径、备注。
2. 查找可信转换路线：优先官方、开源、长期维护工具或可审计脚本；记录工具名称、版本、来源、许可证和验证命令。禁止使用来源不明、破解或绕过访问控制的工具。
3. 小样本转换：优先选择 `sphere.xnb`、最小文件或依赖最少的模型，先做 1 个样本，不直接宣称全量模型已完成。
4. 输出格式：优先 `.gltf/.glb`，可辅以 `.fbx`。输出到 `1.分类资源/模型/<模型名>/`，同目录包含贴图或 `textures/`。
5. 每个模型目录必须包含 `metadata.json`、`export-log.json`、`dependencies.json`、`missing-resources.json`。这些 sidecar 描述来源、转换工具、贴图依赖和缺失原因。
6. Blender 导入验证：检测模型可导入、网格非空、材质/贴图状态可说明。验证日志写入 `4.临时目录/中间索引/MonoGame模型/Blender导入验证.jsonl`。
7. 模型预览链：只在 `1.分类资源/模型/` 中存在 `.gltf/.glb/.fbx` 后运行通用模型预览 manifest、缩略图、BAT 和 GUI 验证。

## CDX/CMX 内嵌 IndexedMesh 导出

Candide/Romestead 的车、轮子、建筑、机关、doodad 可能把 `IndexedMesh` 直接嵌在 `.cdx` 中，地图、POI、建筑外观场景可能把 `IndexedMesh` 嵌在 `.cmx` 中，同时用 `media/textures/...`、`media/sprites/...` 等路径引用贴图；这类资源不会出现在 `Content/mesh/*.xnb`。发现 `i-mesh` 时运行：

```powershell
python "<skill>\scripts\export_candide_cdx_meshes_to_gltf.py" --study-root "<study-root>" --source "<game-root>" --scan-all-embedded-meshes
```

若用户只追查特定对象，可加 `--include-regex "cart|wheel"`。导出的 glTF 写入 `1.分类资源/模型/`，同目录包含贴图副本和 sidecar JSON，然后继续运行模型预览 manifest、缩略图和 viewer 验证链。对 `Content/maps/**/*.cmx` 来源且同名 `.tmx` 存在的无贴图网格，manifest 必须标记 `asset_role=map_structure_mesh`、`visual_source=tmx_tileset_layers`；这类灰色盒体通常是地图结构、体块、碰撞或 greybox 辅助网格，实际视觉来自 TMX 图层和 tileset，不应默认判定为美术模型贴图导出失败。导出规模过大、Blender 渲染耗时长或数量达到万级时，不能跳过执行，也不能用占位缩略图作为最终交付；必须拆成可续跑批次继续渲染，并在日志中记录已完成数量、失败项和下一批命令。占位缩略图只允许作为执行中的临时标记；若最终无法完成，必须记录外部阻塞证据并标为 `DONE_WITH_CONCERNS` 或 `BLOCKED`。

如果无法转换：

1. 写入 `4.临时目录/失败记录/MonoGame模型转换阻塞.md`。
2. 在 `2.报告/00_研究总览.md` 的阻塞项中说明 `ModelReader` 数量、已尝试工具、失败阶段和下一步。
3. 最终回复必须说明模型部分不是完整完成，状态使用 `DONE_WITH_CONCERNS` 或 `BLOCKED`。

## Tiled 地图资源

1. 扫描 `.tmx/.tsx`、tileset 图片、对象层、碰撞层和自定义属性。
2. 地图结构索引写入 `4.临时目录/中间索引/MonoGame地图/`。
3. 地图架构、世界生成、建筑摆放和资源规范结论写入 `2.报告/04_场景关卡搭建.md`，不要把地图索引 CSV/JSON 放进报告目录。

## 音频、渲染与特效

1. FMOD `.bank`、`.strings.bank` 复制或索引到 `1.分类资源/音频/FMOD/`，事件名、bank 清单和不可解码原因写入临时索引。
2. `EffectReader`、`.fx`、渲染相关 XNB 放入 `1.分类资源/渲染/`，分析结论写入 `2.报告/07_渲染管线拆解.md`。
3. 粒子、发射器或自定义 XNB 配置放入 `1.分类资源/特效/配置/`，运行时行为保持“待验证”。

## 禁止项

- 不能把 `.xnb ModelReader` 原样放入 `1.分类资源/模型/` 当作已导出模型。
- 不能因为 `.xnb ModelReader` 不能直接导入 Blender 就跳过转换尝试。
- 不能只索引 `ModelReader`，却不执行转换决策门或阻塞记录。
- 不能把“模型目录为空”当作完成。
- 不能把“XNB 边界索引”当作模型导出。
- 不能在发现 `ModelReader` 后仍用 `DONE` 宣称 full 全量分析完全完成，除非模型转换和验证链已通过。
