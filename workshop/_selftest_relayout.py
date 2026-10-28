"""自测：relayout（VER 块内重排）。

用例全部取自实测盘面（发布版用环境变量 ACE3_TEST_ISO 指定测试盘）：
  A. eid=4004643 (tag0,k0) slot9：「什么？」→「何？」（变短，最小位移 inplace）
  B. 同块 slot9 加长到「最小位移」上限，再多 1 字节必须触发紧凑重排或 BLOCK
  C. 同块多槽批量加长（批量，验证位移累计与独立回读）
  D. 含 book 标签的槽改写（eid=6020 slot131）
  E. 末槽加长
  F. 单槽吃到块总容量上限，再多 1 字节必须 BLOCK
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import env, slots, codec, relayout  # noqa: E402

ISO = os.environ.get("ACE3_TEST_ISO") or r"PATH\TO\TEST.iso"
TOC = slots.cached_toc(ISO)


def load(eid, tag, k):
    mem = slots.load_resource(ISO, eid, TOC).member(tag)
    return mem, mem.block(k)


def rebuild(mem, blk, slot, new_plain):
    seg = codec.Segment(slots.slot_payload(mem.data, blk, slot))
    data, er = codec.rebuild_with_plain(seg, new_plain)
    assert not er.missing, "缺字：%s" % sorted(er.missing)
    return data


def max_growth(mem, blk, slot, base, compact):
    """在给定策略下，该槽最多能加多少 ASCII 字节。"""
    lo, hi = 0, 1 << 16
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cand = base + b"A" * mid
        sim = relayout.simulate(mem.data, blk, {slot: cand}, compact=compact)
        if sim["fits"]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def apply_case(name, eid, tag, k, edits, expect_blocked=False, show=True):
    mem, blk = load(eid, tag, k)
    if show:
        print("=" * 76)
        print("%s  eid=%d tag=%d k=%d  槽数=%d  块总容量余量=%d"
              % (name, eid, tag, k, blk.slot_count(),
                 relayout.block_slack(mem.data, blk)))
    payloads = {}
    for slot, raw in edits.items():
        if isinstance(raw, str):
            data = rebuild(mem, blk, slot, raw)
        else:
            data = raw
        payloads[slot] = data
        if show:
            old = slots.slot_payload(mem.data, blk, slot)
            plain_old = codec.Segment(old).plain
            txt = (codec.Segment(data).plain if data[:1] != b"<" or b">" in data
                   else "")
            print("  slot=%-3d %r(%dB) -> %dB" % (slot, plain_old, len(old), len(data)))
    try:
        new_buf, rep = relayout.relayout(mem.data, blk, payloads)
    except relayout.RelayoutBlocked as e:
        if show:
            print("  BLOCKED：%s" % e)
        assert expect_blocked, "本应成功却 BLOCK"
        return False
    assert not expect_blocked, "本应 BLOCK 却成功"
    errs = relayout.verify_relayout(mem.data, new_buf, blk, rep["new_offs"], payloads)
    if show:
        print("  mode=%s moved=%d span=%d slack_after=%d"
              % (rep["mode"], rep["moved"], rep["span"], rep["slack_after"]))
    assert not errs, "独立校验失败：%s" % errs[:3]
    if show:
        print("  ✓ 独立校验通过（目标槽命中 / 非目标槽逐字节不变 / 块外不变 / 可重新解析）")
    return True


def main():
    # A 变短
    apply_case("A 变短：什么？→何？", 4004643, 0, 0, {9: "何？"})

    mem, blk = load(4004643, 0, 0)
    base = rebuild(mem, blk, 9, "什么？")
    g_min = max_growth(mem, blk, 9, base, compact=False)
    g_cmp = max_growth(mem, blk, 9, base, compact=True)
    print("\n推演：块总容量余量=%d；slot9 最小位移最多 +%d 字节，紧凑重排最多 +%d 字节"
          % (relayout.block_slack(mem.data, blk), g_min, g_cmp))

    # B 最小位移上限
    apply_case("B 最小位移上限：slot9 +%d" % g_min, 4004643, 0, 0,
               {9: base + b"A" * g_min})
    # B2 超 1 字节（应转紧凑重排）
    apply_case("B2 超过最小位移 +1（应转紧凑）", 4004643, 0, 0,
               {9: base + b"A" * (g_min + 1)})
    # F 超总容量 1 字节必须 BLOCK
    apply_case("F 超总容量必须 BLOCK", 4004643, 0, 0,
               {9: base + b"A" * (g_cmp + 1)}, expect_blocked=True)

    # C 多槽批量加长
    apply_case("C 多槽批量加长", 4004643, 0, 0,
               {9: "何？何？何？", 10: "什么？什么？", 12: "怎么了？"})
    # D 含 book 标签槽
    apply_case("D 含 book 标签槽改写", 6020, -1, 13, {131: "甲" * 10})
    # E 末槽
    apply_case("E 末槽加长", 4004643, 0, 0, {12: "怎么了？甲甲甲甲甲甲甲甲甲甲"})

    print("\n全部用例通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
