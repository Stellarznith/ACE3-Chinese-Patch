"""资源装载与 VER 块槽位布局（只读解析 + 就地改写定位）。

核心口径（全部来自实测，与冻结 Text Locator 完全一致）：

* TOC：``databin_toc(iso)`` 给出 ``{eid: (off, size)}``，文件偏移 = ``ISOBASE + off``。
* 资源解压：``_r213_lz.decompress_any``（12 字节头：magic + 大端解压长度 + 4 保留）。
* 资源内若以 ``BND\\0`` 开头则是成员容器：``+0x04`` 声明大小、``+0x08`` 成员数、
  ``+0x10`` 起 ``count × (u32 tag, u32 offset)``；成员各自可能再被 LZ 包。
* 成员/资源解压后可能含一个或多个 VER 块（magic ``00 00 01 00``）：
  ``+0x00`` magic、``+0x04`` 块长度、``+0x10`` cnt、``+0x14`` offs 表偏移。
* **逻辑槽号 slot ≠ 偏移表原始下标 raw_idx**：
  ``offs`` 表有 cnt 项、大量为 0；工具把「非零项排序去重」后的序号叫 **slot**，
  原始表下标叫 **raw_idx**。索引库、命令输出的 slot 全部是前者。
* 一个槽的可写跨度 span = 下一非零 offs − 本槽 offs；末槽 span = 块尾 − 本槽 offs。
  就地改写只允许 ``len(new) + 1（终止 NUL） <= span``，其余字节原样保留。
  跨度不够就 BLOCK：绝不覆盖下一槽、绝不截断、绝不整段清空空隙。
"""
from __future__ import annotations

import struct
from typing import Dict, List, Optional, Tuple

from . import env

env.ensure_sys_path()

from _r213_lz import decompress_any          # noqa: E402
from _r104_audit2 import databin_toc         # noqa: E402


class LayoutError(RuntimeError):
    """布局无法可信解析（写盘必须拒绝）。"""


class VerBlock:
    """一个 VER 块在缓冲内的布局。"""

    __slots__ = ("start", "size", "cnt", "toff", "raw_offs", "used", "payload_end", "note")

    def __init__(self, start: int, size: int, cnt: int, toff: int,
                 raw_offs: List[int], payload_end: int, note: str = "") -> None:
        self.start = start
        self.size = size
        self.cnt = cnt
        self.toff = toff
        self.raw_offs = raw_offs                       # 原始表（长 cnt，含 0）
        self.used = sorted({v for v in raw_offs if v})  # 逻辑槽起点（升序去重）
        self.payload_end = payload_end                 # 最后一个槽的 NUL 之后
        self.note = note

    # -- 槽位（逻辑口径） ------------------------------------------
    def slot_count(self) -> int:
        return len(self.used)

    def slots(self) -> List[int]:
        return list(range(len(self.used)))

    def offset_of(self, slot: int) -> int:
        return self.used[slot]

    def raw_index(self, slot: int) -> int:
        """逻辑槽号 -> 偏移表原始下标。"""
        return self.raw_offs.index(self.used[slot])

    def next_offset(self, slot: int) -> int:
        """下一槽起点（相对块起点）；末槽返回缓冲长度占位值。"""
        if slot + 1 < len(self.used):
            return self.used[slot + 1]
        return self.size

    def span(self, slot: int) -> int:
        """该槽起点到下一槽起点的字节数（含自身 NUL 与填充），相对块起点。"""
        if slot + 1 < len(self.used):
            return self.used[slot + 1] - self.used[slot]
        return self.size - self.used[slot]

    def span_in(self, buf: bytes, slot: int) -> int:
        """``span`` 的缓冲口径版本：末槽上界取缓冲末尾（与 seg_of 一致）。"""
        p = self.start + self.used[slot]
        if slot + 1 < len(self.used):
            end = self.start + self.used[slot + 1]
        else:
            end = len(buf)
        return max(0, end - p)

    def data_start(self) -> int:
        return self.used[0] if self.used else self.toff + 4 * self.cnt

    # -- 尾部空间 --------------------------------------------------
    def tail_free(self, buf: bytes) -> int:
        """末槽之后到块尾的连续 0 字节数。"""
        p = self.payload_end
        limit = self.start + self.size
        n = 0
        while p < limit and buf[p] == 0:
            n += 1
            p += 1
        return n

    def tail_nonzero(self, buf: bytes) -> int:
        return (self.start + self.size - self.payload_end) - self.tail_free(buf)

    def has_foreign_tail(self, buf: bytes) -> bool:
        """块尾在末槽 NUL 之后还有非零字节 ⇒ 有外来数据，禁止整块重排。"""
        return self.tail_nonzero(buf) > 0


