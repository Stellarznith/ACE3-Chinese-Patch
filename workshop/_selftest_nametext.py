# -*- coding: utf-8 -*-
"""名字块（CLM/FNM）适配层自测 —— 只读，不写任何 ISO。

覆盖：
  A. 码位补登记：16 条补丁生效、不覆盖冻结映射、老文本编码不变
  B. 块结构解析：四个目标资源、八道校验、拒绝伪造块
  C. 三条写回路线：等长原位 / 更短补零 / 更长块内重排 / 超容量 BLOCK
  D. 写盘计划：check() 的各类拒绝、build() 的一致性校验
  E. 独立性：解析/重建绝不 split(0x00)、块外零位移
  F. 双引擎分流：FNM 是 VER 块（走槽引擎）、CLM 只能走名字块引擎、
     同一资源两种编辑必须合并成一次构建
  G. 上层接入：as_hit 字段完整（含 mem_tag/ver_k）、hit_key 两种形状、
     editplan.item_from_name 接受 dict 与 NameHit、pipeline.inspect 双引擎
     统计与 can_build、冲突项带 blocking 标记

跑法：
    python _selftest_nametext.py
退出码 0 = 全过。
"""
from __future__ import annotations

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (codec, editplan, env, nametext as N, namewriter as NW,
                  pipeline, slots, writer as W)

ISO = os.environ.get("ACE3_TEST_ISO") or r"PATH\TO\TEST.iso"
EIDS = (501050, 501051, 2801050, 2801051)

_pass = 0
_fail = 0
_fails = []


