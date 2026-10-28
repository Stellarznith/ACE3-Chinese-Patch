#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R206: 屏内存驱动的"上屏出?"修复（**快版**，_r205_memfix.py 的等价实现）。

与前身（_r205_memfix.py）的差别只有一处：**一次扫描同时匹配全部坏行**（re 多模式），
不再"每条坏行全盘扫一遍"。判据完全一致，且比前身多一道闸门：

  ① 坏行必须"像台词行"（含真实标签 `<xx(NN)>`）——否则内存里 4800+ 条 MIPS 二进制会被当台词（前身踩过）
  ② 坏行字节在当前盘上**精确命中**（多处副本全部命中）
  ③ 原版**同偏移**的格必须能 cp932 解出、**ASCII 骨架完全一致**、且原版至少 2 个 2 字节码（真的是文本）
     ※ 不要要求"码位数一致"——中文译文本来就可能比日文原文短（本工具第一版就因此把目标全挡掉了）
  ④ 译文来源 = tmp_r205\\tr.tsv ∪ 翻译\\总对照表.tsv
  ⑤ 长度可容（格 + 格后 NUL 余量）
  写入前二次前置校验、写后回读。默认 dry。

用法:
  python -X utf8 tools/_r206_memfix.py --iso <ISO_PATH> --mem tmp_r205\\ee01.bin --mem tmp_r205\\ee02.bin
  python -X utf8 tools/_r206_memfix.py --iso <src> --dst <new> --mem ... --apply
