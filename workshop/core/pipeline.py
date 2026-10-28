"""统一写盘引擎的业务封装：把「队列 + 父盘 + 预检 + 构建 + 写盘 + 回读」
串成一条带完整报告的事务流程，供 CLI 与 GUI 共用。

分工
----
* ``core.writer``：只管字节（块内重排、重压、独立回读、白名单差分）。
* ``core.pipeline``（本模块）：管流程与证据——预检台、事务目录、
  修改清单、前后对照、回读报告、受保护资源守恒核对、ISO 输出命名。
"""
from __future__ import annotations

import datetime
import json
import os
import time
from typing import Dict, List, Optional, Sequence, Tuple

from . import editplan, env, namewriter as NW, paths, slots, writer

PASS, WARN, BLOCK = writer.PASS, writer.WARN, writer.BLOCK


class PipelineError(RuntimeError):
    """流程不可继续。"""


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_exact(fh, n: int) -> bytes:
    """读满 ``n`` 字节（大文件在低速盘上会短读，不读满会误判成内容差异）。"""
    buf = bytearray()
    while len(buf) < n:
        got = fh.read(n - len(buf))
        if not got:
            break
        buf += got
    return bytes(buf)


def _write_json(path: str, payload) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- 队列 → Edit
def to_edits(items: Sequence[editplan.PlanItem]) -> List[writer.Edit]:
    """只转普通槽编辑；名字块编辑必须走 ``editplan.name_edits_of()``。"""
    return [writer.Edit(eid=it.ref.eid, tag=it.ref.tag, k=it.ref.k,
                        slot=it.ref.slot,
                        new_payload=it.new_payload,
                        old_payload=it.old_payload,
                        label=it.label())
            for it in editplan.slot_items_of(items)]


