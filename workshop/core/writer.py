"""安全写盘引擎核心（先命令行可独立验证，再接 GUI）。

设计原则（对应任务书 §六）
--------------------------
* **只做块内重排**：改字只在本 VER 块内移动槽与重建 offs 表，
  块大小 / cnt / toff / 块外字节一律不动 ⇒ 成员长度不变 ⇒ BND 表不变
  ⇒ 资源大小不变 ⇒ TOC 与 ISO9660 不变 ⇒ ISO 文件大小完全不变。
* **压缩成员按成员处理**：成员重排后整条重压，压完必须 ≤ 原分配容量，
  补 0 填满原区间；超容量即 BLOCK，绝不挤占下一成员。
* **资源级压缩**（整个资源被 LZ 包住）：改完文本整条重压，必须 ≤ 原 size。
* **事务式**：全部资源先内存构建成功，才落盘到独立 dst；dst 绝不等于 src。
* **独立回读**：写盘后重新打开 dst、重新解析资源，逐项比对，不信任构建返回值。
* **差异白名单**：整盘分块比对，任何落在白名单外的字节变化都是失败。

历史教训（写在本文件里防止重犯）
--------------------------------
* 只改压缩流里的明文字节 ⇒ 会改坏与文本共用回溯副本的模型数据，
  ``lz_patch.py`` 已把该路线永久禁用。本模块一律「整条重压」。
* 用 8 字节 LZ 头（少 4 字节保留位）⇒ 回读只解出 18 字节。头必须是 12 字节：
  ``MAGIC(4) + 大端解压长度(4) + 保留(4)``。
* 用「当前串长」当容量上限 ⇒ 会把本来放得下的译文拒掉；也用「块尾连续 0」
  当容量 ⇒ 会低估。正确口径见 ``relayout.block_slack`` / ``relayout.simulate``。
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
import struct
import time
from typing import Dict, List, Optional, Sequence, Tuple

from . import codec, env
from . import relayout as R
from . import slots as S

env.ensure_sys_path()

import lz_encode as LZ                                    # noqa: E402

PASS, WARN, BLOCK = "PASS", "WARN", "BLOCK"
DIFF_CHUNK = 1 << 23          # 8MB

_LZ_RETRY_CAPS = (64, 256, 1024, 4096)


class WriteError(RuntimeError):
    """写盘流程不可继续（必须保留父盘与队列）。"""


# ---------------------------------------------------------------- 数据结构
@dataclasses.dataclass
class Edit:
    """一条待写修改。槽身份只用逻辑口径 ``(eid, tag, k, slot)``。"""

    eid: int
    tag: int
    k: int
    slot: int
    new_payload: bytes
    old_payload: bytes = b""
    label: str = ""

    def key(self) -> Tuple[int, int, int, int]:
        return (self.eid, self.tag, self.k, self.slot)

    def as_dict(self) -> dict:
        return {"eid": self.eid, "tag": self.tag, "k": self.k, "slot": self.slot,
                "new_hex": self.new_payload.hex(), "old_hex": self.old_payload.hex(),
                "old_bytes": len(self.old_payload), "new_bytes": len(self.new_payload),
                "label": self.label}


@dataclasses.dataclass
class Verdict:
    edit: Edit
    state: str = PASS
    reason: str = ""
    compressed: bool = False
    capacity: int = 0
    slack: int = 0
    tags_ok: bool = True
    detail: dict = dataclasses.field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.state != BLOCK

    def as_dict(self) -> dict:
        d = self.edit.as_dict()
        # ``where`` 只用于界面/报告展示，不参与任何写盘判定
        d.update({"state": self.state, "reason": self.reason,
                  "compressed": self.compressed, "capacity": self.capacity,
                  "slack": self.slack, "tags_ok": self.tags_ok,
                  "where": "EID %d · tag%d/k%d · 槽%d"
                           % (self.edit.eid, self.edit.tag,
                              self.edit.k, self.edit.slot)})
        return d


@dataclasses.dataclass
class BuildResult:
    eid: int
    new_raw: bytes
    old_raw: bytes
    res_off: int
    res_size: int
    compressed: bool
    edits: List[Edit]
    notes: List[str] = dataclasses.field(default_factory=list)

    @property
    def region(self) -> Tuple[int, int]:
        """该资源在 ISO 里的可写区间（绝对偏移）。"""
        return (env.ISOBASE + self.res_off,
                env.ISOBASE + self.res_off + self.res_size)


# ---------------------------------------------------------------- 压缩封装
def lz_header(data_len: int) -> bytes:
    return env.LZ_MAGIC + struct.pack(">I", data_len) + b"\x00\x00\x00\x00"


def compress_fit(data: bytes, capacity: int) -> Optional[bytes]:
    """把 ``data`` 压到 ``capacity`` 以内；压不进返回 None。

    每个候选都必须自解压回读逐字节相等才算数（不做"看起来压小了"的判断）。
    """
    if capacity <= env.LZ_HEADER:
        return None
    head = lz_header(len(data))
    for cap in _LZ_RETRY_CAPS:
        if cap > len(data):
            continue
        try:
            body = LZ.encode(data, cap=cap)
        except Exception:
            continue
        blob = head + body
        if len(blob) > capacity:
            continue
        chk, ok = LZ.decode(body, len(data))
        if ok and chk == data:
            return blob + b"\x00" * (capacity - len(blob))
    # DP 解析兜底
    try:
        body = LZ.encode_dp(data, cap=256)
        blob = head + body
        if len(blob) <= capacity:
            chk, ok = LZ.decode(body, len(data))
            if ok and chk == data:
                return blob + b"\x00" * (capacity - len(blob))
    except Exception:
        pass
    return None


def _member_blob(mem: S.Member, new_data: bytes) -> bytes:
    """把重排后的成员数据变成「占满原容量」的 blob。

    注意：压缩成员的 ``new_data`` 是**解压后**长度，天然大于 ``mem.capacity``
    （盘上压缩后大小），所以不能先做裸长度比较——必须先判压缩分支。
    """
    if mem.compressed:
        blob = compress_fit(new_data, mem.capacity)
        if blob is None:
            raise WriteError(
                "成员 tag=%d 压缩后超出原容量 %d 字节（不允许挤占下一成员）"
                % (mem.tag, mem.capacity))
        return blob
    if len(new_data) > mem.capacity:
        raise WriteError(
            "成员 tag=%d 重排后 %d 字节 > 原容量 %d"
            % (mem.tag, len(new_data), mem.capacity))
    if len(new_data) != mem.capacity:
        raise WriteError(
            "非压缩成员 tag=%d 长度变化 %d != %d（块内重排不应改变长度）"
            % (mem.tag, len(new_data), mem.capacity))
    return new_data


def effective_edits(edits: Sequence[Edit]) -> List[Edit]:
    """按槽身份去重，同槽只保留**最后一条**（后者覆盖前者）。

    ``build`` 用 ``dict`` 落表，天然就是"后写覆盖"；``verify`` / 写盘 / 差分
    必须以同一口径取"生效集合"，否则会把被覆盖的旧值当成期望值去比对，
    把自己判成失败（本班踩过）。
    """
    seen: Dict[Tuple[int, int, int, int], Edit] = {}
    for e in edits:
        seen[e.key()] = e
    return list(seen.values())


def superseded_indexes(edits: Sequence[Edit]) -> Dict[int, int]:
    """返回 ``{被覆盖的下标: 覆盖者下标}``，供 GUI 标冲突。"""
    last: Dict[Tuple[int, int, int, int], int] = {}
    for i, e in enumerate(edits):
        last[e.key()] = i
    out: Dict[int, int] = {}
    for i, e in enumerate(edits):
        j = last[e.key()]
        if j != i:
            out[i] = j
    return out


# ---------------------------------------------------------------- 预演
def check(iso: str, edits: Sequence[Edit],
          toc: Optional[Dict[int, Tuple[int, int]]] = None,
          use_fontcov: bool = True) -> List[Verdict]:
    """逐项预演，不写任何字节。返回与 ``edits`` 等长的结论表。"""
    if toc is None:
        toc = S.cached_toc(iso)
    out: List[Verdict] = []
    cov = None
    if use_fontcov:
        try:
            from . import fontcov
            cov = fontcov.coverage()
        except Exception:
            cov = None

    sup = superseded_indexes(edits)
    res_cache: Dict[int, S.Resource] = {}
    for idx, e in enumerate(edits):
        v = Verdict(edit=e)
        if idx in sup:
            v.state = WARN
            v.reason = ("同槽重复修改：第 %d 条会覆盖本条（最终以第 %d 条写入）"
                        % (sup[idx] + 1, sup[idx] + 1))
            v.detail["superseded_by"] = sup[idx]
            v.detail["new_len"] = len(e.new_payload)
            out.append(v)
            continue
        try:
            if e.eid not in toc:
                v.state = BLOCK
                v.reason = "TOC 中不存在 eid=%d" % e.eid
                out.append(v)
                continue
            if e.eid not in res_cache:
                res_cache[e.eid] = S.load_resource(iso, e.eid, toc)
            res = res_cache[e.eid]
            mem = res.member(e.tag)
            if mem is None:
                v.state = BLOCK
                v.reason = "资源 eid=%d 无成员 tag=%d" % (e.eid, e.tag)
                out.append(v)
                continue
            v.compressed = mem.compressed or res.compressed
            v.capacity = mem.capacity
            blk = mem.block(e.k)
            if not (0 <= e.slot < blk.slot_count()):
                v.state = BLOCK
                v.reason = "块 (tag=%d,k=%d) 只有 %d 个逻辑槽" % (
                    e.tag, e.k, blk.slot_count())
                out.append(v)
                continue
            cur = S.slot_payload(mem.data, blk, e.slot)
            if e.old_payload and cur != e.old_payload:
                v.state = BLOCK
                v.reason = ("原字节指纹不符（防串位）：期望 %r，现盘 %r"
                            % (e.old_payload[:32], cur[:32]))
                out.append(v)
                continue
            if b"\x00" in e.new_payload:
                v.state = BLOCK
                v.reason = "新串含 NUL 字节"
                out.append(v)
                continue
            # 标签完整性
            old_sig = codec.tag_signature(codec.Segment(cur).tags)
            new_sig = codec.tag_signature(codec.Segment(e.new_payload).tags)
            v.tags_ok = (old_sig == new_sig)
            if not v.tags_ok:
                v.state = BLOCK
                v.reason = ("控制标签序列变化：%s -> %s（标签只能在显式操作里调整）"
                            % (old_sig, new_sig))
                out.append(v)
                continue
            # 字形覆盖
            if cov is not None:
                plain = codec.Segment(e.new_payload).plain
                state, verdicts, hint = cov.diagnose_text(plain)
                if state == BLOCK:
                    v.state = BLOCK
                    v.reason = "缺字阻断：%s（%s）" % (
                        "".join(x.char for x in verdicts if x.state == BLOCK), hint)
                elif state == WARN:
                    v.state = WARN
                    v.reason = "字库告警：%s" % "；".join(
                        x.short() for x in verdicts if x.state == WARN)[:300]
            # 容量推演（单条，按本块现状）
            sim = R.simulate(mem.data, blk, {e.slot: e.new_payload}, compact=False)
            v.slack = R.block_slack(mem.data, blk)
            if not sim["fits"]:
                sim2 = R.simulate(mem.data, blk, {e.slot: e.new_payload},
                                  compact=True)
                if not sim2["fits"]:
                    v.state = BLOCK
                    v.reason = ("块内空间不足：需要 %d 字节，可用 %d（差 %d）"
                                % (sim2["cursor"] - sim2["limit"] + v.capacity,
                                   v.capacity,
                                   sim2["cursor"] - sim2["limit"]))
                    v.detail["short_by"] = sim2["cursor"] - sim2["limit"]
                else:
                    v.detail["relayout"] = "compact"
            if v.state != BLOCK:
                v.detail["new_len"] = len(e.new_payload)
                v.detail["old_len"] = len(cur)
                v.detail["block_slack"] = v.slack
        except (S.LayoutError, WriteError) as exc:
            v.state = BLOCK
            v.reason = str(exc)
        except Exception as exc:                       # 未预期错误也不许放行
            v.state = BLOCK
            v.reason = "%s: %s" % (type(exc).__name__, exc)
        out.append(v)
    return out


# ---------------------------------------------------------------- 构建
def build(iso: str, edits: Sequence[Edit],
          toc: Optional[Dict[int, Tuple[int, int]]] = None) -> Dict[int, BuildResult]:
    """在内存里构建每个受影响资源的新字节。任一失败整体抛 ``WriteError``。"""
    if toc is None:
        toc = S.cached_toc(iso)
    by_res: Dict[int, List[Edit]] = {}
    for e in effective_edits(edits):
        by_res.setdefault(e.eid, []).append(e)

    results: Dict[int, BuildResult] = {}
    for eid, group in by_res.items():
        if eid not in toc:
            raise WriteError("TOC 中不存在 eid=%d" % eid)
        res = S.load_resource(iso, eid, toc)
        notes: List[str] = []
        # 按成员收集
        by_member: Dict[int, Dict[int, Dict[int, bytes]]] = {}
        for e in group:
            by_member.setdefault(e.tag, {}).setdefault(e.k, {})[e.slot] = e.new_payload

        member_new: Dict[int, bytes] = {}
        for tag, by_k in by_member.items():
            mem = res.member(tag)
            if mem is None:
                raise WriteError("eid=%d 无成员 tag=%d" % (eid, tag))
            data = mem.data
            for k in sorted(by_k):
                blk = mem.block(k)
                data, rep = R.relayout(data, blk, by_k[k])
                notes.append("tag=%d k=%d mode=%s moved=%d slack_after=%d"
                             % (tag, k, rep["mode"], rep["moved"],
                                rep["slack_after"]))
            member_new[tag] = data

        if not res.bnd:
            # 资源整体就是一个成员（tag=-1）
            data = member_new.get(-1)
            if data is None:
                raise WriteError("eid=%d 非 BND 资源但改动 tag 不是 -1" % eid)
            new_raw = _member_blob(res.member(-1), data)
        else:
            buf = bytearray(res.data)
            for tag, data in member_new.items():
                mem = res.member(tag)
                o = mem.file_off - env.ISOBASE - res.off
                end = o + mem.capacity
                if end > len(buf):
                    raise WriteError("eid=%d 成员 tag=%d 区间越界" % (eid, tag))
                buf[o:end] = _member_blob(mem, data)
            new_raw = bytes(buf)
            if len(new_raw) != len(res.data):
                raise WriteError("eid=%d BND 长度变化 %d -> %d"
                                 % (eid, len(res.data), len(new_raw)))
            if res.compressed:
                blob = compress_fit(new_raw, res.size)
                if blob is None:
                    raise WriteError("eid=%d 资源级压缩后超出原 size %d"
                                     % (eid, res.size))
                new_raw = blob
                notes.append("资源级重压")

        if len(new_raw) > res.size:
            raise WriteError("eid=%d 构建结果 %d > 原 size %d"
                             % (eid, len(new_raw), res.size))
        results[eid] = BuildResult(eid=eid, new_raw=new_raw, old_raw=res.raw,
                                   res_off=res.off, res_size=res.size,
                                   compressed=res.compressed,
                                   edits=list(group), notes=notes)
    return results


# ---------------------------------------------------------------- 写盘
def write(src: str, builds: Dict[int, BuildResult], dst: str,
          expect_md5: Optional[str] = None,
          progress=None) -> dict:
    """事务式写盘：复制父盘到 dst，再原位替换受影响资源区间。

    任何一步失败都不留下半个 dst（失败时删除 dst）。
    """
    t0 = time.time()
    if os.path.abspath(src).lower() == os.path.abspath(dst).lower():
        raise WriteError("dst 不能等于父盘（绝不覆盖父盘）")
    if os.path.exists(dst):
        raise WriteError("目标已存在，拒绝覆盖：%s" % dst)
    if not os.path.exists(src):
        raise WriteError("父盘不存在：%s" % src)
    size = os.path.getsize(src)

    md5_src = env.md5_file(src)
    if expect_md5 and md5_src.lower() != expect_md5.lower():
        raise WriteError("父盘 MD5 不符：期望 %s，实际 %s" % (expect_md5, md5_src))

    for b in builds.values():
        lo, hi = b.region
        if hi > size:
            raise WriteError("eid=%d 资源区间越界 %d..%d > %d"
                             % (b.eid, lo, hi, size))

    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    try:
        if progress:
            progress("复制父盘 %s -> %s" % (os.path.basename(src),
                                            os.path.basename(dst)))
        shutil.copyfile(src, dst)
        with open(dst, "r+b") as f:
            for i, eid in enumerate(sorted(builds)):
                b = builds[eid]
                lo, _hi = b.region
                if progress:
                    progress("写入 eid=%d（%d 项，%d 字节）"
                             % (eid, len(b.edits), len(b.new_raw)))
                f.seek(lo)
                f.write(b.new_raw)
                if len(b.new_raw) < b.res_size:
                    f.write(b"\x00" * (b.res_size - len(b.new_raw)))
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            if os.path.exists(dst):
                os.remove(dst)
        finally:
            raise

    return {"dst": dst, "size": os.path.getsize(dst),
            "src_md5": md5_src, "dst_md5": env.md5_file(dst),
            "resources": len(builds),
            "elapsed": round(time.time() - t0, 1)}


# ---------------------------------------------------------------- 独立回读
def verify(dst: str, edits: Sequence[Edit],
           toc_src: Optional[Dict[int, Tuple[int, int]]] = None) -> dict:
    """**独立**回读：重新打开 dst，重新解析 TOC/资源/块，逐项比对。

    不调用 ``build`` 的任何内存结果，只看盘面字节。
    """
    toc = S.load_toc(dst)
    if toc_src is not None:
        if set(toc) != set(toc_src):
            return {"ok": False, "error": "TOC eid 集合变化", "items": []}
        for eid, val in toc_src.items():
            if toc.get(eid) != val:
                return {"ok": False, "error": "TOC 条目变化 eid=%d" % eid,
                        "items": []}
    cache: Dict[int, S.Resource] = {}
    items = []
    ok = True
    for e in effective_edits(edits):
        rec = {"eid": e.eid, "tag": e.tag, "k": e.k, "slot": e.slot,
               "ok": False, "error": ""}
        try:
            if e.eid not in cache:
                cache[e.eid] = S.load_resource(dst, e.eid, toc)
            mem = cache[e.eid].member(e.tag)
            if mem is None:
                rec["error"] = "回读无该成员"
                ok = False
                items.append(rec)
                continue
            blk = mem.block(e.k)
            got = S.slot_payload(mem.data, blk, e.slot)
            rec["got_hex"] = got.hex()
            if got == e.new_payload:
                rec["ok"] = True
            else:
                rec["error"] = "回读不符：%r" % (got[:60],)
                ok = False
        except Exception as exc:
            rec["error"] = "%s: %s" % (type(exc).__name__, exc)
            ok = False
        items.append(rec)
    return {"ok": ok, "items": items, "iso": dst}


def diff_whitelist(src: str, dst: str,
                   builds: Dict[int, BuildResult],
                   chunk: int = DIFF_CHUNK) -> dict:
    """整盘差分：白名单外任何字节变化都算失败。

    做法：先把白名单区间合并，然后按块比对；块内不一致时若该块整体落在
    白名单内就放行，否则逐字节定位真实越界区间。这样既快又能报准位置。
    """
    if os.path.getsize(src) != os.path.getsize(dst):
        return {"ok": False, "error": "文件大小不同", "outside": 0,
                "allowed": 0, "ranges": [], "bad": []}

    allow: List[Tuple[int, int]] = sorted(b.region for b in builds.values())
    merged: List[List[int]] = []
    for lo, hi in allow:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])

    def covered(lo: int, hi: int) -> bool:
        """[lo,hi) 是否被某个白名单区间完整覆盖。"""
        for a, b in merged:
            if lo >= a and hi <= b:
                return True
        return False

    total = os.path.getsize(src)
    outside = 0
    allowed_chunks = 0
    bad_ranges: List[Tuple[int, int]] = []

    def read_exact(fh, n: int) -> bytes:
        """读满 n 字节。

        大文件在低速/高负载盘上会出现短读（``read`` 返回不足 n 字节）。
        不读满就会让两份缓冲区长度不等，被误判成「白名单外有字节变化」——
        实测踩过一次：报 1 字节差异，位置落在资源区间之外，隔离重跑却全部干净。
        """
        buf = bytearray()
        while len(buf) < n:
            got = fh.read(n - len(buf))
            if not got:
                break
            buf += got
        return bytes(buf)

    with open(src, "rb") as fs, open(dst, "rb") as fd:
        pos = 0
        while pos < total:
            n = min(chunk, total - pos)
            a = read_exact(fs, n)
            b = read_exact(fd, n)
            base = pos
            pos += n
            if len(a) != n or len(b) != n:
                # 读不满就是证据不足，直接判失败（宁可拒绝，不许放行）
                return {"ok": False,
                        "error": "差分读取不足：期望 %d 字节，实际 src=%d dst=%d"
                                 % (n, len(a), len(b)),
                        "outside": 0, "allowed": allowed_chunks,
                        "ranges": [], "bad": []}
            if a == b:
                continue
            if covered(base, base + n):
                allowed_chunks += 1
                continue
            i = 0
            while i < n:
                if a[i] == b[i]:
                    i += 1
                    continue
                j = i
                while j < n and a[j] != b[j]:
                    j += 1
                lo, hi = base + i, base + j
                if covered(lo, hi):
                    allowed_chunks += 1
                else:
                    outside += hi - lo
                    bad_ranges.append((lo, hi))
                i = j
    return {"ok": outside == 0, "outside": outside,
            "allowed": allowed_chunks,
            "ranges": [{"lo": lo, "hi": hi} for lo, hi in merged],
            "bad": [{"lo": lo, "hi": hi, "hex": "0x%X" % lo}
                    for lo, hi in bad_ranges[:20]]}


def describe_allow_ranges(builds: Dict[int, BuildResult]) -> List[dict]:
    out = []
    for eid in sorted(builds):
        b = builds[eid]
        lo, hi = b.region
        out.append({"eid": eid, "lo": lo, "hi": hi, "size": hi - lo,
                    "file_off_hex": "0x%X" % lo, "compressed": b.compressed,
                    "edits": len(b.edits), "notes": b.notes})
    return out
