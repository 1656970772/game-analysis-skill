# Klei / Don't Starve Together 工作流

适用范围：检测到 `dontstarve_steam*.exe`、`data/databundles/scripts.zip`、`data/anim/*.zip`、`KTEX`、`.dyn`、FMOD `.fsb/.fev` 的本地授权 Don't Starve Together 或同类 Klei 自研引擎目录。

## 识别事实

- 典型目录包含 `bin`、`bin64`、`data`、`mods`。
- 核心脚本通常在 `data/databundles/scripts.zip`，内部为 `scripts/**/*.lua`。
- 动画 ZIP 常见三件套为 `anim.bin`、`build.bin`、`atlas-0.tex`。
- `KTEX` 是 Klei 纹理容器；未接入可信转换器时只索引和复制原始文件，不把 `.tex` 当成图片交付。
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

3. 脚本输出：
   - 原始 ZIP、解包内容、KTEX、DYN 保留在 `0.原始导出/KleiDST/`。
   - Lua 代码写入 `3.代码/脚本/`。
   - `.wav/.fsb/.fev` 复制到 `1.分类资源/音频/`。
   - CSV/JSON 索引写入 `4.临时目录/中间索引/KleiDST/`。
   - KTEX/DYN/FSB 转换缺口写入 `4.临时目录/失败记录/KleiDST转换阻塞.md`。

## 完成状态判定

- 只完成 ZIP/Lua/音频 bank 导出、KTEX/DYN/FSB 索引时，最终状态应为 `DONE_WITH_CONCERNS`。
- 只有接入并验证可信 `ktech`/`krane`/FSB 解码工具，且 PNG/动画/音频样本真实生成后，才能把图片、动画或音频拆分标为完成。
- `.dyn` 未识别时必须做 boundary inventory：前 64 字节聚类、熵统计、与 `anim_dynamic.zip` 条目名对照、EXE 字符串加载路径检索。

## 推荐工具候选

- `nsimplex/ktools`：`ktech` 转 KTEX/PNG，`krane` 转 Klei 动画到 Spriter/SCML。若本地缺失，默认自动从可信来源下载或构建，沉淀到 Skill `tools/`，并记录来源、许可证、版本输出和哈希。
- `python-fsb5` 或 `vgmstream-cli`：用于 FSB5 样本抽取。若本地缺失，默认自动恢复到 Skill `tools/`。需要结合 FEV 解析恢复事件语义。
