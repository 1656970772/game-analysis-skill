# YooAsset Manifest 只读审计工作流

## 适用场景

Unity `StreamingAssets` 中存在 YooAsset `PackageManifest_<PackageName>_<PackageVersion>.json`，需要把 Asset、Bundle、依赖和磁盘 payload 建立可追溯映射时使用本流程。它只读取 JSON 与 payload 字节以计算大小/摘要，不提取、解密、反序列化或修改 bundle 内容。

## 输入与配置

- `--manifest-root`：归档或只读 manifest 根；脚本按 `references/yooasset-manifest-audit-config.json` 的 `discovery.manifest_globs` 发现 JSON。
- `--streaming-assets-root`：实际 YooAsset 包目录的共同根；每个包目录按 manifest 相对父目录和 `PackageName` 配置化定位。
- `--output-dir`：只能位于两个输入根之外，通常为 `<study-root>/4.临时目录/中间索引/YooAsset/`。
- `--config`：默认 `references/yooasset-manifest-audit-config.json`；包目录候选、命名枚举、payload 扩展名、metadata 模式、哈希算法、CRC 文本编码和输出名均由此文件控制。

已由本地文件名、大小和实际字节哈希共同验证的 `OutputNameStyle`：

| 值 | 名称 | 物理文件名模板 |
|---:|---|---|
| `0` | `HashName` | `{FileHash}{BundleExtension}` |

其他枚举只使用配置中的 fallback 并明确标为 `unknown:<value>:fallback`；遇到新版本时先用对应版本的官方源码与本地小样本核实，再补配置和合成测试，不要把未经验证的枚举值或某个项目的文件名写死到脚本。

`FileHash` 按十六进制长度从配置选择 MD5/SHA-1/SHA-256。YooAsset `FileCRC` 默认按 `little_endian_bytes` 表示：先计算标准 CRC32 数值，再把 32 位值编码为四字节小端十六进制文本。若目标版本使用常见的大端数值文本，把 `file_crc32_encoding` 改为 `big_endian_hex`，并保留项目配置副本与验证证据。

## 执行

Windows 下使用 Skill 自带 Python 与 UTF-8 包装器：

```powershell
pwsh -NoLogo -NoProfile -File "<skill>/scripts/invoke_python_utf8.ps1" `
  -Script "<skill>/scripts/analyze_yooasset_manifests.py" `
  --manifest-root "<manifest-root>" `
  --streaming-assets-root "<StreamingAssets/yoo>" `
  --output-dir "<study-root>/4.临时目录/中间索引/YooAsset" `
  --config "<skill>/references/yooasset-manifest-audit-config.json"
```

退出码：

- `0`：全部结构、ID 边界、文件存在性、大小、FileHash、FileCRC、runtime manifest 和源读取期快照检查通过。
- `2`：输入/配置不安全或不合法，或 `validation.fail_on_mismatch=true` 且审计存在不一致。能完成结构审计时仍会先写出结果，供定位问题。

## 输出契约

输出固定留在临时索引，不进入 `1.分类资源` 或 `2.报告`：

| 文件 | 内容 |
|---|---|
| `yooasset-assets.csv` | 每个 Asset 的 package、索引、Address、AssetPath、GUID、Tags、BundleID 及关联 Bundle 名/哈希 |
| `yooasset-bundles.csv` | 每个 Bundle 的零基 ID、名称、UnityCRC、FileHash/FileCRC/大小预期与实测、Encrypted、Tags、DependIDs、命名解析和文件状态 |
| `yooasset-summary.json` | manifest/package 汇总、ID 边界、声明总字节、实际匹配总字节、缺失/额外/不一致、runtime manifest、源快照和边界声明 |
| `yooasset-summary.md` | 中文摘要；区分观察事实、判断与待验证假设，不复制大段原始 manifest |

CSV 使用 UTF-8 BOM，数组字段使用紧凑 JSON，便于 Excel/脚本重开。JSON 使用确定性键排序且不写运行时间；相同输入重复运行应字节稳定。

## 校验口径

1. `AssetList[*].BundleID` 必须落在 `0 <= id < len(BundleList)`。
2. `BundleList[*].DependIDs` 每个值必须在相同边界内；自依赖、重复依赖分别统计，不与越界合并。
3. 物理 payload 先由 `OutputNameStyle` 模板解析，再按实际字节复算大小、FileHash 和 FileCRC；文件名包含预期哈希不等于哈希校验成功。
4. `PackageManifest_*.json/.bytes/.hash/.version` 和 `link.xml` 默认是包 metadata，不算额外 payload；未声明的 `.bundle/.rawfile` 计为额外 payload，其他未匹配文件计为额外未知文件。
5. 归档 JSON 与 runtime 同名 JSON 做 SHA-256 对账。读取实际 StreamingAssets 前后比较相对路径、大小、mtime、device/inode 快照；变化会使审计失败。
6. `Encrypted` 只复述 manifest 声明，不能外推加密算法、密钥或运行时解密结果。

## 安全边界与完成检查

- 输入根、包目录和扫描文件拒绝 symlink/junction/reparse；输出不得位于输入根内。
- 不执行游戏，不加载 Unity 对象，不打开 bundle 内容，不尝试绕过加密或访问控制。
- 完成前运行 `tests/test_analyze_yooasset_manifests.py`，再对真实 manifest 全量执行并确认退出 `0`。
- 对账后再次检查 `source_integrity.unchanged_during_analysis=true`，并由任务层记录授权源关键 manifest 的前后 SHA-256。

## offset 包装的后续分流

manifest 审计只证明 Asset/Bundle/物理 payload 映射和声明哈希正确，不证明 Unity 对象已被解析。若 UnityPy checkpoint 把 `.bundle` 标为 `completed`，但 `all_resources_index.csv` 中对应 bundle source 的对象数仍为零，必须独立检查文件头；不能把事务完成状态当成资源覆盖。

当 `.bundle` 在配置化上限内表现为“唯一明文 `UnityFS` magic + magic 前缀全部为零”，且不需要密钥、算法或字节变换时，转入 `references/yooasset-offset-recovery-workflow.md`。恢复前必须验证 embedded UnityFS format version、版本字符串和 declared size；非零前缀、magic 歧义、header 不一致、DRM/反作弊/访问控制信号或任何密钥需求均按条目停止并记录阻塞。`.rawfile` 不按 UnityFS 恢复。恢复后必须重新运行 fresh Protocol 2 UnityPy 导出，并把 manifest bundle name/hash→物理 bundle→恢复 bundle→UnityPy source/object 覆盖链对账。