def ck(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  PASS  %s" % name)
    else:
        _fail += 1
        _fails.append(name)
        print("  FAIL  %s%s" % (name, ("   ← " + str(extra)) if extra else ""))


def head(t):
    print("\n== %s ==" % t)


# ---------------------------------------------------------------- A
def test_codes():
    head("A 码位补登记")
    t = codec.table()
    ck("码表说明里写明补登记条数", "补登记 16 条" in t.note, t.note)
    ck("0x938F = 冥", t.char_of(0x938F) == "冥")
    ck("0x9390 = 墓", t.char_of(0x9390) == "墓")
    ck("has_char('冥') 为真", t.has_char("冥"))
    ck("codes_of('冥') = (0x938F,)", t.codes_of("冥") == (0x938F,))
    # 已有码位的字必须仍然优先用原来的码位（老文本编码不变）
    ck("ｆ 原码位优先（0x9271）", t.pick_code("ｆ") == 0x9271,
       [hex(c) for c in t.codes_of("ｆ")])
    ck("ｆ 补丁码位追加在后", 0x8286 in t.codes_of("ｆ"))
    # 冻结原表 2063 码位，补 16 → 2079
    ck("码位总数 2079", len(t.code2char) == 2079, len(t.code2char))
    # 补登记绝不覆盖：抽 5 条冻结码位核对
    for code, ch in ((0x8140, "　"), (0x8D86, "方"), (0x8FEC, "舟"),
                     (0x914C, "连"), (0x8DD5, "框")):
        ck("冻结码位 0x%04X 仍是 %r" % (code, ch), t.char_of(code) == ch,
           t.char_of(code))
    # 编解码往返
    enc = codec.encode_text("冥墓方舟")
    ck("encode('冥墓方舟') = 938f93908d868fec",
       enc.data.hex() == "938f93908d868fec", enc.data.hex())
    ck("encode 无缺字", not enc.missing, enc.missing)
    dec = codec.decode_bytes(bytes.fromhex("938f93908d868fec"))
    ck("decode 回 '冥墓方舟'", dec[0] == "冥墓方舟", dec[0])
    ck("decode 无未知码位", not dec[2], dec[2])
    # patch_note
    ck("patch_note(0x938F) 含「A 类」", "A 类" in codec.patch_note(0x938F))
    ck("patch_note(0x92F6) 含「B 类」", "B 类" in codec.patch_note(0x92F6))
    ck("patch_note(0x8D86) 为空（不是补丁）", codec.patch_note(0x8D86) == "")
    # ★ 40 条拒收的是「新增码位」，不是「该字没有码位」。
    #   框/颜/盘 这些字原本就有别的码位（0x8DD5/0x91DA/0x8EF0），
    #   拒收指的是不把《字库可用字表》里那 40 个新码位登记进码表。
    for code, ch in ((0x82BF, "框"), (0x834A, "颜"), (0x8C25, "盘"),
                     (0x838C, "刃"), (0x968E, "智")):
        ck("拒收码位 0x%04X 未被登记" % code, t.char_of(code) is None,
           t.char_of(code))
    # 真正没有码位的字（编码必须报缺字）
    ck("黄 无码位（天然 BLOCK 用例）", not t.codes_of("黄"))
    ck("encode('黄') 报缺字", codec.encode_text("黄").missing == ["黄"])


# ---------------------------------------------------------------- B
def test_parse(toc):
    head("B 块结构解析")
    for eid in EIDS:
        blks = N.blocks_of(ISO, eid, toc)
        ck("eid=%d 有名字块" % eid, bool(blks), len(blks))
        for b in blks:
            ck("  %s 块 %d==dsize+0x10" % (b.kind, eid),
               b.size == b.dsize + N.HEAD, (b.size, b.dsize))
            ck("  %s 块偏移表在块内" % b.kind,
               b.toff + 4 * b.count <= b.dsize)
            ck("  %s 块条数 %d 条都能解" % (b.kind, b.count),
               len(b.items) == b.count)
            ck("  %s 块 used <= dsize" % b.kind, b.used <= b.dsize,
               (b.used, b.dsize))
            ck("  %s 块 slack >= 0" % b.kind, b.slack() >= 0, b.slack())

    # 精确值核对（实测钉死的四个资源）
    want = {
        501050: [("CLM", 0xE10D0, 0x3E0, 29), ("FNM", 0xE14B0, 0x50, 1)],
        501051: [("CLM", 0xF37E0, 0x270, 18), ("FNM", 0xF3A50, 0x60, 1)],
        2801050: [("FNM", 0x91940, 0x50, 1)],
        2801051: [("FNM", 0x8DC80, 0x60, 1)],
    }
    for eid, exp in want.items():
        got = [(b.kind, b.pos, b.size, b.count) for b in N.blocks_of(ISO, eid, toc)]
        ck("eid=%d 块清单与实测一致" % eid, got == exp, got)

    # 已知文本
    h = {(x.eid, x.mark, x.index): x for eid in EIDS for x in N.hits_of(ISO, eid, toc)}
    ck("501050 CLM[0] = 冥墓方舟",
       h[(501050, "CLM", 0)].plain == "冥墓方舟", h[(501050, "CLM", 0)].plain)
    ck("501050 FNM[0] = 冥墓方舟",
       h[(501050, "FNM", 0)].plain == "冥墓方舟")
    ck("501051 FNM[0] = 冥墓方舟（幽灵）",
       h[(501051, "FNM", 0)].plain == "冥墓方舟（幽灵）")
    ck("2801050 FNM[0] = 冥墓方舟", h[(2801050, "FNM", 0)].plain == "冥墓方舟")
    ck("2801051 FNM[0] = 冥墓方舟（幽灵）",
       h[(2801051, "FNM", 0)].plain == "冥墓方舟（幽灵）")
    ck("501050 CLM 共 29 条且无未知码位",
       all(not h[(501050, "CLM", i)].unknown for i in range(29)))
    ck("501050 CLM[2] = 连击２１", h[(501050, "CLM", 2)].plain == "连击２１",
       h[(501050, "CLM", 2)].plain)
    ck("501050 CLM[28] = 破坏者剑",
       h[(501050, "CLM", 28)].plain == "破坏者剑",
       h[(501050, "CLM", 28)].plain)

    # 八道校验：伪造块必须被拒
    res = slots.load_resource(ISO, 501050, toc)
    mem = list(res.members.values())[0]
    d = mem.data
    clm = [b for b in N.scan(d, 501050) if b.kind == "CLM"][0]

    bad = bytearray(d)
    bad[clm.pos + 4:clm.pos + 8] = struct.pack("<I", 0x10)      # size 太小
    ck("拒绝 size 过小的伪块", N.parse_at(bytes(bad), clm.pos, 501050) is None)

    bad = bytearray(d)
    struct.pack_into("<I", bad, clm.base + 0x10, clm.count + 1)  # N 双写不一致
    ck("拒绝 N 双写不一致的伪块", N.parse_at(bytes(bad), clm.pos, 501050) is None)

    bad = bytearray(d)
    struct.pack_into("<I", bad, clm.base + 0x14, 0x29)           # T 不对齐
    ck("拒绝 T 不对齐的伪块", N.parse_at(bytes(bad), clm.pos, 501050) is None)

    bad = bytearray(d)
    struct.pack_into("<I", bad, clm.base + 0x04, clm.dsize + 4)  # dsize 不符
    ck("拒绝 dsize != size-0x10 的伪块", N.parse_at(bytes(bad), clm.pos, 501050) is None)

    ck("非标记处不解析", N.parse_at(d, clm.pos + 1, 501050) is None)
    ck("越界不解析", N.parse_at(d, len(d) - 4, 501050) is None)

    # scan 不靠 split(0x00)：串里若有 0x00 早就断了；这里验证「块数」精确
    ck("scan(501050) 恰好 2 个块", len(N.scan(d, 501050)) == 2,
       len(N.scan(d, 501050)))


# ---------------------------------------------------------------- C
def test_write_paths(toc):
    head("C 三条写回路线")
    res = slots.load_resource(ISO, 501050, toc)
    d = list(res.members.values())[0].data
    clm = [b for b in N.scan(d, 501050) if b.kind == "CLM"][0]
    orig0 = clm.items[0]

    # C1 等长
    same = codec.encode_text("冥墓方舟").data
    ck("C1 等长新串字节数一致", len(same) == len(orig0), (len(same), len(orig0)))
    new, rep = N.apply_changes(d, clm, {0: same})
    ck("C1 mode=inplace", rep["mode"] == "inplace", rep["mode"])
    b2 = N.parse_at(new, clm.pos, 501050)
    ck("C1 重解析成功", b2 is not None)
    ck("C1 文本未变", b2.plain(0) == "冥墓方舟", b2.plain(0))
    ck("C1 其余 28 条不变",
       [b2.plain(i) for i in range(1, 29)] == [clm.plain(i) for i in range(1, 29)])
    ck("C1 块外零位移",
       new[:clm.pos] == d[:clm.pos] and new[clm.pos + clm.size:] == d[clm.pos + clm.size:])
    ck("C1 块长度未变", len(new) == len(d))

    # C2 更短 + 补零
    shorter = codec.encode_text("冥墓").data
    ck("C2 更短", len(shorter) < len(orig0), (len(shorter), len(orig0)))
    new, rep = N.apply_changes(d, clm, {0: shorter})
    ck("C2 mode=inplace", rep["mode"] == "inplace")
    ck("C2 padded = 差值",
       rep["detail"][0]["padded"] == len(orig0) - len(shorter),
       rep["detail"][0])
    b2 = N.parse_at(new, clm.pos, 501050)
    ck("C2 重解析文本 = 冥墓", b2.plain(0) == "冥墓", b2.plain(0))
    ck("C2 块长度未变", len(new) == len(d))

    # C3 更长 → 块内重排
    longer = codec.encode_text("冥墓方舟改二型").data
    ck("C3 更长", len(longer) > len(orig0))
    new, rep = N.apply_changes(d, clm, {0: longer})
    ck("C3 mode=reflow", rep["mode"] == "reflow", rep["mode"])
    ck("C3 offsets_changed > 0", rep["offsets_changed"] > 0, rep["offsets_changed"])
    b2 = N.parse_at(new, clm.pos, 501050)
    ck("C3 重解析成功", b2 is not None)
    ck("C3 新文本正确", b2.plain(0) == "冥墓方舟改二型", b2.plain(0))
    ck("C3 其余 28 条不变",
       [b2.plain(i) for i in range(1, 29)] == [clm.plain(i) for i in range(1, 29)])
    ck("C3 块 size 未变", b2.size == clm.size, (b2.size, clm.size))
    ck("C3 块外零位移",
       new[:clm.pos] == d[:clm.pos] and new[clm.pos + clm.size:] == d[clm.pos + clm.size:])
    ck("C3 资源长度未变", len(new) == len(d))

    # C4 超容量必须抛
    try:
        N.apply_changes(d, clm, {0: codec.encode_text("冥" * 300).data})
        ck("C4 超容量被拒", False, "没报错")
    except N.NameError_ as exc:
        ck("C4 超容量被拒", "块内空间不足" in str(exc), str(exc)[:50])

    # C5 新串含 0x00 必须拒
    try:
        N.apply_changes(d, clm, {0: b"\x8d\x86\x00\x8f\xec"})
        ck("C5 含 0x00 被拒", False, "没报错")
    except N.NameError_:
        ck("C5 含 0x00 被拒", True)

    # C6 下标越界必须拒
    try:
        N.apply_changes(d, clm, {999: b"\x8d\x86"})
        ck("C6 下标越界被拒", False, "没报错")
    except N.NameError_:
        ck("C6 下标越界被拒", True)

    # C7 原位改写不允许变长
    try:
        N.encode_inplace(clm, 0, longer)
        ck("C7 encode_inplace 拒绝变长", False, "没报错")
    except N.NameError_:
        ck("C7 encode_inplace 拒绝变长", True)


# ---------------------------------------------------------------- D
def test_plan(toc):
    head("D 写盘计划 check()/build()")
    hits = {(x.eid, x.mark, x.index): x
            for eid in EIDS for x in N.hits_of(ISO, eid, toc)}
    h = hits[(501050, "FNM", 0)]
    ck("D 目标 = 冥墓方舟", h.plain == "冥墓方舟", h.plain)

    good = NW.NameEdit(eid=501050, block_pos=h.block_pos, index=0,
                       old_raw=h.raw, new_raw=codec.encode_text("冥墓方舟").data,
                       mark="FNM")
    vs = NW.check(ISO, [good], toc)
    ck("D1 等长编辑通过", vs[0].ok, vs[0].reason)
    ck("D1 mode=inplace", vs[0].mode == "inplace", vs[0].mode)
    ck("D1 无缺字", not vs[0].missing, vs[0].missing)

    # 缺码位必须 BLOCK：0x82BF 是拒收的码位，码表里没有 ⇒ 解出来是未知码位
    bad = NW.NameEdit(eid=501050, block_pos=h.block_pos, index=0,
                      old_raw=h.raw, new_raw=b"\x82\xbf", mark="FNM")
    vs = NW.check(ISO, [bad], toc)
    ck("D2 未知码位被拦（BLOCK）", not vs[0].ok and vs[0].font_state == "BLOCK",
       (vs[0].ok, vs[0].font_state, vs[0].reason[:40]))
    ck("D2 阻断原因点名码位", "0x82BF" in vs[0].reason, vs[0].reason[:50])

    # old_raw 对不上必须拒
    stale = NW.NameEdit(eid=501050, block_pos=h.block_pos, index=0,
                        old_raw=b"\x00\x01\x02\x03",
                        new_raw=codec.encode_text("冥墓").data, mark="FNM")
    vs = NW.check(ISO, [stale], toc)
    ck("D3 盘上字节不符被拒", not vs[0].ok and "盘上这条已经是别的字节" in vs[0].reason,
       vs[0].reason[:40])

    # 块位置不存在必须拒
    gone = NW.NameEdit(eid=501050, block_pos=0x123, index=0,
                       old_raw=b"\x8d\x86", new_raw=b"\x8d\x86", mark="FNM")
    vs = NW.check(ISO, [gone], toc)
    ck("D4 块不存在被拒", not vs[0].ok and "没有可解析的名字块" in vs[0].reason,
       vs[0].reason[:40])

    # 下标越界必须拒
    oob = NW.NameEdit(eid=501050, block_pos=h.block_pos, index=99,
                      old_raw=h.raw, new_raw=b"\x8d\x86", mark="FNM")
    vs = NW.check(ISO, [oob], toc)
    ck("D5 下标越界被拒", not vs[0].ok and "越界" in vs[0].reason, vs[0].reason)

    # 超容量必须拒（reflow 上限）
    huge = NW.NameEdit(eid=501050, block_pos=h.block_pos, index=0,
                       old_raw=h.raw, new_raw=codec.encode_text("冥" * 60).data,
                       mark="FNM")
    vs = NW.check(ISO, [huge], toc)
    ck("D6 超容量被拒", not vs[0].ok and "块内装不下" in vs[0].reason,
       vs[0].reason[:40])

    # 汇总
    vs = NW.check(ISO, [good, bad, stale], toc)
    s = NW.summarize(vs)
    ck("D7 汇总 total=3", s["total"] == 3, s["total"])
    ck("D7 汇总 writable=1", s["writable"] == 1, s["writable"])
    ck("D7 汇总 blocked=2", s["blocked"] == 2, s["blocked"])
    ck("D7 阻断项带原因", all(x["reason"] for x in s["blocked_items"]))

    # build 一致性与多资源合并
    h2 = hits[(2801050, "FNM", 0)]
    e2 = NW.NameEdit(eid=2801050, block_pos=h2.block_pos, index=0,
                     old_raw=h2.raw, new_raw=codec.encode_text("冥墓方舟").data,
                     mark="FNM")
    builds = NW.build(ISO, [good, e2], toc)
    ck("D8 build 覆盖 2 个资源", len(builds) == 2, list(builds))
    ck("D8 资源长度不变",
       all(len(b.new_raw) == b.res_size for b in builds.values()))
    ck("D8 非压缩", all(not b.compressed for b in builds.values()))
    ck("D8 region 与 res_off 一致",
       all(b.region[0] == env.ISOBASE + b.res_off for b in builds.values()))

    # 同一资源多块合并（CLM + FNM 同属 501050）
    hc = hits[(501050, "CLM", 0)]
    ef = NW.NameEdit(eid=501050, block_pos=h.block_pos, index=0,
                     old_raw=h.raw, new_raw=codec.encode_text("冥墓方舟").data,
                     mark="FNM")
    ec = NW.NameEdit(eid=501050, block_pos=hc.block_pos, index=0,
                     old_raw=hc.raw, new_raw=codec.encode_text("冥墓方舟").data,
                     mark="CLM")
    builds = NW.build(ISO, [ef, ec], toc)
    ck("D9 同资源两块合并成 1 个结果", len(builds) == 1, list(builds))
    ck("D9 两条编辑都记了 note",
       sum(1 for n in builds[501050].notes if "名字块" in n) == 2,
       builds[501050].notes)

    # 同一条重复编辑，后者覆盖前者
    a = NW.NameEdit(eid=501050, block_pos=hc.block_pos, index=0,
                    old_raw=hc.raw, new_raw=codec.encode_text("冥墓").data, mark="CLM")
    b = NW.NameEdit(eid=501050, block_pos=hc.block_pos, index=0,
                    old_raw=hc.raw, new_raw=codec.encode_text("冥墓方舟").data, mark="CLM")
    builds = NW.build(ISO, [a, b], toc)
    blk = [x for x in N.scan(builds[501050].new_raw, 501050) if x.kind == "CLM"][0]
    ck("D10 同条重复编辑取后者", blk.plain(0) == "冥墓方舟", blk.plain(0))


# ---------------------------------------------------------------- E
def test_anti_split(toc):
    head("E 独立性：不靠 split(0x00)、块外零位移")
    res = slots.load_resource(ISO, 501050, toc)
    d = list(res.members.values())[0].data
    # 按 split(0x00) 数出来的「串」远多于真实条数（证明不能这么干）
    naive = len([s for s in d.split(b"\x00") if s])
    real = sum(b.count for b in N.scan(d, 501050))
    ck("split(0x00) 数出的串远多于真实条数（%d vs %d）" % (naive, real),
       naive > real * 10, (naive, real))
    # 名字块的总字节只占资源很小一部分
    tot = sum(b.size for b in N.scan(d, 501050))
    ck("名字块只占资源 %.2f%%" % (100.0 * tot / len(d)), tot < len(d) * 0.01,
       (tot, len(d)))
    # 重建后整块除串区与偏移表外逐字节相同
    clm = [b for b in N.scan(d, 501050) if b.kind == "CLM"][0]
    new, _rep = N.apply_changes(d, clm, {0: codec.encode_text("冥墓方舟改二型").data})
    a, b = clm.raw, new[clm.pos:clm.pos + clm.size]
    head_same = a[:N.HEAD + clm.toff] == b[:N.HEAD + clm.toff]
    ck("E 块头与三元组区逐字节未变", head_same)
    ck("E 块尾之后无残留（补零）",
       b[N.HEAD + N.parse_at(new, clm.pos, 501050).used:] ==
       b"\x00" * (clm.size - N.HEAD - N.parse_at(new, clm.pos, 501050).used))


def test_engine_routing(toc):
    """F. 双引擎分流：FNM 是 VER 块（走槽引擎），CLM 只能走名字块引擎。

    这是「同一段字节两种视图」的落地判据：分流错了就会两条链各改一次，
    后写的把前写的盖掉。
    """
    head("F 双引擎分流：FNM → 槽编辑，CLM → 名字块引擎")
    fnm_cnt = clm_cnt = 0
    for eid in EIDS:
        for b in N.blocks_of(ISO, eid, toc):
            if b.kind == "FNM":
                fnm_cnt += 1
                ck("F%d FNM 被标成 VER 块（ver_k=%s）" % (eid, b.ver_k),
                   b.ver_k is not None, b.ver_k)
                ck("F%d FNM 的 ver_k 能取到对应 VER 块" % eid,
                   b.ver_k is not None and
                   b.ver_k < len(slots.load_resource(ISO, eid, toc)
                                 .member(b.mem_tag).blocks))
            else:
                clm_cnt += 1
                ck("F%d CLM 不是 VER 块（ver_k=%s）" % (eid, b.ver_k),
                   b.ver_k is None, b.ver_k)
    ck("F 四个目标资源里 FNM %d 个 / CLM %d 个" % (fnm_cnt, clm_cnt),
       fnm_cnt == 4 and clm_cnt == 2, (fnm_cnt, clm_cnt))

    # to_slot_edit：FNM 能转，CLM 拒绝
    blk_fnm = [b for b in N.blocks_of(ISO, 501050, toc) if b.kind == "FNM"][0]
    blk_clm = [b for b in N.blocks_of(ISO, 501050, toc) if b.kind == "CLM"][0]
    ne_f = NW.NameEdit(eid=501050, block_pos=blk_fnm.pos, index=0,
                       old_raw=blk_fnm.items[0],
                       new_raw=codec.encode_text("冥墓方舟改").data, mark="FNM")
    se = NW.to_slot_edit(ne_f, blk_fnm)
    ck("F FNM 转成槽编辑：tag=%d k=%d slot=%d" % (se.tag, se.k, se.slot),
       (se.tag, se.k, se.slot) == (blk_fnm.mem_tag, blk_fnm.ver_k, 0))
    ck("F 转换后 new_payload 逐字节等于新串", se.new_payload == ne_f.new_raw)
    ne_c = NW.NameEdit(eid=501050, block_pos=blk_clm.pos, index=0,
                       old_raw=blk_clm.items[0],
                       new_raw=codec.encode_text("冥墓方舟・改").data, mark="CLM")
    try:
        NW.to_slot_edit(ne_c, blk_clm)
        ck("F CLM 转槽编辑必须被拒绝", False, "没有抛异常")
    except NW.NameWriteError as exc:
        ck("F CLM 转槽编辑被拒绝", "不是 VER 块" in str(exc), str(exc))

    # split_by_engine：混合输入正确分流
    edits = [ne_f, ne_c]
    se_list, ne_list, notes = NW.split_by_engine(ISO, edits, toc)
    ck("F 混合输入分流成 槽 %d / 名字 %d" % (len(se_list), len(ne_list)),
       len(se_list) == 1 and len(ne_list) == 1, (len(se_list), len(ne_list)))
    ck("F 分流说明逐条给出", len(notes) == 2, notes)
    ck("F 分流说明点名 FNM 走槽编辑", any("FNM" in n and "槽编辑" in n for n in notes), notes)
    ck("F 分流说明点名 CLM 走名字块引擎", any("CLM" in n and "名字块引擎" in n for n in notes), notes)

    # 同一资源既有槽编辑又有 CLM 编辑：合并成一条构建
    slot_builds = W.build(ISO, se_list, toc)
    ck("F 槽编辑先构建出 1 个资源", len(slot_builds) == 1, sorted(slot_builds))
    merged = NW.fold_into_slot_builds(ISO, slot_builds, ne_list, toc)
    ck("F 叠加后仍是 1 个资源（没多出一条）", len(merged) == 1, sorted(merged))
    ck("F 叠加后带「叠加在槽编辑之上」标记",
       "叠加在槽编辑之上" in merged[501050].notes, merged[501050].notes)
    # 合并后的字节里两条改动都在（不靠写盘，直接查内存）
    data = merged[501050].new_raw
    N.clear_cache()
    got = {b.kind: b.plain(0) for b in N.scan(data, 501050)}
    ck("F 合并结果里 FNM 是新值 %r" % got.get("FNM"),
       got.get("FNM") == "冥墓方舟改", got)
    ck("F 合并结果里 CLM 是新值 %r" % got.get("CLM"),
       got.get("CLM") == "冥墓方舟・改", got)
    ck("F 合并结果长度未变", len(data) == len(merged[501050].old_raw),
       (len(data), len(merged[501050].old_raw)))

    # 没有名字编辑时原样返回（一个字节都不动）
    same = NW.fold_into_slot_builds(ISO, slot_builds, [], toc)
    ck("F 无名字编辑时原样返回同一个对象", same is slot_builds)


def test_upper_layers(toc):
    """G. 上层接入：GUI/队列/检查台看到的字段与结论必须自洽。"""
    print("\n[G] 上层接入（as_hit / hit_key / item_from_name / pipeline.inspect）")
    fnm = None
    clm = None
    for h in N.hits_of(ISO, 501050, toc):
        if h.mark == "FNM" and fnm is None:
            fnm = h
        elif h.mark == "CLM" and h.index == 0 and clm is None:
            clm = h
    ck("G 找到 FNM 与 CLM 各一条", fnm is not None and clm is not None)

    hf = N.as_hit(fnm)
    hc = N.as_hit(clm)
    # as_hit 必须带出 mem_tag / ver_k，否则 FNM 会被当成 CLM 走错引擎
    ck("G as_hit 带 mem_tag", "mem_tag" in hf and hf["mem_tag"] == -1)
    ck("G as_hit 带 ver_k（FNM 是 0，不是 None）",
       hf.get("ver_k") == 0, hf.get("ver_k"))
    ck("G as_hit 的 CLM ver_k 是 None", hc.get("ver_k") is None, hc.get("ver_k"))
    ck("G as_hit 的 engine 与 ver_k 一致",
       hf["engine"] == "slot" and hc["engine"] == "name",
       (hf["engine"], hc["engine"]))
    ck("G as_hit 的 seg_hex 与 raw 一致",
       bytes.fromhex(hf["seg_hex"]) == fnm.raw)
    ck("G as_hit 的 kind 是 name", hf.get("kind") == "name")

    # hit_key：槽命中 4 个 int，名字命中带 "name" 前缀
    ck("G hit_key(FNM dict) 是 4 个 int",
       editplan.hit_key(hf) == (501050, -1, 0, 0), editplan.hit_key(hf))
    ck("G hit_key(CLM dict) 带 name 前缀",
       editplan.hit_key(hc) == ("name", 501050, clm.block_pos, 0),
       editplan.hit_key(hc))
    ck("G hit_key 对普通槽命中不变",
       editplan.hit_key({"eid": 4004643, "tag": 0, "k": 0, "slot": 9})
       == (4004643, 0, 0, 9))

    # item_from_name 两种入参都要吃
    it_d, miss_d = editplan.item_from_name(hf, "冥墓方舟改")
    it_o, miss_o = editplan.item_from_name(fnm, "冥墓方舟改")
    ck("G item_from_name 接受 dict", it_d.kind == "slot", it_d.kind)
    ck("G item_from_name 接受 NameHit", it_o.kind == "slot", it_o.kind)
    ck("G 两种入参产出的 key 相同", it_d.key() == it_o.key())
    ck("G 两种入参产出的新 payload 相同",
       it_d.new_payload == it_o.new_payload)
    ck("G FNM 计划 key 是 4 个 int", it_d.key() == (501050, -1, 0, 0),
       it_d.key())
    it_c, _ = editplan.item_from_name(hc, "冥墓方舟改")
    ck("G CLM 计划 kind=name", it_c.kind == "name", it_c.kind)
    ck("G CLM 计划 label 说清块与下标",
       "CLM" in it_c.label() and "@0x" in it_c.label(), it_c.label())

    # 混合队列：4 槽 + 2 名字块
    items = []
    for h in N.hits_of(ISO, 501050, toc):
        if h.mark == "CLM" and h.index in (0, 1):
            items.append(editplan.item_from_name(h, h.plain + "改")[0])
        elif h.mark == "FNM":
            items.append(editplan.item_from_name(h, h.plain + "改")[0])
    for eid in (501051, 2801050, 2801051):
        for h in N.hits_of(ISO, eid, toc):
            if h.mark == "FNM":
                items.append(editplan.item_from_name(h, h.plain + "改")[0])
    n_slot = sum(1 for i in items if i.kind == "slot")
    n_name = sum(1 for i in items if i.kind == "name")
    ck("G 混合队列 4 槽 + 2 名字块", (n_slot, n_name) == (4, 2), (n_slot, n_name))

    rep = pipeline.inspect(ISO, items)
    ck("G inspect 分别统计两类",
       rep["slot_items"] == 4 and rep["name_items"] == 2,
       (rep["slot_items"], rep["name_items"]))
    ck("G inspect 的 safe+warn+blocked 等于总数",
       rep["safe"] + rep["warn"] + rep["blocked"] == rep["total"],
       (rep["safe"], rep["warn"], rep["blocked"], rep["total"]))
    ck("G 无阻断项时 can_build 为真", rep["can_build"] is True,
       rep.get("blocked_detail"))
    ck("G 合并提示都标了 blocking=False",
       all(c.get("blocking") is False for c in rep["conflicts"]),
       rep["conflicts"])
    ck("G 报出「同资源既有槽又有名字」",
       any("合并成一次构建" in c.get("note", "") for c in rep["conflicts"]),
       rep["conflicts"])
    ck("G 报出「同一名字块多条合并重排」",
       any("同一名字块" in c.get("note", "") for c in rep["conflicts"]),
       rep["conflicts"])

    # 名字块装不下时必须 BLOCK，且 can_build 跟着变假
    long_text = "冥" * 60
    bad = [editplan.item_from_name(clm, long_text)[0]]
    rep2 = pipeline.inspect(ISO, bad)
    ck("G 名字块超容量时 blocked=1", rep2["blocked"] == 1, rep2["blocked"])
    ck("G 名字块超容量时 can_build 为假", rep2["can_build"] is False)
    ck("G 阻断说明提到块内余量",
       "余量" in json.dumps(rep2.get("blocked_detail") or [],
                            ensure_ascii=False),
       rep2.get("blocked_detail"))


def main():
    if not os.path.isfile(ISO):
        print("找不到测试盘：%s" % ISO)
        print("（本自测只读，需要 r1354 盘在位）")
        return 2
    print("测试盘：%s" % ISO)
    toc = slots.cached_toc(ISO)
    print("TOC 条目：%d" % len(toc))
    test_codes()
    test_parse(toc)
    test_write_paths(toc)
    test_plan(toc)
    test_anti_split(toc)
    test_engine_routing(toc)
    test_upper_layers(toc)
    print("\n" + "=" * 60)
    print("PASS %d / FAIL %d" % (_pass, _fail))
    if _fails:
        print("失败项：")
        for f in _fails:
            print("  - %s" % f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
