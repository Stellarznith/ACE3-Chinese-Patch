# -*- coding: utf-8 -*-
"""R104: decode a package's Chinese text with the reverse code map and audit glyphs.

For every text string that contains one of the focus characters, print the codes
and whether (a) the package FontMap covers the code and (b) the atlas cell has ink.

usage: python -X utf8 tools/_r104_text_audit.py 战役途束菜 --pkg 4002050
"""
import argparse
import importlib.util
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
from ace3_pack import Bnd2                    # noqa: E402
from _r104_fontmap_lookup import Pkg          # noqa: E402

ISOBASE = 0x422800


def _load_r95():
    p = os.path.join(HERE, "tools", "_r95_missing_codes.py")
    spec = importlib.util.spec_from_file_location("_r95mc", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def load_toc():
    rows = {}
    for ln in open(os.path.join(HERE, "analysis_data_toc.txt"),
                   encoding="utf-8", errors="replace"):
        m = re.match(r"#(\d+)\s+id=(\d+)\s+off=0x([0-9a-fA-F]+)\s+size=(\d+)", ln)
        if m:
            rows[int(m.group(2))] = (int(m.group(3), 16), int(m.group(4)))
    return rows


def load_rev():
    rev = {}
    p = os.path.join(HERE, "build", "cn_code_map_from_iso.tsv")
    with open(p, encoding="utf-8", errors="replace") as f:
        next(f)
        for ln in f:
            parts = ln.rstrip("\n").split("\t")
            if len(parts) >= 2:
                rev[int(parts[1])] = parts[0]
    return rev


def decode_codes(raw, rev):
    out = []
    i = 0
    while i < len(raw):
        b = raw[i]
        if b == 0x3C:
            j = raw.find(b">", i)
            part = raw[i:(j + 1) if j >= 0 else i + 1]
            out.append("".join(chr(x) if 32 <= x < 127 else "." for x in part))
            i = (j + 1) if j >= 0 else i + 1
            continue
        if b == 0x5C and i + 1 < len(raw) and raw[i + 1] == 0x6E:
            out.append("\\n")
            i += 2
            continue
        if b & 0x80:
            code = (b << 8) | (raw[i + 1] if i + 1 < len(raw) else 0)
            out.append(rev.get(code, "{%04X}" % code))
            i += 2
            continue
        out.append(chr(b))
        i += 1
    return "".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("focus", nargs="*", default=[])
    ap.add_argument("--iso", default=os.path.join(HERE, "build", "ACE3-CN-FONT-R23.iso"))
    ap.add_argument("--pkg", type=int, default=4002050)
    ap.add_argument("--max", type=int, default=40)
    a = ap.parse_args()
    rev = load_rev()
    char2code = {v: k for k, v in rev.items()}
    print("reverse map: %d codes" % len(rev))
    for ch in a.focus:
        print("  %s -> %s" % (ch, ("0x%04X" % char2code[ch]) if ch in char2code else "**不在映射表**"))
    P = Pkg(a.iso, a.pkg)
    r95 = _load_r95()
    need = set()
    cover = r95.map_codes(P.m)
    off, size = load_toc()[a.pkg]
    with open(a.iso, "rb") as f:
        f.seek(ISOBASE + off)
        b = Bnd2(f.read(size))
    strings = []
    for i, (t, _o) in enumerate(b.table):
        if t in (2000, 2500):
            continue
        try:
            for r in r95.text_runs(b.blob(i)):
                strings.append(decode_codes(r, rev))
        except Exception:
            pass
    print("pkg=%d 文本串=%d" % (a.pkg, len(strings)))
    hits = 0
    for s in strings:
        if any(ch in s for ch in a.focus if ch):
            hits += 1
            if hits > a.max:
                continue
            codes = []
            for ch in s:
                if ch in char2code:
                    c = char2code[ch]
                    ink = "-"
                    r = None
                    if c in cover:
                        idx = P.idx_of(c)
                        if idx is not None and idx < P.nrec:
                            r = P.rec(idx)
                            i512, i1024, rect = P.ink(r)
                            ink = "%d" % i512
                    codes.append("%s:%04X%s" % (ch, c, "" if c in cover else "!NOMAP"))
                elif ord(ch) >= 0x80:
                    codes.append("%s:?" % ch)
            print("  %s" % s)
            print("     %s" % " ".join(codes[:40]))
    print("含焦点字的串 = %d" % hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
