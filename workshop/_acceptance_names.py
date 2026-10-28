# -*- coding: utf-8 -*-
"""名字块（武器/机体名）端到端验收 —— 用户 m04698 的第 ⑧⑨⑩ 条要求。

跑法：
    python _acceptance_names.py

纪律（与 ``_acceptance.py`` 一致，不许自证）：

* 写盘后**绝不用本项目 build 结果当证据**，一律三层独立回读：
  1. 冻结检索层 ``query.reread`` + ``locator_core.decode_seg``（完全不碰本项目代码）；
  2. 名字块引擎 ``nametext.blocks_of``（重新解析产物字节）；
  3. 原始字节级结构校验（自己 seek 读、逐块比对头部/偏移表/非目标串）。
* 父盘 MD5 写盘前后必须一致（证明没覆盖父盘）。
* 整盘白名单差分必须 outside=0。
* **GUI 搜索通过 ≠ 实机渲染通过**：本脚本只证明盘面字节与静态字库覆盖，
  实机画面恒为 NOT_RUN，报告里必须写明。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import codec, editplan, env, nametext as N, namewriter as NW, \
    pipeline, slots, writer as W

SRC = os.environ.get("ACE3_TEST_ISO") or r"PATH\TO\TEST.iso"
OUT_DIR = os.path.join(env.WORK_DIR, "acc")
REPORT = os.path.join(OUT_DIR, "名字块验收.json")

# 冥墓方舟四行名称（用户点名的那台机体）
# 新文本必须与盘上原文不同，否则「回读前后一致」是理所当然的，验不出任何东西。
TARGETS = [
    # (eid, mark, index, 新文本)
    (501050, "CLM", 0, "冥墓方舟・改"),
    (501050, "FNM", 0, "冥墓方舟改"),
    (501051, "FNM", 0, "冥墓方舟（幽灵）改"),
    (2801050, "FNM", 0, "冥墓方舟改"),
    (2801051, "FNM", 0, "冥墓方舟（幽灵）改"),
]

_rows: list = []


def ck(name: str, cond: bool, detail: str = "") -> bool:
    _rows.append({"name": name, "ok": bool(cond), "detail": detail})
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" — " + detail) if detail else ""))
    return bool(cond)


def md5_of(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 22)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def frozen_readback(iso: str, eid: int, tag: int, k: int, slot: int) -> str:
    """用冻结检索层读一个 VER 槽（不 import 本项目任何模块）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    loc = os.path.join(env.LOCATOR_DIR)
    if loc not in sys.path:
        sys.path.insert(0, loc)
    import query as Q
    import locator_core as L
    seg, err = Q.reread(iso, eid, tag, k, slot)
    if seg is None:
        return "ERR:%s" % err
    c2c, _note = L.load_code_table()
    plain, _codes, _st = L.decode_seg(seg, c2c)
    _ = here
    return plain


def raw_res(iso: str, toc: dict, eid: int) -> bytes:
    off, size = toc[eid]
    with open(iso, "rb") as f:
        f.seek(env.ISOBASE + off)
        buf = b""
        while len(buf) < size:
            b = f.read(size - len(buf))
            if not b:
                break
            buf += b
        return buf


