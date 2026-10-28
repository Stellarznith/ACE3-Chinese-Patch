# `release_manifest.json` 协议 v1

状态：已冻结。协议由本说明、`release_manifest.schema.json` 和 `examples/` 下 FULL/UPDATE 示例共同定义。每次发布只替换 `.xdelta` 和同目录同主体的 `.json`；安装器及字段含义不随汉化版本更新而变化。

## 文件配对

玩家选择 `NAME.xdelta` 后，安装器只读取同目录的 `NAME.json`。例如 `ACE3_CN_v1.0.0_full.xdelta` 对应 `ACE3_CN_v1.0.0_full.json`。JSON 的 `patch.filename` 必须与所选补丁的 basename 完全一致。元数据不能选择可执行文件或指定路径。

`patch.filename` 使用 ASCII 文件名规则：首字符必须为英文字母或数字，后续字符只能是英文字母、数字、点、下划线或连字符，扩展名必须为小写 `.xdelta`，总长最多 120 字符。规则拒绝目录分隔符、冒号、空白、非 ASCII 字符及路径穿越形式。

## 字段语义

- `schema_version`：本 JSON 协议版本。v1 固定为整数 `1`，与 JSON Schema draft 版本无关。
- `patch_type`：`FULL` 表示从受支持的纯净日文原版直接还原为目标中文版；`UPDATE` 表示从受支持的旧版中文版直接还原为目标新版中文版。该字段是展示/说明元数据，兼容性由 `source` 的精确大小与 SHA-256 决定，不通过名称推断。
- `source` / `target`：对应输入 ISO 和预期输出 ISO 的版本标签、精确字节数、SHA-256。版本标签只显示，不作路径或命令片段。
- `patch.filename` / `size_bytes` / `sha256`：配对补丁文件名及该文件精确大小和 SHA-256。
- `patch.format`：v1 固定为 `xdelta3`。
- `patch.format_version`：生成该补丁时所用的 **Xdelta3 编码器版本**。这是描述性来源信息，不代表自动推定补丁格式兼容性；安装器不根据此值推断或验证兼容性。

## 验证与安全

安装器拒绝未知 `schema_version`、未知字段、缺失字段、无效类型/格式、补丁文件名与选择文件不一致、补丁大小或 SHA-256 不匹配，以及输入 ISO 与 `source` 大小或 SHA-256 不匹配。Xdelta3 解码输出先写入目标旁的临时文件，再按 `target` 大小和 SHA-256 验证；验证通过后才以不覆盖方式提交。输入 ISO 和补丁只读，不修改游戏 ISO。

SHA-256 校验只证明文件与这份元数据中的指纹一致，不认证元数据发布者或官方来源。安装器不联网、不签名验证，也不宣称发行来源认证。

严格拒绝未知字段意味着添加或改变语义必须发布新的协议版本，并由安装器明确支持。普通汉化版本更新应保持 v1 字段含义不变。

## 示例

- `examples/ACE3_CN_v1.0.0_full.json`：FULL 示例。
- `examples/ACE3_CN_v1.0.0_to_v1.0.1_update.json`：UPDATE 示例。

示例采用明确标识的测试占位指纹，不是可用发行清单，不能用于真实安装或发布。正式发行元数据必须从实际输入、输出、补丁文件计算并经还原验证取得。
