"""15 包 FontMap 字形覆盖诊断（PASS / WARN / BLOCK + 逐包证据）。

口径来源（全部照抄工程已验收工具，不自创）：

* 权威包列表 = ``locator_core.FONT_PKGS``（15 个），见 ``core.env.FONT_PKGS``。
* 覆盖数据 = ``font_cache.sqlite`` 的 ``cov`` 表，一行 = (code, pkg, page)。
  同一个 code 在同一 pkg 下可能有 page=1（主图集 1024×512）与 page=2
  （256×256 小图集）两行，**任一行 valid=1 即认为该包有该字的有效字形**
  （与 ``ace3_text_locator/coverage.py`` 的 ``COUNT(DISTINCT pkg) WHERE valid=1`` 一致）。
* ``valid`` 由建缓存时按「record 有效 + 墨迹 > 0 + 尺寸合理」算出；
  ``ink`` 是墨迹像素数，``rec`` 是 record 下标，``w``/``h`` 是字框，``t0..t3`` 是
  四条 record 度量（本例全部 0/17/18/0 形态）。
* ``1200002`` / ``1200003`` 是两张 512×512 小图集，设计上只装约 1814 码位；
  **只缺这两包不算缺字**（``coverage.py:128`` 的 ``SMALL`` 口径）。

★ 极其重要（工程纪律）：**某个槽在实机上究竟由哪个包渲染，恒为 UNKNOWN**。
  所以任何 PASS 都只表示「静态覆盖充分」，不表示实机一定显示正确；
  相反，只要不是 15 包齐备，就必须给 WARN 并明确写出缺哪些包，不许假装确定。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Dict, List, Optional, Sequence, Tuple

from . import env

# ------------------------------------------------------------------ 结论常量
PASS = "PASS"
WARN = "WARN"
BLOCK = "BLOCK"

_EVIDENCE = {
    PASS: "T2 工具产物（font_cache.sqlite 静态覆盖，15 包齐备）",
    WARN: "T2 工具产物（font_cache.sqlite 静态覆盖，存在缺包/未知项）",
    BLOCK: "T2 工具产物（font_cache.sqlite / 工程码表：无合法字形路径）",
}

# ------------------------------------------------------------------ 默认码表
# ``diagnose(char, codes_of=None)`` 必须能自己找到候选码位，否则所有中文都会被
# 判成「无合法游戏码位」→ 全体 BLOCK（本班踩过：writer.check 没传 codes_of，
# 结果连「何？」都被阻断）。这里回落到 ``core.codec.CodeTable.char2codes``，
# 与检索层 ``_r211_cmap.char2codes`` 同一来源，不自建第二份码表。
_DEFAULT_COT = None


def _default_codes_of(char: str) -> Sequence[int]:
    """惰性取工程码表的反相查表函数。"""
    global _DEFAULT_COT
    if _DEFAULT_COT is None:
        from . import codec
        _DEFAULT_COT = codec.table().codes_of
    return _DEFAULT_COT(char)


def _as_pref_map(preferred) -> Dict[str, int]:
    """把「原槽码位」的两种传法统一成 ``{字: 码位}``。

    * ``None`` → ``{}``
    * ``{字: 码位}`` → 原样（浅拷贝）
    * ``[码位, …]`` → 逐码位查字建表；同字多码位取**先出现**的那个
      （调用方传进来的顺序就是原槽书写顺序）
    """
    if not preferred:
        return {}
    if isinstance(preferred, dict):
        return dict(preferred)
    out: Dict[str, int] = {}
    from . import codec
    tbl = codec.table()
    for code in preferred:
        try:
            ch = tbl.char_of(int(code))
        except Exception:                        # noqa: BLE001
            continue
        if ch and ch not in out:
            out[ch] = int(code)
    return out


class GlyphRow:
    """chard 在某个包、某一页上的记录。"""

    __slots__ = ("pkg", "page", "rec", "ink", "w", "h", "tail", "valid")

    def __init__(self, pkg: int, page: int, rec: Optional[int], ink: int,
                 w: int, h: int, tail: Sequence[int], valid: int) -> None:
        self.pkg = pkg
        self.page = page
        self.rec = rec
        self.ink = ink
        self.w = w
        self.h = h
        self.tail = tuple(tail)
        self.valid = valid

    def as_dict(self) -> dict:
        return {"pkg": self.pkg, "page": self.page, "rec": self.rec,
                "ink": self.ink, "w": self.w, "h": self.h,
                "tail": list(self.tail), "valid": self.valid}


class CharVerdict:
    """一个字符的完整诊断结论。"""

    __slots__ = ("char", "code", "state", "reason", "have", "missing",
                 "rows", "pkg_count", "notes", "evidence", "path")

    def __init__(self, char: str) -> None:
        self.char = char
        self.code: Optional[int] = None
        self.state: str = BLOCK
        self.reason: str = ""
        self.have: List[int] = []
        self.missing: List[int] = []
        self.rows: List[GlyphRow] = []
        self.pkg_count: int = 0
        self.notes: List[str] = []
        self.evidence: str = ""
        # 编码路径：'dbcs' 双字节码位 / 'ascii' 单字节直写 / 'kana' 半角假名
        self.path: str = ""

    @property
    def ok(self) -> bool:
        return self.state == PASS

    @property
    def blocks_write(self) -> bool:
        return self.state == BLOCK

    def short(self) -> str:
        """一行摘要，给队列 / 状态栏用。"""
        if self.state == PASS:
            if self.path == "blank":
                return "%s PASS（空白）" % self.char
            return "%s PASS（%d/15 包）" % (self.char, self.pkg_count)
        if self.state == WARN:
            if self.path in ("ascii", "kana"):
                return "%s WARN（单字节路径）" % self.char
            return "%s WARN（%d/15 包，缺 %d）" % (self.char, self.pkg_count,
                                                 len(self.missing))
        return "%s BLOCK（%s）" % (self.char, self.reason)

    def as_dict(self) -> dict:
        return {"char": self.char,
                "code": None if self.code is None else "0x%04X" % self.code,
                "path": self.path,
                "state": self.state, "reason": self.reason,
                "pkg_count": self.pkg_count,
                "have": self.have, "missing": self.missing,
                "notes": self.notes, "evidence": self.evidence,
                "rows": [r.as_dict() for r in self.rows]}


class FontCoverage:
    """覆盖库的内存索引。构造一次，之后 per-char 查询是纯字典查找。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or env.FONT_CACHE
        self._by_code: Dict[int, Dict[int, List[GlyphRow]]] = {}
        self._pkg_pages: Dict[int, int] = {}
        self.pkg_meta: Dict[int, dict] = {}
        self._lock = threading.Lock()
        self.loaded = False
        self.note = ""

    # -- 装载 ------------------------------------------------------
    def load(self) -> "FontCoverage":
        if self.loaded:
            return self
        if not os.path.exists(self.db_path):
            self.note = "覆盖缓存不存在：%s" % self.db_path
            self.loaded = True
            return self
        uri = "file:%s?mode=ro" % self.db_path.replace("\\", "/")
        conn = sqlite3.connect(uri, uri=True)
        try:
            cur = conn.execute(
                "SELECT code,pkg,page,rec,ink,w,h,t0,t1,t2,t3,valid FROM cov")
            by = self._by_code
            for (code, pkg, page, rec, ink, w, h, t0, t1, t2, t3, valid) in cur:
                by.setdefault(code, {}).setdefault(pkg, []).append(
                    GlyphRow(pkg, page, rec, ink or 0, w or 0, h or 0,
                             (t0, t1, t2, t3), valid or 0))
            for (pkg, iso_md5, blob_md5, font1, font2, note, built_at) in conn.execute(
                    "SELECT pkg,iso_md5,blob_md5,font1,font2,note,built_at "
                    "FROM pkg_meta"):
                self.pkg_meta[pkg] = {"iso_md5": iso_md5, "blob_md5": blob_md5,
                                      "font1": font1, "font2": font2,
                                      "note": note, "built_at": built_at}
        finally:
            conn.close()
        self.loaded = True
        return self

    def _ensure(self) -> None:
        if not self.loaded:
            with self._lock:
                if not self.loaded:
                    self.load()

    # -- 查询 ------------------------------------------------------
    def rows_of(self, code: int) -> Dict[int, List[GlyphRow]]:
        self._ensure()
        return self._by_code.get(code, {})

    def pkgs_with_glyph(self, code: int) -> List[int]:
        """该码位在哪些包里有 valid=1 的任意一页。"""
        out = []
        for pkg, rows in self.rows_of(code).items():
            if any(r.valid for r in rows):
                out.append(pkg)
        return sorted(out)

    def diagnose(self, char: str, code: Optional[int] = None,
                 codes_of=None) -> CharVerdict:
        """判定单个字符。

        三条编码路径（实测自 ``font_cache.sqlite``，不是推测）：

        * ``dbcs``：自定义双字节码位（0x81..0x9F / 0xE0..0xFC 打头），
          查工程码表 → 查 ``cov``。这是中文正文的主路径。
        * ``ascii``：可打印 ASCII 单字节直写。``cov`` 里以**字节值本身**
          （0x20..0x7E）为 ``code`` 收录，且并非全收录（实测缺 ``Q``/``.``/``?``
          等 12 个），所以按查到的结果给 PASS/WARN，永不 BLOCK。
        * ``kana``：半角片假名单字节（0xA1..0xDF 直写）。
          ``cov`` 里 **0xA1..0xDF 与 0xFF61..0xFF9F 都没有任何记录**，
          说明它们不在 FontMap 图集里、由别的渲染路径处理 ⇒ 一律 WARN。

        ``code`` 显式给出时按该码位判；``codes_of(char)`` 给候选双字节码位。
        """
        self._ensure()
        v = CharVerdict(char)
        if char.isspace():
            v.state = PASS
            v.path = "blank"
            v.reason = "空白字符"
            v.notes.append("空白不参与字形判定")
            v.evidence = _EVIDENCE[PASS]
            return v

        if code is not None:
            return self._judge_code(v, code)

        # 单字节路径先判，避免把它们误当"无码位"
        o = ord(char)
        if 0xFF61 <= o <= 0xFF9F:
            # Unicode 半角片假名 → 游戏单字节 0xA1..0xDF
            return self._judge_kana(v, o - 0xFF61 + 0xA1)
        if o < 0x80:
            return self._judge_ascii(v, o)

        cands = list((codes_of or _default_codes_of)(char) or ())
        if not cands:
            v.state = BLOCK
            v.path = "none"
            v.reason = "无合法游戏码位"
            v.notes.append("工程码表（_r211_cmap）里没有该字符；禁止用 GBK/CP932 顶替")
            v.evidence = _EVIDENCE[BLOCK]
            return v

        best: Optional[Tuple[int, List[int], List[GlyphRow]]] = None
        tried = []
        for c in cands:
            have = self.pkgs_with_glyph(c)
            rows = [r for rs in self.rows_of(c).values() for r in rs]
            tried.append((c, len(have)))
            if best is None or len(have) > len(best[1]):
                best = (c, have, rows)

        assert best is not None
        c, have, rows = best
        if len(cands) > 1:
            v.notes.append("该字有 %d 个码位，已选覆盖最好的 0x%04X；全部候选：%s"
                           % (len(cands), c,
                              "、".join("0x%04X(%d包)" % (cc, n) for cc, n in tried)))
        return self._finish(v, "dbcs", c, have, rows)

    # -- 三条路径 --------------------------------------------------
    def _judge_code(self, v: CharVerdict, code: int) -> CharVerdict:
        have = self.pkgs_with_glyph(code)
        rows = [r for rs in self.rows_of(code).values() for r in rs]
        if code < 0x80:
            path = "ascii"
        elif 0xA1 <= code <= 0xDF:
            path = "kana"
        else:
            path = "dbcs"
        return self._finish(v, path, code, have, rows)

    def _judge_ascii(self, v: CharVerdict, o: int) -> CharVerdict:
        if not (0x20 <= o <= 0x7E):
            v.state = WARN
            v.path = "ascii"
            v.code = o
            v.reason = "控制字符，单字节直写"
            v.notes.append("不在可打印 ASCII 范围，可能影响文本解析")
            v.evidence = _EVIDENCE[WARN]
            return v
        have = self.pkgs_with_glyph(o)
        rows = [r for rs in self.rows_of(o).values() for r in rs]
        if not rows:
            v.state = WARN
            v.path = "ascii"
            v.code = o
            v.reason = "ASCII 单字节直写；FontMap 未收录该码位"
            v.notes.append("cov 表里没有 code=0x%02X 的记录（实测多个符号与 Q/V 等字母都缺）" % o)
            v.notes.append("单字节直写仍有合法编码，实机走内建字模，渲染路径 UNKNOWN")
            v.evidence = _EVIDENCE[WARN]
            return v
        return self._finish(v, "ascii", o, have, rows)

    def _judge_kana(self, v: CharVerdict, o: int) -> CharVerdict:
        v.state = WARN
        v.path = "kana"
        v.code = o
        v.reason = "半角片假名单字节（FontMap 无收录）"
        v.notes.append("cov 表里 0xA1..0xDF 与 0xFF61..0xFF9F 均无任何记录")
        v.notes.append("全库倒排显示这类字节真实用量仅 5 次；渲染路径 UNKNOWN")
        v.evidence = _EVIDENCE[WARN]
        return v

    def _finish(self, v: CharVerdict, path: str, code: int,
                have: List[int], rows: List[GlyphRow]) -> CharVerdict:
        v.path = path
        v.code = code
        v.have = have
        v.rows = rows
        v.pkg_count = len(have)
        v.missing = [p for p in env.FONT_PKGS if p not in have]

        if not have:
            v.state = BLOCK
            v.reason = "15 包均无有效字形"
            v.notes.append("码位 0x%04X 在 15 个包中没有任何 valid=1 的记录" % code)
            v.evidence = _EVIDENCE[BLOCK]
            return v

        if not v.missing:
            v.state = PASS
            v.reason = "15 包齐备"
            v.notes.append("实机由哪个包渲染仍为 UNKNOWN；PASS 仅代表静态覆盖充分")
            v.evidence = _EVIDENCE[PASS]
            return v

        if set(v.missing) == set(env.SMALL_PKGS):
            v.state = PASS
            v.reason = "仅缺两张 512×512 小图集（设计如此）"
            v.notes.append("1200002/1200003 本来就只装约 1814 码位，不算缺字")
            v.notes.append("实机渲染包 UNKNOWN")
            v.evidence = _EVIDENCE[PASS]
            return v

        v.state = WARN
        if len(have) <= 2:
            v.reason = "仅 %d 个包有字形（高度可疑）" % len(have)
        else:
            v.reason = "%d/15 包有字形，缺 %d 包" % (len(have), len(v.missing))
        v.notes.append("缺字包：%s" % "、".join(str(p) for p in v.missing))
        v.notes.append("实机渲染包 UNKNOWN，无法排除实机缺字风险")
        v.evidence = _EVIDENCE[WARN]
        return v

    def diagnose_text(self, text: str, codes_of=None,
                      preferred=None
                      ) -> Tuple[str, List[CharVerdict], List[str]]:
        """整段文本判定。

        ``preferred`` 可传两种形态（都表示"该槽原本用的码位"）：

        * ``Dict[str, int]``：字 → 码位；
        * ``Sequence[int]``：该槽现有的码位列表（多码位字优先沿用原槽那一个）。

        返回 ``(总状态, 逐字结论(去重), 提示)``；总状态取最坏一级。
        换行/制表/回车不参与判定。
        """
        pref = _as_pref_map(preferred)
        seen: Dict[str, CharVerdict] = {}
        notes: List[str] = []
        order = []
        for ch in text:
            if ch in "\n\r\t":
                continue
            if ch in seen:
                continue
            v = self.diagnose(ch, code=pref.get(ch), codes_of=codes_of)
            seen[ch] = v
            order.append(v)

        worst = PASS
        for v in order:
            if v.state == BLOCK:
                worst = BLOCK
                break
            if v.state == WARN:
                worst = WARN
        blocks = [v.char for v in order if v.state == BLOCK]
        warns = [v.char for v in order if v.state == WARN]
        if blocks:
            notes.append("阻断字符：%s" % "".join(blocks))
        if warns:
            notes.append("需留意字符：%s" % "".join(warns))
        return worst, order, notes


_INSTANCE: Optional[FontCoverage] = None
_INST_LOCK = threading.Lock()


def coverage(db_path: Optional[str] = None) -> FontCoverage:
    """进程内单例。"""
    global _INSTANCE
    if _INSTANCE is None:
        with _INST_LOCK:
            if _INSTANCE is None:
                _INSTANCE = FontCoverage(db_path).load()
    return _INSTANCE
