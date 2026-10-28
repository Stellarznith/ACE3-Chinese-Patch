# -*- coding: utf-8 -*-
"""名字块写盘器 —— 把 CLM/FNM 编辑计划变成可写盘结果。

## 为什么单独一层

``core.writer`` 的 ``Edit`` 身份是 ``(eid, tag, k, slot)``，走的是 VER 块槽体系；
名字块不在 VER 块里（实测 VER 块只覆盖资源尾部一小段），所以**不能**塞进 ``Edit``。

但 ``core.writer`` 真正跟盘打交道的那三件事是**资源级**的，天然通用：

* ``writer.write(src, builds, dst)`` —— 复制父盘 + 按 ``BuildResult`` 区间替换 + fsync
* ``writer.verify(...)``             —— 独立回读
* ``writer.diff_whitelist(...)``     —— 整盘白名单差分

所以本模块**只负责构建**（把编辑计划变成 ``writer.BuildResult``），
写盘/回读/差分一律调 ``writer`` 原有实现，``writer.py`` 一个字节都不用改。

## 与槽编辑的差别（必须在 GUI 里说清）

| | VER 槽编辑 | 名字块编辑 |
|---|---|---|
| 身份 | eid + tag + k + slot | eid + 块位置 + 块内下标 |
| 容量 | 到下一个槽的距离 | 块内串间/块尾 padding |
| 增长 | 块内重排（``relayout``） | 块内重排（本模块） |
| 压缩 | 成员可能压缩 | 实测四个目标资源全部非压缩 |

## FNM 不是「另一套体系」，它就是 VER 块（实测）

``VER.start == FNM.base``、``VER.size == FNM.dsize``、``VER.cnt == 1``、
``VER.toff == 0x28`` —— 抽查 8 个资源 14 个名字块，**8 个 FNM 全部是 VER 块**，
6 个 CLM 全部不在 VER 体系里。所以：

* **FNM（机体名）走 ``writer.Edit`` 槽路径**，不需要本模块。
  实测 501050 的 FNM 从 8 字节改到 14 字节（``冥墓方舟`` → ``冥墓方舟改二型``），
  ``mode=inplace``、独立回读 PASS、白名单外 0 字节。
* **CLM（指令/武器名表）只能走本模块**，``relayout`` 看不到它。

两条链改的是同一段字节的两种视图，所以同一资源同时有两种编辑时
**必须用 ``fold_into_slot_builds()`` 合成一次构建**；分两次写盘会让后写的
那份把前一份整个盖掉（实测已证）。
"""
from __future__ import annotations

import dataclasses
from typing import Dict, List, Optional, Sequence, Tuple

from . import codec, env, nametext as N, slots
from . import writer as W


class NameWriteError(Exception):
    """名字块写盘前的拒绝（容量 / 码位 / 字库 / 一致性）。"""


@dataclasses.dataclass
class NameEdit:
    """一条名字编辑计划。

    ``old_raw`` 是**盘上当前字节**，写盘前会逐字节复核；对不上就拒绝，
    防止拿过期编辑计划往新盘上写。
    """

    eid: int
    block_pos: int          # 块在资源内的偏移
    index: int              # 块内第几条
    old_raw: bytes
    new_raw: bytes
    mark: str = ""          # "CLM" / "FNM"，只用于展示
    note: str = ""

    def key(self) -> Tuple[str, int, int, int]:
        return ("name", self.eid, self.block_pos, self.index)

    def label_text(self) -> str:
        """人话位置（检查台/报告里用，与 ``PlanItem.label()`` 同一口径）。"""
        return "EID %d · %s 块 @0x%X · 第 %d 条" % (
            self.eid, self.mark or "名字", self.block_pos, self.index)

    def as_dict(self) -> dict:
        return {"kind": "name", "eid": self.eid, "block_pos": self.block_pos,
                "index": self.index, "mark": self.mark,
                "old_hex": self.old_raw.hex(), "new_hex": self.new_raw.hex(),
                "note": self.note}


@dataclasses.dataclass
class NameVerdict:
    """一条名字编辑的预演结论。"""

    edit: NameEdit
    ok: bool
    mode: str = ""              # inplace / reflow / block
    reason: str = ""            # 不通过时的原因
    capacity: int = 0           # 原串字节数
    need: int = 0               # 新串字节数
    slack: int = 0              # 该块可吸收字节
    missing: List[str] = dataclasses.field(default_factory=list)
    font_state: str = ""        # PASS / WARN / BLOCK
    font_detail: str = ""
    plain_old: str = ""
    plain_new: str = ""

    def as_dict(self) -> dict:
        d = self.edit.as_dict()
        d.update({"ok": self.ok, "mode": self.mode, "reason": self.reason,
                  "capacity": self.capacity, "need": self.need,
                  "slack": self.slack, "missing": list(self.missing),
                  "font_state": self.font_state, "font_detail": self.font_detail,
                  "plain_old": self.plain_old, "plain_new": self.plain_new})
        return d