def parse_blocks(buf: bytes, limit: int = 400) -> List[VerBlock]:
    """在缓冲里枚举 VER 块。

    判据**逐条照抄**冻结工具 ``_r223_apply_plan.ver_blocks`` +
    ``struct_unpack_i`` + ``struct_unpack_offs``，目的是让块序号 ``k`` 与
    索引库 ``slots.k`` 严格对齐（索引库就是这套判据产出的）：

    1. 找 ``00 00 01 00``；剩余不足 0x18 字节则跳过；
    2. ``cnt = u32@+0x10``、``toff = u32@+0x14``；要求 ``0 < cnt <= 4096``、
       ``toff >= 0x18``、``i + toff + 4*cnt <= len(buf)``；
    3. ``offs`` 表取值（**相对块起点**），去 0、排序去重；必须非空，
       且 ``max(offs) < len(buf) - i``。

    ★ 不要额外加「``min(offs) >= toff + 4*cnt``」之类更严的条件：
    冻结判据没有它，加了会导致块序号错位、进而索引库 ``k`` 对不上。
    """
    out: List[VerBlock] = []
    start = 0
    n = len(buf)
    while len(out) < limit:
        i = buf.find(env.VER_MAGIC, start)
        if i < 0:
            break
        start = i + 1
        if n - i < 0x18:
            continue
        cnt = struct.unpack_from("<I", buf, i + 0x10)[0]
        toff = struct.unpack_from("<I", buf, i + 0x14)[0]
        if cnt <= 0 or cnt > 4096 or toff < 0x18 or i + toff + 4 * cnt > n:
            continue
        raw_offs = list(struct.unpack_from("<%dI" % cnt, buf, i + toff))
        used = {v for v in raw_offs if v}
        if not used:
            continue
        if max(used) >= n - i:
            continue

        # 块长字段：只用来自我描述，越界/明显不合理时退化到「到缓冲末尾」。
        declared = struct.unpack_from("<I", buf, i + 4)[0]
        note = ""
        if declared <= 0 or i + declared > n or declared <= max(used):
            size = n - i
            note = "块长字段不可信，按到缓冲末尾处理"
        else:
            size = declared

        last = max(used)
        z = buf.find(b"\x00", i + last)
        if z < 0 or z >= i + size:
            z = i + size - 1
        out.append(VerBlock(i, size, cnt, toff, list(raw_offs), z + 1, note))
    return out


def slot_payload(buf: bytes, blk: VerBlock, slot: int) -> bytes:
    """槽的原始字节（到第一个 NUL 为止）。

    ★ 取值口径**逐字照抄** ``locator_core.seg_of``：先按「下一槽起点」定上界
    ``end = start + next_offset(slot)``（末槽用块尾），再在该区间里截到第一个 NUL。
    不能直接全局找 NUL：二进制里的假 VER 块偏移项之间没有 NUL，两种写法会给出
    不同结果，导致与索引库逐槽字节对不上。
    """
    p = blk.start + blk.used[slot]
    end = p + blk.span_in(buf, slot)
    if end > len(buf):
        end = len(buf)
    z = buf.find(b"\x00", p, end)
    return buf[p:(z if z >= 0 else end)]


class Member:
    """资源内的一个可写单元：要么整个资源（tag=-1），要么 BND 的一个成员。"""

    __slots__ = ("eid", "tag", "data", "compressed", "file_off", "blob_off",
                 "capacity", "blocks")

    def __init__(self, eid: int, tag: int, data: bytes, compressed: bool,
                 file_off: int, capacity: int, blob_off: int) -> None:
        self.eid = eid
        self.tag = tag
        self.data = data          # 解压后（或原样）成员缓冲
        self.compressed = compressed
        self.file_off = file_off  # 成员 blob 在 ISO 内的起点（含 LZ 头）
        self.blob_off = blob_off  # 同 file_off，保留别名便于阅读
        self.capacity = capacity  # 成员在盘上的分配字节数
        self.blocks = parse_blocks(data)

    def key(self) -> Tuple[int, int]:
        return (self.eid, self.tag)

    def block(self, k: int) -> VerBlock:
        if not (0 <= k < len(self.blocks)):
            raise LayoutError("成员 tag=%d 只有 %d 个 VER 块，请求 k=%d"
                              % (self.tag, len(self.blocks), k))
        return self.blocks[k]


