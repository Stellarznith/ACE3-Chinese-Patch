# scripts —— 构建与校验脚本

## 约束

1. **只读输入，只写 `build/` 或指定输出目录。** 任何修改输入文件的行为都视为缺陷。
2. **不内嵌译文。** 译文只从 `corpus/` 读，保证两套许可不互相污染。
3. 每个脚本必须能在失败时**明确中止**，不得产出"看起来能用"的半成品。
4. 输出校验值，方便与 `manifests/` 比对。

## 规划中的脚本

| 脚本 | 作用 | 状态 |
|---|---|---|
| `verify_source.py` | 校验原盘身份 | 待补齐 |
| `extract.py` | 提取目标资源 | 待补齐 |
| `build_font.py` | 生成字形与字库 | 待补齐 |
| `writeback_text.py` | 写入译文 | 待补齐 |
| `writeback_font.py` | 写回字形 | 待补齐 |
| `rebuild_iso.py` | 封盘 | 待补齐 |
| `verify_output.py` | 成品回读校验 | 待补齐 |
| `make_patch.py` | 生成 xdelta 差分 | 待补齐 |
| `build_release.py` | 一键串起全流程 | 待补齐 |

## 许可

本目录适用 **MIT**，见 [LICENSE](../LICENSE)。