# ---------------------------------------------------------------- 预演
def _font_state(text: str, raw: bytes = b"") -> Tuple[str, str, List[str]]:
    """按 15 包字库判 PASS / WARN / BLOCK，返回 ``(状态, 说明, 缺码字)``。

    ``fontcov.diagnose_text`` 返回 ``(总状态, 逐字结论, 提示)`` 三元组。

    ★ 先查 ``raw`` 里有没有码表不认识的码位：那种字节写进去必然显示成
      乱字或空位，必须在编码这一层就 BLOCK，不能等字库检查（字库缓存里
      可能恰好有那个码位的字形，但码表没登记，引擎照样解不出来）。
    """
    from . import fontcov
    if raw:
        _plain, _codes, unknown, _ctrl = codec.decode_bytes(raw)
        if unknown:
            bad = "、".join("0x%04X" % c for c in unknown)
            return ("BLOCK",
                    "新文本里有游戏码表未登记的码位：%s。写进去会显示成乱字或空位。"
                    % bad, [])
    res = codec.encode_text(text)
    if res.missing:
        return ("BLOCK",
                "这些字在游戏码表里没有合法码位，写进去会变成乱字或空位：%s"
                % "、".join(res.missing), list(res.missing))
    try:
        state, per_char, notes = fontcov.coverage().diagnose_text(text)
    except Exception as exc:                     # 字库缓存读不到就保守判 WARN
        return ("WARN", "字库缓存不可用（%s），无法确认字形覆盖" % exc, [])
    if state == "PASS":
        return ("PASS", "全部字体包都有字形（实机由哪个包渲染仍是 UNKNOWN）", [])
    detail = "；".join(notes) if notes else "部分字体包缺字"
    return (state or "WARN", detail, [])


def check(iso: str, edits: Sequence[NameEdit],
          toc: Optional[Dict[int, Tuple[int, int]]] = None) -> List[NameVerdict]:
    """逐条预演，不写任何字节。"""
    if toc is None:
        toc = slots.cached_toc(iso)
    out: List[NameVerdict] = []
    # 每个块只读一次
    cache: Dict[Tuple[int, int], Optional[N.NameBlock]] = {}

    for e in edits:
        v = NameVerdict(edit=e, ok=False, capacity=len(e.old_raw),
                        need=len(e.new_raw))
        plain_old, _c, _u, _t = codec.decode_bytes(e.old_raw)
        plain_new, _c2, _u2, _t2 = codec.decode_bytes(e.new_raw)
        v.plain_old, v.plain_new = plain_old, plain_new

        key = (e.eid, e.block_pos)
        if key not in cache:
            blk = None
            for b in N.blocks_of(iso, e.eid, toc):
                if b.pos == e.block_pos:
                    blk = b
                    break
            cache[key] = blk
        blk = cache[key]
        if blk is None:
            v.reason = ("资源 eid=%d 里 0x%X 处没有可解析的名字块（盘可能已变，"
                        "请重新搜索）" % (e.eid, e.block_pos))
            out.append(v)
            continue
        if not (0 <= e.index < blk.count):
            v.reason = "块内下标 %d 越界（该块只有 %d 条）" % (e.index, blk.count)
            out.append(v)
            continue
        if blk.items[e.index] != e.old_raw:
            v.reason = ("盘上这条已经是别的字节了（计划里 %r，盘上 %r）。"
                        "多半是父盘被换过，请重新搜索再改。"
                        % (e.old_raw.hex(), blk.items[e.index].hex()))
            out.append(v)
            continue

        v.slack = blk.slack()
        if b"\x00" in e.new_raw:
            v.reason = "新串里不能含 0x00（引擎会当成终止符）"
            out.append(v)
            continue

        state, detail, missing = _font_state(plain_new, e.new_raw)
        v.font_state, v.font_detail, v.missing = state, detail, missing
        if state == "BLOCK":
            v.reason = detail
            out.append(v)
            continue

        if len(e.new_raw) <= len(e.old_raw):
            v.ok = True
            v.mode = "inplace"
            v.reason = ("等长原位覆盖" if len(e.new_raw) == len(e.old_raw)
                        else "更短，原位覆盖 + 0x00 补满 %d 字节"
                             % (len(e.old_raw) - len(e.new_raw)))
            out.append(v)
            continue

        grow = len(e.new_raw) - len(e.old_raw)
        # 重排后整块要装得下：新串区总长 = 旧串区总长 + grow
        room = blk.dsize - (blk.toff + 4 * blk.count)
        used = blk.used - (blk.toff + 4 * blk.count)
        if used + grow > room:
            v.reason = ("块内装不下：要长 %d 字节，这块只剩 %d 字节余量"
                        "（块内重排上限）。请缩短译文——本工具不推动块外数据。"
                        % (grow, room - used))
            out.append(v)
            continue
        v.ok = True
        v.mode = "reflow"
        v.reason = ("长 %d 字节，整块重排（吃掉串间/块尾 padding，块外零位移），"
                    "重排后块内还剩 %d 字节" % (grow, room - used - grow))
        out.append(v)
    return out


