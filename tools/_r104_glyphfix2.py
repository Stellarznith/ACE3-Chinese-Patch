#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R104 缺字闭环 v2：LZ 解压所有文本块 → 需求全集 → 对 13 个字体包 FontMap →
补 glyph（含空白占位格回收）→ 写盘 → 逐条自检。

约束（按用户口径）：不动 ELF / 不动 1024x1024 TH10 双页连续方案 / 不拆字体 /
不找新落点 / 不做 page selector。只动包内 tag2000（图集空位内容）与 tag2500（record UV/半字/必要的码区间扩展）。

用法：
  python -X utf8 tools/_r104_glyphfix2.py --audit
  python -X utf8 tools/_r104_glyphfix2.py --apply --src build/ACE3-CN-FONT-R24-370000.iso \
      --dst build/ACE3-CN-FONT-R29-GLYPHFIX2.iso
"""
import argparse
import importlib.util
import os
import shutil
import struct
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
from ace3_pack import Bnd2                                        # noqa: E402

# ★R224：**别名盘修正**（存档记的"别名盘读取器陷阱"）
#   R221 把 tag2001/tag2501 的 offset 别名到 tag2000/tag2500 后，两个成员**共享同一 offset**，
#   而 Bnd2.blob() 用"按 offset 排序后的**下一个**成员"当上界 ⇒ 共享者被判成长度 0
#   （`struct.error: ... actual size is 0`）。
#   这里在本进程内把上界改成"下一个**严格大于**本 offset 的成员" —— 不改 ace3_pack.py 源文件。
def _blob_fixed(self, i):
    off = self.table[i][1]
    end = self.size
    for _t, o in self.table:
        if o > off and o < end:
            end = o
    return self.data[off:end]


Bnd2.blob = _blob_fixed
from front_atlas import decode as fa_decode, encode as fa_encode  # noqa: E402
from _r104_audit2 import all_codes, databin_toc, sjis_next        # noqa: E402
from _r104_text_audit import load_rev                             # noqa: E402
from lz_decode_entry import lzss_decode                           # noqa: E402

ISOBASE = 0x422800
EXPORT = os.path.join(HERE, "导出_字体重绘_思源黑19_描边1_20260920")
PKGS = [4002050, 4002051, 4002052, 4002053, 4002054, 4002057,
        1200000, 1200001, 1200004, 1200005, 1200006, 1200007, 1200011]
CELL = 19
LZ_MAGIC = b"\x00\x01\x00\x00"


def _load_r95():
    p = os.path.join(HERE, "tools", "_r95_missing_codes.py")
    spec = importlib.util.spec_from_file_location("_r95mc", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Pkg:
    def __init__(self, iso, eid):
        off, size = databin_toc(iso)[eid]
        self.eid, self.off, self.size = eid, off, size
        with open(iso, "rb") as f:
            f.seek(ISOBASE + off)
            self.blob = f.read(size)
        b = Bnd2(self.blob)
        self.b = b
        self.at = b.blob(b.tag_index(2000))
        self.fm = bytearray(b.blob(b.tag_index(2500)))
        tex0 = struct.unpack_from("<Q", self.at, 0x10)[0]
        self.th = (tex0 >> 30) & 0xF
        self.h = 512 if self.th == 9 else 1024
        self.payload = (self.h * 1024) // 2
        # ★（R160）宽度按载荷推导（常规 1024；1200002/1200003 = 512×512 小图集）
        _raw = bytes(self.at[0x20:0x20 + self.payload])
        self.w = max(1, (len(_raw) * 2) // max(1, self.h))
        self.atlas = fa_decode(_raw, h=self.h, w=self.w)
        u32 = lambda o: struct.unpack_from("<I", self.fm, o)[0]
        self.n = struct.unpack_from("<H", self.fm, 0x10)[0]
        self.ib, self.rb = u32(0x14), u32(0x18)
        self.nrec = (len(self.fm) - self.rb) // 24
        self.entries = [struct.unpack_from("<3I", self.fm, self.ib + i * 12)
                        for i in range(self.n)]
        self.code2idx = {}
        for i, (lo, hi, base) in enumerate(self.entries):
            # ★R174（关键修正）：引擎的查表公式是 **base + (code - lo)**（字节差），
            #   不是"SJIS 有效码序号"。两者只在区间跨过 0x7F 时差 1（0x837E→0x8380 跳过 0x837F），
            #   而原版区间都在 0x837E/0x8380 处断开、从不跨 —— 我们若用 sjis_next 数序号，
            #   就会把 0x8386(达) 算成 rec276，而引擎算成 rec277(ョ) ⇒ 屏上「达」变「反向 E」。
            #   实测证据：GS 绘制清单里该字采样的是 atlas=(954,73)=rec277 的格子。
            for c in range(lo, hi + 1):
                self.code2idx[c] = base + (c - lo)

    def rect(self, idx):
        o = self.rb + idx * 24
        u0, v0, u1, v1 = struct.unpack_from("<4f", self.fm, o)
        # ★R194：u 的归一基准 = **本包图集宽**（大包 1024 / F、G 小包 512）。
        #   地面真值：原版 1200002 的记录 u*512 ≤ 495 且 100% 落在 record 矩形内；
        #   若仍按 ×1024 解，512 宽图集里 x≥256 的记录会被判"越界/无墨"（假红）。
        return (int(round(u0 * self.w)), int(round(v0 * self.h)),
                int(round(u1 * self.w)) - 1, int(round(v1 * self.h)) - 1)

    def halfwords(self, idx):
        return struct.unpack_from("<4H", self.fm, self.rb + idx * 24 + 16)

    def ink(self, idx):
        x0, y0, x1, y1 = self.rect(idx)
        H, W = self.atlas.shape
        if x0 < 0 or y0 < 0 or x1 >= W or y1 >= H or x1 < x0 or y1 < y0:
            return 0
        return int((self.atlas[y0:y1 + 1, x0:x1 + 1] != 0).sum())


def decompress_block(blob):
    """返回 (data, how)；LZSS 块先解压。"""
    for off in (0,):
        if blob[:4] == LZ_MAGIC and len(blob) > 8:
            usize = struct.unpack_from(">I", blob, 4)[0]
            if 0 < usize <= (1 << 26):
                out, ended, _p = lzss_decode(blob, 8, usize)
                if len(out) >= 16:
                    return out, "lz"
    return blob, "raw"


def codes_in(data, valid):
    """NUL 分隔、整串都是合法码/ASCII 的串里的码；再补一遍“直接成对出现”的合法码。"""
    out = set()
    n = len(data)
    i = 0
    while i < n:
        j = data.find(b"\x00", i)
        if j < 0:
            j = n
        p = i
        run = []
        ok = True
        while p < j:
            b = data[p]
            if b >= 0x81 and p + 1 < j:
                c = (b << 8) | data[p + 1]
                if c in valid:
                    run.append(c)
                    p += 2
                    continue
                ok = False
                break
            if 0x20 <= b < 0x7F:
                run.append(b)
                p += 1
                continue
            ok = False
            break
        if ok and len(run) >= 2:
            out.update(run)
        i = j + 1
    return out


def load_assets():
    from PIL import Image
    pages = [np.array(Image.open(os.path.join(EXPORT, "atlas_p%d_Bold.png" % p)).convert("L"))
             for p in (0, 1)]
    rows = []
    with open(os.path.join(EXPORT, "layout_all.tsv"), encoding="utf-8", errors="replace") as f:
        f.readline()
        for ln in f:
            c = ln.rstrip("\n").split("\t")
            if len(c) < 11:
                continue
            try:
                rows.append((c[0], int(c[2]), int(c[5]), int(c[6])))
            except ValueError:
                continue
    return pages, rows


def detect_bold(rows, pages, ref_bits):
    per = len(rows) // 4
    for b in range(4):
        for ch, pg, cx, cy in rows[b * per:(b + 1) * per]:
            cell = pages[pg][cy:cy + CELL, cx:cx + CELL]
            if ref_bits is not None and cell.tobytes() == ref_bits:
                return b
    best = (-1, -1)
    for b in range(4):
        seg = rows[b * per:(b + 1) * per][:300]
        ink = sum(int((pages[pg][cy:cy + CELL, cx:cx + CELL] > 0).sum())
                  for _ch, pg, cx, cy in seg)
        if ink > best[0]:
            best = (ink, b)
    return best[1]


def char_of(code, rev):
    # ★ 口径修正：游戏 CN 文本用的是 **cp932 码位**（实证：战役=8157/96F0、生=90B6、体=91CC），
    #   所以优先按 cp932 解码；只有解不出（简体专用字/私用码）才退回反查表。
    if code < 0x100:
        return None
    try:
        ch = bytes([code >> 8, code & 0xFF]).decode("cp932")
        if ch and ch != "\ufffd":
            return ch
    except Exception:
        pass
    return rev.get(code)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(HERE, "build", "ACE3-CN-FONT-R24-370000.iso"))
    ap.add_argument("--dst", default=os.path.join(HERE, "build", "ACE3-CN-FONT-R29-GLYPHFIX2.iso"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--extra", default="", help="显式追加要补的字符（例如截图里已确认缺的字）")
    ap.add_argument("--pkgs", default="", help="只处理这些包 id（逗号分隔），空=全部")
    a = ap.parse_args()
    rev = load_rev()
    pages, rows = load_assets()
    ref = Pkg(a.src, 4002050)
    idx = ref.code2idx[0x8157]
    x0, y0, x1, y1 = ref.rect(idx)
    ref_bits = ref.atlas[y0:y1 + 1, x0:x1 + 1].tobytes() if (x1 - x0 + 1, y1 - y0 + 1) == (CELL, CELL) else None
    blk = detect_bold(rows, pages, ref_bits)
    per = len(rows) // 4
    char2cell = {}
    for ch, pg, cx, cy in rows[blk * per:(blk + 1) * per]:
        if ch:
            char2cell.setdefault(ch, (pg, cx, cy))
    print("Bold 段=第%d段（%s）；资产字符=%d" % (blk, "字形匹配" if ref_bits else "退化", len(char2cell)))

    extra_codes = set()
    for ch in a.extra:
        try:
            b = ch.encode("cp932")
        except Exception:
            continue
        extra_codes.add(b[0] if len(b) == 1 else (b[0] << 8) | b[1])
    only = set(int(x) for x in a.pkgs.split(",") if x.strip()) if a.pkgs else None
    plans = {}
    for eid in PKGS:
        if only and eid not in only:
            continue
        try:
            P = Pkg(a.src, eid)
        except Exception as e:
            print("  [%d] 读取失败 %s" % (eid, e))
            continue
        valid = set(P.code2idx)
        need = set()
        kinds = {}
        for i, (t, _o) in enumerate(P.b.table):
            if t in (2000, 2500):
                continue
            try:
                sb = bytes(P.b.blob(i))
            except Exception:
                continue
            data, how = decompress_block(sb)
            kinds[how] = kinds.get(how, 0) + 1
            need |= codes_in(data, valid)
        need = set(c for c in need if c >= 0x100 and c not in (0x0A, 0x0D))
        no_code = [c for c in sorted(need) if c not in P.code2idx]
        blank = [c for c in sorted(need) if c in P.code2idx and P.ink(P.code2idx[c]) == 0]
        todo, unfix = [], []
        for c in sorted(set(no_code) | set(blank) | extra_codes):
            if c in [x for x, _ in todo]:
                continue
            if c in [x for x, _ in unfix]:
                continue
            ch = char_of(c, rev)
            (todo if (ch and ch in char2cell) else unfix).append((c, ch))
        plans[eid] = dict(P=P, need=need, no_code=no_code, blank=blank, todo=todo, unfix=unfix)
        print("  [%d] 块=%d%s 需求码=%d 缺码=%d 空白格=%d 可补=%d"
              % (eid, len(P.b.table), "(LZ %d)" % kinds.get("lz", 0) if kinds.get("lz") else "",
                 len(need), len(no_code), len(blank), len(todo)))
    if not a.apply:
        print("（audit 模式，未写盘）")
        return 0

    shutil.copyfile(a.src, a.dst)
    with open(a.dst, "r+b") as f:
        for eid, info in plans.items():
            P = info["P"]
            if not info["todo"]:
                continue
            H, W = P.atlas.shape
            occ = np.zeros((H, W), dtype=bool)
            needed_idx = set()
            for c in info["need"]:
                i2 = P.code2idx.get(c)
                if i2 is not None and i2 < P.nrec:
                    needed_idx.add(i2)
            blank_cells = []
            for i2 in range(P.nrec):
                x0, y0, x1, y1 = P.rect(i2)
                if x1 < x0 or y1 < y0 or x1 >= W or y1 >= H or x0 < 0 or y0 < 0:
                    continue
                if P.ink(i2) > 0:
                    occ[y0:y1 + 1, x0:x1 + 1] = True
                elif i2 not in needed_idx:
                    blank_cells.append((x0, y0, x1, y1))
            free = []
            for y in range(H - CELL, -1, -CELL):
                for x in range(0, W - CELL + 1, CELL):
                    if not occ[y:y + CELL, x:x + CELL].any():
                        free.append((x, y))
            for (x0, y0, x1, y1) in blank_cells:
                if x1 - x0 + 1 >= CELL and y1 - y0 + 1 >= CELL and not occ[y0:y0 + CELL, x0:x0 + CELL].any():
                    free.append((x0, y0))
            atlas = P.atlas.copy()
            fm = bytearray(P.fm)
            tmpl = None
            for i2 in range(P.nrec):
                x0, y0, x1, y1 = P.rect(i2)
                if P.ink(i2) > 0 and (x1 - x0 + 1, y1 - y0 + 1) == (CELL, CELL):
                    tmpl = P.halfwords(i2)
                    break
            busy = set(needed_idx)
            done = 0
            # ★ 目标字优先：用户已确认缺的字（--extra）先占格，避免被大量日文遗留字挤掉
            pri = [(c, ch) for c, ch in info["todo"] if c in extra_codes]
            rest = [(c, ch) for c, ch in info["todo"] if c not in extra_codes]
            for code, ch in pri + rest:
                if not free:
                    break
                pg, cx, cy = char2cell[ch]
                cell = pages[pg][cy:cy + CELL, cx:cx + CELL]
                if not (cell > 0).any():
                    continue
                x, y = free.pop(0)
                if occ[y:y + CELL, x:x + CELL].any():
                    continue
                occ[y:y + CELL, x:x + CELL] = True
                atlas[y:y + CELL, x:x + CELL] = cell
                i2 = P.code2idx.get(code)
                if i2 is None:
                    cand = None
                    for e, (lo, hi, base) in enumerate(P.entries):
                        if hi < code and (cand is None or hi > P.entries[cand][1]):
                            cand = e
                    if cand is None:
                        continue
                    lo, hi, base = P.entries[cand]
                    rng = []
                    c2 = lo
                    while c2 <= code:
                        rng.append(c2)
                        c2 = sjis_next(c2)
                    i2 = base + len(rng) - 1
                    if i2 >= P.nrec or any((base + j) in busy for j in range(1, len(rng))):
                        continue
                    struct.pack_into("<I", fm, P.ib + cand * 12 + 4, code)
                    P.entries[cand] = (lo, code, base)
                    P.code2idx[code] = i2
                if i2 in busy and P.ink(i2) > 0:
                    continue
                o = P.rb + i2 * 24
                struct.pack_into("<4f", fm, o,
                                 x / 1024.0, y / float(P.h),
                                 (x + CELL) / 1024.0, (y + CELL) / float(P.h))
                if tmpl:
                    struct.pack_into("<4H", fm, o + 16, *tmpl)
                busy.add(i2)
                done += 1
            if not done:
                print("  [%d] 无空位/无可补，跳过" % eid)
                continue
            b = Bnd2(P.blob)
            blobs = [bytes(b.blob(i)) for i in range(b.count)]
            blobs[b.tag_index(2000)] = bytes(P.at[:0x20]) + fa_encode(atlas, h=P.h) + bytes(P.at[-0x40:])
            blobs[b.tag_index(2500)] = bytes(fm)
            new_blob = b.rebuild(blobs, keep_offsets=True)
            if len(new_blob) != len(P.blob):
                raise SystemExit("pkg=%d 长度变化" % eid)
            f.seek(ISOBASE + P.off)
            f.write(new_blob)
            print("  [%d] 补 %d 字（共需补 %d，资产缺 %d）" % (eid, done, len(info["todo"]), len(info["unfix"])))

    print("=== 自检（逐条回读）===")
    bad = 0
    for eid, info in plans.items():
        P2 = Pkg(a.dst, eid)
        for c in info["no_code"] + info["blank"]:
            i2 = P2.code2idx.get(c)
            if i2 is None or i2 >= P2.nrec or P2.ink(i2) == 0:
                ch = char_of(c, rev)
                if ch and ch in char2cell:
                    bad += 1
                    if bad <= 12:
                        print("   ★ pkg=%d code=0x%04X (%s) 仍缺" % (eid, c, ch))
    print("自检：本应补上却仍缺 = %d" % bad)
    import hashlib
    h = hashlib.md5()
    with open(a.dst, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 23), b""):
            h.update(chunk)
    print("dst md5 = %s" % h.hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
