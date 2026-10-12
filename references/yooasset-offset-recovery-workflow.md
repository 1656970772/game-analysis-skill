# YooAsset 零前缀 offset 包装恢复工作流

## 适用边界

本流程只处理 YooAsset manifest 已标记的 `.bundle` 中一种可审计的格式包装：在配置化扫描上限内恰好出现一次 `UnityFS`/`UnityWeb`/`UnityRaw` magic，magic 之前全部为零，且已配置的 UnityFS header 解析器能验证 format version、版本字符串和 embedded declared size。恢复只是从 magic 偏移开始复制原字节，不使用密钥、算法或任何字节变换，因此不是密码学解密。

遇到以下任一条件，必须停止该条目并写入 `blocked`，不得猜测或尝试绕过：

- magic 前缀含非零字节；
- 配置上限内 magic 缺失或不唯一；
- magic 没有已配置的 header 解析器；
- UnityFS format version、版本字符串、blocks-info 大小或 declared size 校验失败；
- 路径命中配置化 DRM、反作弊或访问控制信号；
- 需要密钥、解密算法、字节异或/重排或运行游戏才能继续；
- source/output/audit 重叠，路径链包含 symlink/junction/reparse，或输出根不是 fresh；
- manifest 审计状态、物理大小或声明哈希不一致。

YooAsset manifest 的 `Encrypted=true` 只作为声明原样记录，不能单独证明载荷使用了密码学。只有满足上述零前缀和 header 门禁的条目才允许恢复。`.rawfile` 不进入 UnityFS 恢复范围。

## 前置条件

1. 先按 `references/yooasset-manifest-workflow.md` 完成只读审计，且 `yooasset-bundles.csv` 的文件存在性、大小、FileHash/FileCRC 均通过。
2. 用原 UnityPy 索引独立统计 `UniqueSourcesWithObjects` 和 `.bundle` source 对象覆盖；不能把 checkpoint 的 `completed` 当作对象已解析。
3. 抽样确认现象确为零前缀 + 明文 magic，并先运行合成 RED/GREEN 测试。
4. 源目录只读，恢复根、audit 根与源目录完全分离；恢复根必须不存在。

## 恢复命令

Windows 下通过 Skill UTF-8 包装器调用：

```powershell
pwsh -NoLogo -NoProfile -File "<skill>\scripts\invoke_python_utf8.ps1" `
  -Script "<skill>\scripts\recover_yooasset_offset_bundles.py" `
  --source-root "<StreamingAssets\yoo>" `
  --bundle-csv "<study-root>\4.临时目录\中间索引\YooAsset\yooasset-bundles.csv" `
  --output-root "<study-root>\0.原始导出\YooAssetOffsetRecovered" `
  --audit-dir "<study-root>\4.临时目录\中间索引\YooAssetOffset恢复" `
  --config "<skill>\references\yooasset-offset-recovery-config.json"
```

脚本先对全部 manifest `.bundle` 做安全路径、物理大小/哈希、magic、零前缀和 UnityFS header 校验，再为合格条目保持包/物理文件相对层级复制 embedded suffix。每个目标先写同目录随机 `.partial`，flush/fsync 后无覆盖发布；异常时只清理本事务 partial。源 bundle 在恢复前后分别做全文件 SHA-256 快照，恢复文件再次独立哈希并与源 embedded suffix 对账。

输出：

| 路径 | 内容 |
|---|---|
| `0.原始导出/YooAssetOffsetRecovered/<Package>/<physical>.bundle` | 去除零前缀后的原始 embedded bundle 字节 |
| `4.临时目录/中间索引/YooAssetOffset恢复/offset-recovery-files.csv` | manifest bundle name/hash、物理路径、offset、header、源/恢复 SHA-256、状态与阻塞原因 |
| `4.临时目录/中间索引/YooAssetOffset恢复/offset-recovery-summary.json` | 数量、字节、offset/magic/format 分布、源快照、完成门禁与安全边界 |

配置化参数位于 `references/yooasset-offset-recovery-config.json`。项目名、固定偏移和当前 bundle 数量不得写入脚本或通用配置。

## UnityPy 小样本与全量接力

恢复完成后，先从恢复根选取少量大/中/小 bundle，用 Skill Python 环境和 UnityPy 只读加载，验证文件从 `UnityFS` 开始且每个样本对象数大于零。样本失败时不得启动全量导出。

样本通过后，用 fresh Protocol 2 输出运行：

```powershell
pwsh -NoLogo -NoProfile -File "<skill>\scripts\invoke_python_utf8.ps1" `
  -Script "<skill>\scripts\extract_all_readable_resources.py" `
  --source "<study-root>\0.原始导出\YooAssetOffsetRecovered" `
  --output "<study-root>\0.原始导出\UnityPy\UnityPyYooAssetOffsetRecovered" `
  --audio-mode metadata
```

全量成功后立即用相同命令跑一次 no-work resume。Protocol 2 的独立门禁必须重新读取 summary、checkpoint、全部 fragment 和 `all_resources_index.csv`，并检查：

- discovered/completed source 数等于恢复 bundle 数；failed/pending/retry 为零；
- index 中所有恢复 bundle 都有对象，`BundleSourcesWithObjects > 0`，并量化准确覆盖数；
- fragment 文件 exact set 与 checkpoint source key exact set 一致，没有 extra/missing；
- source_file、asset type、status、对象数和物理导出文件状态可对账；
- 整个 UnityPy 输出树递归 `.partial=0`；
- no-work resume 没有 reprocess/retry，关键资源与控制面快照不变；
- UnityPy 读取前后的恢复根全文件大小/SHA-256 快照一致。

## YooAsset 覆盖对账

使用 `yooasset-assets.csv`、`yooasset-bundles.csv`、`offset-recovery-files.csv` 和恢复后的 UnityPy 索引建立以下链路：

```text
AssetPath / extension
  -> manifest BundleID / bundle_name / file_hash
  -> resolved physical .bundle
  -> recovered relative .bundle
  -> UnityPy source_file
  -> object_count / type / status / export_path
```

`AssetPath` 与 Unity object name 不是同一命名域，不要求逐条名称相等。必须按 AssetPath 扩展统计 manifest 声明分布，并报告 bundle 覆盖数、对象总数、对象类型/状态和任何零对象/缺失映射；不能用“命名不一致”掩盖缺口。完成状态要求 manifest 标记的所有可恢复 `.bundle` 都进入 UnityPy source/object 统计。所有 CSV/JSON/日志只写 `4.临时目录`，不得进入 `1.分类资源` 或 `2.报告`。

## 测试与完成门禁

```powershell
pwsh -NoLogo -NoProfile -File "<skill>\scripts\run_skill_tests.ps1" `
  -SkillRoot "<skill>" `
  -PytestArgs @("tests/test_recover_yooasset_offset_bundles.py", "tests/test_analyze_yooasset_manifests.py", "-q")
```

完成前必须确认：

- 合成动态偏移恢复、非零前缀、declared-size mismatch、重复 magic、安全信号、source/output overlap 和 non-fresh output 回归全绿；
- 全量 `recovered_count == manifest .bundle count`，或每个未恢复条目都有精确阻塞原因；
- `source_embedded_sha256 == recovered_sha256` 且 `recovered_size == embedded_declared_size`；
- 源安装目录和恢复根在各自只读窗口前后快照一致；
- 恢复、UnityPy、覆盖对账目录递归无 `.partial`；
- 没有使用新第三方工具时，不修改 `tooling-manifest.json`。
