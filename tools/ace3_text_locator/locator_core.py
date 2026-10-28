#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACE3 统一文本检索索引层（只读）。

设计约束（来自《ACE3 高速文本检索与故障诊断系统·建设方案》）：
  * 只读：绝不写 ISO / 字体包 / 权威译文表 / 工程事实库.sqlite。
  * 独立：不走 ace3_text.run() 的 ident 身份闸门，自建只读适配层。
  * 复用：databin_toc / locate / Bnd2 / ver_blocks 直接 import，不重新实现。
  * 解码：用工程权威码表 _r211_cmap.code2char() 的快照，**绝不用 CP932 直解盘面**。
    CP932 只作为"仅供对照"的附加列存在。

索引是**可删除、可重建的检索缓存**，不是工程事实库。
"""

import hashlib
import json
import os
import re
import sqlite3
import struct
import sys
import time

ROOT = os.environ.get("ACE3_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from _r104_glyphfix2 import databin_toc                     # noqa: E402
from _r223_apply_plan import locate                         # noqa: E402

SCHEMA_VERSION = 2
TAGRE = re.compile(rb"<[^<>]{0,96}>")
TAGRE_S = re.compile(r"<[^<>]{0,96}>")

# SJIS 风格双字节区（ACE3 自定义中文码位全部落在 0x81..0x9F / 0xE0..0xFC）
def _is_lead(c):
    return 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC


# ---------------------------------------------------------------- 码表快照
def load_code_table():
    """工程权威码位→字符表（R211 口径）。返回 (code2char, source_note)。"""
    from _r211_cmap import code2char as _c2c
    m = dict(_c2c())
    note = ("_r211_cmap.code2char() = load_cur(tmp_r206_memfix) + "
            "newcode_map{r205} + REPAINTED{_r211_override}")
    return m, note


def cp932_view(seg):
    """仅供对照：同一段字节按 CP932 直解的结果。解不出用 \ufffd。"""
    try:
        return seg.decode("cp932", errors="replace")
    except Exception:
        return ""


# ---------------------------------------------------------------- 槽解码
def decode_seg(seg, c2c):
    """把一条槽的原始字节解码成 (plain_text, codes, stats)。

    stats = {"unk": 未知码位数, "ctrl": 控制字节数, "bytes": 原始长度}

    规则：
      * `<...>` 控制标签整体切走，原样保留（不进 plain）
      * 单字节 <0x80  -> chr(c)（0x00..0x1F 计为 ctrl，除非 \\n）
      * 单字节 0xA1..0xDF -> 半角片假名 U+FF61..
      * 双字节 lead -> 查工程码表；查不到记 unk，用 〔XXXX〕 占位
    ※ 绝不用 CP932 兜底：查不到就是查不到，宁可暴露 UNKNOWN。
    """
    out = []
    codes = []
    unk = 0
    ctrl = 0
    i = 0
    n = len(seg)
    while i < n:
        c = seg[i]
        if c == 0x3C:                      # '<' 尝试吃掉整段标签
            m = TAGRE.match(seg, i)
            if m:
                i = m.end()
                continue
        if c < 0x80:
            if c < 0x20 and c != 0x0A:
                ctrl += 1
            out.append(chr(c))
            i += 1
            continue
        if c == 0x7F:
            ctrl += 1
            i += 1
            continue
        if 0xA1 <= c <= 0xDF:
            out.append(chr(0xFF61 + c - 0xA1))
            i += 1
            continue
        if _is_lead(c):
            if i + 1 >= n:
                unk += 1
                out.append("\u3014%02X\u3015" % c)
                codes.append(c << 8)
                break
            code = (c << 8) | seg[i + 1]
            codes.append(code)
            ch = c2c.get(code)
            if ch is None:
                unk += 1
                out.append("\u3014%04X\u3015" % code)
            else:
                out.append(ch)
            i += 2
            continue
        # 0x80 / 0xA0 / 0xFD..0xFF：非法单字节
        unk += 1
        out.append("\u3014%02X\u3015" % c)
        i += 1
    return "".join(out), codes, {"unk": unk, "ctrl": ctrl, "bytes": len(seg)}


def quality(plain, stats):
    """槽质量分级，用来把"二进制数据段"与"文本"分开，但**不丢记录**。

    1 = 干净文本（无未知码位、无控制字节）
    2 = 含少量未知/控制（疑似缺字或缺映射，诊断时优先看这些）
    0 = 二进制/数据段（未知或控制占比过高，不是文本）
    """
    if not plain.strip():
        return 0
    bad = stats["unk"] + stats["ctrl"]
    if bad == 0:
        return 1
    frac = bad / float(max(1, len(plain)))
    return 0 if frac > 0.25 else 2


def split_tags(seg):
    """(带标签的可读串, 纯文本串)。标签形如 <operator(23010,1)>，原样保留在可读串里。"""
    text = seg.decode("latin-1")           # 逐字节一一对应，仅供人类看
    plain = TAGRE.sub(b"", seg).decode("latin-1")
    return text, plain


def seg_of(data, base, offs, i):
    """取第 i 槽的原始字节（与 ace3_scan._seg_at 同口径，但 O(1)）。

    offs 已由 ver_blocks 排序去重，故 "下一个更大的偏移" 就是 offs[i+1]。
    """
    s = base + offs[i]
    if s >= len(data):
        return b""
    e = base + offs[i + 1] if i + 1 < len(offs) else len(data)
    if e > len(data):
        e = len(data)
    seg = data[s:e]
    j = seg.find(b"\x00")
    if j >= 0:
        seg = seg[:j]
    return seg


# ---------------------------------------------------------------- ISO 指纹
def iso_fingerprint(path, use_md5=True):
    st = os.stat(path)
    fp = {"path": os.path.abspath(path), "size": st.st_size,
          "mtime": int(st.st_mtime), "md5": ""}
    if use_md5:
        h = hashlib.md5()
        with open(path, "rb") as f:
            while True:
                b = f.read(1 << 22)
                if not b:
                    break
                h.update(b)
        fp["md5"] = h.hexdigest()
    return fp


# ---------------------------------------------------------------- 扫描
# 15 个字体包 eid（工程权威口径，见 tools/ace3_font.py:42-43）。
# 它们是图集+码表的二进制资源，不是文本块；默认跳过扫描并在 meta 里注明，
# 免得几千万个垃圾"槽"塞满索引。第二阶段做字体诊断时单独读这些 eid。
FONT_PKGS = frozenset([1200000, 1200001, 1200002, 1200003, 1200004, 1200005,
                       1200006, 1200007, 1200011, 4002050, 4002051, 4002052,
                       4002053, 4002054, 4002057])


def scan_iso(path, c2c, progress=None, limit_eids=None, want_rows=True,
             skip_eids=None):
    """全盘扫描，产出槽级记录。

    yield dict(eid,tag,k,slot,abs_off,base,comp,seg,codes,plain,unknown,q,is_text)
    """
    toc = databin_toc(path)
    eids = sorted(toc)
    if limit_eids:
        eids = eids[:limit_eids]
    if skip_eids:
        eids = [e for e in eids if e not in skip_eids]
    total = len(eids)
    with open(path, "rb") as f:
        for idx, eid in enumerate(eids):
            if progress and idx % 200 == 0:
                progress(idx, total, eid)
            try:
                res = locate(path, toc, eid)
            except Exception as exc:                    # 单条失败不拖垮全盘
                yield {"eid": eid, "error": repr(exc)[:200]}
                continue
            for (tag, k), (abs_off, data, base, offs, comp) in res.items():
                nslots = len(offs)
                for si in range(nslots):
                    seg = seg_of(data, base, offs, si)
                    # 槽在 ISO 里的绝对文件偏移区间。
                    # 实测口径（见 README「偏移口径」）：
                    #   file_off = abs_off + base + offs[si]
                    #   file_end = abs_off + base + (offs[si+1] 或 len(data))
                    # abs_off 已含 ISOBASE，故这就是 ISO 文件真实偏移。
                    f_off = abs_off + base + offs[si]
                    f_end = (abs_off + base + (offs[si + 1] if si + 1 < nslots
                                               else len(data)))
                    if not want_rows:
                        yield {"eid": eid, "tag": tag, "k": k, "slot": si,
                               "nslots": nslots, "seg_len": len(seg),
                               "file_off": f_off, "file_end": f_end}
                        continue
                    plain, codes, st = decode_seg(seg, c2c)
                    q = quality(plain, st)
                    yield {
                        "eid": eid, "tag": tag, "k": k, "slot": si,
                        "nslots": nslots, "abs_off": abs_off, "base": base,
                        "comp": 1 if comp else 0, "seg": seg,
                        "codes": codes, "plain": plain,
                        "unknown": st["unk"], "ctrl": st["ctrl"], "q": q,
                        "is_text": 1 if q else 0,
                        "file_off": f_off, "file_end": f_end,
                    }


def iter_eids(path, skip_eids=None, limit_eids=None):
    """按 eid 逐个 yield {"eid":..., "blocks":{(tag,k): {abs_off,data,base,offs,comp}}}
    或 {"eid":..., "error":...}。

    供增量索引（日文缓存等）复用同一套 locate 口径，避免再写一份解析。
    """
    toc = databin_toc(path)
    eids = sorted(toc)
    if limit_eids:
        eids = eids[:limit_eids]
    if skip_eids:
        eids = [e for e in eids if e not in skip_eids]
    for eid in eids:
        try:
            res = locate(path, toc, eid)
        except Exception as exc:
            yield {"eid": eid, "error": repr(exc)[:200]}
            continue
        blocks = {}
        for (tag, k), (abs_off, data, base, offs, comp) in res.items():
            blocks[(tag, k)] = {"abs_off": abs_off, "data": data, "base": base,
                                "offs": offs, "comp": comp}
        yield {"eid": eid, "blocks": blocks}


def probe(path, c2c, limit_eids=None, skip_eids=None):
    """只统计规模，不写库。用于定 schema 参数。"""
    t0 = time.time()
    n_eid = n_slot = n_text = n_code = n_err = 0
    qdist = {0: 0, 1: 0, 2: 0}
    chars = {}
    bigrams = set()
    plain_chars = 0
    max_slots = 0
    last_eid = None
    for row in scan_iso(path, c2c, limit_eids=limit_eids, skip_eids=skip_eids):
        if "error" in row:
            n_err += 1
            continue
        if row["eid"] != last_eid:
            last_eid = row["eid"]
            n_eid += 1
        n_slot += 1
        max_slots = max(max_slots, row.get("nslots", 0))
        n_code += len(row["codes"])
        qdist[row["q"]] = qdist.get(row["q"], 0) + 1
        if row["is_text"]:
            n_text += 1
            p = row["plain"]
            plain_chars += len(p)
            for ch in p:
                chars[ch] = chars.get(ch, 0) + 1
            for i in range(len(p) - 1):
                bigrams.add(p[i:i + 2])
    return {
        "elapsed_s": round(time.time() - t0, 2),
        "eids_scanned": n_eid, "errors": n_err,
        "slots": n_slot, "text_slots": n_text,
        "q1_clean": qdist[1], "q2_suspect": qdist[2], "q0_binary": qdist[0],
        "codes": n_code, "plain_chars": plain_chars,
        "distinct_chars": len(chars),
        "distinct_bigrams": len(bigrams),
        "max_slots_per_block": max_slots,
    }
