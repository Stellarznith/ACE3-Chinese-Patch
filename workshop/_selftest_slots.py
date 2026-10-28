"""一次性自测：core.slots 的布局解析、逻辑槽口径、与索引库逐槽对齐。"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import slots  # noqa: E402
from core import env    # noqa: E402

ISO = os.environ.get("ACE3_TEST_ISO") or r"PATH\TO\TEST.iso"
DB = os.path.join(env.LOCATOR_DB, "r1356_lenton_what", "text_index.sqlite")
print("DB =", DB, os.path.exists(DB))


def main():
    toc = slots.load_toc(ISO)
    print("TOC 条目:", len(toc))

    res = slots.load_resource(ISO, 4004643, toc)
    print("eid=4004643 compressed=%s bnd=%s members=%s"
          % (res.compressed, res.bnd, res.member_order))
    m = res.member(0)
    print("  tag0 len=%d cap=%d blocks=%d" % (len(m.data), m.capacity, len(m.blocks)))
    blk = m.block(0)
    print("  block0 start=%d size=%d cnt=%d toff=%d 非零=%d note=%r"
          % (blk.start, blk.size, blk.cnt, blk.toff, blk.slot_count(), blk.note))
    print("  逻辑槽 0..12 raw_idx:", [blk.raw_index(s) for s in range(blk.slot_count())])
    print("  slot9 offs=%d span=%d payload=%s"
          % (blk.offset_of(9), blk.span(9), slots.slot_payload(m.data, blk, 9)))
    print("  tail_free=%d tail_nonzero=%d foreign=%s"
          % (blk.tail_free(m.data), blk.tail_nonzero(m.data),
             blk.has_foreign_tail(m.data)))

    # 与索引库逐槽对齐（全库抽样 40 个块）
    conn = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    rows = conn.execute(
        "SELECT slot,seg FROM slots WHERE eid=4004643 AND tag=0 AND k=0 ORDER BY slot"
    ).fetchall()
    ok = 0
    print("  索引库该块槽数:", len(rows))
    for slot, seg in rows:
        mine = slots.slot_payload(m.data, blk, slot)
        if mine == seg:
            ok += 1
        else:
            print("   MISMATCH slot=%d idx=%r mine=%r" % (slot, seg, mine))
    print("  逐槽字节一致: %d/%d" % (ok, len(rows)))
    assert ok == len(rows)

    # 抽样 40 块做同样的核对
    keys = conn.execute(
        "SELECT DISTINCT eid,tag,k FROM slots WHERE comp=0 "
        "ORDER BY eid LIMIT 60").fetchall()
    total = same = 0
    bad = []
    for eid, tag, k in keys:
        try:
            r2 = slots.load_resource(ISO, eid, toc)
            mem = r2.member(tag)
            if mem is None:
                bad.append((eid, tag, k, "no member"))
                continue
            b2 = mem.block(k)
        except Exception as e:                      # noqa: BLE001
            bad.append((eid, tag, k, repr(e)))
            continue
        for slot, seg in conn.execute(
                "SELECT slot,seg FROM slots WHERE eid=? AND tag=? AND k=? ORDER BY slot",
                (eid, tag, k)):
            if slot >= b2.slot_count():
                bad.append((eid, tag, k, "slot %d >= %d" % (slot, b2.slot_count())))
                continue
            total += 1
            if slots.slot_payload(mem.data, b2, slot) == seg:
                same += 1
            elif len(bad) < 8:
                bad.append((eid, tag, k, "slot %d bytes differ" % slot))
    print("  抽样 %d 块：逐槽一致 %d/%d" % (len(keys), same, total))
    for b in bad[:8]:
        print("   issue:", b)

    # 压缩资源
    r3 = slots.load_resource(ISO, 701009, toc)
    print("eid=701009 compressed=%s bnd=%s raw=%d data=%d members=%s"
          % (r3.compressed, r3.bnd, len(r3.raw), len(r3.data), r3.member_order))
    for mem in r3.text_members():
        print("   tag=%d blocks=%d len=%d cap=%d"
              % (mem.tag, len(mem.blocks), len(mem.data), mem.capacity))
        for k, b in enumerate(mem.blocks[:4]):
            print("      k=%d start=%d size=%d cnt=%d 非零=%d tail_nz=%d"
                  % (k, b.start, b.size, b.cnt, b.slot_count(),
                     b.tail_nonzero(mem.data)))

    # 字体包结构（只验证可解析，不解 PSMT4）
    r4 = slots.load_resource(ISO, 1200000, toc)
    print("eid=1200000 compressed=%s bnd=%s members=%s"
          % (r4.compressed, r4.bnd, r4.member_order[:8]))
    for tag in r4.member_order[:6]:
        mem = r4.member(tag)
        print("   tag=%d len=%d cap=%d blocks=%d" % (tag, len(mem.data),
                                                     mem.capacity, len(mem.blocks)))

    print("\nALL OK")


if __name__ == "__main__":
    main()
