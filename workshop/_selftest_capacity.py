"""自测：容量与可用空间口径（对照冻结工具 _r223_apply_plan 的判据）。

判据（写盘引擎必须一致）：
  nxt   = 下一个非零 offs（无则 base+len(data)）
  gap   = nxt - offs[slot]
  avail = min(len(jp_seg), gap)        ← 原版日文串长度也是上界
  放行   len(new) + 1 <= avail          ← +1 是终止 NUL
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import env, slots, codec  # noqa: E402

ISO = os.environ.get("ACE3_TEST_ISO") or r"PATH\TO\TEST.iso"
JP_ISO = env.ORIG_JP_ISO
JP_DB = os.path.join(env.LOCATOR_SHARED, "jp_cache.sqlite")


def main() -> int:
    import sqlite3
    toc = slots.load_toc(ISO)
    jp = sqlite3.connect("file:%s?mode=ro" % JP_DB.replace("\\", "/"), uri=True)
    jp.row_factory = sqlite3.Row

    print("原版日文盘:", JP_ISO, os.path.exists(JP_ISO))

    cases = [(4004643, 0, 0), (6020, -1, 13), (6200, -1, 0)]
    for eid, tag, k in cases:
        res = slots.load_resource(ISO, eid, toc)
        mem = res.member(tag)
        blk = mem.block(k)
        print("=" * 74)
        print("eid=%d tag=%d k=%d  槽数=%d  comp=%s"
              % (eid, tag, k, blk.slot_count(), mem.compressed))
        for slot in range(min(blk.slot_count(), 6)):
            cur = slots.slot_payload(mem.data, blk, slot)
            row = jp.execute(
                "SELECT jp_text,jp_hex,jp_unk FROM jps WHERE eid=? AND tag=? "
                "AND k=? AND slot=?", (eid, tag, k, slot)).fetchone()
            jp_seg = b""
            if row and row["jp_hex"]:
                jp_seg = bytes.fromhex(row["jp_hex"])
            gap = blk.span_in(mem.data, slot)
            avail = min(len(jp_seg), gap) if jp_seg else gap
            seg = codec.Segment(cur)
            print("  slot=%d offs=%d gap=%d jp_len=%d avail=%d cur_len=%d"
                  % (slot, blk.offset_of(slot), gap, len(jp_seg), avail, len(cur)))
            print("       cur=%r" % (seg.plain_with_tags()[:70],))
            if row:
                print("       jp =%r" % ((row["jp_text"] or "")[:70],))
            ok = len(cur) + 1 <= avail
            print("       fits(现状)=%s" % ok)

    print("\n== 验收用例：4004643 (0,0) slot9 「何？」→「什么？」==")
    res = slots.load_resource(ISO, 4004643, toc)
    mem = res.member(0)
    blk = mem.block(0)
    cur = slots.slot_payload(mem.data, blk, 9)
    print("  现盘 payload = %r" % cur)
    seg = codec.Segment(cur)
    print("  正文 = %r  标签 = %s" % (seg.plain, [t.decode() for t in seg.tags]))
    for new in ("何？", "什么？", "这是很长的一句话？", "甲" * 20):
        data, er = codec.rebuild_with_plain(seg, new)
        gap = blk.span_in(mem.data, 9)
        # 原版日文「何？」= 4 字节
        jp_len = 4
        avail = min(jp_len, gap)
        print("  改 %-10r -> %2d 字节  avail=%d(+NUL 需 %d) 能装=%s  missing=%s"
              % (new, len(data), avail, len(data) + 1,
                 len(data) + 1 <= avail, sorted(er.missing) or "-"))

    print("\n== 全库容量统计：有多少槽在「原位扩写」时会卡住 ==")
    conn = sqlite3.connect(
        "file:%s?mode=ro" % os.path.join(
            env.LOCATOR_DB, "r1356_lenton_what", "text_index.sqlite"
        ).replace("\\", "/"), uri=True)
    n_slot = n_jp = 0
    for row in conn.execute("SELECT COUNT(*) FROM slots"):
        n_slot = row[0]
    for row in jp.execute("SELECT COUNT(*) FROM jps WHERE jp_hex IS NOT NULL "
                          "AND jp_hex<>''"):
        n_jp = row[0]
    print("  索引槽数=%d  有原版日文串=%d" % (n_slot, n_jp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