"""
import argparse
import csv
import mmap
import os
import re
import shutil
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ACE3_ROOT") or os.path.dirname(HERE)
SECTOR, DATA_LBA, COUNT = 2048, 2117, 10344
BLANKS = {0x8140, 0x816D}
TAG = re.compile(rb"<[a-z]{2,6}\(\d*\)>")
_PKGCACHE = {}
OVR = {}          # 逐字码覆盖：char -> code（仅对"该屏渲染包"生效，本班只对 1200000 用腾字新码）
ZHOVR = {}        # 逐句译法覆盖：原版日文（含标签）-> 改写后的中文
FIXNAMES = []     # 专名订正：[(错写法, 正确写法)] —— 对算出的中文做全局替换，防止专名写错


def pkg_drawable(iso, eid, codes):
    """该包能不能画出这些码（码在 FontMap 且该格 ink>0）——"改一处坏一处"的关键闸门。"""
    from _r104_glyphfix2 import Pkg
    if eid not in _PKGCACHE:
        _PKGCACHE[eid] = Pkg(iso, eid)
    P = _PKGCACHE[eid]
    bad = []
    for c in codes:
        if c in BLANKS:      # 0x8140/0x816D 是空白码，不算"画不出"（沿用项目既有规则）
            continue
        i = P.code2idx.get(c)
        if i is None or P.ink(i) == 0:
            bad.append(c)
    return bad


def codes_of_bytes(b):
    out, i = [], 0
    while i < len(b):
        if b[i] < 0x80:
            i += 1
        elif i + 1 < len(b):
            out.append((b[i] << 8) | b[i + 1])
            i += 2
        else:
            break
    return out


def _find_data(name, *relpaths):
    """在若干候选位置里找数据文件：发布版随包的 tools/data/ 优先，再退回工程根。

    发布仓库里码表随 `tools/data/map_r203.tsv` 一起分发；在原始工程里它位于
    `<工程根>/tmp_r203/map_r203.tsv`。两处都找不到时抛 FileNotFoundError，
    错误信息里列出找过的路径，避免"静默少字"。
    """
    tried = []
    for base in (os.path.join(HERE, "data"), ROOT):
        for rel in relpaths:
            p = os.path.join(base, rel)
            tried.append(p)
            if os.path.exists(p):
                return p
    raise FileNotFoundError(
        "找不到数据文件 %s，已尝试：%s" % (name, " | ".join(tried)))


def load_cur():
    c2h, h2c = {}, {}
    map_path = _find_data("map_r203.tsv", "map_r203.tsv", os.path.join("tmp_r203", "map_r203.tsv"))
    for line in open(map_path, encoding="utf-8-sig"):
        q = line.rstrip("\r\n").split("\t")
        if len(q) >= 2 and len(q[0]) == 1:
            try:
                c = int(q[1], 16)
            except ValueError:
                continue
            c2h.setdefault(c, q[0])
            h2c.setdefault(q[0], c)
    for nm in ("newcode_map.tsv", "newcode_map2.tsv"):
        p = None
        for cand in (os.path.join(HERE, "data", nm), os.path.join(ROOT, "tmp_r205", nm)):
            if os.path.exists(cand):
                p = cand
                break
        if p is None:
            continue
        for line in open(p, encoding="utf-8"):
            if line.startswith("#") or line.startswith("code"):
                continue
            q = line.rstrip("\r\n").split("\t")
            if len(q) >= 2 and len(q[1]) == 1:
                try:
                    c = int(q[0], 16)
                except ValueError:
                    continue
                h2c.setdefault(q[1], c)
                c2h.setdefault(c, q[1])
    for b in BLANKS:
        c2h[b] = "\u3000"
    h2c.setdefault("\u3000", 0x8140)
    return c2h, h2c


def norm(s):
    return s.replace("\u3000", " ").replace("\r\n", "\n")


def tagless(s):
    """去掉 <...> 控制标签后的纯文本（战斗台词主表里的 jp_text 多为无标签形态）。"""
    return re.sub(r"<[^>]*>", "", s)


def _zhovr_contains(s):
    """译法覆盖的兜底匹配：键是"原版日文的独特片段"（长键优先）。"""
    for k in sorted(ZHOVR, key=len, reverse=True):
        if k and k in s:
            return ZHOVR[k]
    return None


def enc(s, h2c):
    out, miss = bytearray(), []
    for ch in s:
        o = ord(ch)
        if o < 0x80:
            out.append(o)
            continue
        c = OVR.get(ch) or h2c.get(ch)
        if c is None:
            miss.append(ch)
            continue
        out += bytes([(c >> 8) & 0xFF, c & 0xFF])
    return bytes(out), miss


def asc(b):
    """ASCII 骨架：**按 2 字节码对齐走查**后取 ASCII（<0x80）字节。

    ※ 绝不能"粗暴过滤所有 <0x80 的字节"——双字节码的低字节常 <0x80
      （如 0x8e 0xd7 的 0xd7 不是，但 0x8e 0x43、0x81 0x71 的是），
      那样会把骨架污染成乱码，永远配不上原文（_r205_memfix 就栽在这）。
    """
    out = bytearray()
    i = 0
    n = len(b)
    while i < n:
        v = b[i]
        if v < 0x80:
            out.append(v)
            i += 1
        else:
            i += 2
    return bytes(out)


def ncode(b):
    """2 字节码的个数（ASCII 按 1 字节走）——用于配对时要求"字数"一致。"""
    i, n = 0, 0
    while i < len(b):
        if b[i] < 0x80:
            i += 1
        else:
            n += 1
            i += 2
    return n


def read_cell(fh, off, cap=400):
    """按**码对齐**读一个格：在码边界遇到 0x00 才算结束。

    ※ 不能"逐字节读到 NUL 就停"——双字节码的低字节可能是 0x00（如死码 `8A00`），
      那样会把格提前截断（本工具第一版就把裘尼那格截成 27B，导致误判"配不到原文"）。
    """
    fh.seek(off)
    o = bytearray()
    while len(o) < cap:
        b = fh.read(1)
        if not b:
            break
        v = b[0]
        if v == 0:                      # 码边界上的 0x00 = 串结束
            break
        if v < 0x80:
            o += b
        else:
            b2 = fh.read(1)
            if not b2:
                break
            o += b + b2
    return bytes(o)


def cell_at(buf, off, cap=400):
    """从 buf（bytes/mmap）按**码对齐**取一格：码边界遇 0x00 结束。"""
    o = bytearray()
    i = off
    n = len(buf)
    while len(o) < cap and i < n:
        v = buf[i]
        if v == 0:
            break
        if v < 0x80:
            o.append(v)
            i += 1
        else:
            if i + 1 >= n:
                break
            o += bytes(buf[i:i + 2])
            i += 2
    return bytes(o)


def cell_start(buf, k, maxback=400):
    """把"半行偏移"回推到它所在整格的起点（按码对齐校验；对不上就原样返回）。"""
    lo = max(0, k - maxback)
    i = k - 1
    while i >= lo and buf[i] != 0:
        i -= 1
    start = i + 1
    j = start
    while j < k:
        j += 1 if buf[j] < 0x80 else 2
    return start if j == k else k


def load_toc(iso):
    with open(iso, "rb") as f:
        f.seek(DATA_LBA * SECTOR)
        head = f.read(0x20 + COUNT * 16)
    ent = []
    base = DATA_LBA * SECTOR
    for i in range(COUNT):
        _a, sz, off, e = struct.unpack_from("<IIII", head, 0x20 + i * 16)
        if e:
            ent.append((base + off, base + off + sz, e))
    ent.sort()
    return ent


def eid_of(ent, k):
    lo, hi = 0, len(ent) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        s, e, eid = ent[mid]
        if k < s:
            hi = mid - 1
        elif k >= e:
            lo = mid + 1
        else:
            return eid
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", required=True)
    ap.add_argument("--dst", default=None)
    ap.add_argument("--orig", default=os.path.join(
        ROOT, "A.C.E. - Another Century's Episode 3 - The Final [NTSC-J] [SLPS-25784].iso"))
    ap.add_argument("--mem", action="append", default=[])
    ap.add_argument("--badtsv", default=None,
                    help="直接给坏行清单（_r206_minep2s.py 的产物，hex 列），与 --mem 二选一补充")
    ap.add_argument("--maxhit", type=int, default=40)
    ap.add_argument("--pkg", type=int, default=1200000,
                    help="判定「画得出」用哪个包（战斗/字幕/提示框屏 = 1200000）")
    ap.add_argument("--skip-packgate", action="store_true")
    ap.add_argument("--no-cell-align", dest="cell_align", action="store_false", default=True,
                    help="关掉「把半行片段回推到整格」的配对方式（默认开启）")
    ap.add_argument("--override", default=None,
                    help="逐字码覆盖，如 呜=8ADB,邪=8EDD（腾字后的新码）")
    ap.add_argument("--zhfile", default=None, help="改写译法 TSV：原版日文<TAB>新中文")
    ap.add_argument("--fixnames", default=None,
                    help="专名订正，如 库埃斯=奎丝,宇宙世纪=宇宙世纪（对中文全局替换，防止专名错写）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", default=None, help="把待处理逐条写成 TSV（原因/原版日文/现译/hex）供人工裁定")
    a = ap.parse_args()

    c2h, h2c = load_cur()
    if not a.mem and not a.badtsv:
        raise SystemExit("必须给 --mem 或 --badtsv")
    if a.override:
        for kv in a.override.split(","):
            k, v = kv.split("=")
            OVR[k.strip()] = int(v.strip(), 16)
        print("[override] %s" % ", ".join("%s=%04X" % (k, v) for k, v in OVR.items()))
    if a.zhfile and os.path.exists(a.zhfile):
        for line in open(a.zhfile, encoding="utf-8"):
            if line.startswith("#") or "\t" not in line:
                continue
            k, v = line.rstrip("\n").split("\t", 1)
            ZHOVR[norm(k.replace("\\n", "\n"))] = v.replace("\\n", "\n")
        print("[zhfile] %d 条改写译法" % len(ZHOVR))
    if a.fixnames:
        for kv in a.fixnames.split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                FIXNAMES.append((k.strip(), v.strip()))
        print("[fixnames] %s" % ", ".join("%s->%s" % t for t in FIXNAMES))
    tr = {}
    p = os.path.join(ROOT, "tmp_r205", "tr.tsv")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            if line.startswith("#") or "\t" not in line:
                continue
            k, v = line.rstrip("\n").split("\t", 1)
            tr[norm(k.replace("\\n", "\n"))] = v.replace("\\n", "\n")
    db = {}
    for r in csv.reader(open(os.path.join(ROOT, "翻译", "总对照表.tsv"), encoding="utf-8", newline=""),
                        delimiter="\t"):
        if len(r) >= 2 and r[0]:
            db.setdefault(norm(r[0].replace("\\n", "\n")), r[1].replace("\\n", "\n"))
    # R198 的战斗台词主表（id / jp_text / zh_text）
    p = os.path.join(ROOT, "翻译", "战斗台词译文.tsv")
    if os.path.exists(p):
        for r in csv.reader(open(p, encoding="utf-8-sig", newline=""), delimiter="\t"):
            if len(r) >= 3 and r[1]:
                db.setdefault(norm(r[1].replace("\\n", "\n")), r[2].replace("\\n", "\n"))
    # 剧情对照表（键带控制标签，最贴近这些"格"的原文形态）
    for nm in ("剧情对照表.tsv", "剧情对照表_全量参考.tsv"):
        p = os.path.join(ROOT, "翻译", nm)
        if not os.path.exists(p):
            continue
        for r in csv.reader(open(p, encoding="utf-8-sig", newline=""), delimiter="\t"):
            if len(r) >= 6 and r[4] and r[5]:
                db.setdefault(norm(r[4].replace("\\n", "\n")), r[5].replace("\\n", "\n"))

    # ① 从内存取"像台词行"的坏行
    segs, seen = [], set()
    if a.badtsv and os.path.exists(a.badtsv):
        n0 = 0
        for line in open(a.badtsv, encoding="utf-8"):
            q = line.rstrip("\r\n").split("\t")
            if not q or q[0] in ("hex", "") or len(q[0]) < 8:
                continue
            try:
                seg = bytes.fromhex(q[0])
            except ValueError:
                continue
            if seg not in seen:
                seen.add(seg)
                segs.append(seg)
                n0 += 1
        print("[badtsv] 载入 %d 条坏行" % n0)
    for mp in a.mem:
        d = open(mp, "rb").read()
        n = len(d)
        pos = 0
        while pos < n:
            if pos > 0 and d[pos - 1] != 0:
                pos += 1
                continue
            e = d.find(b"\x00", pos)
            if e < 0:
                e = n
            seg = bytes(d[pos:e])
            if 4 <= len(seg) <= 400 and TAG.search(seg) and seg not in seen:
                miss, i = 0, 0
                while i < len(seg):
                    if seg[i] < 0x80:
                        i += 1
                        continue
                    if i + 1 >= len(seg):
                        break
                    if ((seg[i] << 8) | seg[i + 1]) not in c2h:
                        miss += 1
                    i += 2
                if miss:
                    seen.add(seg)
                    segs.append(seg)
            pos = e + 1
    print("[1] 内存里的真坏行（去重）%d 条" % len(segs))

    # ② 一次扫描同时匹配全部坏行
    pat = re.compile(b"|".join(re.escape(s) for s in sorted(segs, key=len, reverse=True)))
    fi = open(a.iso, "rb")
    d = mmap.mmap(fi.fileno(), 0, access=mmap.ACCESS_READ)
    hits = {}
    for m in pat.finditer(d):
        hits.setdefault(m.group(0), []).append(m.start())
    print("[2] 盘上命中：%d 条坏行有副本，共 %d 处" %
          (len(hits), sum(len(v) for v in hits.values())))

    _ogf = open(a.orig, "rb")
    og = mmap.mmap(_ogf.fileno(), 0, access=mmap.ACCESS_READ)
    ent = load_toc(a.iso)
    rows, todo = [], []
    REPORT = []
    for seg, offs in sorted(hits.items()):
        done = False
        found = False
        for k in offs[:a.maxhit]:
            ks = cell_start(d, k) if a.cell_align else k
            cur = cell_at(d, ks)
            if not cur:
                continue
            o = cell_at(og, ks)
            if ncode(o) < 2:
                continue
            if asc(o) != asc(cur):
                continue
            found = True
            try:
                jp = o.decode("cp932")
            except Exception:
                continue
            zh = (ZHOVR.get(norm(jp)) or _zhovr_contains(norm(jp))
                  or tr.get(norm(jp)) or db.get(norm(jp))
                  or db.get(norm(tagless(jp))) or tr.get(norm(tagless(jp))))
            if zh:
                for bad, good in FIXNAMES:
                    if bad in zh:
                        zh = zh.replace(bad, good)
            if not zh:
                todo.append((seg, "无译文: " + jp.replace("\u3000", " ")[:46]))
                REPORT.append(("无译文", jp, "", seg.hex()))
                done = True
                break
            nb, missch = enc(zh, h2c)
            if missch:
                todo.append((seg, "码表缺字: " + "".join(missch) + "  | " + jp[:30]))
                REPORT.append(("码表缺字:" + "".join(missch), jp, zh, seg.hex()))
                done = True
                break
            room = 0
            j = ks + len(cur)
            while room < 64 and j < len(d) and d[j] == 0:
                room += 1
                j += 1
            if len(nb) + 1 > len(cur) + room:
                todo.append((seg, "放不下 %d>%d : %s" % (len(nb), len(cur) + room, zh[:30])))
                REPORT.append(("放不下 %d>%d" % (len(nb), len(cur) + room), jp, zh, seg.hex()))
                continue
            if not a.skip_packgate:
                badc = pkg_drawable(a.iso, a.pkg, codes_of_bytes(nb))
                if badc:
                    todo.append((seg, "包 %d 画不出: %s | %s" %
                                 (a.pkg, ",".join("%04X" % c for c in badc), zh[:28])))
                    REPORT.append(("包%d画不出:%s" % (a.pkg, ",".join("%04X" % c for c in badc)),
                                   jp, zh, seg.hex()))
                    continue
            total = max(len(cur), len(nb) + 1)
            rows.append((ks, cur, nb + b"\x00" * (total - len(nb)), zh, jp))
            done = True
            continue          # ★同一句的**其余副本也要写**（不许只改一处）——不能 break
        if not done and not found:
            todo.append((seg, "配不到原文（骨架/码位数不符）"))
            REPORT.append(("配不到原文", "", "", seg.hex()))
    if a.report:
        with open(a.report, "w", encoding="utf-8", newline="") as rf:
            rf.write("原因\t原版日文\t现译\tseg_hex\n")
            for why, jp, zh, hx in REPORT:
                rf.write("%s\t%s\t%s\t%s\n" % (why, jp.replace("\n", "\\n"),
                                               zh.replace("\n", "\\n"), hx))
        print("[report] %d 条 -> %s" % (len(REPORT), a.report))
    print("[3] 可写 %d 处 ；待处理 %d 条" % (len(rows), len(todo)))
    for k, seg, nb, zh, jp in rows:
        print("    %#010x  条目=%-8s  %s   <=  %s" %
              (k, eid_of(ent, k), zh.replace("\n", " ")[:48], jp.replace("\u3000", " ")[:40]))
    for seg, why in todo:
        print("    [待处理] %s | %s" % (why[:60], seg.hex()[:50]))
    if not a.apply:
        print("[dry] 未写盘")
        return 0
    if not a.dst:
        raise SystemExit("--apply 需要 --dst")
    if not os.path.exists(a.dst):
        print("复制 ->", a.dst)
        shutil.copyfile(a.iso, a.dst)
    if os.path.getsize(a.dst) != os.path.getsize(a.iso):
        os.remove(a.dst)
        raise SystemExit("★ 复制后尺寸不符 ⇒ 已删除残件")
    done = set()
    with open(a.dst, "r+b") as fo:
        for k, seg, nb, zh, jp in rows:
            if k in done:
                continue
            done.add(k)
            fo.seek(k)
            if fo.read(len(seg)) != seg:
                raise SystemExit("前置校验不符 @%#x" % k)
            fo.seek(k)
            fo.write(nb)
    bad = 0
    with open(a.dst, "rb") as fo:
        for k, seg, nb, zh, jp in rows:
            fo.seek(k)
            if fo.read(len(nb)) != nb:
                bad += 1
    print("回读不符:", bad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
