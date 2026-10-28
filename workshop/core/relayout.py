"""VER 块内的最小位移重排（写盘引擎的布局核心）。

设计目标
--------
1. **能装就装**：新串比原跨度长时，把该槽之后的槽整体后推，用块内真实空闲补齐。
2. **最小位移**：不改动的槽尽量留在原位（``pos = max(cursor, 原位置)``），
   绝不因为前面某槽变短就把后面的槽往前拉。
3. **绝不越界**：可用上界由 ``usable_end`` 决定（末槽之后有外来数据时就是外来
   数据起点）；放不下就 ``RelayoutBlocked``，不覆盖、不截断。

与冻结工具的关系
----------------
冻结工具（``_r223_apply_plan``）的判据是「可用长度 = min(原版日文串长, 到下一槽距离)」，
即**只允许不变或变短**。本模块在此基础上多给一条经过结构验证的合法路径：

* 先把新串放进原跨度（等价于冻结判据里的 ``gap``）；
* 不够时，用本块的真实空闲把后续槽后推。

块内重排只改本块的 offs 表与槽数据区，**块大小、cnt、toff、块头其它字段、
外来尾段一律不动**，因此资源大小不变、BND 表不变、ISO9660 不变。
"""
from __future__ import annotations

import struct
from typing import Dict, List, Tuple

from . import slots as S


class RelayoutBlocked(RuntimeError):
    """本块内放不下（必须 BLOCK，不允许越界写）。"""


# ---------------------------------------------------------------- 基础口径
def payload_bounds(buf: bytes, blk: S.VerBlock, slot: int) -> Tuple[int, int]:
    """返回 ``(payload 起点, payload 终点)``，终点是第一个 NUL 或跨度末尾。"""
    p = blk.start + blk.used[slot]
    span = blk.span_in(buf, slot)
    z = buf.find(b"\x00", p, p + span)
    return p, (z if z >= 0 else p + span)


def usable_end(buf: bytes, blk: S.VerBlock) -> int:
    """本块可写到的最右位置（不含）。

    * 无外来尾段：块尾（``start + size``，且不越过缓冲）；
    * 有外来尾段：外来数据起点（末槽 NUL + 连续 0 之后）。
    """
    block_end = min(blk.start + blk.size, len(buf))
    if blk.has_foreign_tail(buf):
        return min(blk.payload_end + blk.tail_free(buf), block_end)
    return block_end


def free_tail(buf: bytes, blk: S.VerBlock) -> int:
    """块尾连续 0 字节数（只反映尾段，不代表总可扩写量）。"""
    block_end = min(blk.start + blk.size, len(buf))
    if blk.has_foreign_tail(buf):
        return max(0, block_end - usable_end(buf, blk))
    return max(0, block_end - blk.payload_end)


def block_table_end(blk: S.VerBlock) -> int:
    """offsets 表在缓冲里的结束位置（数据区不得早于此）。"""
    return blk.start + blk.toff + 4 * blk.cnt


# ---------------------------------------------------------------- 推演
def simulate(buf: bytes, blk: S.VerBlock,
             new_payloads: Dict[int, bytes],
             compact: bool = False) -> dict:
    """推演重排结果，不写任何字节。

    ``compact=False``（默认，最小位移）：槽``pos = max(cursor, 原位置)``，
    前面变短不会把后面的槽往前拉，改动面最小。
    ``compact=True``（紧凑重排）：一律``pos = cursor``，把槽间填充空隙也回收。
    只有最小位移放不下时才用，且在块内闭环，不影响块外。

    返回 ``{offs, cursor, moved, limit, slack, fits, mode}``。
    """
    n = blk.slot_count()
    if n == 0:
        raise RelayoutBlocked("块内没有逻辑槽")
    for slot in new_payloads:
        if not (0 <= slot < n):
            raise RelayoutBlocked("逻辑槽越界 slot=%d（本块 %d 槽）" % (slot, n))

    limit = usable_end(buf, blk)
    data_start = blk.start + blk.used[0]
    if data_start < block_table_end(blk):
        raise RelayoutBlocked(
            "槽数据区与 offsets 表重叠（数据区 %d < 表尾 %d）"
            % (data_start, block_table_end(blk)))

    offs: List[int] = []
    moved = 0
    cursor = data_start
    for slot in range(n):
        p_old, e_old = payload_bounds(buf, blk, slot)
        pl = new_payloads.get(slot)
        if pl is None:
            pl = bytes(buf[p_old:e_old])
        elif b"\x00" in pl:
            raise RelayoutBlocked("slot=%d 新串含 NUL 字节" % slot)
        pos = cursor if compact else (p_old if p_old >= cursor else cursor)
        if pos != p_old:
            moved += 1
        offs.append(pos)
        cursor = pos + len(pl) + 1
    return {
        "offs": offs,
        "cursor": cursor,
        "moved": moved,
        "limit": limit,
        "slack": max(0, limit - cursor),
        "fits": cursor <= limit,
        "mode": "compact" if compact else "minimal",
    }


