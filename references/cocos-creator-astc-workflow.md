# Cocos Creator ASTC 工作流

用于授权的 Cocos Creator APK、解包目录或移动端包体，尤其是存在 `assets/assets/<bundle>/cc.config.json`、`native/`、`import/`、`.astc`、`.atlas`，且画廊里角色/场景只显示图标或零碎 PNG 的情况。

## 识别特征

- `assets/assets/<bundle>/cc.config.json`，常见 bundle 包括 `localRemoteRes`、`resources`、`fgui`。
- `cc.config.json` 中出现 `types`、`uuids`、`paths`、`packs`，类型可能包括 `cc.ImageAsset`、`cc.Texture2D`、`sp.SkeletonData`、`cc.Asset`。
- `native/<uuid前两位>/<uuid>.astc` 存在大量纹理；`import/<uuid前两位>/<uuid>.json` 存在 metadata。
- 角色/场景语义路径可能存在于 `paths`，但原始文件扩展名扫描只把 `.json/.atlas` 分进角色/场景，把 `.astc` 留在未分类。

## 映射规则

解析 `cc.config.json` 时不要按数组位置猜测 UUID：

- `paths` 是对象；每个属性名通常是 `uuids` 的索引。
- `value[0]` 是语义路径，例如角色、背景、战斗场景或 UI 路径。
- `value[1]` 是 `types` 的索引。
- `value[2]` 不是可直接用于 native 纹理的 UUID 索引；不要用它定位纹理。
- 预览用图片优先处理 `cc.ImageAsset`，通常跳过同名 `/texture` 的 `cc.Texture2D`，避免重复和错配。

定位 native 文件：

```text
compact_uuid = uuids[int(paths_key)]
uuid = 展开 Cocos 压缩 UUID，去掉 @suffix
native = assets/assets/<bundle>/native/<uuid前两位>/<uuid>.*
```

## Cocos 压缩 UUID

压缩 UUID 常见长度为 22，可能带 `@xxxxx` 后缀。展开规则：

1. 去掉 `@` 后缀。
2. 前两个字符是 UUID 十六进制前缀。
3. 剩余 20 个字符按每 2 个 base64 字符一组处理。
4. base64 字母表为 `ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/`。
5. 每组 `left * 64 + right` 得到 12 bit，格式化成 3 个十六进制字符。
6. 得到 32 位 hex 后按 `8-4-4-4-12` 加连字符。

必须先写测试锁住：

- 压缩 UUID 展开。
- 已知本地样本 ASTC 头读取尺寸。
- 语义路径能映射到真实 `native/<uuid>.astc`。

## ASTC 头和解码

ASTC 文件头：

- magic：`13 AB A1 5C`
- block size：字节 4、5、6。
- width：字节 7、8、9，小端 24 bit。
- height：字节 10、11、12，小端 24 bit。
- depth：字节 13、14、15，小端 24 bit。

PowerShell 直接位移 byte 可能溢出，必须转 `[int]`；Python `bytes` 读取不需要额外转换。

解码工具优先使用 Skill 内置的 ARM 官方 `astcenc`。如果 Skill 内工具缺失，再从 ARM 官方 `astc-encoder` Release 获取。Windows x64 选 `windows-x64` 包，不要误选 `windows-arm64`。Skill 内可复用工具路径：

```text
tools/astcenc_5.4.0_windows_x64/bin/astcenc-sse4.1.exe
```

一次性或临时验证工具也可放在当前 study-root：

```text
4.临时目录/tools/astcenc/<version>/bin/astcenc-sse4.1.exe
```

解码命令：

```powershell
& $astcenc -dl $inputAstc $outputPng
```

不要给解码命令额外传 `8x8` 或质量参数；新版 `astcenc -dl` 会从 ASTC 文件头读取 block size。下载或验证工具后必须记录 URL、版本、许可证、SHA256、`-version` 或 `-help` 输出。

## 角色与场景分类

分类要以 Cocos 语义路径为主，而不是 native 文件名。常见参考：

- 角色：`spine/role/`、`spine/role_lunjf/`、`spine/cuzhi/`、`friendsImg/`、`monsterIcon/`、`cuzhi/`、`roleImgRank/`、`gdRole/`、`tongRen/`。
- 场景：`spine/fightScene/`、`bg/`、`realm/`、`schoolBg/`、`chapterIcon/`、`bgIcon/`、`qiyu/`、`diGongIcon/`、活动背景路径。

输出要求：

- 原始 ASTC 副本：`1.分类资源/图片/<角色资源或场景资源>/ASTC原始纹理/<子类>/`
- 可预览 PNG：`1.分类资源/图片/<角色资源或场景资源>/可预览_ASTC解码/<子类>/`
- 主角候选：`1.分类资源/图片/主角候选资源/可预览_ASTC解码/`
- 映射索引：`4.临时目录/中间索引/cocos_astc/` 或项目专用子目录。
- 专项报告：`2.报告/角色场景资源专项修复.md`。

可复用脚本：

```powershell
python "<GameAnalysis>/scripts/cocos_creator_astc_repair.py" --source-root "<授权APK解包目录>" --study-root "<study-root>"
```

如果旧画廊只收集 PNG/JPG/WEBP，必须新增角色/场景专项画廊，不能用“未分类 ASTC 存在”替代用户可查看的角色/场景资源。

## 性能和路径注意事项

- 逐个读取 ASTC 头在机械盘或外置盘上可能很慢；先做阶段计时，再给长任务足够超时。
- `mapping-only` 只应生成映射和必要头信息，不应复制大批 ASTC 或解码 PNG。
- 若工具不支持中文、空格或长路径，使用 study-root 下 `4.临时目录/_ascii_work/` 暂存，再回填标准 0-4 目录并记录路径兼容原因。
- 不写入源 APK/解包目录；所有产物写入 study-root。

## 画廊验收

HTML 画廊必须可本地打开。推荐验收：

- manifest item 数等于实际可预览 PNG 数。
- 浏览器中 `figure` 数和 `img` 数等于 manifest item 数。
- 控制台 error 为 0。
- 桌面和移动视口都检查；图片过多导致 full-page screenshot 超时时，保存 visible screenshot 并在验证日志说明。
- 最终回复说明角色/场景数量、主角候选数量、失败数量和入口路径。

## 常见失败

- 只看文件扩展名：导致 `.json/.atlas` 进角色/场景，大量 `.astc` 留在未分类。
- 用 `paths` 的遍历序号或 `value[2]` 查 UUID：导致语义路径和 native 图错配。
- 把 `cc.Texture2D` `/texture` 当主预览项：导致重复或全部指向错误纹理。
- 下载 Windows ARM64 版 `astcenc` 到 x64 机器：工具不可运行或命令失败。
- 给 `astcenc -dl` 传 block size/quality：新版工具会报参数不识别。
- 画廊用 fetch 读取本地 JSON：直接双击打开时可能被浏览器拦截；重要交付优先内联 manifest 或通过本地 server 验证。
