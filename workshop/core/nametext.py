# -*- coding: utf-8 -*-
"""ACE3 名字块（CLM / FNM）适配层 —— 非标准文本的检索与写回。

## 为什么需要这一层

冻结检索层（``tools\\ace3_text_locator``，V3，241/241 PASS）只索引「VER 块」里的
文本槽（``tag=-1, k=0, slot=n``）。而武器名 / 指令名 / 机体名存在 VMD 容器的
``CLM``（指令名表）与 ``FNM``（机体名单串）块里，**完全不在 VER 块体系内**，
所以 ``slots`` 表里搜不到「冥墓方舟」——实测 ``eid=501050`` 在索引里只有 1 个槽
（``plain='〔938F〕〔9390〕方舟'``），CLM 的 29 条与 FNM 的 1 条都没进去。

本模块**只读冻结层的产物**（码表 ``core.codec``、字库缓存 ``font_cache.sqlite``、
TOC ``core.slots.load_toc``），自己解析 VMD 名字块，不改冻结工具一个字节。

## 块布局（实测钉死，``r1354_grayline_clamp_test.iso`` 上 322 个块全部成立）

::

    pos+0x00  b"CLM\\x00" / b"FNM\\x00"     4 字节 ASCII 标记
    pos+0x04  u32 size                      块总长（含 0x10 头）
    pos+0x08  0
    pos+0x0C  0
    base = pos + 0x10
    base+0x00 0
    base+0x04 u32 dsize                     数据区大小 == size - 0x10
    base+0x08 0
    base+0x0C u32 N                         名字条数
    base+0x10 u32 N                         双写校验（必须相等）
    base+0x14 u32 T                         偏移表相对 base 的位置（≥0x28，(T-0x28)%12==0）
    base+0x18 0
    base+0x1C 0
    base+0x20 ?  base+0x24 ?                （观察值，非必需，原样保留）
    base+0x28 起 每 12 字节一组 (名字索引, cmd, cmd)，共 (T-0x28)/12 组
    base+T     N 项 u32：第 i 项 = 第 i 个名字相对 base 的偏移
    base+T+4N  串区：NUL 结尾的游戏码位串，**串间有零 padding**（实测每串 0..15 字节）
    块尾        最后一个串的 NUL 之后到 base+dsize 的零 padding
    块外        名字块之间**零空隙**串联；资源尾余量实测为 0

## 编码

**不是 cp932**。实测同一段字节：

============================  ================  ========================
字节                          cp932 解           游戏码表解（本模块口径）
============================  ================  ========================
``938f93908d868fec``          ``'棟盗号嬢'``      ``'冥墓方舟'``
``914c89fc924b924a``          ``'銑改狸谷'``      ``'连击２１'``
``8f618bd78f618bd7...``       ``'渋禽渋禽...'``   ``'积层积层装甲回旋镖...'``
============================  ================  ========================

所以本模块一律走 ``core.codec`` 的权威码表，**禁止 GBK/CP932 兜底**。

## 写回的三条路线（按优先级）

1. **等长改写**：新串字节数 == 原串 → 原位覆盖，块内偏移表不动，零风险。
2. **更短改写**：新串更短 → 原位覆盖 + 用 ``0x00`` 补满原跨度。
   注意：**串中间的 0x00 会截断字符串**，所以补零只在原跨度内、且引擎读串遇 0 即停，
   效果等于「短串 + 尾部垃圾」，与 ``tools\\patch_weapon_names.py`` 的历史做法一致
   （该做法已在实机验证过）。
3. **更长改写（块内重排）**：整块重建 —— 串区从 ``T+4N`` 起重新紧密排布（吃掉串间
   padding），偏移表 N 项同步重写，块 size 不变。实测可吸收额度：501050 CLM 175 字节 /
   501051 CLM 等，见 ``block_slack()``。**超出额度一律 BLOCK**，绝不推动块外数据。

块外推动（改块 size + 平移后续块）**不实现**：实测四个目标资源尾部余量均为 0，
没有可信证据证明引擎能容忍块 size 变化。
"""
from __future__ import annotations

