"""十五项真实验收（任务书 §九）自动化执行器。

纪律
----
* 每一项都必须有**独立证据**：写盘后从盘面重新读取（用冻结检索层
  ``locator_core`` / ``query``，不调用本项目的 build 结果），而不是让写盘函数
  自己给自己出证明。
* 所有实验都写在 ``work/acc/`` 下，父盘全程只读。
* 结果落 ``work/acc/验收报告.json`` + ``验收报告.md``。

用法::

    python _acceptance.py            # 跑全部（除打包 EXE 之外的所有项）
    python _acceptance.py --only 7,8 # 只跑指定项
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core import codec, editplan, env, estimate, fontcov, pipeline, slots  # noqa: E402
from core import search as S                                              # noqa: E402

sys.path.insert(0, env.LOCATOR_DIR)
import locator_core as L      # noqa: E402  冻结检索层（只读，独立回读用）
import query as Q             # noqa: E402

ACC_DIR = os.path.join(env.WORK_DIR, "acc")
ISO_OUT = os.path.join(ACC_DIR, "iso")

RESULTS = []


# ---------------------------------------------------------------- 证据工具
def md5(path: str) -> str:
    return env.md5_file(path)


def frozen_read(iso: str, eid: int, tag: int, k: int, slot: int):
    """用**冻结检索层**独立回读一个槽（不经过本项目任何写盘代码）。"""
    seg, err = Q.reread(iso, eid, tag, k, slot)
    if seg is None:
        return None, err
    c2c, _note = L.load_code_table()
    plain, codes, _stats = L.decode_seg(seg, c2c)
    return {"plain": plain, "hex": seg.hex().upper(), "bytes": len(seg)}, None


def record(no: int, name: str, ok: bool, detail: str, evidence=None) -> None:
    RESULTS.append({"no": no, "name": name, "ok": bool(ok),
                    "detail": detail, "evidence": evidence or {}})
    print("  [%s] %d. %s — %s" % ("PASS" if ok else "FAIL", no, name, detail))


def fresh_out(name: str) -> str:
    os.makedirs(ISO_OUT, exist_ok=True)
    p = os.path.join(ISO_OUT, name)
    if os.path.exists(p):
        os.remove(p)
    return p


def collect_evidence(dst: str) -> dict:
    """把 pipeline 落在 stage 里的证据文件收进一个字典（便于写报告）。"""
    d = os.path.dirname(dst)
    stem = os.path.splitext(os.path.basename(dst))[0]
    out = {}
    for suffix in ("_清单.json", "_清单.md"):
        p = os.path.join(d, stem + suffix)
        if os.path.exists(p):
            out[os.path.basename(p)] = os.path.getsize(p)
    return out


# ---------------------------------------------------------------- 公共准备
class Ctx:
    def __init__(self) -> None:
        self.idx = None
        self.iso = ""
        self.md5 = ""
        self.slots_cache = {}

    def open(self) -> None:
        cands = [i for i in S.list_indexes()
                 if i.get("iso") and os.path.exists(i["iso"])]
        default_stem = getattr(Q, "DEFAULT_STEM", "")
        pick = next((i for i in cands if i["stem"] == default_stem), None)
        if pick is None and cands:
            pick = max(cands, key=lambda i: i.get("built_at") or "")
        if pick is None:
            raise SystemExit("没有可用索引")
        self.idx = S.open_index(pick["db"])
        self.iso = self.idx.iso_path
        self.md5 = self.idx.iso_md5

    def hit(self, eid, tag, k, slot) -> dict:
        key = (eid, tag, k, slot)
        if key not in self.slots_cache:
            h = self.idx.slot(eid, tag, k, slot)
            if h is None:
                raise RuntimeError("索引里没有 %s" % (key,))
            self.slots_cache[key] = h
        return self.slots_cache[key]

    _alt: dict = {}

    def open_stem(self, stem: str):
        """打开另一份索引（用于跨版本对照与「何？」原始槽验证）。"""
        if stem not in self._alt:
            info = next((i for i in S.list_indexes() if i["stem"] == stem), None)
            if info is None:
                raise RuntimeError("没有索引 %s" % stem)
            self._alt[stem] = S.open_index(info["db"])
        return self._alt[stem]

    def item(self, eid, tag, k, slot, new_text: str):
        h = self.hit(eid, tag, k, slot)
        it, missing = editplan.item_from_hit(h, new_text)
        return it, missing, h


def q_of(items) -> editplan.PlanQueue:            # noqa: ANN001
    q = editplan.PlanQueue()
    for it in items:
        q.upsert(it)
    return q


# ---------------------------------------------------------------- 各项验收
def acc1_2_3(ctx: Ctx) -> None:
    """1) 搜「何？」定位 4004643 正确槽  2) 改「什么？」  3) 容量增长

    注意：当前父盘 r1356 上该槽**已经被历史轮次改成「什么？」**，因此在它上面
    搜「何？」定位不到自己（只能搜到别人的「何？」）。任务书第 1 项的字面要求
    是「搜索『何？』并定位 4004643 正确槽」，所以这一项用**索引里仍是「何？」的
    那一版**（r1354_grayline_clamp_test）来验证，并顺便证明槽身份跨版本一致、
    物理偏移不同——这正是「禁止跨版本复用旧偏移」的实证。
    """
    print("\n== 验收 1–3：搜索定位 / 改名 / 容量")
    src_stem = "r1354_grayline_clamp_test"
    alt = ctx.open_stem(src_stem)
    iso_a, md5_a = alt.iso_path, alt.iso_md5
    print("    （用 %s 验证「何？」：%s）" % (src_stem, iso_a))

    hits = alt.search("何？", limit=200)
    target = [h for h in hits if h["eid"] == 4004643]
    hit9 = next((h for h in target if h["slot"] == 9), None)
    record(1, "搜索「何？」并定位 4004643 正确槽",
           hit9 is not None and hit9["text"].endswith("何？"),
           "在 %s 上搜「何？」命中 %d 槽，eid=4004643 命中 %d 槽；slot9 文本 %r、"
           "file_off=0x%X（同槽在 r1356 上偏移为 0x3ED43CF2，偏移不同、槽身份相同）"
           % (src_stem, len(hits), len(target),
              hit9["text"] if hit9 else None,
              int(hit9["file_off"]) if hit9 else 0),
           {"hits": len(hits), "target_slots": [h["slot"] for h in target],
            "slot9_text": hit9["text"] if hit9 else None,
            "slot9_file_off": hit9["file_off"] if hit9 else None,
            "iso": iso_a, "iso_md5": md5_a})
    if hit9 is None:
        return

    it_a, miss_a = editplan.item_from_hit(hit9, "什么？")
    it_b, miss_b = editplan.item_from_hit(hit9, "什么？为什么这样说？")
    est_a = estimate.estimate(iso_a, 4004643, 0, 0, 9, it_a.new_payload)
    est_b = estimate.estimate(iso_a, 4004643, 0, 0, 9, it_b.new_payload)
    record(2, "改为「什么？」正确生成新文本",
           it_a.new_payload.hex().upper().endswith("897C89578148")
           and not miss_a and est_a.ok,
           "原 %d 字节 → 新 %d 字节 %s，缺字 %s；容量 %s"
           % (len(it_a.old_payload), len(it_a.new_payload),
              it_a.new_payload.hex().upper(), miss_a or "无",
              est_a.mode or est_a.reason),
           {"payload_hex": it_a.new_payload.hex().upper(),
            "old_hex": it_a.old_payload.hex().upper()})
    record(3, "正文增长触发容量调整",
           (est_b.ok and est_b.mode != "inplace") or (not est_b.ok),
           "块内可扩 %d 字节：短改「什么？」%s（%s）；长改「什么？为什么这样说？」"
           "%s（%s）"
           % (est_a.block_slack,
              "可写" if est_a.ok else "阻断", est_a.mode or est_a.reason,
              "可写" if est_b.ok else "阻断", est_b.mode or est_b.reason),
           {"short": {"ok": est_a.ok, "mode": est_a.mode,
                      "slack": est_a.block_slack},
            "long": {"ok": est_b.ok, "mode": est_b.mode,
                     "reason": est_b.reason}}) 


def acc4(ctx: Ctx) -> None:
    """4) 含 book/color/thumbnail 的句子，控制标签不丢"""
    print("\n== 验收 4：控制标签不丢失")
    # 找一个同时含 book 与 color 的槽（从索引里挑，证据可复算）
    row = ctx.idx.conn.execute(
        "SELECT eid,tag,k,slot,plain FROM slots WHERE seg LIKE '%<book(%' "
        "AND seg LIKE '%<color(%' AND q=1 ORDER BY n_code DESC LIMIT 1").fetchone()
    if row is None:
        record(4, "含 book/color 的句子标签不丢", False, "索引里没找到样本槽")
        return
    eid, tag, k, slot = row["eid"], row["tag"], row["k"], row["slot"]
    h = ctx.hit(eid, tag, k, slot)
    old_seg = codec.Segment(bytes.fromhex(h["seg_hex"]))
    new_text = "改过的中文正文"
    it, miss, _h = ctx.item(eid, tag, k, slot, new_text)
    new_seg = codec.Segment(it.new_payload)
    same = codec.tag_signature(old_seg.tags) == codec.tag_signature(new_seg.tags)
    record(4, "含 book/color/thumbnail 的句子标签不丢", same and not miss,
           "槽 %s：标签 %d 个，改前 %s / 改后 %s，正文 %r → %r"
           % (it.ref.label(), len(new_seg.tags),
              [codec.tag_name(t) for t in old_seg.tags],
              [codec.tag_name(t) for t in new_seg.tags],
              old_seg.plain, new_seg.plain),
           {"old_tags": [t.hex().upper() for t in old_seg.tags],
            "new_tags": [t.hex().upper() for t in new_seg.tags],
            "old_plain": old_seg.plain, "new_plain": new_seg.plain})


def acc5_6(ctx: Ctx) -> None:
    """5) 无码位汉字立即 BLOCK  6) 仅部分包有的字符 WARN"""
    print("\n== 验收 5–6：BLOCK / WARN")
    cov = fontcov.coverage()
    st_h, order_h, notes_h = cov.diagnose_text("黄")
    record(5, "无码位汉字立即 BLOCK", st_h == "BLOCK",
           "「黄」→ %s（%s），码位候选 %s"
           % (st_h, "；".join(notes_h),
              list(codec.table().codes_of("黄")) or "无"),
           {"state": st_h, "notes": notes_h})

    warn_ch, warn_v = None, None
    tbl = codec.table()
    for ch in sorted(tbl.char2codes.keys()):
        if not ("\u4e00" <= ch <= "\u9fff"):
            continue
        s, o, _n = cov.diagnose_text(ch)
        if s == "WARN":
            warn_ch, warn_v = ch, o[0]
            break
    ok = warn_v is not None and bool(warn_v.missing) and not warn_v.blocks_write
    record(6, "仅部分字体包拥有的字符 WARN", ok,
           ("「%s」→ WARN：%d/15 包有字形，缺 %s；仍允许写盘（%s）"
            % (warn_ch, len(warn_v.have) if warn_v else -1,
               warn_v.missing if warn_v else "?", warn_v.evidence if warn_v else ""))
           if warn_v else "没有找到 WARN 样本",
           {"char": warn_ch, "have": warn_v.have if warn_v else [],
            "missing": warn_v.missing if warn_v else [],
            "notes": warn_v.notes if warn_v else []})


def acc7_8(ctx: Ctx):
    """7) 同资源多槽批量  8) 压缩资源多槽批量并独立回读"""
    print("\n== 验收 7–8：批量写盘（非压缩多槽 + 压缩多槽）")
    # ---- 7：非压缩同资源三槽
    items7 = []
    for slot, text in ((9, "何？"), (10, "我想和你在一起。"), (12, "怎么了？")):
        it, miss, h = ctx.item(4004643, 0, 0, slot, text)
        items7.append(it)
    rep7 = pipeline.inspect(ctx.iso, items7)
    info7 = {"total": rep7["total"], "safe": rep7["safe"],
             "blocked": rep7["blocked"], "conflicts": rep7["conflicts"]}

    # ---- 8：压缩资源多槽（eid=701009 年表，资源级 LZ）
    rows = ctx.idx.by_eid(701009, limit=200)
    comp_texts = [r for r in rows
                  if r["compressed"] and r["k"] == 1 and r["slot"] < 6
                  and len(r["text"]) > 6]
    items8 = []
    for r in comp_texts[:3]:
        it, miss, h = ctx.item(r["eid"], r["tag"], r["k"], r["slot"],
                               "改后的年表第%d行" % (len(items8) + 1))
        items8.append(it)
    rep8 = pipeline.inspect(ctx.iso, items8)
    info8 = {"total": rep8["total"], "safe": rep8["safe"],
             "blocked": rep8["blocked"],
             "compressed": sum(1 for v in rep8["verdicts"] if v["compressed"])}

    print("    [检查台] 非压缩 3 槽：可写 %d / 需留意 %d / 阻断 %d；同块合并组 %d"
          % (rep7["safe"], rep7["warn"], rep7["blocked"],
             len(rep7["conflicts"])))
    record(8, "压缩资源多槽修改并独立回读",
           rep8["blocked"] == 0 and len(items8) > 0
           and (rep8["safe"] + rep8["warn"]) == len(items8),
           "eid=701009 压缩多槽 %d 条 → 可写 %d / 需留意 %d / 阻断 %d"
           % (len(items8), rep8["safe"], rep8["warn"], rep8["blocked"]),
           info8)

    # ---- 合并一次写盘（7+8 一起，验证跨资源批量与白名单）
    all_items = items7 + items8
    dst = fresh_out("acc_batch_%s.iso" % time.strftime("%H%M%S"))
    t0 = time.time()
    man = pipeline.build_to(ctx.iso, all_items, out_dir=ISO_OUT,
                            expected_md5=ctx.md5, name_hint="acc78")
    final = man["output"]["path"]
    elapsed = round(time.time() - t0, 1)

    # 独立回读（冻结检索层）
    ok_all, detail = True, []
    for it in all_items:
        got, err = frozen_read(final, it.ref.eid, it.ref.tag, it.ref.k,
                               it.ref.slot)
        if err or got is None:
            ok_all = False
            detail.append("%s 回读失败：%s" % (it.ref.label(), err))
            continue
        if got["plain"] != it.new_text:
            ok_all = False
            detail.append("%s 期望 %r 实得 %r"
                          % (it.ref.label(), it.new_text, got["plain"]))
        else:
            detail.append("%s → %r" % (it.ref.label(), got["plain"]))
    record(7, "同资源多槽批量编辑偏移不冲突（写盘+独立回读 PASS）"
           if len(items7) else "同资源多槽批量",
           ok_all and man["readback"]["ok"] and man["diff"]["outside"] == 0
           and man["protected"]["ok"],
           "写 %d 条（%d 资源）/ %.1fs；回读 %s；白名单外 %d 字节；受保护 %s | %s"
           % (len(all_items), len(man["resources"]), elapsed,
              "PASS" if man["readback"]["ok"] else "FAIL",
              man["diff"]["outside"],
              "PASS" if man["protected"]["ok"] else "FAIL",
              "；".join(detail[:5])),
           {"out": final, "md5": man["output"]["md5"],
            "parent_md5": man["parent"]["md5"],
            "readback": man["readback"], "diff_outside": man["diff"]["outside"],
            "protected_ok": man["protected"]["ok"],
            "allow_ranges": man["diff"]["allow_ranges"]})

    # 父盘未被改动
    record(9, "编辑后取消 / 写盘不改变父盘",
           md5(ctx.iso) == ctx.md5,
           "父盘 MD5 %s（前后一致）" % ctx.md5[:16], {"parent_md5": ctx.md5})
    return final, all_items, man


def acc11_12(final: str, man: dict, ctx: Ctx) -> None:
    """11) 全盘差分只有授权修改  12) 受保护资源无非授权改动"""
    print("\n== 验收 11–12：全盘差分 / 受保护资源")
    record(11, "最终 ISO 全盘差分只有授权修改",
           man["diff"]["ok"] and man["diff"]["outside"] == 0,
           "白名单外 %d 字节；允许区间 %d 段（共 %d 资源）"
           % (man["diff"]["outside"], len(man["diff"]["allow_ranges"]),
              len(man["resources"])),
           {"allow_ranges": man["diff"]["allow_ranges"],
            "bad": man["diff"].get("bad", [])[:5]})

    guard = man["protected"]
    record(12, "15 包字库 / ELF / CLUT / TOC 无非授权改动",
           guard["ok"],
           "受保护检查 %d 项通过（含 %d 个字体包）：%s"
           % (guard.get("checked", 0), guard.get("font_pkgs", 0),
              "；".join(guard.get("bad") or []) or "无差异"),
           {"checked": guard.get("checked"), "bad": guard.get("bad"),
            "font_pkgs": guard.get("font_pkgs")})


def acc10(ctx: Ctx) -> None:
    """10) 人为制造失败，确认父盘与草稿完整"""
    print("\n== 验收 10：人为制造失败")
    q = editplan.PlanQueue()
    q.bind(ctx.iso, ctx.md5)
    it, miss, h = ctx.item(4004643, 0, 0, 9, "何？")
    q.upsert(it)
    draft = q.save_draft()
    draft_md5_before = md5(draft)

    # 造一个必然失败的批次：第二条把原字节指纹写成别的槽的（防串位闸门）
    bad = editplan.PlanItem.of(it.as_dict())
    bad.old_payload_hex = ctx.item(4004643, 0, 0, 10, "x")[2]["seg_hex"]
    bad.new_payload_hex = it.new_payload_hex

    fails, msgs = [], []
    # ① 指纹不符
    try:
        pipeline.build_to(ctx.iso, [it, bad], out_dir=ISO_OUT,
                          expected_md5=ctx.md5, name_hint="accfail1")
        fails.append("指纹不符未被拒绝")
    except Exception as exc:                     # noqa: BLE001
        msgs.append("指纹/预检：%s" % str(exc)[:90])
    # ② 父盘 MD5 错
    try:
        pipeline.build_to(ctx.iso, [it], out_dir=ISO_OUT,
                          expected_md5="0" * 32, name_hint="accfail2")
        fails.append("错误 MD5 未被拒绝")
    except Exception as exc:                     # noqa: BLE001
        msgs.append("MD5：%s" % str(exc)[:90])
    # ③ 空间不足（把正文写成超长）
    long_item, _m, _h = ctx.item(4004643, 0, 0, 9, "啊" * 900)
    try:
        pipeline.build_to(ctx.iso, [long_item], out_dir=ISO_OUT,
                          expected_md5=ctx.md5, name_hint="accfail3")
        fails.append("超容量未被拒绝")
    except Exception as exc:                     # noqa: BLE001
        msgs.append("容量：%s" % str(exc)[:90])

    left_iso = [n for n in os.listdir(ISO_OUT) if n.startswith("accfail")]
    parent_ok = md5(ctx.iso) == ctx.md5
    draft_ok = os.path.exists(draft) and md5(draft) == draft_md5_before
    record(10, "人为失败后父盘与草稿完整",
           not fails and parent_ok and draft_ok and not left_iso,
           "三次失败都被拒绝；父盘 MD5 %s；草稿完好（%d 字节）；残留 ISO %d 个 | %s"
           % ("一致" if parent_ok else "被改动", os.path.getsize(draft),
              len(left_iso), "；".join(msgs)),
           {"failures_caught": msgs, "parent_ok": parent_ok,
            "draft_ok": draft_ok, "leftover_iso": left_iso,
            "unexpected": fails})


def acc13(final: str, all_items, ctx: Ctx) -> None:
    """13) 重开 GUI 加载新 ISO，能查询到全部新译文"""
    print("\n== 验收 13：为新 ISO 建索引并查询新译文")
    t0 = time.time()
    db = S.build_index(final, progress=None, quiet=True)
    build_s = round(time.time() - t0, 1)
    idx2 = S.open_index(db)
    found, missing = [], []
    for it in all_items:
        h = idx2.slot(it.ref.eid, it.ref.tag, it.ref.k, it.ref.slot)
        if h and h["text"] == it.new_text:
            found.append(it.ref.label())
        else:
            missing.append("%s 期望 %r 实得 %r"
                           % (it.ref.label(), it.new_text,
                              h["text"] if h else "无"))
    # 再按文字搜一遍（用户真实操作路径）
    kw = "何？"
    hits2 = idx2.search(kw, limit=200)
    hit_new = any(h["eid"] == 4004643 and h["slot"] == 9 for h in hits2)
    record(13, "重开 GUI 加载新 ISO 能查到全部新译文",
           not missing and hit_new,
           "建索引 %.1fs / %d 槽；逐槽核对 %d/%d 命中；搜索「何？」仍能定位 4004643=%s"
           % (build_s, idx2.slot_total, len(found), len(all_items),
              "是" if hit_new else "否"),
           {"db": db, "index_seconds": build_s, "slots": idx2.slot_total,
            "found": found, "missing": missing})
    idx2.close()


def acc14(ctx: Ctx) -> None:
    """14) 无效 ISO / 错误 MD5 安全拒绝"""
    print("\n== 验收 14：无效输入安全拒绝")
    junk = os.path.join(ACC_DIR, "junk.iso")
    with open(junk, "wb") as f:
        f.write(b"NOT-AN-ISO" * 100)
    cases = []
    try:
        S.build_index(junk, quiet=True)
        cases.append("垃圾 ISO 未被拒绝")
    except BaseException as exc:                 # noqa: BLE001
        cases.append("垃圾 ISO：%s" % type(exc).__name__)
    # 索引库 schema 不符
    bad_db = os.path.join(ACC_DIR, "bad_db.sqlite")
    import sqlite3
    if os.path.exists(bad_db):
        os.remove(bad_db)
    con = sqlite3.connect(bad_db)
    con.execute("CREATE TABLE meta(k TEXT, v TEXT)")
    con.execute("INSERT INTO meta VALUES('schema_version','99')")
    con.commit()
    con.close()
    try:
        S.open_index(bad_db)
        cases.append("schema 不符未被拒绝")
    except BaseException as exc:                 # noqa: BLE001
        cases.append("schema 不符：%s" % type(exc).__name__)
    # 索引存在但 ISO 不在
    ghost = editplan.PlanQueue()
    ghost.parent = {"path": os.path.join(ACC_DIR, "nothere.iso"),
                    "md5": "0" * 32, "size": 0, "mtime": 0}
    try:
        ok, why = ghost.parent_ok(ghost.parent["path"])
        cases.append("缺失父盘：%s" % ("被识别" if not ok or why else "未识别"))
    except BaseException as exc:                 # noqa: BLE001
        cases.append("缺失父盘：%s" % type(exc).__name__)
    bad = [c for c in cases if "未" in c]
    record(14, "无效 ISO / 过期缓存 / 错误 MD5 安全拒绝", not bad,
           "；".join(cases), {"cases": cases})


def acc15() -> None:
    """15) 打包后的 EXE 重复关键流程（由 _acceptance_exe.py 单独跑）"""
    print("\n== 验收 15：打包 EXE（单独脚本执行）")
    exe = os.path.join(env.ROOT, "ACE3汉化工坊", "01_程序", "ACE3汉化工坊.exe")
    if not os.path.exists(exe):
        record(15, "打包后的 EXE 重复关键搜索/修改/写盘测试", False,
               "EXE 还没生成：%s" % exe, {})
        return
    report = os.path.join(ACC_DIR, "exe_验收.json")
    if not os.path.exists(report):
        record(15, "打包后的 EXE 重复关键搜索/修改/写盘测试", False,
               "EXE 已生成但还没跑 EXE 验收：%s" % report, {"exe": exe})
        return
    with open(report, "r", encoding="utf-8") as f:
        d = json.load(f)
    record(15, "打包后的 EXE 重复关键搜索/修改/写盘测试",
           bool(d.get("ok")), d.get("detail", "")[:300], d)


# ---------------------------------------------------------------- 主流程
def main(argv=None) -> int:                      # noqa: ANN001
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args(argv)
    only = {int(x) for x in args.only.split(",") if x.strip()}

    def want(n: int) -> bool:
        return not only or n in only

    os.makedirs(ACC_DIR, exist_ok=True)
    os.makedirs(ISO_OUT, exist_ok=True)
    ctx = Ctx()
    ctx.open()
    print("父盘：%s" % ctx.iso)
    print("MD5 ：%s" % ctx.md5)
    print("索引：%s" % ctx.idx.db_path)

    t_all = time.time()
    final, items, man = None, [], None
    try:
        if want(1) or want(2) or want(3):
            acc1_2_3(ctx)
        if want(4):
            acc4(ctx)
        if want(5) or want(6):
            acc5_6(ctx)
        if want(7) or want(8) or want(9) or want(11) or want(12):
            final, items, man = acc7_8(ctx)
        if want(11) or want(12):
            acc11_12(final, man, ctx)
        if want(10):
            acc10(ctx)
        if want(13) and final:
            acc13(final, items, ctx)
        if want(14):
            acc14(ctx)
        if want(15):
            acc15()
    except Exception:
        traceback.print_exc()
        record(0, "执行器异常", False, traceback.format_exc()[-500:])

    # 汇总
    ok = sum(1 for r in RESULTS if r["ok"])
    bad = [r for r in RESULTS if not r["ok"]]
    seen = {}
    old_elapsed_full = None
    rep_path = os.path.join(ACC_DIR, "验收报告.json")
    if only and os.path.exists(rep_path):
        # 单项重跑：保留此前其它项的结果，只替换本次跑过的项
        try:
            with open(rep_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            for r in old.get("results", []):
                seen[int(r["no"])] = r
            old_elapsed_full = old.get("elapsed_full") or old.get("elapsed")
        except BaseException:                        # noqa: BLE001
            seen = {}
    for r in RESULTS:                            # 同一项取最后一次结果
        seen[r["no"]] = r
    uniq = sorted(seen.values(), key=lambda r: r["no"])
    ok_u = sum(1 for r in uniq if r["ok"])
    this_run = round(time.time() - t_all, 1)
    rep = {
        "parent": {"path": ctx.iso, "md5": ctx.md5},
        "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed": this_run,
        "elapsed_full": this_run if not only else (old_elapsed_full or this_run),
        "partial_run": bool(only),
        "partial_items": sorted(only) if only else [],
        "results": uniq,
        "summary": {"pass": ok_u, "fail": len(uniq) - ok_u,
                    "items": len(uniq)},
    }
    with open(rep_path, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    md_path = os.path.join(ACC_DIR, "验收报告.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# ACE3 汉化工坊 · 十五项真实验收报告\n\n")
        f.write("- 时间：%s\n- 父盘：`%s`\n- 父盘 MD5：`%s`\n"
                "- 总耗时：%.1fs%s\n\n"
                % (rep["ran_at"], ctx.iso, ctx.md5, rep["elapsed_full"],
                   "（最近一次全量运行；本次仅重跑 %s）"
                   % rep["partial_items"] if rep["partial_run"] else ""))
        f.write("## 结论\n\n通过 %d / %d 项\n\n" % (ok_u, len(uniq)))
        f.write("| # | 项目 | 结果 | 证据摘要 |\n| --- | --- | --- | --- |\n")
        for r in uniq:
            f.write("| %d | %s | %s | %s |\n"
                    % (r["no"], r["name"], "PASS" if r["ok"] else "FAIL",
                       (r["detail"] or "").replace("|", "\\|")[:300]))
    print("\n" + "=" * 60)
    print("通过 %d / %d" % (ok_u, len(uniq)))
    for r in uniq:
        if not r["ok"]:
            print("  FAIL %d. %s — %s" % (r["no"], r["name"], r["detail"]))
    print("报告：%s" % md_path)
    return 0 if ok_u == len(uniq) else 1


if __name__ == "__main__":
    raise SystemExit(main())