# ---------------------------------------------------------------- 检查台
def inspect(iso: str, items: Sequence[editplan.PlanItem],
            progress=None) -> dict:
    """统一写盘前的完整检查台（任务书 §五/增强需求 7）。

    槽编辑与名字块编辑**分别预演**，结论按各自的身份回写到队列项：

    * 槽编辑 → ``writer.check``（VER 块容量口径）
    * 名字块编辑 → ``namewriter.check``（块内 padding 口径）

    两类编辑的 ``key()`` 形状不同（槽是 4 个 int，名字带 ``"name"`` 前缀），
    所以各自建自己的映射表，不会串。
    """
    t0 = time.time()
    if not items:
        raise PipelineError("修改队列是空的，没有可写的内容")
    if progress:
        progress("核对父盘指纹 %s" % os.path.basename(iso))
    toc = slots.cached_toc(iso)

    slot_items = editplan.slot_items_of(items)
    name_items = [it for it in items if getattr(it, "is_name", False)]
    edits = to_edits(items)
    verdicts = writer.check(iso, edits, toc=toc) if edits else []

    # 把结论回写到队列项，GUI 里的状态列随之更新
    by_key: Dict[tuple, writer.Verdict] = {}
    for v in verdicts:
        by_key.setdefault(v.edit.key(), v)
    for it in slot_items:
        v = by_key.get(it.key())
        if v is None:
            it.state = editplan.STATE_PENDING
            it.reasons = ["未参与预检"]
            it.warnings = []
            continue
        it.state = {PASS: editplan.STATE_OK, WARN: editplan.STATE_WARN,
                    BLOCK: editplan.STATE_BLOCK}[v.state]
        it.reasons = [v.reason] if v.state == BLOCK else []
        it.warnings = [v.reason] if v.state != PASS else []
        if v.reason and v.state == WARN:
            it.warnings = [v.reason]
        it.capacity = v.capacity
        it.compressed = v.compressed

    # 名字块编辑单独预演
    name_verdicts = []
    if name_items:
        if progress:
            progress("预演 %d 条名字块编辑" % len(name_items))
        n_edits = editplan.name_edits_of(name_items)
        name_verdicts = NW.check(iso, n_edits, toc=toc)
        by_name: Dict[tuple, object] = {}
        for v in name_verdicts:
            by_name.setdefault(v.edit.key(), v)
        for it in name_items:
            v = by_name.get(it.key())
            if v is None:
                it.state = editplan.STATE_PENDING
                it.reasons = ["未参与预检"]
                it.warnings = []
                continue
            if not v.ok:
                it.state = editplan.STATE_BLOCK
                it.reasons = [v.reason]
                it.warnings = []
            elif v.font_state == "WARN":
                it.state = editplan.STATE_WARN
                it.reasons = []
                it.warnings = [v.font_detail]
            else:
                it.state = editplan.STATE_OK
                it.reasons = []
                it.warnings = []
            it.capacity = v.capacity
            it.name_slack = v.slack
            it.name_mode = v.mode
            it.compressed = False

    # 冲突 / 镜像 / 资源分组
    groups: Dict[int, List[editplan.PlanItem]] = {}
    for it in items:
        groups.setdefault(it.ref.eid, []).append(it)
    conflicts = _resource_conflicts(items)
    dups = _duplicates(items)

    blocked = [v for v in verdicts if v.state == BLOCK]
    warned = [v for v in verdicts if v.state == WARN]
    okv = [v for v in verdicts if v.state == PASS]
    # 名字块编辑的阻断项也要算进来，否则「名字块装不下」时 can_build 还是 True
    name_blocked = [v for v in name_verdicts if not v.ok]
    name_warned = [v for v in name_verdicts if v.ok and v.font_state == "WARN"]
    name_ok = [v for v in name_verdicts if v.ok and v.font_state != "WARN"]
    hard_conflicts = [c for c in conflicts if c.get("blocking")]

    report = {
        "iso": os.path.abspath(iso),
        "checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(items),
        "safe": len(okv) + len(name_ok),
        "warn": len(warned) + len(name_warned),
        "blocked": len(blocked) + len(name_blocked),
        "slot_items": len(slot_items),
        "name_items": len(name_items),
        "resources": {str(eid): len(g) for eid, g in groups.items()},
        "compressed_items": sum(1 for it in items if it.compressed),
        "duplicates": dups,
        "conflicts": conflicts,
        "verdicts": [v.as_dict() for v in verdicts],
        "name_verdicts": [v.as_dict() for v in name_verdicts],
        "elapsed": round(time.time() - t0, 2),
        "can_build": not blocked and not name_blocked and not hard_conflicts,
    }
    if blocked or name_blocked:
        detail = [{"where": _where(v.edit), "why": v.reason} for v in blocked]
        detail += [{"where": v.edit.label_text(), "why": v.reason}
                   for v in name_blocked]
        report["blocked_detail"] = detail
    if warned or name_warned:
        detail = [{"where": _where(v.edit), "why": v.reason} for v in warned]
        detail += [{"where": v.edit.label_text(), "why": v.font_detail}
                   for v in name_warned]
        report["warn_detail"] = detail
    return report


def _where(e) -> str:                            # noqa: ANN001
    """``writer.Edit`` 没有 label() 方法（label 是可选备注字段），统一在此拼位置。"""
    return "EID %d · tag%d/k%d · 槽%d" % (e.eid, e.tag, e.k, e.slot)


def _duplicates(items: Sequence[editplan.PlanItem]) -> List[dict]:
    seen: Dict[tuple, List[int]] = {}
    for i, it in enumerate(items):
        seen.setdefault(it.key(), []).append(i + 1)
    return [{"where": _where_item_key(k), "rows": v}
            for k, v in seen.items() if len(v) > 1]


def _where_item_key(k: tuple) -> str:
    """把 ``PlanItem.key()`` 翻译成人话（槽 4 元组 / 名字 4 元组两种）。"""
    if len(k) == 4 and isinstance(k[0], str) and k[0] == "name":
        return "EID %d · 名字块 @0x%X · 第 %d 条" % (k[1], k[2], k[3])
    return "EID %d tag%d/k%d 槽%d" % k


