#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R211：**正确的"码 -> 字"反查表**（本班多次翻车的根因，独立成模块，所有工具都用它）。

为什么不能直接用 `load_cur()` 的 c2h：
  · `load_cur()` 里 map_r203 先进（setdefault），**旧码优先**；
  · R206/R210/R211 的"腾字"把某些码**改画**了（同码换字）：
      8ADB 原 咱 -> 现 呜 ;  8EDD 畏->邪 ;  8FA4 绪->默 ;  8FD3 胎->叔 ;
      8A95 厢->娃 ;  8764 -> 肃 ;  878E -> 嘲
  · 于是 `c2h[0x8ADB]` 仍是"咱"，用它解码会把"呜哦！！"读成"咱哦！！"，
    再拿"咱"去查可画性 ⇒ **假阳性**（本班 f138 白跑一张盘就是栽在这）。

本模块的做法与 `_r211_override.py` 一致（权威口径）：
  ① 先取 load_cur() 的 c2h
  ② 用 tmp_r205/newcode_map{.tsv,2.tsv} 补登记（不覆盖已存在的码）
  ③ **最后**用"腾字改画表"覆盖同码旧字（REPAINTED）
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _r206_memfix import load_cur          # noqa: E402
from _r211_override import REPAINTED, load_registry   # noqa: E402

TAG = re.compile(r"<[^>]*>")


def norm_key(s):
    """★统一的"内容归一化"：去标签 + 去空白 + **去字面 `\\n`**。

    R210 F18 / 铁则 366 早就写明：《总对照表》《剧情对照表》里**有的行存真换行、有的存字面 `\n`**，
    比对前必须先归一化。本班一开始漏了"去字面 \\n"，导致剧情对照表 19,200 行里
    **5,395 行永远匹配不上**，被误报成"译表里没有这条"（这是"为什么还剩这么多未翻译"的假象来源之一）。
    """
    s = (s or "").replace("\\n", "")
    return re.sub(r"[\s\u3000]+", "", TAG.sub("", s))


def code2char():
    c2h_cur, _h2c = load_cur()
    m = dict(c2h_cur)
    for c, ch in load_registry().items():
        m.setdefault(c, ch)
    for c, ch in REPAINTED.items():
        m[c] = ch          # ★最后覆盖
    return m


def char2codes():
    out = {}
    for c, ch in code2char().items():
        out.setdefault(ch, set()).add(c)
    return out


if __name__ == "__main__":
    m = code2char()
    for c in (0x8ADB, 0x8EDD, 0x8FA4, 0x8FD3, 0x8A95, 0x8764, 0x878E,
              0x92E9, 0x92EE, 0x92EF, 0x92F0, 0x92F1, 0x92BA, 0x92E5, 0x92B4):
        print("  %04X -> %s" % (c, m.get(c, "（不在表里）")))