import dataclasses
import os
import struct
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import codec, env, slots

NAME_MARKS = (b"CLM\x00", b"FNM\x00")
MARK_NAME = {b"CLM\x00": "CLM", b"FNM\x00": "FNM"}
HEAD = 0x10              # 块头长度（magic + size + 8 字节保留）
MIN_T = 0x28             # 偏移表最小相对位置
GROUP = 12               # 三元组长度


class NameError_(Exception):
    """名字块解析/写回失败。故意不叫 NameError，避免遮蔽内建异常。"""


@dataclasses.dataclass
class NameBlock:
    """一个 CLM / FNM 名字块。"""

    eid: int
    pos: int                       # 块在资源内的偏移
    mark: bytes
    size: int                      # 块总长
    base: int                      # pos + HEAD
    dsize: int                     # 数据区大小
    count: int                     # 名字条数
    toff: int                      # 偏移表相对 base 的位置
    offs: List[int]                # N 项，相对 base
    items: List[bytes]             # N 条原始字节（不含 NUL）
    raw: bytes                     # 整块原始字节（用于 diff）
    # -- 由 blocks_of() 填（parse_at / scan 直接调用时保持默认） -----------
    res_off: int = 0               # 所属资源在 DATA.BIN 内的偏移
    res_size: int = 0              # 所属资源大小
    mem_tag: int = -1              # 所属成员 tag
    ver_k: Optional[int] = None    # 若是 VER 块，记其下标（FNM 有，CLM 没有）

    # -- 展示 ------------------------------------------------------
    @property
    def kind(self) -> str:
        return MARK_NAME.get(self.mark, "?")

    @property
    def used(self) -> int:
        """最后一个串的 NUL 之后，相对 base 的位置。"""
        return self.offs[-1] + len(self.items[-1]) + 1

    @property
    def pad(self) -> int:
        """块内尾部零 padding 字节数。"""
        return self.dsize - self.used

    def slack(self) -> int:
        """块内还能塞进多少字节（含串间 padding 与块尾 padding）。

        算法：串区可用空间 = ``dsize - (toff + 4*count)``；
        当前已用 = ``used - (toff + 4*count)``；差值即可增长量。
        """
        room = self.dsize - (self.toff + 4 * self.count)
        return room - (self.used - (self.toff + 4 * self.count))

    def plain(self, idx: int) -> str:
        """第 idx 条的显示文本（未知码位显示成 〔XXXX〕）。"""
        return codec.decode_bytes(self.items[idx])[0]

    def unknown(self, idx: int) -> List[int]:
        return codec.decode_bytes(self.items[idx])[2]

    def label(self) -> str:
        return "EID %d · %s 块 @0x%X · %d 条" % (self.eid, self.kind,
                                                  self.pos, self.count)


# ---------------------------------------------------------------- 解析

def parse_at(data: bytes, pos: int, eid: int = 0) -> Optional[NameBlock]:
    """在 ``data[pos:]`` 处解析名字块；不合规返回 None（绝不猜）。"""
    if pos < 0 or pos + HEAD > len(data):
        return None
    mark = data[pos:pos + 4]
    if mark not in NAME_MARKS:
        return None
    size = struct.unpack_from("<I", data, pos + 4)[0]
    if not (HEAD + MIN_T <= size <= len(data) - pos):
        return None
    base = pos + HEAD
    dsize = struct.unpack_from("<I", data, base + 0x04)[0]
    if dsize != size - HEAD:
        return None
    n1 = struct.unpack_from("<I", data, base + 0x0C)[0]
    n2 = struct.unpack_from("<I", data, base + 0x10)[0]
    if n1 != n2 or not (1 <= n1 <= 1024):
        return None
    toff = struct.unpack_from("<I", data, base + 0x14)[0]
    if toff < MIN_T or (toff - MIN_T) % GROUP:
        return None
    if toff + 4 * n1 > dsize:
        return None
    offs = [struct.unpack_from("<I", data, base + toff + 4 * i)[0]
            for i in range(n1)]
    if offs != sorted(offs):
        return None
    if offs[0] < toff + 4 * n1 or offs[-1] >= dsize:
        return None
    items: List[bytes] = []
    for o in offs:
        end = data.find(b"\x00", base + o, base + dsize)
        if end < 0:
            return None
        items.append(data[base + o:end])
    return NameBlock(eid=eid, pos=pos, mark=mark, size=size, base=base,
                     dsize=dsize, count=n1, toff=toff, offs=offs,
                     items=items, raw=bytes(data[pos:pos + size]))


