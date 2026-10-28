# tools —— ACE3 资源格式底层模块

A.C.E.3（SLPS-25784）汉化工程的底层库。负责读写游戏 ISO 内的资源结构：LZ 压缩包、
字库图集（front-end atlas，tag=2000）、码表 / FontMap、TEX，以及文本槽的定位与写回。
汉化工坊（`workshop\`）和文本定位器（`ace3_text_locator\`）都直接 import 这里的模块。

本目录只放被上游工具依赖的模块，不放工程内的实验脚本、一次性脚本和批处理脚本。

---

## 模块分组

### LZ 编解码

| 模块 | 作用 |
|---|---|
| `lz_encode.py` | LZ 压缩（写回用），纯标准库 |
| `lz_decode_entry.py` | 单条目 LZ 解压（`lzss_decode`） |
| `_r213_lz.py` | 多格式解压分派（`decompress_any`），对 `lz_encode` 做一层封装 |

### 资源包

| 模块 | 作用 |
|---|---|
| `ace3_pack.py` | 包容器解析（`Bnd2`）：表项、子块切分、成员定位。所有上层模块的入口 |

### 字库图集与字形

| 模块 | 作用 |
|---|---|
| `front_atlas.py` | front-end 字库图集（tag=2000）的乱序映射、`decode` / `encode` |
| `_r104_glyphfix2.py` | 字体包（`Pkg`）读写：FontMap、记录、rect、墨迹，字形替换 |
| `_r104_audit2.py` | 字体包结构审计（码位、槽、sjis 切分） |
| `_r104_text_audit.py` | 文本侧审计，`load_rev` 读权威码表 |
| `_r104_fontmap_lookup.py` | FontMap 反查（码位 ↔ 记录 ↔ 实际字形） |
| `_r95_missing_codes.py` | 缺码统计。被 `_r104_glyphfix2` / `_r104_text_audit` 以文件路径动态加载，不能删 |

### 内存与覆盖

| 模块 | 作用 |
|---|---|
| `_r206_memfix.py` | 屏内存驱动的坏行修复；`load_cur` 提供当前码表快照 |
| `_r211_override.py` | 腾字改画登记表（`REPAINTED`）与码位登记加载 |
| `_r211_cmap.py` | 合成码表 `code2char`：码位 → 汉字，供所有解码路径使用 |

### 编辑计划应用

| 模块 | 作用 |
|---|---|
| `_r223_apply_plan.py` | 生成 / 应用写盘计划（绝对偏移、可用长度、目标字节），含 `locate` |

### 文本定位器

`ace3_text_locator\` 收录 `locator_core.py`（解析层）、`build_index.py`（建索引）、
`query.py`（查询与偏移反查）与原始 `README.md`。它依赖本目录的
`ace3_pack` / `_r104_glyphfix2` / `_r211_cmap` / `_r223_apply_plan`。

---

## 依赖

- `numpy`：`front_atlas`、`_r104_glyphfix2`、`_r104_audit2`、`_r104_fontmap_lookup`
- `Pillow`（`PIL`）：仅 `_r104_glyphfix2`（图像输出）
- 其余模块纯标准库，无需第三方包

---

## 目录布局

这些模块按**扁平 import** 写（`import ace3_pack`、`from _r211_cmap import code2char`），
不带包前缀。调用方必须在 import 之前把本目录加入 `sys.path`：

```python
import os, sys
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 指向本目录
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import ace3_pack
```

`workshop\` 与 `ace3_text_locator\` 就是这么做的。把本目录改名、拆散或作为包（加
`__init__.py`）安装，都会让上层 import 失败。

### 工程根目录

`_r206_memfix.py`、`_r211_override.py`、`ace3_text_locator\locator_core.py` 需要知道
工程根目录（读译文 TSV、临时目录、原版 ISO）。解析顺序：

1. 环境变量 `ACE3_ROOT`
2. 由模块自身位置向上推导

想指向别处就设 `ACE3_ROOT`：

```powershell
$env:ACE3_ROOT = "<工程根目录>"
```

---

## 免责

这些模块会**读写 ISO 内的游戏资源**，属于汉化工程内部工具：

- 只对日版 SLPS-25784 的特定数据结构有效，换版本、换区域或换游戏都不保证可用；
- 参数写错会直接改坏镜像，**不保证可恢复**；
- 使用前务必备份原始文件，先用副本验证，确认结果再动正式盘；
- 自担风险。

---

## 许可

与仓库根 `LICENSE`（MIT）一致，适用范围见 `LICENSE-SCOPE.md`。