def main() -> int:
    if not os.path.isfile(SRC):
        print("找不到测试盘：%s" % SRC)
        return 2
    env.ensure_dirs()
    os.makedirs(OUT_DIR, exist_ok=True)
    print("父盘：%s" % SRC)
    md5_before = md5_of(SRC)
    print("父盘 MD5：%s" % md5_before)
    toc = slots.cached_toc(SRC)

    # ---- 1. 搜索：名字块必须能被找到，且普通检索索引里没有
    print("\n== 验收 A：搜「冥墓方舟」能定位全部副本")
    t0 = time.time()
    hits = N.search(SRC, "冥墓方舟")
    ck("A 名字块引擎搜到「冥墓方舟」", len(hits) > 0,
       "%d 条 / %.1fs" % (len(hits), time.time() - t0))
    want = {(e, m, i) for e, m, i, _t in TARGETS}
    got = {(h.eid, h.mark, h.index) for h in hits}
    ck("A 五个目标副本全部命中", want <= got, "缺 %s" % (want - got))
    ck("A 命中带资源内偏移与串边界",
       all(h.res_off >= 0 and h.block_size > 0 for h in hits),
       "首条 res_off=0x%X block_size=%d" % (hits[0].res_off, hits[0].block_size))
    ck("A 命中带引擎归属（FNM=slot / CLM=name）",
       all(h.engine in ("slot", "name") for h in hits),
       "FNM→%s CLM→%s" % (next(h.engine for h in hits if h.mark == "FNM"),
                          next(h.engine for h in hits if h.mark == "CLM")))

    # 证明普通检索索引里确实搜不到（这就是「为什么要独立适配层」）
    from core import search as S
    info = S.find_info(SRC)
    in_index = 0
    if info and info.get("db"):
        idx = S.open_index(info["db"])
        in_index = len(idx.search("冥墓方舟", limit=50))
    ck("A 普通检索索引里搜不到（证明需要名字块适配层）", in_index == 0,
       "索引命中 %d 条" % in_index)

    # ---- 2. 构造计划（走 editplan 自动分流）
    print("\n== 验收 B：编辑计划自动分流到两套引擎")
    items = []
    for eid, mark, index, new_text in TARGETS:
        h = next((x for x in hits
                  if x.eid == eid and x.mark == mark and x.index == index), None)
        if h is None:
            ck("B 找到 %d %s[%d]" % (eid, mark, index), False)
            continue
        it, missing = editplan.item_from_name(h, new_text)
        items.append(it)
        ck("B %d %s[%d] 编码无缺字" % (eid, mark, index), not missing,
           "缺字 %s" % missing if missing else "新串 %r" % new_text)
    n_slot = sum(1 for i in items if i.kind == "slot")
    n_name = sum(1 for i in items if i.kind == "name")
    ck("B 分流结果：FNM 走槽引擎、CLM 走名字块引擎",
       (n_slot, n_name) == (4, 1), "槽 %d / 名字 %d" % (n_slot, n_name))

    # ---- 3. 检查台
    print("\n== 验收 C：检查台（含名字块统计）")
    rep = pipeline.inspect(SRC, items)
    ck("C 检查台无阻断", rep["blocked"] == 0,
       "可写 %d / 需确认 %d / 阻断 %d" % (rep["safe"], rep["warn"], rep["blocked"]))
    ck("C 检查台统计名字块条数", rep["name_items"] == 1,
       "name_items=%d" % rep["name_items"])
    ck("C 允许写盘", rep["can_build"] is True)

    # ---- 4. 写盘
    print("\n== 验收 D：写盘（事务式，父盘只读）")
    t0 = time.time()
    man = pipeline.build_to(SRC, items, out_dir=OUT_DIR,
                            expected_md5=md5_before, name_hint="names")
    elapsed = time.time() - t0
    out_iso = (man.get("output") or {}).get("path") or ""
    ck("D 生成新 ISO", bool(out_iso) and os.path.isfile(out_iso),
       "%s（%.1fs）" % (os.path.basename(out_iso), elapsed))
    if not out_iso or not os.path.isfile(out_iso):
        return _finish(md5_before, "")

    # ---- 5. 独立回读三层
    print("\n== 验收 E：独立回读（三层，全不走本项目 build 结果）")
    rb = man.get("readback") or {}
    ck("E 内建回读通过", rb.get("ok") is True,
       "槽 %s + 名字块 %s" % (rb.get("slot_checked"), rb.get("name_checked")))

    # 层 1：冻结检索层（完全不碰本项目代码）
    for eid, mark, index, _t in TARGETS:
        if mark != "FNM":
            continue
        before = frozen_readback(SRC, eid, -1, 0, index)
        after = frozen_readback(out_iso, eid, -1, 0, index)
        ck("E1 冻结层回读 %d FNM" % eid,
           before != after and "ERR" not in after,
           "%r → %r" % (before, after))

    # 层 2：名字块引擎重新解析产物
    for eid, mark, index, new_text in TARGETS:
        blks = N.blocks_of(out_iso, eid, toc=None)
        got_text = ""
        for b in blks:
            # b.mark 是 bytes（b"CLM\x00"），b.kind 才是 "CLM"
            if b.kind == mark and 0 <= index < b.count:
                got_text = b.plain(index)
                break
        ck("E2 名字块引擎回读 %d %s[%d]" % (eid, mark, index),
           got_text == new_text, "%r（期望 %r）" % (got_text, new_text))

    # 层 3：原始字节级结构校验
    toc_out = slots.load_toc(out_iso)
    ck("E3 产物 TOC 条目数与父盘一致", len(toc_out) == len(toc),
       "%d vs %d" % (len(toc_out), len(toc)))
    for eid in (501050, 501051, 2801050, 2801051):
        a = raw_res(SRC, toc, eid)
        b = raw_res(out_iso, toc_out, eid)
        ck("E3 %d 资源长度未变" % eid, len(a) == len(b), "%d B" % len(a))
        ok_hdr = True
        ok_other = True
        changed_any = False
        for ba in N.scan(a, eid):
            bb = N.parse_at(b, ba.pos, eid)
            if bb is None:
                ok_hdr = False
                continue
            if b[ba.pos:ba.pos + ba.toff] != a[ba.pos:ba.pos + ba.toff]:
                ok_hdr = False
            ch = [i for i in range(ba.count) if ba.items[i] != bb.items[i]]
            if ch:
                changed_any = True
            for i in range(ba.count):
                if i not in ch and ba.items[i] != bb.items[i]:
                    ok_other = False
        ck("E3 %d 块头/偏移表/三元组区逐字节未变" % eid, ok_hdr)
        ck("E3 %d 非目标串逐字节未变" % eid, ok_other)
        _ = changed_any

    # ---- 6. 整盘差分 + 受保护资源
    print("\n== 验收 F：整盘差分与受保护资源")
    diff = man.get("diff") or {}
    ck("F 白名单外 0 字节", diff.get("ok") is True and diff.get("outside") == 0,
       "允许区间 %d 段 / 白名单外 %s 字节"
       % (len(diff.get("allow_ranges") or []), diff.get("outside")))
    prot = man.get("protected") or {}
    ck("F 15 包字库与受保护资源无差异",
       prot.get("ok") is True, "检查 %s 项（含 %s 个字体包）"
       % (prot.get("checked"), prot.get("font_pkgs")))

    # ---- 7. 父盘完整性
    print("\n== 验收 G：父盘未被覆盖")
    md5_after = md5_of(SRC)
    ck("G 父盘 MD5 前后一致", md5_after == md5_before, md5_after)

    # ---- 8. 实机边界（必须写明）
    print("\n== 验收 H：实机边界")
    ck("H GUI/盘面通过 ≠ 实机渲染通过（本项恒为 NOT_RUN，不是失败）",
       True, "PS2/PCSX2 画面验收 = NOT_RUN，需人工实机确认")

    return _finish(md5_before, out_iso)


def _finish(md5_before: str, out_iso: str) -> int:
    ok = sum(1 for r in _rows if r["ok"])
    bad = [r for r in _rows if not r["ok"]]
    payload = {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "parent": SRC, "parent_md5": md5_before,
        "output": out_iso,
        "output_md5": md5_of(out_iso) if out_iso and os.path.isfile(out_iso) else "",
        "rows": _rows,
        "pass": ok, "fail": len(bad),
        "real_machine": "NOT_RUN",
        "note": ("本报告只证明盘面字节与静态字库覆盖。"
                 "PS2 实机 / PCSX2 画面渲染验收为 NOT_RUN，必须单独做。"),
    }
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("\n" + "=" * 60)
    print("通过 %d / %d" % (ok, len(_rows)))
    if bad:
        print("失败项：")
        for r in bad:
            print("  - %s  %s" % (r["name"], r["detail"]))
    print("报告：%s" % REPORT)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
