# 补丁制作 Agent 交接：release_manifest v1

安装器的正式元数据协议为 v1，字段定义以 `release_manifest.schema.json` 为准，语义说明见 `release_manifest_protocol.md`。后续每个补丁发行只新增/替换 `.xdelta` 与同目录同主体 JSON，不修改 v1 字段含义。

## 必须生成的配对文件

例如：

```text
ACE3_CN_v1.0.0_full.xdelta
ACE3_CN_v1.0.0_full.json
```

JSON `patch.filename` 必须精确等于补丁 basename。文件名须符合 ASCII 规则：首字符 `[A-Za-z0-9]`，之后只允许 `[A-Za-z0-9._-]`，总长不超过 120，扩展名 `.xdelta`。

使用 `installer/make_patch.py` 生成补丁和 JSON。依赖安装：`python -m pip install -r installer/requirements-generator.txt`。FULL 必须把独立确认的纯净原版 SHA-256 通过 `--expected-source-sha256` 显式传入；工具会重新计算并比较输入 ISO。UPDATE 输入指定的旧版中文版，不传该参数。

```powershell
python installer/make_patch.py --type FULL `
  --source "D:\ACE3\原版日文.iso" --source-version "确认的原版标签" `
  --target "D:\ACE3\目标中文版.iso" --target-version "确认的目标标签" `
  --patch "D:\发行暂存\ACE3_CN_vX_full.xdelta" `
  --xdelta "installer\third_party\xdelta3-3.2.2\xdelta3.exe" `
  --expected-source-sha256 "<独立确认的64位SHA-256>"

python installer/make_patch.py --type UPDATE `
  --source "D:\ACE3\上一版中文版.iso" --source-version "确认的旧版标签" `
  --target "D:\ACE3\目标中文版.iso" --target-version "确认的目标标签" `
  --patch "D:\发行暂存\ACE3_CN_vX_update.xdelta" `
  --xdelta "installer\third_party\xdelta3-3.2.2\xdelta3.exe"
```

命令为运行示例，`vX`、版本标签和 SHA-256 必须替换为实际经确认的信息；不可使用占位值发行。生成器不覆盖已有输出，生成成功后会以冻结 v1 Schema 校验同名 JSON。

每次发行必须从实际文件计算精确 `size_bytes` 和 SHA-256：

- `source`：被支持的输入 ISO。FULL 应为确切的纯净日文原版；UPDATE 应为确切的旧版中文版。
- `target`：正式目标中文版 ISO。
- `patch`：本次生成的 `.xdelta` 文件。

用准备随包发行的 Xdelta3 实际还原一次，并确认输出大小/SHA-256 等于 target，输入 ISO 前后 SHA-256 不变。不得从文件名、历史 MD5、其他版本或示例 JSON 推测任何发行值。

`patch_type` 为 `FULL` 或 `UPDATE`；安装器以 source 的精确大小和 SHA-256 判断输入兼容性。`patch.format` 固定为 `xdelta3`。`patch.format_version` 记录生成补丁所用的 Xdelta3 编码器版本，只是描述性元数据，不表示格式兼容性，也不用于推断兼容性。

协议示例目录中的哈希是测试占位值，严禁用于发行。SHA-256 只校验内容与元数据相符，不是发布者签名或来源认证。不要把游戏 ISO、Xdelta 可执行文件路径或任意文件路径写入 JSON。Xdelta3 可执行文件单独部署在安装器 EXE 同目录，并由发行流程单独核验来源及再分发许可。