# ---------------------------------------------------------------- 构建
def build(iso: str, edits: Sequence[NameEdit],
          toc: Optional[Dict[int, Tuple[int, int]]] = None,
          prior: Optional[Dict[int, bytes]] = None
          ) -> Dict[int, W.BuildResult]:
    """把名字编辑构建成 ``writer.BuildResult``（每个受影响 eid 一条）。

    同一资源内多条/多块的编辑**合并成一次构建**：块内重排只动块内区间，
    各块区间互不重叠，所以按块分组后原地替换即可，不存在「先改一条让后面
    偏移失效」的问题。

    ``prior`` = ``{eid: 解压后的资源新字节}``，用于**叠加在槽编辑结果之上**。
    同一资源里既有普通槽编辑、又有 CLM 名字编辑时，先跑 ``writer.build``
    拿到该资源的新字节，再传进来继续改名字块；否则两条链各写一份，
    后写的那份会把前一份的改动整个盖掉。实测 FNM 块本身就是 VER 块
    （``ver_k`` 不为 None），这类编辑由调用方转成 ``writer.Edit`` 走槽路径，
    **不要**混进这里，免得同一段字节被两套引擎各改一次。
    """
    if toc is None:
        toc = slots.cached_toc(iso)
    # 同一 (eid, block_pos, index) 只保留最后一条（后者覆盖前者）
    seen: Dict[Tuple[str, int, int, int], NameEdit] = {}
    for e in edits:
        seen[e.key()] = e
    live = list(seen.values())

    by_eid: Dict[int, List[NameEdit]] = {}
    for e in live:
        by_eid.setdefault(e.eid, []).append(e)

    results: Dict[int, W.BuildResult] = {}
    for eid, group in by_eid.items():
        if eid not in toc:
            raise NameWriteError("TOC 中不存在 eid=%d" % eid)
        res = slots.load_resource(iso, eid, toc)
        if res.bnd and len(res.members) != 1:
            raise NameWriteError(
                "eid=%d 是含 %d 个成员的 BND 资源，名字块写盘暂不支持这种布局"
                "（实测目标资源都是单成员非压缩）" % (eid, len(res.members)))
        mem = list(res.members.values())[0]
        if res.compressed and prior and eid in prior:
            raise NameWriteError(
                "eid=%d 是压缩资源，不能把名字编辑叠加在槽编辑结果上"
                "（槽编辑结果已是压缩流，无法再按偏移改名字块）" % eid)
        data = prior[eid] if prior and eid in prior else mem.data
        notes: List[str] = []
        if prior and eid in prior:
            notes.append("叠加在槽编辑结果之上（长度 %d）" % len(data))

        # 按块分组（块内重排，区间不重叠，顺序无关）
        by_block: Dict[int, List[NameEdit]] = {}
        for e in group:
            by_block.setdefault(e.block_pos, []).append(e)

        for pos in sorted(by_block):
            blk = None
            for b in N.scan(data, eid):
                if b.pos == pos:
                    blk = b
                    break
            if blk is None:
                raise NameWriteError("eid=%d 0x%X 处没有名字块" % (eid, pos))
            changes: Dict[int, bytes] = {}
            for e in by_block[pos]:
                if blk.items[e.index] != e.old_raw:
                    raise NameWriteError(
                        "eid=%d 块 0x%X 第 %d 条：盘上字节与计划不符"
                        "（计划 %r，盘上 %r）"
                        % (eid, pos, e.index, e.old_raw.hex(),
                           blk.items[e.index].hex()))
                changes[e.index] = e.new_raw
            data, rep = N.apply_changes(data, blk, changes)
            notes.append("名字块 %s@0x%X 改 %d 条 mode=%s"
                         % (blk.kind, pos, len(changes), rep["mode"]))
            if rep["mode"] == "reflow":
                notes.append("  reflow grown=%d slack_after=%d offsets_changed=%d"
                             % (rep["grown"], rep["slack_after"],
                                rep["offsets_changed"]))

        if len(data) != len(mem.data):
            raise NameWriteError(
                "eid=%d 构建后长度变了 %d -> %d（块内重排不应改变资源长度）"
                % (eid, len(mem.data), len(data)))

        if res.compressed:
            blob = W.compress_fit(data, res.size)
            if blob is None:
                raise NameWriteError(
                    "eid=%d 是压缩资源，重压后超出原 size %d" % (eid, res.size))
            new_raw = blob
            notes.append("资源级重压")
        else:
            new_raw = data

        if len(new_raw) > res.size:
            raise NameWriteError("eid=%d 构建结果 %d > 原 size %d"
                                 % (eid, len(new_raw), res.size))
        # 叠加场景下 old_raw 仍取盘上原字节：差分白名单只认区间，
        # 而 verify 走的是独立的槽/名字回读，不依赖 old_raw。
        results[eid] = W.BuildResult(
            eid=eid, new_raw=new_raw, old_raw=res.raw,
            res_off=res.off, res_size=res.size,
            compressed=res.compressed, edits=[], notes=notes)
    return results