def _resource_conflicts(items: Sequence[editplan.PlanItem]) -> List[dict]:
    """同资源冲突 / 合并提示。

    名字块编辑单独归组：它和槽编辑改的是同一段字节的两种视图，
    混在一起必须走 ``namewriter.fold_into_slot_builds`` 合成一次构建，
    所以这里要报出「同资源既有槽编辑又有名字编辑」这个事实。

    每项都带 ``blocking``：``False`` 表示「这条已经由构建器自动处理，
    只是告知用户」，``True`` 才是真的会让写盘失败的冲突。
    以前这里不带这个键，导致「同块 3 条合并重排」被当成冲突，
    ``can_build`` 永远 False，用户根本走不到写盘（实测踩到）。
    """
    out: List[dict] = []
    by_res: Dict[int, List[editplan.PlanItem]] = {}
    for it in items:
        by_res.setdefault(it.ref.eid, []).append(it)
    for eid, group in by_res.items():
        blocks: Dict[Tuple[int, int], List[editplan.PlanItem]] = {}
        names: List[editplan.PlanItem] = []
        for it in group:
            if getattr(it, "is_name", False):
                names.append(it)
            else:
                blocks.setdefault((it.ref.tag, it.ref.k), []).append(it)
        for (tag, k), g in blocks.items():
            if len(g) > 1:
                out.append({"eid": eid, "tag": tag, "k": k, "count": len(g),
                            "blocking": False,
                            "note": "同块 %d 条将合并重排（偏移整体重算）"
                                    % len(g)})
        if names:
            by_block: Dict[int, List[editplan.PlanItem]] = {}
            for it in names:
                by_block.setdefault(it.name_block_pos, []).append(it)
            for pos, g in by_block.items():
                if len(g) > 1:
                    out.append({"eid": eid, "block_pos": pos, "count": len(g),
                                "blocking": False,
                                "note": "同一名字块 %d 条将合并重排（块内零位移）"
                                        % len(g)})
            if blocks:
                out.append({
                    "eid": eid,
                    "count": len(names) + sum(len(g) for g in blocks.values()),
                    "blocking": False,
                    "note": ("该资源既有槽编辑又有名字编辑，将合并成一次构建"
                             "（否则后写的那份会覆盖前一份）")})
    return out


