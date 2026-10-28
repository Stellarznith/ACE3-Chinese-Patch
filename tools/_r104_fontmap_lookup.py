# -*- coding: utf-8 -*-
"""R104: code -> glyph record -> atlas cell -> ink, for one font package.

FontMap (tag2500) layout, measured from the file itself:
   +0x00 u32 version 0x00010000
   +0x04 u32 declared size
   +0x08 u32 font size (19)
   +0x10 u16 index count n
   +0x12 u16 (unknown, 0x571)
   +0x14 u32 index table offset (0x20)
   +0x18 u32 records offset (0x2294)
   index entry = 12B (lo u32, hi u32, base_idx u32) ; idx = base_idx + (code - lo)
   record = 24B: 4 floats uv0..uv3 (uv -> atlas cell) + 2 halfwords

usage:
  python -X utf8 tools/_r104_fontmap_lookup.py chars 战役途束菜
  python -X utf8 tools/_r104_fontmap_lookup.py allstats
"""
import argparse
import os
import re
import struct
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
from ace3_pack import Bnd2      # noqa: E402
from front_atlas import decode  # noqa: E402

ISOBASE = 0x422800


def load_toc():
    rows = {}
    for ln in open(os.path.join(HERE, "analysis_data_toc.txt"),
                   encoding="utf-8", errors="replace"):
        m = re.match(r"#(\d+)\s+id=(\d+)\s+off=0x([0-9a-fA-F]+)\s+size=(\d+)", ln)
        if m:
            rows[int(m.group(2))] = (int(m.group(3), 16), int(m.group(4)))
    return rows


class Pkg:
    def __init__(self, iso, eid):
        off, size = load_toc()[eid]
        with open(iso, "rb") as f:
            f.seek(ISOBASE + off)
            b = Bnd2(f.read(size))
        self.m = b.blob(b.tag_index(2500))
        at = b.blob(b.tag_index(2000))
        self.atlas = decode(bytes(at[0x20:0x20 + 0x40000]), h=512)
        u32 = lambda o: struct.unpack_from("<I", self.m, o)[0]
        self.n = struct.unpack_from("<H", self.m, 0x10)[0]
        self.idx_base = u32(0x14)
        self.rec_base = u32(0x18)
        self.size = u32(4)
        self.nrec = (self.size - self.rec_base) // 24
        self.entries = [struct.unpack_from("<3I", self.m, self.idx_base + i * 12)
                        for i in range(self.n)]

    def idx_of(self, code):
        lo_i = 0
        hi_i = self.n - 1
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
        h0, h1, h2, h3 = struct.unpack_from("<4H", self.m, o + 16)
        return dict(u0=u0, v0=v0, u1=u1, v1=v1, h=(h0, h1, h2, h3), off=o)

    def ink(self, r):
        x0 = int(round(r["u0"] * 1024)); x1 = int(round(r["u1"] * 1024)) - 1
        y0 = int(round(r["v0"] * 512)); y1 = int(round(r["v1"] * 512)) - 1
        y0b = int(round(r["v0"] * 1024)); y1b = int(round(r["v1"] * 1024)) - 1
        h, w = self.atlas.shape
        def c(x0, y0, x1, y1):
            if x0 < 0 or y0 < 0 or x1 >= w or y1 >= h or x1 < x0 or y1 < y0:
                return -1
            return int((self.atlas[y0:y1 + 1, x0:x1 + 1] != 0).sum())
        return c(x0, y0, x1, y1), c(x0, y0b, x1, y1b), (x0, y0, x1, y1)


def dec(code):
    if code < 0x100:
        return chr(code) if 0x20 <= code < 0x7F else "\\x%02X" % code
    try:
        return bytes([code >> 8, code & 0xFF]).decode("cp932")
    except Exception:
        return "?%04X" % code


def code_of(ch):
    try:
        b = ch.encode("cp932")
    except UnicodeEncodeError:
        return None
    return b[0] if len(b) == 1 else (b[0] << 8) | b[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--iso", default=os.path.join(HERE, "build", "ACE3-CN-FONT-R23.iso"))
    ap.add_argument("--pkg", type=int, default=4002050)
    a = ap.parse_args()
    P = Pkg(a.iso, a.pkg)
    print("iso=%s pkg=%d idx_base=0x%X n=%d rec_base=0x%X size=0x%X nrec=%d"
          % (os.path.basename(a.iso), a.pkg, P.idx_base, P.n, P.rec_base, P.size, P.nrec))
    if a.cmd == "chars":
        for ch in a.rest:
            c = code_of(ch)
            if c is None:
                print("%s : cp932 无此码（简体专用字，需查映射表）" % ch)
                continue
            idx = P.idx_of(c)
            if idx is None:
                print("%s code=0x%04X : **索引表无此码**" % (ch, c))
                continue
            r = P.rec(idx)
            i512, i1024, rect = P.ink(r)
            print("%s code=0x%04X idx=%d uv=(%.6f,%.6f)-(%.6f,%.6f) rect=%s ink512=%d ink1024=%d h=%s"
                  % (ch, c, idx, r["u0"], r["v0"], r["u1"], r["v1"], rect, i512, i1024, r["h"]))
    elif a.cmd == "allstats":
        blank512 = []
        blank1024 = []
        seen = 0
        for e_i, (lo, hi, base) in enumerate(P.entries):
            c = lo
            while c <= hi:
                idx = base + (c - lo)
                if 0 <= idx < P.nrec:
                    r = P.rec(idx)
                    i512, i1024, rect = P.ink(r)
                    seen += 1
                    if i512 <= 0:
                        blank512.append(c)
                    if i1024 <= 0:
                        blank1024.append(c)
                nxt = c + 1
                if c >= 0x100:
                    t = c & 0xFF
                    ld = c >> 8
                    if t < 0x7E:
                        nxt = (ld << 8) | (t + 1)
                    elif t == 0x7E:
                        nxt = (ld << 8) | 0x80
                    elif t < 0xFC:
                        nxt = (ld << 8) | (t + 1)
                    else:
                        ld2 = ld + 1
                        if ld2 == 0xA0:
                            ld2 = 0xE0
                        nxt = (ld2 << 8) | 0x40
                c = nxt
        print("遍历码数=%d  512 标尺下空白=%d  1024 标尺下空白=%d"
              % (seen, len(blank512), len(blank1024)))
        print("512 标尺空白码: %s" % " ".join("%04X" % c for c in blank512[:60]))
        print("  对应字符: %s" % "".join(dec(c) for c in blank512[:60]))
        print("1024 标尺空白码: %s" % " ".join("%04X" % c for c in blank1024[:60]))
        print("  对应字符: %s" % "".join(dec(c) for c in blank1024[:60]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
