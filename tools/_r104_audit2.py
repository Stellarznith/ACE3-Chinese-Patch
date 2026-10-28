# -*- coding: utf-8 -*-
"""R104: correct font audit using the ISO's own DATA.BIN TOC (analysis_data_toc.txt
is stale for the R12/R23 line and points at the wrong entries).

Layout measured on R23 entry 4002050:
  tag2000 = 524384 B  -> 0x20 header + 1024x1024 PSMT4 (0x80000) + 0x40 CLUT
  tag2500 = 131872 B  -> n=3223 index entries at 0x20 (12 B), records at 0x9734 (24 B)
  record: 4 floats uv (x*1024, y*1024) + 4 halfwords

usage:
  python -X utf8 tools/_r104_audit2.py chars 战役途束菜
  python -X utf8 tools/_r104_audit2.py pages
  python -X utf8 tools/_r104_audit2.py blank
"""
import argparse
import os
import struct
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
from ace3_pack import Bnd2      # noqa: E402
from front_atlas import decode  # noqa: E402
from _r104_text_audit import load_rev  # noqa: E402

ISOBASE = 0x422800
SECTOR = 2048
DATA_LBA = 2117
PKG = 4002050


def databin_toc(iso):
    with open(iso, "rb") as f:
        f.seek(DATA_LBA * SECTOR)
        head = f.read(0x20)
        cnt = struct.unpack_from("<I", head, 0x10)[0]
        toc = f.read(cnt * 16)
    out = {}
    for i in range(cnt):
        a, sz, o, e = struct.unpack_from("<IIII", toc, i * 16)
        out[e] = (o, sz)
    return out


class Pkg2:
    def __init__(self, iso, eid=PKG):
        off, size = databin_toc(iso)[eid]
        with open(iso, "rb") as f:
            f.seek(ISOBASE + off)
            b = Bnd2(f.read(size))
        self.atlas_raw = b.blob(b.tag_index(2000))
        self.m = b.blob(b.tag_index(2500))
        self.tex0 = struct.unpack_from("<Q", self.atlas_raw, 0x10)[0]
        self.atlas = decode(bytes(self.atlas_raw[0x20:0x20 + 0x80000]), h=1024)
        u32 = lambda o: struct.unpack_from("<I", self.m, o)[0]
        self.n = struct.unpack_from("<H", self.m, 0x10)[0]
        self.idx_base = u32(0x14)
        self.rec_base = u32(0x18)
        self.nrec = (len(self.m) - self.rec_base) // 24
        self.entries = [struct.unpack_from("<3I", self.m, self.idx_base + i * 12)
                        for i in range(self.n)]

    def idx_of(self, code):
        lo_i, hi_i = 0, self.n - 1
        while lo_i <= hi_i:
            mid = (lo_i + hi_i) // 2
            lo, hi, base = self.entries[mid]
            if code < lo:
                hi_i = mid - 1
            elif code > hi:
                lo_i = mid + 1
            else:
                return base + (code - lo)
        return None

    def rec(self, idx):
        o = self.rec_base + idx * 24
        u0, v0, u1, v1 = struct.unpack_from("<4f", self.m, o)
        h = struct.unpack_from("<4H", self.m, o + 16)
        return u0, v0, u1, v1, h

    def rect(self, idx):
        u0, v0, u1, v1, hh = self.rec(idx)
        return (int(round(u0 * 1024)), int(round(v0 * 1024)),
                int(round(u1 * 1024)) - 1, int(round(v1 * 1024)) - 1, hh)

    def ink(self, idx):
        x0, y0, x1, y1, hh = self.rect(idx)
        H, W = self.atlas.shape
        if x0 < 0 or y0 < 0 or x1 >= W or y1 >= H or x1 < x0 or y1 < y0:
            return -1, (x0, y0, x1, y1), hh
        return int((self.atlas[y0:y1 + 1, x0:x1 + 1] != 0).sum()), (x0, y0, x1, y1), hh

    def code_of(self, idx):
        for lo, hi, base in self.entries:
            if base <= idx <= base + (hi - lo):
                # 码按 SJIS 步进
                c = lo
                k = 0
                while c <= hi:
                    if base + k == idx:
                        return c
                    c = sjis_next(c)
                    k += 1
        return None


def sjis_next(c):
    if c < 0x100:
        return c + 1
    hi, lo = c >> 8, c & 0xFF
    lo += 1
    if lo == 0x7F:
        lo = 0x80
    if lo == 0xFD:
        lo = 0x40
        hi += 1
    return (hi << 8) | lo


def all_codes(P):
    out = []
    for lo, hi, base in P.entries:
        c = lo
        k = 0
        while c <= hi:
            out.append((c, base + k))
            c = sjis_next(c)
            k += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--iso", default=os.path.join(HERE, "build", "ACE3-CN-FONT-R23.iso"))
    ap.add_argument("--pkg", type=int, default=PKG)
    a = ap.parse_args()
    P = Pkg2(a.iso, a.pkg)
    rev = load_rev()
    print("iso=%s pkg=%d atlas=%s TEX0=0x%016X TBP0=0x%04X TW=%d TH=%d | FontMap n=%d rec=%d"
          % (os.path.basename(a.iso), a.pkg, P.atlas.shape, P.tex0, P.tex0 & 0x3FFF,
             (P.tex0 >> 26) & 0xF, (P.tex0 >> 30) & 0xF, P.n, P.nrec))
    codes = all_codes(P)
    print("码位数=%d" % len(codes))
    if a.cmd == "chars":
        for ch in a.rest:
            c = None
            for code, _i in codes:
                if rev.get(code) == ch:
                    c = code
                    break
            if c is None:
                print("%s : 码表中无（解码表未覆盖）" % ch)
                continue
            idx = P.idx_of(c)
            ink, rect, hh = P.ink(idx)
            page = "page1" if rect[1] >= 512 else "page0"
            print("%s code=0x%04X idx=%-5d rect=%-24s ink=%-5d %s h=%s"
                  % (ch, c, idx, rect, ink, page, hh))
    elif a.cmd == "codes":
        for s in a.rest:
            c = int(s, 0)
            idx = P.idx_of(c)
            if idx is None:
                print("code=0x%04X : FontMap 无此码" % c)
                continue
            ink, rect, hh = P.ink(idx)
            page = "page1" if rect[1] >= 512 else "page0"
            print("code=0x%04X %s idx=%-5d rect=%-24s ink=%-5d %s h=%s"
                  % (c, rev.get(c, "?"), idx, rect, ink, page, hh))
    elif a.cmd == "pages":
        n0 = n1 = 0
        ink0 = ink1 = 0
        for c, idx in codes:
            ink, rect, hh = P.ink(idx)
            if rect[1] >= 512:
                n1 += 1
                ink1 += 1 if ink > 0 else 0
            else:
                n0 += 1
                ink0 += 1 if ink > 0 else 0
        print("page0(行<512): 码 %d 个，其中有墨 %d" % (n0, ink0))
        print("page1(行>=512): 码 %d 个，其中有墨 %d" % (n1, ink1))
    elif a.cmd == "blank":
        bl = []
        for c, idx in codes:
            ink, rect, hh = P.ink(idx)
            if ink <= 0:
                bl.append((c, idx, rect, rev.get(c, "?")))
        print("空白/越界 record = %d" % len(bl))
        for c, idx, rect, ch in bl[:80]:
            print("   code=0x%04X %s idx=%-5d rect=%s" % (c, ch, idx, rect))
    return 0


if __name__ == "__main__":
    sys.exit(main())
