#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R223（只读）：生成**可落地写盘计划**（含 ISO 绝对偏移 / 可用长度 / 目标字节）。

目标字节来源：
  A「照抄」：同一 JP 的另一槽现盘 CN 全部可画 ⇒ 直接抄它的字节（零重编码）。
  B「对照表」：按总对照表译名，用**当前码表**编码（码只在字体并集里选）。
门禁（每条都查，不通过就 SKIP 并记录原因）：
  ① 现盘该槽字节 == pair 索引里记录的字节（防串位）
  ② 目标长度 +1 <= 可用长度（可用长度 = 该槽到下一个槽/下一个 NUL 的余量，取保守值）
  ③ 目标字节全部可画（码 ∈ 字体并集 ∪ 空格类）
  ④ 数字一致性（全角归一化后数字集合必须相同）
输出：tmp_r223/apply_plan_f620.tsv（含 raw_off / avail / cur / new / 依据 / 备注）
用法：python -X utf8 tools/_r223_apply_plan.py
"""
import argparse
import collections
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ace3_pack                                    # noqa: E402
from ace3_pack import Bnd2                          # noqa: E402


def _blob_fixed(self, i):
    off = self.table[i][1]
    end = self.size
    for _t, o in self.table:
        if o > off and o < end:
            end = o
    return self.data[off:end]


Bnd2.blob = _blob_fixed
ace3_pack.Bnd2.blob = _blob_fixed

from _r104_glyphfix2 import Pkg, ISOBASE, databin_toc   # noqa: E402
from _r213_lz import decompress_any                     # noqa: E402
from _r211_cmap import code2char as _c2c                # noqa: E402

PKGS = [1200000, 1200001, 1200004, 1200005, 1200006, 1200007, 1200011,
        4002050, 4002051, 4002052, 4002053, 4002054, 4002057]
SPACE = {0x20, 0x8140, 0x8141, 0x816D}
KANA = (set(range(0x829F, 0x82F2)) | set(range(0x8340, 0x8397))
        | set(range(0x88A0, 0x88F2)) | set(range(0xFF61, 0xFFA0)))
TAGSPLIT = re.compile(r"(<[^>]*>)")
VER = b"\x00\x00\x01\x00"
BND = b"BND\x00"


def codes(seg):
    out, i = [], 0
    while i < len(seg):
        c = seg[i]
        if c < 0x80:
            i += 1
            continue
        if i + 1 >= len(seg):
            break
        out.append((c << 8) | seg[i + 1])
        i += 2
    return out


def norm(s):
    # ★TSV 里的换行是**转义的 "\n" 两个字符**，盘上解出的是真换行 ⇒ 两者都要去掉
    s = TAGSPLIT.sub("", s)
    return (s.replace("\\n", "").replace("\\t", "")
             .replace("　", "").replace(" ", "").replace("\n", "").replace("\r", ""))


def digits(s):
    s = "".join(chr(ord(c) - 0xFEE0) if "０" <= c <= "９" else c for c in s)
    return re.sub(r"[^0-9]", "", s)


def ver_blocks(blob, limit=400):
    out, start, n = [], 0, len(blob)
    while len(out) < limit:
        i = blob.find(VER, start)
        if i < 0:
            break
        start = i + 1
        if n - i < 0x18:
            continue
        cnt, toff = struct_unpack_i(blob, i)
        if cnt is None:
            continue
        offs = struct_unpack_offs(blob, i, cnt, toff)
        if offs is None:
            continue
        out.append((i, offs))
    return out


def struct_unpack_i(blob, i):
    import struct
    cnt = struct.unpack_from("<I", blob, i + 0x10)[0]
    toff = struct.unpack_from("<I", blob, i + 0x14)[0]
    if cnt <= 0 or cnt > 4096 or toff < 0x18 or i + toff + 4 * cnt > len(blob):
        return None, None
    return cnt, toff


def struct_unpack_offs(blob, i, cnt, toff):
    import struct
    offs = struct.unpack_from("<%dI" % cnt, blob, i + toff)
    used = [x for x in offs if x]
    if not used or max(used) >= len(blob) - i:
        return None
    return sorted(set(used))


def locate(iso, toc, eid):
    """返回 {key: (raw_off_of_blob, decompressed_blob, base, offs)}；同时标出是否压缩。

    ★R243e 兜底块（默认关闭，靠环境变量 `ACE3_FALLBACK_BLOCKS=1` 打开）：
      有些"表格类条目"（例 eid 4301012 的武器名表）里的文本**不在** VER 块结构里，
      块枚举只覆盖到缓冲尾部一小段 ⇒ 那些文本既定位不到、也进不了写手的闸门。
      打开后，对每个缓冲再补一个块（tag 不变、**k=9000**，base=0），
      offs = 该缓冲里所有"NUL 分隔且像文本"的段起点（判定沿用 R215 结构判据：
      每字节是可打印 ASCII 或合法 SJIS 双字节，且含 ≥2 个双字节码）。
      这样任意文本段都能被编成槽，写盘仍走原有的整条重压通道与九道闸门。
      默认关闭 ⇒ 其它工具与三套自检的行为**逐字节不变**。
    """
    off, size = toc[eid]
    with open(iso, "rb") as f:
        f.seek(ISOBASE + off)
        raw = f.read(size)
    comp = decompress_any(raw)[0] is not raw
    data = decompress_any(raw)[0]
    res = {}
    if data[:4] == BND:
        B = Bnd2(data)
        for i in range(B.count):
            tag = B.table[i][0]
            mo = B.table[i][1]
            mb = B.blob(i)
            mc = decompress_any(mb)[0] is not mb
            d = decompress_any(mb)[0]
            for k, (base, offs) in enumerate(ver_blocks(d)):
                res[(tag, k)] = (ISOBASE + off + mo, d, base, offs, mc)
            if _FALLBACK_ON:
                fb = _fallback_offs(d)
                if fb:
                    res[(tag, 9000)] = (ISOBASE + off + mo, d, 0, fb, mc)
    else:
        for k, (base, offs) in enumerate(ver_blocks(data)):
            res[(-1, k)] = (ISOBASE + off, data, base, offs, comp)
        if _FALLBACK_ON:
            fb = _fallback_offs(data)
            if fb:
                res[(-1, 9000)] = (ISOBASE + off, data, 0, fb, comp)
    return res


_FALLBACK_ON = os.environ.get("ACE3_FALLBACK_BLOCKS") == "1"


def _fallback_offs(d):
    """该缓冲里"像文本"的 NUL 段起点（R215 结构判据）。"""
    out = []
    i = 0
    n = len(d)
    while i < n:
        if d[i] == 0:
            i += 1
            continue
        j = d.find(b"\x00", i)
        if j < 0:
            j = n
        seg = d[i:j]
        if 4 <= len(seg) <= 2000 and _texty(seg):
            out.append(i)
        i = j + 1
    return out


def _texty(b):
    db = 0
    i = 0
    while i < len(b):
        c = b[i]
        if 0x20 <= c <= 0x7E or c in (0x0A, 0x09):
            i += 1
            continue
        if i + 1 < len(b) and (0x81 <= c <= 0x9F or 0xE0 <= c <= 0xEF) \
                and 0x40 <= b[i + 1] <= 0xFC and b[i + 1] != 0x7F:
            db += 1
            i += 2
            continue
        return False
    return db >= 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=None,
                    help="要读写的 CN ISO 路径（不给则必须显式传 --iso）")
    ap.add_argument("--ref", default=os.path.join(ROOT, "build", "ACE3-CN-FONT-R119-ORIGBASE.iso"))
    ap.add_argument("--out", default=os.path.join(ROOT, "tmp_r223", "apply_plan_f620.tsv"))
    ap.add_argument("--pair", default=os.path.join(ROOT, "tmp_r223", "pair_f620.tsv"),
                    help="以哪张盘为基准的逐槽索引（必须与 --iso 同一张盘）")
    ap.add_argument("--all", action="store_true",
                    help="不只修'画不出'的槽，**所有**与译名表权威译文不一致的槽都算（用于修错位/乱码）")
    a = ap.parse_args()

    fam = set()
    for e in PKGS:
        fam |= set(Pkg(a.iso, e).code2idx.keys())
    ch2code = {}
    # ★优先用本班校准过的字表（自检：可画性 99.49%），不再用可能过时的 _r211 反相
    cb = os.path.join(ROOT, "tmp_r223", "codebook_r223.tsv")
    if os.path.exists(cb):
        with io.open(cb, encoding="utf-8") as f:
            f.readline()
            for ln in f:
                c = ln.rstrip("\n").split("\t")
                if len(c) >= 2:
                    ch2code[c[0]] = c[1].upper()
        print("字→码表 = 校准表 codebook_r223（%d 字）" % len(ch2code))
        trusted = set(ch2code)          # 投票得来的字（最可信）
        # ★回退：校准表没有的字，用 R222 自举表 + 旧表反相补（只在字体并集内），
        #   最终由下面两道硬闸（可画 + 回解一致）把关，坏回退会被自动拦下。
        n0 = len(ch2code)
        avail_chars = set()
        # ★回退①《字库可用字表》(2062 字)：本工程历史字符集，码=历史分配
        # ★《字库可用字表》已过时（R222：剧=8923 假、真码 8A55）⇒ **仅作字符清单，不采信其码**
        av = os.path.join(ROOT, "翻译", "字库可用字表.tsv")
        if os.path.exists(av):
            with io.open(av, encoding="utf-8", errors="replace") as f:
                f.readline()
                for ln in f:
                    c = ln.rstrip("\n").split("\t")
                    if len(c) >= 2 and c[0]:
                        avail_chars.add(c[0])
        n1 = len(ch2code)
        mp = os.path.join(ROOT, "tmp_r222", "code_map.tsv")
        if os.path.exists(mp):
            with io.open(mp, encoding="utf-8") as f:
                f.readline()
                for ln in f:
                    c = ln.rstrip("\n").split("\t")
                    if len(c) >= 2:
                        ch2code.setdefault(c[0], c[1].upper())
        n2 = len(ch2code)
        _rev = {}
        for k, v in _c2c().items():
            if k in fam and v and len(v) == 1 and (v not in _rev or k < _rev[v]):
                _rev[v] = k
        for ch, code in _rev.items():
            ch2code.setdefault(ch, "%04X" % code)
        print("  +回退①可用字表(仅字符清单) +%d ；+回退②自举/反相 +%d ⇒ %d 字"
              % (n1 - n0, len(ch2code) - n2, len(ch2code)))
    else:
        with io.open(os.path.join(ROOT, "tmp_r222", "code_map.tsv"), encoding="utf-8") as f:
            f.readline()
            for ln in f:
                c = ln.rstrip("\n").split("\t")
                if len(c) >= 2:
                    ch2code[c[0]] = c[1].upper()
        print("字→码表 = R222 自举表（%d 字）" % len(ch2code))
    print("字体并集=%d 字→码=%d" % (len(fam), len(ch2code)))
    # ★去掉"多个字撞同一个码"的碰撞项（否则解码回检一定会失败，且写进去必是错字）
    _bycode = collections.defaultdict(list)
    for ch, cs in ch2code.items():
        _bycode[cs].append(ch)
    _before = len(ch2code)
    drop = set()
    for cs, chs in _bycode.items():
        if len(chs) == 1:
            continue
        t = [c for c in chs if c in trusted]
        if len(t) == 1:
            for c in chs:
                if c != t[0]:
                    drop.add(c)
        else:
            drop.update(chs)
    for c in drop:
        ch2code.pop(c, None)
    print("  去码碰撞：%d ⇒ %d 字（剔除 %d）" % (_before, len(ch2code), len(drop)))

    # ★可信的"码→字"参照：由最终 字→码 反相（校准表优先；_r211 垫底）。
    #   注意：《字库可用字表》已过时（R222：表里 剧=8923，真码 8A55），**不作为编码依据**。
    c2c = {}
    for ch, cs in ch2code.items():
        try:
            c2c[int(cs, 16)] = ch
        except Exception:
            pass
    for k, v in _c2c().items():
        c2c.setdefault(k, v)

    def dec_str(b):
        o, k = [], 0
        while k < len(b):
            c = b[k]
            if c < 0x80:
                o.append(chr(c))
                k += 1
                continue
            if k + 1 >= len(b):
                return None
            ch = c2c.get((c << 8) | b[k + 1])
            if ch is None:
                return None
            o.append(ch)
            k += 2
        return "".join(o)

    def enc(s):
        s = s.replace("\\n", "\n")   # ★把 TSV 的转义换行还原成真换行
        o = bytearray()
        for ch in s:
            if ch == "\n":
                o += b"\x0a"
                continue
            if ch == " ":
                o += b"\x81\x40"
                continue
            if ord(ch) < 0x80:
                o += bytes([ord(ch)])
                continue
            c = ch2code.get(ch)
            if c is None:
                return None
            o += bytes([int(c, 16) >> 8, int(c, 16) & 0xFF])
        return bytes(o)

    # 干净副本表
    def clean(seg):
        for c in codes(seg):
            if c not in fam and c not in SPACE:
                return False
        return True

    pairs = []
    with io.open(a.pair, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            c = ln.rstrip("\n").split("\t")
            if len(c) >= 6:
                pairs.append((int(c[0]), int(c[1]), int(c[2]), int(c[3]),
                              bytes.fromhex(c[4]), bytes.fromhex(c[5])))
    by_jp = collections.defaultdict(collections.Counter)
    for eid, tag, vk, i, jp, cn in pairs:
        by_jp[jp][cn] += 1
    best_clean = {}
    for jp, cnt in by_jp.items():
        cl = [v for v in cnt if clean(v)]
        if cl:
            best_clean[jp] = max(cl, key=lambda v: cnt[v])

    # ★译名索引：并入全部来源表（总对照表 / 剧情对照表 / 战斗台词译文 / 武器名 / 机体名 …）
    tbl = {}

    def add(jp, cn):
        if jp and cn:
            k = norm(jp)
            if k and k not in tbl:
                tbl[k] = cn

    def rows_of(p):
        if not os.path.exists(p):
            return
        with io.open(p, encoding="utf-8", errors="replace") as f:
            f.readline()
            for ln in f:
                c = ln.rstrip("\n").split("\t")
                if len(c) >= 2:
                    yield c

    for c in rows_of(os.path.join(ROOT, "翻译", "总对照表.tsv")):
        add(c[0], c[1])
    n0 = len(tbl)
    for c in rows_of(os.path.join(ROOT, "翻译", "剧情对照表.tsv")):
        if len(c) >= 6:
            add(c[4], c[5])
    n1 = len(tbl)
    for c in rows_of(os.path.join(ROOT, "翻译", "战斗台词译文.tsv")):
        if len(c) >= 3:
            add(c[1], c[2])
    n2 = len(tbl)

    def is_jp(s):
        return sum(1 for ch in s if ("\u3040" <= ch <= "\u30ff") or ("\uff61" <= ch <= "\uff9f")) >= 1
    for fn in ("武器名对照表.tsv", "机体名_数据副本.tsv", "名字表_中文名.tsv",
               "机体名_战斗用.tsv", "剧情译名表.tsv"):
        for c in rows_of(os.path.join(ROOT, "翻译", fn)):
            ji = ci = None
            for i, v in enumerate(c):
                if ji is None and is_jp(v):
                    ji = i
                if ci is None and re.search(r"[\u4e00-\u9fff]", v):
                    ci = i
            if ji is not None and ci is not None and ji != ci:
                add(c[ji], c[ci])
    print("译名索引=%d（总对照表 %d / +剧情 %d / +战斗 %d / +其它 %d）" %
          (len(tbl), n0, n1 - n0, n2 - n1, len(tbl) - n2))

    def resolve_part(part):
        k = norm(part)
        if k in tbl:
            return tbl[k]
        segs = TAGSPLIT.split(part)
        out, ok = [], True
        for s in segs:
            if not s:
                continue
            if s.startswith("<") and s.endswith(">"):
                out.append(s)
                continue
            kk = norm(s)
            if kk and kk in tbl:
                out.append(tbl[kk])
            elif kk == "":
                out.append(s)
            else:
                ok = False
                break
        return "".join(out) if ok else None

    def resolve_full(js):
        k = norm(js)
        if k in tbl:
            return tbl[k]
        outs = []
        for L in js.split("\n"):
            r = resolve_part(L)
            if r is None:
                return None
            outs.append(r)
        return "\n".join(outs)

    todo = []
    stat = collections.Counter()
    toc = databin_toc(a.iso)
    cache = {}
    for eid, tag, vk, i, jp, cn in pairs:
        if not jp:
            continue
        if clean(cn) and not a.all:
            continue
        if sum(1 for c in codes(jp) if c in KANA) < 2:
            continue
        tgt, src = None, ""
        rstr = None
        js = jp.decode("cp932", "replace")
        for cand in (js, js[1:], js[2:], js.lstrip("\n")):
            r = resolve_full(cand)
            if r:
                rstr = r
                break
        if rstr is None:
            stat["SKIP:无译文"] += 1
            continue
        # ★路①「可验证照抄」：同句副本必须【全可画】且【解码回 = 译表原文】才算数
        for v in sorted(by_jp[jp].keys(), key=lambda v: -by_jp[jp][v]):
            if clean(v) and dec_str(v) == rstr:
                tgt, src = v, "照抄(已验证)"
                break
        if tgt is None:
            # ★路②：用当前字表编码译表原文
            b = enc(rstr)
            if b is not None:
                tgt, src = b, "对照表"
            else:
                src = "SKIP:编不出"
        if tgt is None:
            stat[src or "SKIP:无译文"] += 1
            continue
        if tgt == cn:
            stat["已一致"] += 1
            continue
        # ★硬闸①：新字节里的每个码都必须**可画**；②必须能**解码回**目标文字
        if any(c not in fam and c not in SPACE and c != 0x0A for c in codes(tgt)):
            stat["SKIP:新码不可画"] += 1
            continue
        back = dec_str(tgt)
        if back is None:
            stat["SKIP:回解不出"] += 1
            continue
        if back != rstr:
            stat["SKIP:回解与原译不一致"] += 1
            continue
        if eid not in cache:
            cache.clear() if len(cache) > 400 else None
            cache[eid] = locate(a.iso, toc, eid)
        loc = cache[eid].get((tag, vk))
        if loc is None:
            stat["SKIP:定位失败"] += 1
            continue
        blob_off, d, base, offs, mc = loc
        if i >= len(offs):
            stat["SKIP:槽越界"] += 1
            continue
        raw_off = blob_off + base + offs[i]
        # 可用长度 = 到下一个槽起点 或 到本串 NUL 的较大者，取"到下一个槽起点"（保守）
        nxt = None
        for v in offs:
            if v > offs[i] and (nxt is None or v < nxt):
                nxt = v
        if nxt is not None:
            gap = nxt - offs[i]
        else:
            gap = len(d) - base - offs[i]
        # ★安全口径（R222 同款）：可用长度 = **原版这条日文串本身的长度**（不含 NUL），
        #   再受"到本块下一个槽的距离"约束。补零只补到 avail 内，
        #   绝不整段清空空隙（空隙里可能躺着别的 VER 块的字符串）。
        avail = min(len(jp), gap)
        if len(tgt) + 1 > avail:
            stat["SKIP:放不下"] += 1
            continue
        def dec_cn(b):
            out, k = [], 0
            c2c = _c2c()
            while k < len(b):
                ch = b[k]
                if ch < 0x80:
                    out.append(chr(ch))
                    k += 1
                    continue
                if k + 1 >= len(b):
                    break
                out.append(c2c.get((ch << 8) | b[k + 1], "?"))
                k += 2
            return "".join(out)
        if digits(jp.decode("cp932", "replace")) != digits(dec_cn(tgt)):
            stat["SKIP:数字不符"] += 1
            continue
        todo.append((eid, tag, vk, i, raw_off, avail, cn.hex(" "), tgt.hex(" "), src, mc))

    out = a.out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with io.open(out, "w", encoding="utf-8", newline="") as w:
        w.write("eid\ttag\tver\tslot\traw_off\tavail\tcur_hex\tnew_hex\t依据\t压缩成员\n")
        for eid, tag, vk, i, ro, av, cur, new, src, mc in todo:
            w.write("%d\t%d\t%d\t%d\t0x%X\t%d\t%s\t%s\t%s\t%s\n" %
                    (eid, tag, vk, i, ro, av, cur, new, src, "LZ" if mc else "-"))
    print("可写槽 =%d" % len(todo))
    print("  依据:", dict(collections.Counter(t[8] for t in todo)))
    print("  压缩成员内的可写槽 =", sum(1 for t in todo if t[9]))
    print("  SKIP:", dict(stat))
    print("计划 ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
