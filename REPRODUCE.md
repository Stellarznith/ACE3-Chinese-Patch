# 复现说明

> **状态：框架已定，命令待随脚本搬入仓库后逐条补齐。**
> 标注 `[待补齐]` 的位置，是指工程侧尚未把该步骤固化成可发布脚本；
> 这些位置在首个 Release 之前必须补到"照抄能跑通"的程度。

## 一、你能复现什么

从**你自己持有的日版原盘**出发，重建与发布版**逐字节一致**的汉化镜像。
本项目承诺的是：给定相同的输入与相同的环境，脚本产出的镜像哈希与 Release 一致。

不能复现的部分也说清楚：字形设计的主观取舍、译文的语感判断，属于人工决策，
脚本只能重放结论，不能重放"为什么这么选"。

## 二、环境

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 x64（主要）、Linux x64（次要） |
| Python | 3.10 或以上 |
| 依赖 | 见 `requirements.txt` `[待补齐]` |
| 外部工具 | xdelta3（仅生成/应用差分时需要） |
| 磁盘 | 至少 3 份镜像大小的空闲空间（原盘 + 工作副本 + 成品） |
| 内存 | 8 GB 以上（整盘扫描与字库重建会占内存） |

## 三、输入

| 输入 | 身份 |
|---|---|
| 日版原盘 | 《Another Century's Episode 3 THE FINAL》，PS2，**SLPS-25784** |
| 原盘校验值 | `[待补齐]`（首次构建时由脚本输出并记入 `manifests/`） |
| 译文源 | 仓库 `corpus/` 下的表格文件 |
| 字形源 | 由 Source Han Sans SC 渲染，渲染参数记录在 `docs/01-构建与复现.md` |

## 四、构建流程（骨架）

```bash
# 0. 校验原盘身份（不通过则中止）
python3 scripts/verify_source.py --iso original.iso [待补齐]

# 1. 提取目标资源
python3 scripts/extract.py --iso original.iso --out build/extract/

# 2. 生成字形与字库
python3 scripts/build_font.py --corpus corpus/ --out build/font/

# 3. 写入译文（资源级写回，不改结构）
python3 scripts/writeback_text.py --in build/extract/ --corpus corpus/ [待补齐]

# 4. 写回字形
python3 scripts/writeback_font.py --in build/extract/ --font build/font/ [待补齐]

# 5. 封盘
python3 scripts/rebuild_iso.py --in build/extract/ --out build/ace3-zh.iso [待补齐]

# 6. 回读校验
python3 scripts/verify_output.py --iso build/ace3-zh.iso --expect manifests/<版本>.sha256 [待补齐]
```

## 五、每步的判据

| 步骤 | 通过判据 | 失败处理 |
|---|---|---|
| 0 校验 | 原盘 SHA-256 与记录一致 | 直接中止，不做任何写操作 |
| 1 提取 | 目标成员数量与大小与清单一致 | 中止并报告缺哪个成员 |
| 2 字形 | 目标字符全部有字位，无"缺码" | 中止，列出缺字清单 |
| 3 写回文本 | 回读译文与 `corpus/` 逐条一致 | 中止，列出不一致条目 |
| 4 写回字形 | 图集像素 diff 只落在允许区域 | 中止，输出 diff 区间 |
| 5 封盘 | 输出大小与结构表与原盘同构 | 中止，保留 `.tmp` 不产出成品 |
| 6 回读 | 全盘哈希与 `manifests/` 记录一致 | 判定构建失败 |

**任何一步失败都不得产出成品。** 这是硬约束：宁可没有产物，也不要一个来源不明的镜像。

## 六、已知的复现难点

1. **字库与 VRAM 的落点冲突**：字形数据必须在引擎认可的位置，改动错位会导致黑屏或花屏。
   相关结论与禁区记录在 `docs/01-构建与复现.md`。
2. **逐包容量不一**：文本分散在多个字库包中，容量必须逐包核算，不能按单包推全局。
3. **模拟器差异**：同一镜像在不同 PCSX2 版本下表现可能不同，验收记录必须写清版本。

## 七、记录要求

每次构建产出：

- 输入原盘 SHA-256
- 每个中间产物的 SHA-256
- 最终镜像 SHA-256 与大小
- 构建时间、脚本版本（仓库 commit）
- 验收屏幕清单与结果

以上写入 `manifests/<版本>.json`，与 Release 一同发布。