# ---------------------------------------------------------------- 独立回读
def verify(dst: str, edits: Sequence[NameEdit],
           toc_src: Optional[Dict[int, Tuple[int, int]]] = None) -> dict:
    """**独立**回读：重新打开 dst，重新解析名字块，逐条比对。

    与 ``writer.verify`` 同一纪律：不调用 ``build`` 的任何内存结果，只看盘面。
    名字块的回读不能借用 ``writer.verify``——它按 VER 槽体系取 payload，
    而 CLM 块根本不在 VER 块里（实测），走错路径会读到相邻的别的字节。

    返回 ``{"ok": bool, "items": [...], "iso": dst}``。
    """
    toc = slots.load_toc(dst)
    if toc_src is not None:
        if set(toc) != set(toc_src):
            return {"ok": False, "error": "TOC eid 集合变化", "items": []}
        for eid, val in toc_src.items():
            if toc.get(eid) != val:
                return {"ok": False, "error": "TOC 条目变化 eid=%d" % eid,
                        "items": []}

    seen: Dict[Tuple[str, int, int, int], NameEdit] = {}
    for e in edits:
        seen[e.key()] = e
    live = list(seen.values())

    cache: Dict[int, List[N.NameBlock]] = {}
    items: List[dict] = []
    ok = True
    for e in live:
        rec = {"kind": "name", "eid": e.eid, "block_pos": e.block_pos,
               "index": e.index, "mark": e.mark, "ok": False, "error": ""}
        try:
            if e.eid not in cache:
                cache[e.eid] = N.blocks_of(dst, e.eid, toc)
            blk = None
            for b in cache[e.eid]:
                if b.pos == e.block_pos:
                    blk = b
                    break
            if blk is None:
                rec["error"] = "回读无该名字块"
                ok = False
                items.append(rec)
                continue
            if not (0 <= e.index < len(blk.items)):
                rec["error"] = "回读下标越界（该块 %d 条）" % len(blk.items)
                ok = False
                items.append(rec)
                continue
            got = blk.items[e.index]
            rec["got_hex"] = got.hex()
            rec["got_plain"] = blk.plain(e.index)
            if got == e.new_raw:
                rec["ok"] = True
            else:
                rec["error"] = "回读不符：%r" % (got[:60],)
                ok = False
        except Exception as exc:                     # noqa: BLE001
            rec["error"] = "%s: %s" % (type(exc).__name__, exc)
            ok = False
        items.append(rec)
    return {"ok": ok, "items": items, "iso": dst}


