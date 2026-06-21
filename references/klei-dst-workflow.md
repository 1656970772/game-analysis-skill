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

5. 转换脚本输出：
   - KTEX -> PNG 写入 `1.分类资源/图片/图集/KleiTEX/`。
   - FSB -> OGG/WAV 写入 `1.分类资源/音频/KleiFSB/`；`python-fsb5` 失败时自动降级到 `vgmstream-cli`。
   - `anim.bin/build.bin` -> XML 写入 `4.临时目录/中间索引/Klei动画XML/`。
   - 转换 CSV 索引写入 `4.临时目录/中间索引/学习输出/`，失败 JSONL 写入 `4.临时目录/失败记录/`。

## 完成状态判定

- 只完成 ZIP/Lua/音频 bank 导出、KTEX/DYN/FSB 索引时，最终状态应为 `DONE_WITH_CONCERNS`。
- 只有运行 `convert_klei_dst_resources.py` 且 PNG/动画 XML/音频样本真实生成后，才能把图片、动画结构或音频拆分标为完成。
- `.dyn` 未识别时必须做 boundary inventory：前 64 字节聚类、熵统计、与 `anim_dynamic.zip` 条目名对照、EXE 字符串加载路径检索。

## 推荐工具候选

- `Stexatlaser`：默认 KTEX/TEX -> PNG 转换器，Windows 静态包沉淀到 Skill `tools/`。
- `DstAnimTool`：默认将 `anim.bin/build.bin` 反编译为 XML；本地副本带 Python 3 兼容补丁。
- `python-fsb5` + `vgmstream-cli`：默认 FSB5 样本抽取组合。`python-fsb5` 保留 OGG 原始重建，`vgmstream-cli` 作为异常 bank 的 WAV fallback。需要结合 FEV 解析恢复事件语义。
- `nsimplex/ktools`：`ktech`/`krane` 是候选替代工具；若下载的镜像包缺 DLL 或无法运行，必须记录退出码并使用上述可运行工具链。