def scan(data: bytes, eid: int = 0) -> List[NameBlock]:
    """扫出资源内所有名字块（按偏移升序）。

    只认「标记 + 合规结构」的块；不按 ``split(b"\\x00")`` 猜。
    """
    out: List[NameBlock] = []
    i = 0
    n = len(data)
    while i < n - 8:
        if data[i:i + 4] in NAME_MARKS:
            blk = parse_at(data, i, eid)
            if blk is not None:
                out.append(blk)
                i += blk.size
                continue
        i += 1
    return out


# ---------------------------------------------------------------- 重建

def rebuild(blk: NameBlock, changes: Dict[int, bytes]) -> Tuple[bytes, dict]:
    """块内重排，返回 ``(新块字节, 报告)``。

    ``changes`` = ``{串下标: 新串字节（不含 NUL）}``。

    规则：
      * 块 size / dsize / count / toff **一个都不改**（块外零位移）。
      * 串区从 ``toff + 4*count`` 起紧密重排，吃掉串间与块尾 padding。
      * 偏移表 N 项同步重写。
      * 头部其余字段（含三元组区、``base+0x20/0x24``）**逐字节照抄**。
      * 装不下就抛 ``NameError_``，绝不撑长容器。
    """
    items = list(blk.items)
    for i, raw in changes.items():
        if not (0 <= i < blk.count):
            raise NameError_("串下标越界：%d（该块只有 %d 条）" % (i, blk.count))
        if b"\x00" in raw:
            raise NameError_("新串里不能含 0x00（会被引擎当成终止符）")
        items[i] = raw

    body = bytearray()
    new_offs: List[int] = []
    cursor = blk.toff + 4 * blk.count
    for raw in items:
        new_offs.append(cursor)
        body.extend(raw + b"\x00")
        cursor += len(raw) + 1
    if cursor > blk.dsize:
        raise NameError_(
            "块内空间不足：需要 %d 字节，块内可用 %d（差 %d）。"
            "本工具不推动块外数据，请缩短译文。"
            % (cursor, blk.dsize, cursor - blk.dsize))

    out = bytearray(blk.raw)                    # ★ 从原块整块拷贝，其余字段天然保留
    start = blk.toff + 4 * blk.count            # 相对 base
    end = start + len(body)
    if HEAD + end > blk.size:
        raise NameError_("写入区间越界：%d..%d > size %d"
                         % (HEAD + start, HEAD + end, blk.size))
    for i, o in enumerate(new_offs):
        struct.pack_into("<I", out, HEAD + blk.toff + 4 * i, o)
    out[HEAD + start:HEAD + end] = body
    # 串区之后的块内空间清零（原版就是全零，保持同构）
    tail = HEAD + end
    if tail < blk.size:
        out[tail:blk.size] = b"\x00" * (blk.size - tail)
    if len(out) != blk.size:
        raise NameError_("重排后块长度变了：%d -> %d" % (blk.size, len(out)))
    rep = {"mode": "reflow", "grown": cursor - blk.used,
           "slack_after": blk.dsize - cursor,
           "offsets_changed": sum(1 for a, b in zip(blk.offs, new_offs) if a != b)}
    return bytes(out), rep


