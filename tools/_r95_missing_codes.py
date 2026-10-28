# -*- coding: utf-8 -*-
"""R95 · 18px 最终化 · 第二节：找出"文本需要、但该包 FontMap 未覆盖"的码（缺字清单）。

背景（R95 第一节结论）：R12 的三个包各自带 **18px 图集 + 自己的 FontMap**，但三份 FontMap 的码集**不统一**
（菜单有而整备缺 145 码、战斗缺 225 码）。要判断这是否造成游戏内缺字，必须拿**该包自己的文本**去比。

做法：
  ① 用 `ace3_pack.Bnd2` 遍历包内每个子块；
  ② 用 `build_cn.scan_text_resources` 的判据（u32==0x10000 的文本资源表）找出文本条目；
  ③ 取出字符串（到 0 结尾），按 SJIS 规则切码（跳过 `<...>` 标记），得到"该包需要的码集"；
  ④ 用 `build_refit.code_set` 的判据取 FontMap(tag2500) 覆盖码集；
  ⑤ 输出 `需要 - 覆盖` 的缺码清单（这就是"18px 未完整覆盖"的量化目标）。

用法: python -X utf8 tools\\_r95_missing_codes.py <iso> [--ids 4002050,4002053,1200000]
"""
import os
import re
import struct
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# 工程根目录：优先读环境变量 ACE3_ROOT，否则按模块位置向上推导（<工程根>/tools/x.py）。
# main() 里的 analysis_data_toc.txt / tmp_r95 都相对这个目录，需要时请显式设置 ACE3_ROOT。
ROOT = os.environ.get("ACE3_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ace3_pack import Bnd2  # noqa: E402

ISOBASE = 0x422800
DEFAULT_IDS = [4002050, 4002052, 4002053, 4002054, 4002057,
               1200000, 1200001, 1200004, 1200005, 1200006, 1200007]


def sjis_next(c):
    if c < 0x100:
        return c + 1
    lead, trail = c >> 8, c & 0xFF
    if trail < 0x7F:
        nt = trail + 1
        if nt == 0x7F:
            nt = 0x80
    elif trail < 0xFC:
        nt = trail + 1
    else:
        nt, lead = 0x40, lead + 1
        if lead == 0xA0:
            lead = 0xE0
    return (lead << 8) | nt


def map_codes(m):
    table = struct.unpack_from("<I", m, 0x18)[0]
    out = set()
    off = 0x2C
    while off + 12 <= table:
        a, z, _g = struct.unpack_from("<3I", m, off)
        off += 12
        c = a
        while c <= z:
            out.add(c)
            c = sjis_next(c)
    return out


def text_runs(blob):
    """返回该 blob 里所有文本条目（原始字节）。判据同 build_cn.scan_text_resources。"""
    runs = []
    for off in range(0, max(0, len(blob) - 0x20), 4):
        if struct.unpack_from("<I", blob, off)[0] != 0x10000:
            continue
        cnt = struct.unpack_from("<I", blob, off + 0x10)[0]
        tab = struct.unpack_from("<I", blob, off + 0x14)[0]
        if not (0 < cnt < 4000 and 0x20 <= tab and off + tab + cnt * 4 <= len(blob)):
            continue
        offs = list(struct.unpack_from("<%dI" % cnt, blob, off + tab))
        ok = all(not v or (off + v < len(blob) and blob[off + v - 1] == 0) for v in offs)
        if not ok:
            continue
        for v in offs:
            if not v:
                continue
            p = off + v
            q = p
            while q < len(blob) and blob[q] != 0:
                q += 1
            if q > p:
                runs.append(blob[p:q])
    return runs


def codes_of(raw):
    """SJIS/游戏码切分，跳过 <...> 标记。"""
    out = []
    i = 0
    while i < len(raw):
        b = raw[i]
        if b == 0x3C:                     # '<' 标记
            j = raw.find(b">", i)
            i = (j + 1) if j >= 0 else i + 1
            continue
        if b >= 0x80 or (0x81 <= b <= 0x9F) or (0xE0 <= b <= 0xEF):
            if i + 1 < len(raw):
                out.append((b << 8) | raw[i + 1])
                i += 2
                continue
        out.append(b)
        i += 1
    return out


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    iso_path = sys.argv[1]
    ids = [int(x) for x in sys.argv[sys.argv.index("--ids") + 1].split(",")] \
        if "--ids" in sys.argv else DEFAULT_IDS
    toc = {}
    for ln in open(os.path.join(ROOT, "analysis_data_toc.txt"), encoding="utf-8", errors="replace"):
        m = re.match(r"#(\d+)\s+id=(\d+)\s+off=0x([0-9a-fA-F]+)\s+size=(\d+)", ln)
        if m:
            toc[int(m.group(2))] = (int(m.group(3), 16), int(m.group(4)))
    iso = open(iso_path, "rb")
    log = []

    def P(s=""):
        print(s)
        log.append(s)

    P("ISO = %s" % os.path.basename(iso_path))
    P("%-9s %-8s %-9s %-9s %-9s %-9s %s"
      % ("id", "文本条目", "需要码", "FontMap码", "缺码", "未用到的覆盖码", "缺码样例(前10)"))
    totals = Counter()
    for eid in ids:
        if eid not in toc:
            continue
        off, size = toc[eid]
        iso.seek(ISOBASE + off)
        blob = iso.read(size)
        b = Bnd2(blob)
        runs = []
        for i, (t, _o) in enumerate(b.table):
            try:
                runs += text_runs(b.blob(i))
            except Exception:
                pass
        need = set()
        for r in runs:
            need.update(codes_of(r))
        try:
            cover = map_codes(b.blob(b.tag_index(2500)))
        except KeyError:
            cover = set()
        miss = need - cover
        unused = cover - need
        for c in miss:
            totals[c] += 1
        P("%-9d %-8d %-9d %-9d %-9d %-9d %s"
          % (eid, len(runs), len(need), len(cover), len(miss), len(unused),
             " ".join("%04X" % c for c in sorted(miss)[:10])))
    P("")
    P("=== 缺码在多个包里重复出现的（前 20，按出现包数排序）===")
    for c, n in totals.most_common(20):
        P("  码 %04X 出现在 %d 个包的缺码清单里" % (c, n))
    P("")
    P("判读：`缺码` = 该包自己的文本用到、但它的 FontMap 没覆盖的码 ⇒ 这些字符在游戏内该处**无法正常显示**。")
    out = os.path.join(ROOT, "tmp_r95")
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, "missing_codes.log")
    open(p, "w", encoding="utf-8").write("\n".join(log) + "\n")
    print("日志 -> %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