# ---------------------------------------------------------------- 叠加
def fold_into_slot_builds(iso: str, slot_builds: Dict[int, W.BuildResult],
                          name_edits: Sequence[NameEdit],
                          toc: Optional[Dict[int, Tuple[int, int]]] = None
                          ) -> Dict[int, W.BuildResult]:
    """把名字编辑叠加到已有的槽编辑构建结果上，返回合并后的构建表。

    这是「同一资源既有槽编辑、又有 CLM 名字编辑」的唯一正确做法：
    以槽编辑的结果为底，再在它上面改名字块，最后只产出一条 ``BuildResult``。
    分两次写盘会让后写的那份把前一份的改动整个盖掉（同一个资源区间）。

    没有名字编辑的资源原样返回，槽编辑结果一个字节都不动。
    """
    if not name_edits:
        return slot_builds
    if toc is None:
        toc = slots.cached_toc(iso)
    prior: Dict[int, bytes] = {}
    for eid, br in slot_builds.items():
        if br.compressed:
            continue                    # 压缩资源不允许叠加，由 build() 拒绝
        prior[eid] = br.new_raw
    name_builds = build(iso, name_edits, toc=toc, prior=prior)
    out = dict(slot_builds)
    for eid, nb in name_builds.items():
        if eid in out:
            nb.notes = ["叠加在槽编辑之上"] + nb.notes
        out[eid] = nb
    return out


def to_slot_edit(edit: NameEdit, blk: "N.NameBlock",
                 label: str = "") -> W.Edit:
    """把 **VER 背书的名字编辑**（FNM）转成普通槽编辑。

    FNM 块本身就是一个 VER 块（``blk.ver_k`` 不为 None），走 ``writer.Edit``
    即可复用已验证的 ``relayout`` 引擎；``blk.ver_k`` 为 None（CLM）时拒绝，
    那种只能走本模块的块内重排。
    """
    if blk.ver_k is None:
        raise NameWriteError(
            "%s 块不是 VER 块（CLM 类），不能转成槽编辑，请走名字块写盘路径"
            % blk.kind)
    return W.Edit(eid=edit.eid, tag=blk.mem_tag, k=blk.ver_k, slot=edit.index,
                  new_payload=edit.new_raw, old_payload=edit.old_raw,
                  label=label or ("EID %d · %s 第 %d 条"
                                  % (edit.eid, blk.kind, edit.index)))


def split_by_engine(iso: str, edits: Sequence[NameEdit],
                    toc: Optional[Dict[int, Tuple[int, int]]] = None
                    ) -> Tuple[List[W.Edit], List[NameEdit], List[str]]:
    """按「谁能安全写」把名字编辑分流成两组。

    返回 ``(槽编辑列表, 名字块编辑列表, 分流说明)``：

    * FNM（VER 块）→ 转成 ``writer.Edit``，走已验证的槽引擎
    * CLM（非 VER）→ 留在名字块引擎

    这样调用方不需要自己判断块的类型，也不会把同一段字节交给两套引擎。
    """
    if toc is None:
        toc = slots.cached_toc(iso)
    slot_edits: List[W.Edit] = []
    name_edits: List[NameEdit] = []
    notes: List[str] = []
    cache: Dict[Tuple[int, int], Optional[N.NameBlock]] = {}
    for e in edits:
        k = (e.eid, e.block_pos)
        if k not in cache:
            cache[k] = None
            for b in N.blocks_of(iso, e.eid, toc):
                if b.pos == e.block_pos:
                    cache[k] = b
                    break
        blk = cache[k]
        if blk is None:
            name_edits.append(e)        # 让 check/build 去报「块不存在」
            continue
        if blk.ver_k is not None:
            slot_edits.append(to_slot_edit(e, blk))
            notes.append("EID %d %s 第 %d 条 → 槽编辑 tag%d/k%d/slot%d"
                         % (e.eid, blk.kind, e.index, blk.mem_tag,
                            blk.ver_k, e.index))
        else:
            name_edits.append(e)
            notes.append("EID %d %s 第 %d 条 → 名字块引擎"
                         % (e.eid, blk.kind, e.index))
    return slot_edits, name_edits, notes


# ---------------------------------------------------------------- 汇总
def summarize(verdicts: Sequence[NameVerdict]) -> dict:
    """给检查台用的汇总。"""
    ok = [v for v in verdicts if v.ok]
    bad = [v for v in verdicts if not v.ok]
    warn = [v for v in ok if v.font_state == "WARN"]
    return {
        "total": len(verdicts),
        "writable": len(ok),
        "blocked": len(bad),
        "warn": len(warn),
        "inplace": sum(1 for v in ok if v.mode == "inplace"),
        "reflow": sum(1 for v in ok if v.mode == "reflow"),
        "blocked_items": [{"eid": v.edit.eid, "block_pos": v.edit.block_pos,
                           "index": v.edit.index, "reason": v.reason}
                          for v in bad],
        "warn_items": [{"eid": v.edit.eid, "block_pos": v.edit.block_pos,
                        "index": v.edit.index, "reason": v.font_detail}
                       for v in warn],
    }