def encode_inplace(blk: NameBlock, idx: int, new_raw: bytes) -> Tuple[bytes, dict]:
    """原位改写单条（等长或更短），块内其它字节一个不动。

    更短时用 ``0x00`` 补满原跨度 —— 引擎读串遇 ``0x00`` 即停，
    等价于「短串 + 尾部垃圾」，与历史已验证做法一致。
    """
    old = blk.items[idx]
    if b"\x00" in new_raw:
        raise NameError_("新串里不能含 0x00")
    if len(new_raw) > len(old):
        raise NameError_("原位改写要求不长于原串：新 %d > 原 %d"
                         % (len(new_raw), len(old)))
    out = bytearray(blk.raw)
    o = HEAD + blk.offs[idx]
    out[o:o + len(old)] = new_raw + b"\x00" * (len(old) - len(new_raw))
    rep = {"mode": "inplace", "old_len": len(old), "new_len": len(new_raw),
           "padded": len(old) - len(new_raw)}
    return bytes(out), rep


def apply_changes(data: bytes, blk: NameBlock,
                  changes: Dict[int, bytes]) -> Tuple[bytes, dict]:
    """按需选路线：全部不长于原串 → 原位；否则整块重排。"""
    if not changes:
        raise NameError_("没有要改的条目")
    # ★ 先校验下标，再判长度。顺序反了会在越界时直接 IndexError（自测踩过）。
    for i in changes:
        if not (0 <= i < blk.count):
            raise NameError_("串下标越界：%d（该块只有 %d 条）" % (i, blk.count))
    longer = [i for i, raw in changes.items() if len(raw) > len(blk.items[i])]
    if not longer:
        cur = bytes(data)
        reps = []
        for i in sorted(changes):
            piece, rep = encode_inplace(blk, i, changes[i])
            o = blk.pos
            cur = cur[:o] + piece + cur[o + len(piece):]
            reps.append(rep)
        return cur, {"mode": "inplace", "items": len(changes),
                     "detail": reps, "slack_after": blk.slack()}
    piece, rep = rebuild(blk, changes)
    o = blk.pos
    return data[:o] + piece + data[o + len(piece):], rep


# ---------------------------------------------------------------- 资源级
@dataclasses.dataclass
class NameHit:
    """一条名字（搜索结果 / 编辑对象）。"""

    eid: int
    block_pos: int
    index: int
    mark: str
    raw: bytes
    plain: str
    unknown: List[int]
    capacity: int              # 该条原串字节数
    slack: int                 # 该块还能吸收多少字节
    block_size: int
    block_count: int
    res_off: int               # 资源在 DATA.BIN 内的偏移
    res_size: int
    mem_tag: int = -1          # 所属成员 tag
    ver_k: Optional[int] = None  # 若是 VER 块（FNM）记其下标；CLM 为 None

    @property
    def file_off(self) -> int:
        """ISO 绝对偏移（资源偏移 + DATA.BIN 基址 + 串内偏移）。"""
        return env.ISOBASE + self.res_off

    @property
    def engine(self) -> str:
        """该条该走哪条写盘链：``"slot"``（FNM，VER 槽引擎）或 ``"name"``（CLM）。"""
        return "slot" if self.ver_k is not None else "name"

    def as_dict(self) -> dict:
        return {"eid": self.eid, "block_pos": self.block_pos, "index": self.index,
                "mark": self.mark, "raw_hex": self.raw.hex(),
                "plain": self.plain, "unknown": list(self.unknown),
                "capacity": self.capacity, "slack": self.slack,
                "block_size": self.block_size, "block_count": self.block_count,
                "res_off": self.res_off, "res_size": self.res_size,
                "mem_tag": self.mem_tag, "ver_k": self.ver_k,
                "engine": self.engine}


_CACHE: Dict[Tuple[str, int], List[NameBlock]] = {}


