"""实时估算：给编辑器用，输入即判（不写盘、不建索引）。

与 ``writer.check`` 的区别
--------------------------
``writer.check`` 是写盘前的正式闸门（逐条全量校验，含独立回读准备）。
本模块只回答「现在这一条能不能装下、会怎么动」，走内存里缓存的
``Resource`` / ``VerBlock``，键盘输入时也能毫秒级返回。

判据与写盘引擎**完全一致**（同一个 ``relayout.simulate``），
所以这里说「装得下」，正式写盘时也一定装得下；反之亦然。
"""
from __future__ import annotations

import threading
from typing import Dict, Optional, Tuple

from . import env, relayout, slots as S

_RES_CACHE: Dict[Tuple[str, int], S.Resource] = {}
_CACHE_LOCK = threading.Lock()
_MAX_CACHE = 8


class Estimate:
    """一次容量估算的结果。"""

    __slots__ = ("ok", "state", "reason", "capacity", "compressed",
                 "mode", "moved", "slack_after", "block_slack", "limit",
                 "slot_count", "detail")

    def __init__(self) -> None:
        self.ok = False
        self.state = "BLOCK"
        self.reason = ""
        self.capacity = 0
        self.compressed = False
        self.mode = ""
        self.moved = 0
        self.slack_after = 0
        self.block_slack = 0
        self.limit = 0
        self.slot_count = 0
        self.detail: dict = {}

    def as_dict(self) -> dict:
        return {"ok": self.ok, "state": self.state, "reason": self.reason,
                "capacity": self.capacity, "compressed": self.compressed,
                "mode": self.mode, "moved": self.moved,
                "slack_after": self.slack_after,
                "block_slack": self.block_slack, "limit": self.limit,
                "slot_count": self.slot_count}


def cached_resource(iso: str, eid: int) -> S.Resource:
    """带 LRU 的资源缓存（GUI 反复点同一个资源时不重复解压）。"""
    key = (str(iso).lower(), int(eid))
    with _CACHE_LOCK:
        res = _RES_CACHE.get(key)
        if res is not None:
            return res
    res = S.load_resource(iso, eid)
    with _CACHE_LOCK:
        if len(_RES_CACHE) >= _MAX_CACHE:
            _RES_CACHE.pop(next(iter(_RES_CACHE)))
        _RES_CACHE[key] = res
    return res


def invalidate(iso: Optional[str] = None) -> None:
    with _CACHE_LOCK:
        if iso is None:
            _RES_CACHE.clear()
            return
        low = str(iso).lower()
        for k in [k for k in _RES_CACHE if k[0] == low]:
            _RES_CACHE.pop(k, None)


def estimate(iso: str, eid: int, tag: int, k: int, slot: int,
             new_payload: bytes) -> Estimate:
    """估算把某个槽改成 ``new_payload`` 的可行性与容量。"""
    est = Estimate()
    try:
        res = cached_resource(iso, eid)
    except Exception as exc:                        # noqa: BLE001
        est.state = "BLOCK"
        est.reason = "资源读取失败：%s" % exc
        return est
    mem = res.member(tag)
    if mem is None:
        est.state = "BLOCK"
        est.reason = "资源里没有 tag=%d 成员" % tag
        return est
    est.compressed = bool(mem.compressed or res.compressed)
    est.capacity = mem.capacity
    blk = mem.block(k)
    if blk is None:
        est.state = "BLOCK"
        est.reason = "成员里没有 k=%d 块" % k
        return est
    est.slot_count = blk.slot_count()
    if not (0 <= slot < est.slot_count):
        est.state = "BLOCK"
        est.reason = "逻辑槽越界 slot=%d（本块 %d 槽）" % (slot, est.slot_count)
        return est
    if b"\x00" in new_payload:
        est.state = "BLOCK"
        est.reason = "新文本编码后含 NUL 字节"
        return est
    try:
        est.block_slack = relayout.block_slack(mem.data, blk)
        sim = relayout.simulate(mem.data, blk, {slot: new_payload},
                                compact=False)
        if not sim["fits"]:
            sim2 = relayout.simulate(mem.data, blk, {slot: new_payload},
                                     compact=True)
            if not sim2["fits"]:
                est.state = "BLOCK"
                est.reason = ("块内空间不足：紧凑重排也需要到 %d，可用到 %d，"
                              "差 %d 字节"
                              % (sim2["cursor"], sim2["limit"],
                                 sim2["cursor"] - sim2["limit"]))
                est.limit = sim2["limit"] - blk.start
                return est
            sim = sim2
    except relayout.RelayoutBlocked as exc:
        est.state = "BLOCK"
        est.reason = str(exc)
        return est
    est.ok = True
    est.state = "PASS"
    est.mode = ("inplace" if sim["moved"] == 0 else
                ("shift" if sim["mode"] == "minimal" else "compact"))
    est.moved = sim["moved"]
    est.slack_after = sim["slack"]
    est.limit = sim["limit"] - blk.start
    est.detail = {"new_len": len(new_payload),
                  "old_len": len(S.slot_payload(mem.data, blk, slot)),
                  "block_start": blk.start, "block_size": blk.size,
                  "table_end": blk.toff + 4 * blk.cnt}
    if est.compressed:
        est.reason = "压缩成员：将整条解压→改字→整条重压"
    elif est.mode == "inplace":
        est.reason = "原位写入（后续槽不动）"
    elif est.mode == "shift":
        est.reason = "需后移 %d 个槽（块内偏移重算）" % est.moved
    else:
        est.reason = "需紧凑重排（回收槽间空隙 %d 字节）" % est.moved
    return est


def free_bytes(iso: str, eid: int, tag: int, k: int) -> int:
    """本块在不移动任何槽的前提下还能塞多少字节（给 UI 显示容量条）。"""
    try:
        res = cached_resource(iso, eid)
    except Exception:                               # noqa: BLE001
        return 0
    mem = res.member(tag)
    if mem is None:
        return 0
    blk = mem.block(k)
    if blk is None:
        return 0
    return relayout.block_slack(mem.data, blk)