def block_slack(buf: bytes, blk: S.VerBlock) -> int:
    """本块**总可插入字节数**（不移动任何现有槽位置时的容量余量）。

    口径：``usable_end − (数据区起点 + Σ(payload 长度 + 1))``。
    也就是「各槽紧密排布后还能剩多少」，是决定一次改写的理论上限。
    单槽的即时余量还要看它后面各槽的原位置，用 ``simulate`` 推演才准。
    """
    n = blk.slot_count()
    if n == 0:
        return 0
    limit = usable_end(buf, blk)
    data_start = blk.start + blk.used[0]
    used = 0
    for slot in range(n):
        p, e = payload_bounds(buf, blk, slot)
        used += (e - p) + 1
    return max(0, limit - data_start - used)


# ---------------------------------------------------------------- 重排写入
def relayout(buf: bytes, blk: S.VerBlock,
             new_payloads: Dict[int, bytes]) -> Tuple[bytes, dict]:
    """在块内就地重排，返回 ``(新缓冲, 报告)``。

    ``new_payloads`` 只给**需要改写**的逻辑槽；其余槽原样保留。
    先试最小位移；放不下再试紧凑重排；都放不下才 ``RelayoutBlocked``。
    """
    if not new_payloads:
        return buf, {"mode": "noop", "moved": 0,
                     "slack": block_slack(buf, blk)}

    sim = simulate(buf, blk, new_payloads, compact=False)
    if not sim["fits"]:
        sim2 = simulate(buf, blk, new_payloads, compact=True)
        if not sim2["fits"]:
            raise RelayoutBlocked(
                "块内空间不足：紧凑重排也需要到 %d（块内偏移 %d），"
                "可用到 %d（块内偏移 %d），差 %d 字节"
                % (sim2["cursor"], sim2["cursor"] - blk.start,
                   sim2["limit"], sim2["limit"] - blk.start,
                   sim2["cursor"] - sim2["limit"]))
        sim = sim2

    n = blk.slot_count()
    limit = sim["limit"]
    out = bytearray(buf)
    for slot in range(n):
        p_old, e_old = payload_bounds(buf, blk, slot)
        pl = new_payloads.get(slot)
        edited = pl is not None
        if not edited:
            pl = bytes(buf[p_old:e_old])
        pos = sim["offs"][slot]
        if not edited and pos == p_old:
            continue
        end = pos + len(pl)
        if end >= len(out):
            raise RelayoutBlocked("slot=%d 写越界（到 %d，缓冲 %d）"
                                  % (slot, end, len(out)))
        out[pos:end] = pl
        out[end] = 0
        if edited:
            # 只清「本槽旧跨度」内、新串之后的残留，绝不越过下一槽起点
            span_end = blk.start + blk.used[slot] + blk.span_in(buf, slot)
            clear_to = min(span_end, limit, len(out))
            for q in range(end + 1, clear_to):
                if out[q]:
                    out[q] = 0

    # 回写 offs 表：保持 cnt / toff / 0 项不变，别名项自动跟随
    value_map = {blk.used[i]: sim["offs"][i] - blk.start for i in range(n)}
    raw = [value_map.get(v, v) if v else 0 for v in blk.raw_offs]
    struct.pack_into("<%dI" % blk.cnt, out, blk.start + blk.toff, *raw)

    rep = {
        "mode": ("inplace" if sim["moved"] == 0
                 else ("shift" if sim["mode"] == "minimal" else "compact")),
        "moved": sim["moved"],
        "offs": [v - blk.start for v in (blk.start + u for u in blk.used)],
        "new_offs": [o - blk.start for o in sim["offs"]],
        "span": sim["cursor"] - blk.start,
        "slack_before": block_slack(buf, blk),
        "slack_after": sim["slack"],
        "limit": limit - blk.start,
    }
    return bytes(out), rep


# ---------------------------------------------------------------- 独立校验
def verify_relayout(buf: bytes, new_buf: bytes, blk: S.VerBlock,
                    new_offs: List[int],
                    edited: Dict[int, bytes]) -> List[str]:
    """独立校验重排结果（只看字节，不信任 relayout 的返回值）。

    检查：① 目标槽等于期望 ② 非目标槽逐字节不变 ③ 块前块后字节不变
    ④ 新缓冲能被 ``parse_blocks`` 重新识别为同一块且槽数一致。
    """
    errs: List[str] = []
    fake = S.VerBlock(blk.start, blk.size, blk.cnt, blk.toff,
                      list(new_offs), blk.payload_end, blk.note)
    for slot in range(blk.slot_count()):
        got = S.slot_payload(new_buf, fake, slot)
        want = edited.get(slot)
        if want is None:
            want = S.slot_payload(buf, blk, slot)
        if got != want:
            errs.append("slot=%d 内容不符：期望 %r，实际 %r"
                        % (slot, want[:40], got[:40]))
    if new_buf[:blk.start] != buf[:blk.start]:
        errs.append("块前字节被改")
    tail = blk.start + blk.size
    if new_buf[tail:] != buf[tail:]:
        errs.append("块后字节被改")
    again = [b for b in S.parse_blocks(new_buf) if b.start == blk.start]
    if not again:
        errs.append("重排后块无法重新识别")
    elif again[0].slot_count() != blk.slot_count():
        errs.append("槽数变化 %d -> %d"
                    % (blk.slot_count(), again[0].slot_count()))
    return errs