def blocks_of(iso: str, eid: int,
              toc: Optional[Dict[int, Tuple[int, int]]] = None) -> List[NameBlock]:
    """读资源并返回其名字块（带缓存，缓存键含 ISO 路径与 eid）。"""
    key = (iso, eid)
    if key in _CACHE:
        return _CACHE[key]
    if toc is None:
        toc = slots.cached_toc(iso)
    if eid not in toc:
        return []
    res = slots.load_resource(iso, eid, toc)
    out = []
    for tag, mem in res.members.items():
        found = scan(mem.data, eid)
        # VER 块与名字块的关系：实测 FNM 块**本身就是一个 VER 块**
        # （VER.start == FNM.base、VER.size == FNM.dsize、cnt == 1、toff == 0x28）。
        # CLM 块从不出现在 VER 块体系里。这个区别决定写盘走哪条路：
        #   * ver_k is not None（FNM）→ 交给 relayout，与普通槽编辑同一套引擎
        #   * ver_k is None（CLM）    → 只能本模块的块内重排
        vers = mem.blocks
        for b in found:
            b.res_off = res.off
            b.res_size = res.size
            b.mem_tag = tag
            k = None
            for i, v in enumerate(vers):
                if v.start == b.base:
                    k = i
                    break
            b.ver_k = k
        out.extend(found)
    _CACHE[key] = out
    return out


def clear_cache() -> None:
    """清空块缓存与 VMD 粗筛缓存（换盘、重建索引后调用）。"""
    _CACHE.clear()
    _VMD_CACHE.clear()


def hits_of(iso: str, eid: int,
            toc: Optional[Dict[int, Tuple[int, int]]] = None) -> List[NameHit]:
    """把某资源的名字块摊平成 ``NameHit`` 列表。"""
    out: List[NameHit] = []
    for blk in blocks_of(iso, eid, toc):
        for i, raw in enumerate(blk.items):
            plain, _codes, unk, _ctrl = codec.decode_bytes(raw)
            out.append(NameHit(
                eid=eid, block_pos=blk.pos, index=i, mark=blk.kind,
                raw=raw, plain=plain, unknown=unk,
                capacity=len(raw), slack=blk.slack(),
                block_size=blk.size, block_count=blk.count,
                res_off=getattr(blk, "res_off", 0),
                res_size=getattr(blk, "res_size", 0),
                mem_tag=getattr(blk, "mem_tag", -1),
                ver_k=getattr(blk, "ver_k", None)))
    return out


def search(iso: str, needle: str, eids: Optional[Iterable[int]] = None,
           limit: int = 500) -> List[NameHit]:
    """在名字块里搜文本。

    匹配口径：命中「显示文本」或「原始字节按游戏码表解出的文本」。
    对含未知码位的串，额外允许按 ``〔XXXX〕`` 占位形态匹配。
    """
    if not needle:
        return []
    toc = slots.cached_toc(iso)
    targets = list(eids) if eids is not None else _all_vmd_eids(iso, toc)
    out: List[NameHit] = []
    for eid in targets:
        for h in hits_of(iso, eid, toc):
            if needle in h.plain or needle in h.raw.decode("latin-1", "ignore"):
                out.append(h)
                if len(out) >= limit:
                    return out
    return out


def search_hits(iso: str, needle: str, eids: Optional[Iterable[int]] = None,
                limit: int = 500) -> List[dict]:
    """同 ``search()``，但直接返回 GUI 可用的 dict 列表（见 ``as_hit``）。"""
    return [as_hit(h, needle) for h in search(iso, needle, eids=eids, limit=limit)]


_VMD_CACHE: Dict[str, List[int]] = {}


def _all_vmd_eids(iso: str,
                  toc: Optional[Dict[int, Tuple[int, int]]] = None) -> List[int]:
    """粗筛：只看 50xxxx / 28xxxxx 段（实测名字块只在这些条目里）。

    不逐个读整条资源（4.4 GB 盘 10,343 条目逐个读要几分钟），
    只读每个条目的前 4 KiB 判断有没有 VMD 标记或名字块标记。

    结果按 ISO 路径缓存：同一张盘第二次搜名字块是 0 秒级，
    否则每敲一次搜索都要等 19 秒（实测）。缓存只存 eid 列表，
    不含任何文本，盘变了由 ``clear_cache()`` 或换 ISO 自然失效。
    """
    key = os.path.abspath(iso)
    if key in _VMD_CACHE:
        return _VMD_CACHE[key]
    if toc is None:
        toc = slots.cached_toc(iso)
    out: List[int] = []
    with open(iso, "rb") as f:
        for eid in sorted(toc):
            if not (500000 <= eid < 600000 or 2800000 <= eid < 2900000):
                continue
            off, size = toc[eid]
            f.seek(env.ISOBASE + off)
            head = f.read(min(4096, size))
            if head[:4] == b"VMD\x00" or any(m in head for m in NAME_MARKS):
                out.append(eid)
    _VMD_CACHE[key] = out
    return out


