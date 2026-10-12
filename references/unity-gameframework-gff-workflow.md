# Unity Game Framework GFP/GFF 资源恢复流程

## 适用范围与边界

当 Unity 构建的 `StreamingAssets` 中同时出现 `GameFrameworkVersion.dat`
与大量 `.dat`，且前者魔数为 `GFP`、文件系统容器魔数为 `GFF` 时，执行
本流程。该格式来自开源 Unity Game Framework 的资源版本列表与虚拟文件
系统，不等同于 DRM；只在用户授权的本地副本上恢复资源，不上传或分发。

参考实现：

- Unity Game Framework：
  <https://github.com/EllanJiang/GameFramework>
- UnityGameFramework：
  <https://github.com/EllanJiang/UnityGameFramework>

格式判断和算法必须以版本字段、边界校验与小样本结果为准，不应仅凭扩展名
硬套。本 Skill 当前支持：

- `GFP` 版本列表版本 `2`
- `GFF` 文件系统版本 `0`
- 无解密、快速 XOR、完整 XOR 三类资源载入方式

遇到其他版本时停止提取，记录版本、头部、已研究源码位置和待实现项，不要
猜测字段布局。

## 格式事实

`GameFrameworkVersion.dat` 记录资源名、变体、扩展名、载入方式、长度、
哈希、依赖和文件系统归属。资源 `hashCode` 按有符号 32 位值的低 4 字节、
小端序形成 XOR 密钥：

- 无解密：直接复制。
- 快速解密：仅对开头 220 字节循环 XOR。
- 完整解密：对整个资源循环 XOR。

`GFF` v0 容器包含 4 字节文件系统密钥、块表、固定 256 字节名称槽和
4096 字节数据簇。名称槽用文件系统密钥 XOR；资源载荷位于块表指定的
cluster/offset/length 范围内。载荷再按版本列表中的资源 `hashCode` 和
载入方式解密。

## 两阶段执行

先 probe，验证所有引用、路径和边界，并查看解码魔数分布：

```powershell
rtk "<skill>\tools\python-env\Scripts\python.exe" `
  "<skill>\scripts\export_unity_gameframework_resources.py" `
  --source-root "<game>\StreamingAssets" `
  --mode probe `
  --index-dir "<study-root>\4.临时目录\中间索引\DAT边界\probe"
```

只有 probe 的错误已解释且目标格式合理时才全量恢复：

```powershell
rtk "<skill>\tools\python-env\Scripts\python.exe" `
  "<skill>\scripts\export_unity_gameframework_resources.py" `
  --source-root "<game>\StreamingAssets" `
  --mode extract `
  --output-dir "<study-root>\0.原始导出\自定义DAT恢复\GameFramework解密资源" `
  --index-dir "<study-root>\4.临时目录\中间索引\DAT边界\extract" `
  --overwrite
```

## 输出与验收

索引目录应包含：

- `gameframework_asset_index.csv`
- `gameframework_resource_index.csv`
- `gameframework_file_system_index.csv`
- `errors.jsonl`
- `summary.json`
- `summary.md`

完成前核对：

1. `resource_count` 与版本列表声明一致。
2. `status_counts` 中所有资源为 `probed` 或 `extracted`。
3. `error_count=0`，或每个错误都有资源级边界记录。
4. 解码魔数与目标引擎相符；Unity 资源通常为 `UnityFS`，但不能写死为唯一
   合法值。
5. 导出文件的长度等于版本列表的压缩长度；提取模式已记录 SHA-256。
6. 源目录前后文件数、长度、修改时间和关键文件哈希保持不变。
7. 路径校验拒绝绝对路径、`..`、Windows 保留名和越出输出根的路径。
8. 文件系统块范围不越界、不重叠，引用的 GFF 名称和资源名一致。

## 后续接力

恢复出的文件是 Unity bundle 输入层，不是最终分类资源。继续执行：

1. UnityPy 全量可读资源导出和对象索引。
2. AssetRipper 对恢复目录的补充工程导出。
3. UnityPy 图片、模型、动画、VFX 分类与预览流程。
4. 把外层 Unity 导出和恢复 bundle 导出分别统计，避免重复计数。

外层 AssetRipper 成功并不代表 GFF/GFP 内部资源已覆盖；只有解密资源分支
实际跑通并有完整数量对账，才能宣称完成该资源面。