class Resource:
    """一个 TOC 条目。"""

    __slots__ = ("eid", "off", "size", "raw", "compressed", "data", "bnd",
                 "members", "member_order")

    def __init__(self, eid: int, off: int, size: int, raw: bytes) -> None:
        self.eid = eid
        self.off = off
        self.size = size
        self.raw = raw
        dec = decompress_any(raw)[0]
        self.compressed = dec is not raw
        self.data = dec
        self.bnd = dec[:4] == env.BND_MAGIC
        self.members: Dict[int, Member] = {}
        self.member_order: List[int] = []
        self._split_members()

    def _split_members(self) -> None:
        d = self.data
        if not self.bnd:
            # 非 BND：整个资源就是一个成员。capacity 必须是**盘上分配字节数**
            # （= len(self.raw)），不是解压后长度——资源级 LZ 压缩时两者差很大
            # （eid=701009：盘上 1690816、解压 3377616）。用错会把压缩结果
            # 补 0 补到解压长度，写盘时超界（本班踩过）。
            m = Member(self.eid, -1, d, self.compressed,
                       env.ISOBASE + self.off, len(self.raw),
                       env.ISOBASE + self.off)
            self.members[-1] = m
            self.member_order.append(-1)
            return
        declared = struct.unpack_from("<I", d, 4)[0]
        limit = min(declared, len(d)) if declared else len(d)
        count = struct.unpack_from("<I", d, 8)[0]
        table: List[Tuple[int, int]] = []
        for i in range(count):
            p = 0x10 + i * 8
            if p + 8 > len(d):
                break
            tag, o = struct.unpack_from("<II", d, p)
            table.append((tag, o))
        order = sorted(range(len(table)), key=lambda i: table[i][1])
        for rank, idx in enumerate(order):
            tag, o = table[idx]
            end = table[order[rank + 1]][1] if rank + 1 < len(order) else limit
            if not (0 <= o < limit) or end <= o:
                continue
            blob = d[o:end]
            dec = decompress_any(blob)[0]
            comp = dec is not blob
            self.members[tag] = Member(self.eid, tag, dec, comp,
                                       env.ISOBASE + self.off + o, end - o,
                                       env.ISOBASE + self.off + o)
            self.member_order.append(tag)

    # -- 便捷 ------------------------------------------------------
    def member(self, tag: int) -> Optional[Member]:
        return self.members.get(tag)

    def text_members(self) -> List[Member]:
        return [self.members[t] for t in self.member_order
                if t in self.members and self.members[t].blocks]


def load_toc(iso: str) -> Dict[int, Tuple[int, int]]:
    return databin_toc(iso)


_TOC_CACHE: Dict[str, Dict[int, Tuple[int, int]]] = {}


def cached_toc(iso: str) -> Dict[int, Tuple[int, int]]:
    key = iso.lower()
    if key not in _TOC_CACHE:
        _TOC_CACHE[key] = databin_toc(iso)
    return _TOC_CACHE[key]


def load_resource(iso: str, eid: int,
                  toc: Optional[Dict[int, Tuple[int, int]]] = None) -> Resource:
    if toc is None:
        toc = cached_toc(iso)
    if eid not in toc:
        raise LayoutError("TOC 中不存在 eid=%d" % eid)
    off, size = toc[eid]
    with open(iso, "rb") as f:
        f.seek(env.ISOBASE + off)
        raw = f.read(size)
    if len(raw) != size:
        raise LayoutError("eid=%d 读取不足：期望 %d，实际 %d" % (eid, size, len(raw)))
    return Resource(eid, off, size, raw)


def find_slot(member: Member, k: int, slot: int) -> Tuple[VerBlock, bytes]:
    blk = member.block(k)
    if not (0 <= slot < blk.slot_count()):
        raise LayoutError("块 (tag=%d,k=%d) 只有 %d 个逻辑槽，请求 slot=%d"
                          % (member.tag, k, blk.slot_count(), slot))
    return blk, slot_payload(member.data, blk, slot)