def vmd_eids(iso: str) -> List[int]:
    """全盘所有可能是 VMD 容器的 eid（供 GUI 枚举版本资源用）。"""
    return _all_vmd_eids(iso)


# ---------------------------------------------------------------- 容量/覆盖
def capacity_report(hit: NameHit) -> dict:
    """一条名字的可写容量报告（给 GUI 中栏显示）。"""
    return {"capacity": hit.capacity, "slack": hit.slack,
            "inplace_max": hit.capacity,
            "reflow_max": hit.capacity + hit.slack,
            "mode_inplace": "等长或更短：原位覆盖，块内偏移不动",
            "mode_reflow": ("更长：整块重排，吃掉串间/块尾 padding，"
                            "块外零位移；上限 %d 字节" % (hit.capacity + hit.slack))}


def as_hit(hit: NameHit, needle: str = "") -> dict:
    """把 ``NameHit`` 转成 GUI 结果列表/编辑面板通用的 dict。

    键与 ``core.search.Index._hit()`` 保持同一套（``eid/text/bytes/seg_hex/
    compressed/quality/file_off/...``），这样左栏委托、中栏编辑器、
    队列都不需要为名字块写第二套渲染逻辑。

    额外多出三个键：``kind="name"``、``mark``、``engine``——
    GUI 据此显示「CLM 指令表 / FNM 机体名」并决定写盘引擎。
    """
    plain = hit.plain or ""
    match_at: List[tuple] = []
    if needle:
        p = plain.find(needle)
        while p >= 0:
            match_at.append((p, p + len(needle)))
            p = plain.find(needle, p + 1)
    return {
        "kind": "name",
        "eid": hit.eid,
        "tag": hit.mem_tag,
        "k": hit.ver_k if hit.ver_k is not None else -1,
        "slot": hit.index,
        "nslots": hit.block_count,
        "key": "EID %d · %s 块 · 第 %d 条" % (hit.eid, hit.mark, hit.index),
        "key_short": "%d/%s/%d" % (hit.eid, hit.mark, hit.index),
        "key_text": "%s 块 @0x%X" % (hit.mark, hit.block_pos),
        "text": plain,
        "bytes": len(hit.raw),
        "seg_hex": hit.raw.hex().upper(),
        "codes_hex": " ".join("%04X" % c for c in
                              list(codec.encode_text(plain).used_codes.values())[:24])
                      or "—",
        "compressed": False,
        "quality": 2 if hit.unknown else 1,
        "unknown_codes": len(hit.unknown),
        "control_bytes": 0,
        "file_off": hit.file_off + hit.block_pos,
        "file_end": hit.file_off + hit.block_pos + hit.block_size,
        "abs_off": hit.file_off,
        "base": 0,
        "jp_text": "",
        "jp_status": "NO_BLOCK",
        "zh_authority": "",
        "trans": [],
        "scene": "%s 块 / 第 %d 条" % (hit.mark, hit.index),
        "match_at": match_at,
        "mark": hit.mark,
        "engine": hit.engine,
        "block_pos": hit.block_pos,
        "block_index": hit.index,
        "block_size": hit.block_size,
        "block_count": hit.block_count,
        # mem_tag / ver_k 必须一起带出去：FNM 块本身是 VER 块（ver_k 非 None），
        # GUI 与 editplan 靠这两个值决定走槽编辑还是名字块引擎；
        # 少了它们 FNM 会被当成 CLM 处理，写盘路径就走错了。
        "mem_tag": hit.mem_tag,
        "ver_k": hit.ver_k,
        "slack": hit.slack,
        "capacity": hit.capacity,
        "context": [],
    }