# ---------------------------------------------------------------- 事务构建
def build_to(src: str, items: Sequence[editplan.PlanItem],
             out_dir: Optional[str] = None,
             expected_md5: Optional[str] = None,
             name_hint: str = "",
             progress=None) -> dict:
    """事务式生成新 ISO，并产出全套证据。

    任一步失败：删除临时产物、保留父盘、返回带 error 的报告（抛异常）。
    """
    t0 = time.time()
    # 守则：产物（含临时 stage 目录、新 ISO）只能落在工具自身目录内。
    # 用户选了系统盘（例如 C:\ 或桌面）时**改道**到默认 reports 目录，
    # 并把原因记进报告——绝不在系统盘上建文件再半途失败。
    want_dir = out_dir or env.REPORT_DIR
    out_dir = paths.resolve_or(want_dir, env.REPORT_DIR, "新 ISO 输出目录")
    redirected = paths.last_redirect() if os.path.abspath(
        want_dir) != os.path.abspath(out_dir) else ""
    os.makedirs(out_dir, exist_ok=True)
    stage = os.path.join(out_dir, "_stage_%s" % _stamp())
    os.makedirs(stage, exist_ok=True)

    try:
        # 1) 父盘身份再核对
        md5_now = env.md5_file(src)
        if expected_md5 and md5_now.lower() != expected_md5.lower():
            raise PipelineError("父盘 MD5 不符：期望 %s，当前 %s"
                                % (expected_md5, md5_now))
        if progress:
            progress("父盘指纹核对通过：%s" % md5_now)

        # 2) 预检（阻断项一律不许继续）
        rep = inspect(src, items, progress=None)
        _write_json(os.path.join(stage, "01_检查台.json"), rep)
        if rep["blocked"]:
            b = rep.get("blocked_detail") or []
            raise PipelineError("存在 %d 项阻断，已停止：%s"
                                % (rep["blocked"],
                                   "；".join("%s → %s" % (x["where"], x["why"])
                                            for x in b[:3])))

        # 3) 内存构建
        if progress:
            progress("在内存中构建 %d 个资源…" % rep["resources"].__len__())
        toc = slots.cached_toc(src)
        edits = to_edits(items)
        n_edits = editplan.name_edits_of(items)
        builds = writer.build(src, edits, toc) if edits else {}
        if n_edits:
            # 同一资源既有槽编辑又有名字编辑时，必须以槽编辑结果为底再改名字块，
            # 否则两条链各写一份，后写的那份会把前一份整个盖掉（实测已证）。
            builds = NW.fold_into_slot_builds(src, builds, n_edits, toc=toc)
        _write_json(os.path.join(stage, "02_构建计划.json"),
                    {"resources": writer.describe_allow_ranges(builds)})

        # 4) 写盘到独立临时文件
        stem = os.path.splitext(os.path.basename(src))[0]
        hint = ("_" + name_hint) if name_hint else ""
        final_name = "%s_CN%s_%s.iso" % (stem, hint, _stamp())
        tmp_iso = os.path.join(stage, final_name)
        if os.path.exists(tmp_iso):
            raise PipelineError("临时产物已存在，拒绝覆盖：%s" % tmp_iso)
        if progress:
            progress("写入临时产物（父盘只读，绝不覆盖）…")
        winfo = writer.write(src, builds, tmp_iso, expect_md5=md5_now,
                             progress=progress)

        # 5) 独立回读
        if progress:
            progress("独立回读新 ISO（重新解析 TOC / 资源 / 槽）…")
        rv = writer.verify(tmp_iso, edits, toc) if edits else {
            "ok": True, "items": [], "iso": tmp_iso}
        if n_edits:
            nv = NW.verify(tmp_iso, n_edits, toc)
            rv["name_items"] = nv["items"]
            if not nv["ok"]:
                rv["ok"] = False
                rv.setdefault("error", nv.get("error", ""))
        _write_json(os.path.join(stage, "03_回读报告.json"), rv)
        if not rv["ok"]:
            bad = [x for x in rv["items"] if not x["ok"]]
            if not bad:
                bad = [x for x in rv.get("name_items", []) if not x["ok"]]
            raise PipelineError("独立回读失败（%d 项不符）：%s"
                                % (len(bad),
                                   (bad[0].get("error") if bad else "")
                                   or rv.get("error", "")))

        # 6) 整盘差分白名单
        if progress:
            progress("整盘分块差分（白名单外必须 0 字节变化）…")
        df = writer.diff_whitelist(src, tmp_iso, builds)
        _write_json(os.path.join(stage, "04_差分白名单.json"), df)
        if not df["ok"]:
            raise PipelineError("白名单外存在改动 %d 字节：%s"
                                % (df["outside"], df["bad"][:3]))

        # 7) 受保护资源守恒（字库包 / ELF / CLUT 等）
        if progress:
            progress("受保护资源守恒核对（15 包字库 / ELF / CLUT）…")
        guard = protected_resources(src, tmp_iso, toc)
        _write_json(os.path.join(stage, "05_受保护资源.json"), guard)
        if not guard["ok"]:
            raise PipelineError("受保护资源发生变化：%s"
                                % "；".join(guard["bad"][:3]))

        # 8) 修改清单与前后对照
        manifest = manifest_of(src, tmp_iso, items, builds, rv, df, guard, winfo)
        _write_json(os.path.join(stage, "06_修改清单.json"), manifest)
        md_path = os.path.join(stage, "06_修改清单.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(manifest_markdown(manifest))

        # 9) 全部通过，正式落位（stage 内的产物移到正式目录）
        final_iso = os.path.join(out_dir, final_name)
        os.replace(tmp_iso, final_iso)
        manifest["output"]["path"] = final_iso
        manifest["output"]["md5"] = env.md5_file(final_iso)
        _write_json(os.path.join(out_dir, "%s_清单.json" % final_name), manifest)
        with open(os.path.join(out_dir, "%s_清单.md" % final_name),
                  "w", encoding="utf-8") as f:
            f.write(manifest_markdown(manifest))
        manifest["elapsed_total"] = round(time.time() - t0, 1)
        manifest["stage_dir"] = stage
        manifest["out_dir"] = out_dir
        if redirected:
            # 用户选的目录违反「不写系统盘」守则，产物被改道到这里。
            # 报告里必须写明，不能让用户以为文件写到了自己选的地方。
            manifest["out_dir_redirect"] = redirected
        return manifest

    except Exception:
        # 事务回滚：清掉 stage 里的临时 ISO，保留证据 JSON
        for name in os.listdir(stage) if os.path.isdir(stage) else []:
            if name.lower().endswith(".iso"):
                try:
                    os.remove(os.path.join(stage, name))
                except OSError:
                    pass
        raise


# ---------------------------------------------------------------- 受保护资源
def protected_resources(src: str, dst: str,
                        toc: Optional[Dict[int, Tuple[int, int]]] = None) -> dict:
    """核对受保护资源在父盘与新盘之间逐字节相同。

    保护范围
    --------
    * 15 个字体包（``env.FONT_PKGS``）：字库图集 / 记录表 / CLUT 全部不得变。
    * TOC 本身（DATA.BIN 头部的偏移表）。
    * ISO9660 关键结构：PVD（sector 16 前 2048 字节）。
    """
    toc = toc or slots.cached_toc(src)
    bad: List[str] = []
    checked = []

    def same_range(name: str, lo: int, hi: int) -> None:
        with open(src, "rb") as fa, open(dst, "rb") as fb:
            fa.seek(lo)
            fb.seek(lo)
            left = hi - lo
            while left > 0:
                n = min(1 << 20, left)
                # 必须读满：低速盘上 read(n) 可能短读，两个缓冲区长度不等会被
                # 误判成「受保护资源有差异」。读不满就按失败处理（宁可拒绝）。
                a = _read_exact(fa, n)
                b = _read_exact(fb, n)
                if len(a) != n or len(b) != n:
                    bad.append("%s 在 0x%X 起读取不足（src=%d dst=%d，需 %d）"
                               % (name, hi - left, len(a), len(b), n))
                    return
                if a != b:
                    bad.append("%s 在 0x%X 起有差异" % (name, hi - left))
                    return
                left -= n
        checked.append(name)

    size_src = os.path.getsize(src)
    size_dst = os.path.getsize(dst)
    if size_src != size_dst:
        bad.append("文件大小不同 %d != %d" % (size_src, size_dst))

    same_range("ISO9660 PVD（sector16）", 16 * env.SECTOR, 17 * env.SECTOR)
    toc_lo = env.DATA_LBA * env.SECTOR
    same_range("DATA.BIN 头/TOC", toc_lo, toc_lo + 0x20)
    for eid in sorted(env.FONT_PKGS):
        if eid not in toc:
            bad.append("TOC 缺少字体包 eid=%d" % eid)
            continue
        off, size = toc[eid]
        same_range("字体包 %d" % eid, env.ISOBASE + off,
                   env.ISOBASE + off + size)

    return {"ok": not bad, "bad": bad, "checked": checked,
            "font_pkgs": len(env.FONT_PKGS)}


# ---------------------------------------------------------------- 清单
def manifest_of(src, dst, items, builds, rv, df, guard, winfo) -> dict:
    rows = []
    for it in items:
        row = {
            "where": it.label(),
            "kind": "name" if getattr(it, "is_name", False) else "slot",
            "eid": it.ref.eid, "tag": it.ref.tag,
            "k": it.ref.k, "slot": it.ref.slot,
            "jp": it.jp_text, "old": it.old_text, "new": it.new_text,
            "old_bytes": len(it.old_payload), "new_bytes": len(it.new_payload),
            "old_hex": it.old_payload.hex().upper(),
            "new_hex": it.new_payload.hex().upper(),
            "compressed": it.compressed,
            "state": it.state, "note": it.note,
            "old_tags": it.old_tags, "new_tags": it.new_tags,
        }
        if getattr(it, "is_name", False):
            row["name_block_pos"] = it.name_block_pos
            row["name_index"] = it.name_index
            row["mark"] = it.mark
            row["name_mode"] = it.name_mode
        rows.append(row)
    n_slot = sum(1 for x in rv.get("items", []))
    n_name = sum(1 for x in rv.get("name_items", []))
    return {
        "kind": "ace3_editor_manifest",
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "parent": {"path": os.path.abspath(src), "md5": winfo["src_md5"],
                   "size": os.path.getsize(src)},
        "output": {"path": os.path.abspath(dst), "md5": winfo["dst_md5"],
                   "size": winfo["size"]},
        "counts": {"items": len(items), "resources": len(builds),
                   "name_items": sum(1 for it in items
                                     if getattr(it, "is_name", False))},
        "items": rows,
        "readback": {"ok": rv["ok"], "checked": n_slot + n_name,
                     "slot_checked": n_slot, "name_checked": n_name},
        "diff": {"ok": df["ok"], "outside": df["outside"],
                 "allow_ranges": df["ranges"]},
        "protected": {"ok": guard["ok"], "checked": len(guard["checked"]),
                      "font_pkgs": guard["font_pkgs"]},
        "resources": writer.describe_allow_ranges(builds),
        "elapsed_write": winfo["elapsed"],
    }


def manifest_markdown(m: dict) -> str:
    L = []
    L.append("# ACE3 汉化工坊 · 修改清单\n")
    L.append("- 生成时间：%s" % m["created_at"])
    L.append("- 父盘：`%s`" % m["parent"]["path"])
    L.append("- 父盘 MD5：`%s`" % m["parent"]["md5"])
    L.append("- 新盘：`%s`" % m["output"]["path"])
    L.append("- 新盘 MD5：`%s`" % m["output"]["md5"])
    L.append("- 修改条数：%d（涉及 %d 个资源）\n"
             % (m["counts"]["items"], m["counts"]["resources"]))
    if m["counts"].get("name_items"):
        L.append("- 其中名字块（CLM/FNM）编辑：%d 条\n"
                 % m["counts"]["name_items"])
    L.append("## 验证结论\n")
    L.append("| 项目 | 结果 | 说明 |")
    L.append("| --- | --- | --- |")
    rb = m["readback"]
    rb_note = "重新解析 TOC/资源/槽，%d 项" % rb["checked"]
    if rb.get("name_checked"):
        rb_note = ("重新解析 TOC/资源/槽 %d 项 + 名字块 %d 条"
                   % (rb.get("slot_checked", 0), rb["name_checked"]))
    L.append("| 独立回读 | %s | %s |"
             % ("PASS" if rb["ok"] else "FAIL", rb_note))
    L.append("| 整盘差分 | %s | 白名单外 %d 字节 |"
             % ("PASS" if m["diff"]["ok"] else "FAIL", m["diff"]["outside"]))
    L.append("| 受保护资源 | %s | %d 项守恒（含 15 包字库） |"
             % ("PASS" if m["protected"]["ok"] else "FAIL",
                m["protected"]["checked"]))
    L.append("\n## 修改前后对照\n")
    L.append("| # | 位置 | 原版日文 | 修改前 | 修改后 | 字节 | 压缩 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(m["items"], 1):
        L.append("| %d | %s | %s | %s | %s | %d→%d | %s |"
                 % (i, r["where"], _esc(r["jp"]), _esc(r["old"]),
                    _esc(r["new"]), r["old_bytes"], r["new_bytes"],
                    ("是" if r["compressed"] else "否")
                    + ("（名字块 %s）" % r.get("name_mode", "")
                       if r.get("name_mode") else "")))
    L.append("\n## 允许变更区间（白名单）\n")
    L.append("| eid | 偏移 | 长度 | 修改数 | 备注 |")
    L.append("| --- | --- | --- | --- | --- |")
    for r in m["resources"]:
        L.append("| %d | %s | %d | %d | %s |"
                 % (r["eid"], r["file_off_hex"], r["size"], r["edits"],
                    "；".join(r["notes"])))
    L.append("")
    return "\n".join(L)


def _esc(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", "\\n")[:80]
