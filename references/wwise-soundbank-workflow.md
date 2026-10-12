# Wwise SoundbanksInfo 与 vgmstream 全量音频恢复

## 适用范围与边界

当授权构建中存在 `SoundbanksInfo.xml`、`.bnk` 或 `.wem` 时，使用本流程
恢复可审计的本地学习音频。不要上传或分发原音频。

`SoundbanksInfo.xml` 提供语义，但不一定是运行时文件数量的完整真值：

- `StreamedFiles/File` 与事件的 `ReferencedStreamedFiles` 描述外置媒体；
- 事件的 `IncludedMemoryFiles` 描述 bank 内嵌媒体；
- `Event` 提供事件名、对象路径和时长范围；
- 生成版本、语言 bank、优化、prefetch 和未被当前事件引用的媒体，可能让
  XML 的唯一 Media Id 数与实际 `.wem` 数或 vgmstream `stream count`
  不相等。此时保留差异为语义覆盖边界，不能据此丢弃实际可解码资源。

完成数量的主证据是：

1. 实际发现的每个 `.wem` 都有一个有效外置 WAV；
2. 每个可解码 `.bnk` 的 vgmstream `stream count` 与有效内嵌 WAV 数一致；
3. XML 的 Memory/Loose/事件映射覆盖率单独统计。

仅含控制数据或外部引用的 `Init`、`Settings`、语言控制 bank 等可能没有
可解码子流。只有 XML 同时声明了内嵌媒体时，零子流才算失败；否则记录为
`skipped_no_decodable_streams`。

## 强制只读探测

vgmstream 的 `-I` 只控制 JSON 信息输出，不会自动禁用解码。单独运行
`-I <bank.bnk>` 可能仍在输入旁生成默认 `<bank.bnk>.wav`。metadata 探测
必须同时使用 `-m -I`：

```powershell
rtk "<skill>\tools\vgmstream-win64-r2117\vgmstream-cli.exe" `
  -m -I "<bank.bnk>"
```

脚本和人工命令都不得省略 `-m`。真实解码必须始终显式传 `-o`，输出目标
必须位于 study-root，不能依赖 vgmstream 默认输出名。

## 推荐自动化命令

使用通用脚本扫描输入树中所有 WEM/BNK、解析 SoundbanksInfo、逐文件验证
WAV，并生成可续跑状态：

```powershell
& "<skill>\tools\python-env\Scripts\python.exe" `
  "<skill>\scripts\export_wwise_audio.py" `
  --input-dir "<game-root>\...\StreamingAssets\Audio" `
  --output-dir "<study-root>\0.原始导出\Wwise" `
  --classified-dir "<study-root>\1.分类资源\音频\Wwise" `
  --index-dir "<study-root>\4.临时目录\中间索引\Wwise" `
  --log-dir "<study-root>\4.临时目录\工具日志\Wwise" `
  --vgmstream "<skill>\tools\vgmstream-win64-r2117\vgmstream-cli.exe" `
  --link-mode hardlink `
  --workers 4
```

可先追加 `--dry-run --sample-limit 2` 检查计划，再用 `--sample-limit 1`
做真实小样本。`--sample-limit` 分别限制 WEM 和 BNK 输入数量；不传时全量
执行。中断后重跑会复用已验证 WAV；确需覆盖时显式加 `--overwrite`。

`--link-mode hardlink` 让原始工具输出和分类目录指向同一 NTFS 文件数据，
保留两套导航路径而不重复占用大体积 WAV。跨卷或不支持硬链接时，可改用
`copy`，并在报告中注明额外磁盘占用。

## 底层命令契约

外置 WEM：

```powershell
rtk "<vgmstream-cli.exe>" -i -o "<study-root 内目标.wav>" "<media-id>.wem"
```

bank 全部子流：

```powershell
rtk "<vgmstream-cli.exe>" -i -S 0 `
  -o "<study-root 内目录>\<安全-bank-name>__?s.wav" "<bank.bnk>"
```

输出模板使用显式安全 bank 名；不要对绝对输入路径使用 `?f`。每个 subsong
再用 `-m -I -s <index>` 只读取得 stream name/media id，关联
SoundbanksInfo 语义。

## 输出布局

```text
0.原始导出/Wwise/
  外置WEM/<语言>/<源相对目录>/*.wav
  银行内嵌WAV/<源相对目录>/<bank>/*.wav
  源元数据/.../SoundbanksInfo.xml
  源输入清单.csv
  导出清单.json

1.分类资源/音频/Wwise/
  外置WEM/.../*.wav
  银行内嵌WAV/.../*.wav

4.临时目录/中间索引/Wwise/
  Wwise媒体语义映射.csv
  Wwise事件索引.csv
  Wwise银行索引.csv
  Wwise转换索引.csv
  Wwise失败记录.jsonl
  Wwise导出摘要.json

4.临时目录/工具日志/Wwise/
```

分类目录只含真实 WAV。XML、事件表、转换索引、失败记录和日志不能进入
`1.分类资源/音频/Wwise`。

## 完成判定

不能只看退出码；vgmstream 某些失败路径仍可能返回 `0`。每个导出 WAV
至少验证：

- 文件存在且大于 WAV 最小头；
- RIFF/RF64 + WAVE 头有效；
- frame、sample rate、channel 数均大于零；
- duration 大于零；
- bank 子流可由 `bank + subsong + media id` 回到语义映射；XML 无记录时
  仍保留 media id 并标为语义未覆盖。

交付前还要核验源音频树文件数、总字节和修改时间清单前后一致，并由主
任务复核完整源根文件数、总字节与关键文件 SHA-256。
