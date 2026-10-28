"""全量核对：core.slots 的槽位解析与索引库 slots 表 94,863 槽逐一比对。

判据（全部逐字节，不做任何近似）：
  * 槽位身份：索引库 ``(eid, tag, k, slot)`` 直接喂给 ``load_resource`` +
    ``Member.block(k)`` + ``slot_payload(…, slot)``；
  * 必须与 ``slots.seg`` 字节完全相同；
  * 同时核对 ``comp`` 标志与 ``file_off``（= ISOBASE + toc.off + 成员相对偏移）。

输出：work/verify_all_slots.json + 控制台摘要。任何一处不一致都必须为 0。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import env, slots  # noqa: E402

ISO = os.environ.get("ACE3_TEST_ISO") or r"PATH\TO\TEST.iso"
DB = os.path.join(env.LOCATOR_DB, "r1356_lenton_what", "text_index.sqlite")
OUT = os.path.join(env.WORK_DIR, "verify_all_slots.json")


def main() -> int:
    env.ensure_dirs()
    t0 = time.time()
    toc = slots.load_toc(ISO)
    conn = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    conn.row_factory = sqlite3.Row

    keys = conn.execute(
        "SELECT DISTINCT eid FROM slots ORDER BY eid").fetchall()
    eids = [r[0] for r in keys]
    print("索引库 distinct eid: %d ；TOC: %d" % (len(eids), len(toc)))

    total = same = 0
    bad = []
    missing_eid = 0
    comp_cnt = 0
    off_bad = 0
    per_eid_ms = []

    for n, eid in enumerate(eids, 1):
        te = time.time()
        try:
            res = slots.load_resource(ISO, eid, toc)
        except Exception as e:                       # noqa: BLE001
            bad.append({"eid": eid, "why": "load_resource: %r" % (e,)})
            continue
        by_key = defaultdict(list)
        for r in conn.execute(
                "SELECT tag,k,slot,seg,comp,file_off,abs_off,base FROM slots "
                "WHERE eid=? ORDER BY tag,k,slot", (eid,)):
            by_key[(r["tag"], r["k"])].append(r)

        for (tag, k), rows in by_key.items():
            mem = res.member(tag)
            if mem is None:
                bad.append({"eid": eid, "tag": tag, "k": k,
                            "why": "member 缺失 members=%s" % (res.member_order,)})
                continue
            try:
                blk = mem.block(k)
            except Exception as e:                   # noqa: BLE001
                bad.append({"eid": eid, "tag": tag, "k": k,
                            "why": "block: %r blocks=%d" % (e, len(mem.blocks))})
                continue
            if blk.slot_count() != len(rows):
                bad.append({"eid": eid, "tag": tag, "k": k,
                            "why": "槽数 %d != 索引 %d"
                                   % (blk.slot_count(), len(rows))})
                continue
            for r in rows:
                total += 1
                mine = slots.slot_payload(mem.data, blk, r["slot"])
                if mine != r["seg"]:
                    if len(bad) < 40:
                        bad.append({"eid": eid, "tag": tag, "k": k,
                                    "slot": r["slot"], "why": "字节不同",
                                    "mine": mine[:60].hex(),
                                    "theirs": r["seg"][:60].hex()})
                    continue
                if bool(r["comp"]) != bool(res.compressed or mem.compressed):
                    comp_cnt += 1
                    if len(bad) < 40:
                        bad.append({"eid": eid, "tag": tag, "k": k,
                                    "slot": r["slot"], "why": "comp 标志不符"})
                    continue
                # file_off = abs_off + base + offs[slot]
                #           = (ISOBASE+off+成员相对偏移) + 块起点 + 槽相对块偏移
                exp_off = mem.blob_off + blk.start + blk.used[r["slot"]]
                if r["file_off"] != exp_off:
                    off_bad += 1
                    if len(bad) < 40:
                        bad.append({"eid": eid, "tag": tag, "k": k,
                                    "slot": r["slot"], "why": "file_off 不符",
                                    "mine": hex(exp_off),
                                    "theirs": hex(r["file_off"])})
                same += 1
        per_eid_ms.append((time.time() - te) * 1000.0)
        if n % 100 == 0 or n == len(eids):
            el = time.time() - t0
            print("  %d/%d eid  %d 槽  一致=%d  bad=%d  %.1fs  (最近 %.0f ms/eid)"
                  % (n, len(eids), total, same, len(bad), el, per_eid_ms[-1]))

    report = {
        "iso": ISO,
        "iso_md5_expected": "95f8197709d38071cd57ab63d5a9aa76",
        "db": DB,
        "distinct_eid": len(eids),
        "slots_total": total,
        "slots_pass": same,
        "slots_fail": total - same,
        "comp_flag_mismatch": comp_cnt,
        "file_off_mismatch": off_bad,
        "elapsed_sec": round(time.time() - t0, 1),
        "bad": bad,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n总槽 %d  一致 %d  不一致 %d  comp标志不符 %d  file_off 不符 %d  用时 %.1fs"
          % (total, same, total - same, comp_cnt, off_bad, time.time() - t0))
    print("报告:", OUT)
    return 0 if total == same else 1


if __name__ == "__main__":
    sys.exit(main())
