#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R213：**唯一正确的"条目解压"入口**（本班踩坑后固化）。

坑：`_r104_glyphfix2.decompress_block()` 用的是 `lzss_decode(blob, 8, usize)` —— **偏移 8 是错的**。
    实测 eid 701349（片尾 staff）头是 `00 01 00 00 | 00 20 4d d0 | 00 00 00 00`，
    正文从 **偏移 12** 开始；用偏移 8 只能解出 12 字节就 ended ⇒ 工具内部回落成 raw ⇒
    **所有压缩条目的文本在扫描里"消失"**（本班第一版 `_r213_garbage.py` / `_r213_slots.py` /
    `_r213_findslot.py` 全部中招，已全部改为调用本模块）。
    正确入口（`_r212_lzsub.py` 一直用的是这个）：`lz_encode.decode(blob[12:], n)`，n = 偏移 4 的大端 u32。
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lz_encode        # noqa: E402

MAGIC = b"\x00\x01\x00\x00"
MAX_N = 1 << 26


def is_lz(blob):
    return len(blob) > 12 and blob[:4] == MAGIC


def decompress_any(blob):
    """返回 (data, how)；how ∈ {'lz','raw'}。压缩块解压失败时返回 (blob,'raw')。"""
    if not blob:
        return blob, "raw"
    if is_lz(blob):
        n = struct.unpack_from(">I", blob, 4)[0]
        if 0 < n <= MAX_N:
            try:
                r = lz_encode.decode(blob[12:], n)
                r = r[0] if isinstance(r, tuple) else r
                if r is not None and len(r) >= 16:
                    return bytes(r), "lz"
            except Exception:      # noqa: BLE001
                pass
    return blob, "raw"
