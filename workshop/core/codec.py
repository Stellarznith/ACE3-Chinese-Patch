"""ACE3 文本编解码（权威码表 + 受保护标签解析）。

设计口径（与冻结工具 locator_core.decode_seg 完全一致，绝不用 GBK/CP932 兜底）：

* ``<...>`` 形式的控制标签整体视为「受保护片段」，不参与正文字符统计，
  编辑时以结构化 token 呈现，普通输入无法破坏。
* 单字节 < 0x80：直转（``\\n`` -> 0x0A）。
* 单字节 0xA1..0xDF：半角片假名 U+FF61+。
* 前导字节 0x81..0x9F / 0xE0..0xFC：双字节游戏码位，查权威码表。
* 查不到的码位标记为未知，不猜测。

编码方向：
* ASCII 可打印 -> 单字节。
* 半角片假名 -> 单字节。
* 空格 -> 优先沿用「该槽原码位」里出现过的空格码，否则 0x8140。
* 其余 -> 查 char2codes，优先原槽用过的码位，其次最小码位。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from . import env

TAG_RE = re.compile(rb"<[^<>]{0,96}>")          # bytes 侧（解析原始槽）
TAG_RE_S = re.compile(r"<[^<>]{0,96}>")          # str 侧（用户输入/展示）
TAG_SPLIT_RE = re.compile(r"(<[^<>]{0,96}>)")

# 常用空格码位（都在字体并集内；0x8141 是「、」不要当空格）
SPACE_CODES = (0x8140, 0x816D, 0x20)
DEFAULT_SPACE_CODE = 0x8140


def is_lead(c: int) -> bool:
    return 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC


def is_kana_byte(c: int) -> bool:
    return 0xA1 <= c <= 0xDF


# ---------------------------------------------------------------- 码表

# ---------------------------------------------------------------- 码位补丁
# 来源：`翻译\字库可用字表.tsv` 里标注「R223验证(盘上投票)」的 1,284 行，
# 逐条与 `_r211_cmap.code2char()` 比对后，有 56 个码位冻结码表里**没有**。
# 其中 40 条被拒收（字形证据 <3 个包，或码位已被别的字占用）。
# 下面 16 条收下，判据是「该码位在冻结码表里是空的」+「字库缓存里确实有字形」。
#
# ★ 为什么必须补：VMD 名字块（CLM/FNM，武器名/指令名/机体名）用的就是这套
#   游戏码位。缺 0x938F/0x9390 就解不出「冥墓方舟」，编辑时也会误判成 BLOCK。
#
# 证据分级（字库缓存 `font_cache.sqlite` 的 cov，page=1 valid=1 的包数）：
#   A 类 ≥12 包：可直接用，实机风险低
#   B 类 3..11 包：能用，但要提示「部分字体包缺字」
# 注意 0x938F(冥) 恰好缺 1200011 包，其余 14 包都有。
CODE_PATCHES: Dict[int, str] = {
    # ---- A 类：≥12 个包有字形
    0x8156: "偷", 0x819C: "哇", 0x819F: "链",
    0x8286: "ｆ", 0x8288: "ｈ", 0x828D: "ｍ", 0x828F: "ｏ", 0x8292: "ｒ",
    0x938D: "鲜", 0x938E: "铳", 0x938F: "冥", 0x9390: "墓",
    # ---- B 类：3..11 个包，补但标 WARN
    0x8161: "弯", 0x8173: "澳", 0x92C2: "吒", 0x92F6: "吊",
}

# 补丁码位的字库证据（包数）。GUI 用它决定 PASS / WARN。
PATCH_PKGS: Dict[int, int] = {
    0x8156: 14, 0x819C: 15, 0x819F: 15,
    0x8286: 15, 0x8288: 15, 0x828D: 15, 0x828F: 15, 0x8292: 15,
    0x938D: 15, 0x938E: 15, 0x938F: 14, 0x9390: 15,
    0x8161: 11, 0x8173: 11, 0x92C2: 8, 0x92F6: 3,
}

PATCH_A_MIN = 12          # ≥ 这个包数算 A 类


def patch_note(code: int) -> str:
    """补丁码位的说明（空串表示不是补丁）。"""
    ch = CODE_PATCHES.get(code)
    if ch is None:
        return ""
    n = PATCH_PKGS.get(code, 0)
    tier = "A" if n >= PATCH_A_MIN else "B"
    return ("补登记码位 0x%04X → %s（%s 类，%d/15 个字体包有字形；"
            "冻结码表原先没有这条，来源《字库可用字表》R223 盘上投票）"
            % (code, ch, tier, n))


class CodeTable:
    """权威字符 <-> 游戏码位映射（来自 _r211_cmap，只读复用 + 16 条补登记）。"""

    __slots__ = ("code2char", "char2codes", "_note")

    def __init__(self) -> None:
        env.ensure_sys_path()
        from _r211_cmap import code2char, char2codes  # 延迟导入，冻结工具

        self.code2char: Dict[int, str] = dict(code2char())
        self.char2codes: Dict[str, Tuple[int, ...]] = {
            ch: tuple(sorted(codes)) for ch, codes in char2codes().items()
        }
        base_codes = len(self.code2char)
        # ★ 补登记：只补「码位是空的」那些，绝不覆盖冻结码表已有的映射。
        #   已有别的码位的字（如 ｆ 原本是 0x9271），把补丁码位**追加到末尾**，
        #   这样 pick_code() 仍优先返回原来的码位，老文本编码结果一个字节都不变。
        added = 0
        for code, ch in sorted(CODE_PATCHES.items()):
            if code in self.code2char:
                continue
            self.code2char[code] = ch
            old = self.char2codes.get(ch, ())
            if code not in old:
                self.char2codes[ch] = old + (code,)
            added += 1
        self._note = "权威码表 %d 码位 / %d 字（含补登记 %d 条）" % (
            len(self.code2char),
            len(self.char2codes),
            added,
        )
        self._note += "｜冻结原表 %d 码位" % base_codes

    @property
    def note(self) -> str:
        return self._note

    def codes_of(self, ch: str) -> Tuple[int, ...]:
        return self.char2codes.get(ch, ())

    def char_of(self, code: int) -> Optional[str]:
        return self.code2char.get(code)

    def has_char(self, ch: str) -> bool:
        if ord(ch) < 0x80:
            return True
        if is_kana_byte(ord(ch) - 0xFF61 + 0xA1) and 0xFF61 <= ord(ch) <= 0xFF9F:
            return True
        return bool(self.codes_of(ch))

    def pick_code(self, ch: str, preferred: Sequence[int] = ()) -> Optional[int]:
        """为一个字挑码位：优先原槽用过的，其次最小码。"""
        codes = self.codes_of(ch)
        if not codes:
            return None
        for p in preferred:
            if p in codes:
                return p
        return codes[0]


_TABLE: Optional[CodeTable] = None


def table() -> CodeTable:
    global _TABLE
    if _TABLE is None:
        _TABLE = CodeTable()
    return _TABLE


# ---------------------------------------------------------------- 段解析

class Segment:
    """一个文本槽的解析结果：原始字节 + 标签/正文交替序列。"""

    __slots__ = ("raw", "tokens", "plain", "codes", "unknown", "ctrl_bytes")

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.tokens: List[Tuple[str, object]] = []   # ("tag", bytes) / ("text", str)
        self.plain = ""
        self.codes: List[int] = []
        self.unknown: List[int] = []
        self.ctrl_bytes: List[int] = []
        self._parse()

    def _parse(self) -> None:
        c2c = table().code2char
        raw = self.raw
        pos = 0
        plain_parts: List[str] = []
        n = len(raw)
        while pos < n:
            if raw[pos : pos + 1] == b"<":
                m = TAG_RE.match(raw, pos)
                if m:
                    self.tokens.append(("tag", m.group(0)))
                    pos = m.end()
                    continue
            # 正文：一段连续到下一个 '<标签>' 之前
            nxt = raw.find(b"<", pos)
            if nxt < 0:
                nxt = n
            else:
                m = TAG_RE.match(raw, nxt)
                if not m:
                    nxt = n
            chunk = raw[pos:nxt]
            text, codes, unk, ctrl = decode_bytes(chunk, c2c)
            if chunk:
                self.tokens.append(("text", text))
            plain_parts.append(text)
            self.codes.extend(codes)
            self.unknown.extend(unk)
            self.ctrl_bytes.extend(ctrl)
            pos = nxt
        self.plain = "".join(plain_parts).rstrip("\x00")

    # -- 便捷属性 -------------------------------------------------
    @property
    def tags(self) -> List[bytes]:
        return [t[1] for t in self.tokens if t[0] == "tag"]

    @property
    def tag_names(self) -> List[str]:
        out = []
        for t in self.tags:
            s = t.decode("latin-1")
            out.append(s[1 : s.find("(")] if "(" in s else s.strip("<>"))
        return out

    def plain_with_tags(self) -> str:
        return "".join(
            t[1].decode("latin-1") if t[0] == "tag" else t[1] for t in self.tokens
        )


def decode_bytes(
    chunk: bytes, c2c: Optional[Dict[int, str]] = None
) -> Tuple[str, List[int], List[int], List[int]]:
    """字节 -> (可读文本, 双字节码位列表, 未知码位, 控制字节)。"""
    if c2c is None:
        c2c = table().code2char
    out: List[str] = []
    codes: List[int] = []
    unk: List[int] = []
    ctrl: List[int] = []
    i = 0
    n = len(chunk)
    while i < n:
        c = chunk[i]
        if c == 0:
            break
        if c < 0x80:
            if 0x00 < c < 0x20 and c not in (0x0A, 0x09):
                ctrl.append(c)
                out.append(chr(c))
            else:
                out.append(chr(c))
            i += 1
            continue
        if is_kana_byte(c):
            out.append(chr(0xFF61 + c - 0xA1))
            i += 1
            continue
        if is_lead(c) and i + 1 < n:
            code = (c << 8) | chunk[i + 1]
            codes.append(code)
            ch = c2c.get(code)
            if ch is None:
                unk.append(code)
                out.append("\u3016%04X\u3017" % code)   # 〔XXXX〕占位
            else:
                out.append(ch)
            i += 2
            continue
        # 孤立高位字节
        unk.append(c)
        out.append("\u3016%02X\u3017" % c)
        i += 1
    return "".join(out), codes, unk, ctrl


# ---------------------------------------------------------------- 编码

class EncodeResult:
    __slots__ = ("data", "missing", "used_codes", "notes")

    def __init__(self) -> None:
        self.data = b""
        self.missing: List[str] = []
        self.used_codes: Dict[str, int] = {}
        self.notes: List[str] = []


def encode_text(text: str, preferred_codes: Sequence[int] = ()) -> EncodeResult:
    """正文字符串 -> 游戏字节。缺码位的字符进 missing，不猜、不代替。"""
    res = EncodeResult()
    tb = table()
    out = bytearray()
    for ch in text:
        if ch == "\n":
            out.append(0x0A)
            continue
        if ch == "\r":
            continue
        if ch == "\t":
            out.append(0x09)
            continue
        o = ord(ch)
        if o < 0x80:
            out.append(o)
            res.used_codes[ch] = o
            continue
        if 0xFF61 <= o <= 0xFF9F:
            out.append(o - 0xFF61 + 0xA1)
            res.used_codes[ch] = o - 0xFF61 + 0xA1
            continue
        if ch == " ":
            code = DEFAULT_SPACE_CODE
            for p in preferred_codes:
                if p in SPACE_CODES:
                    code = p
                    break
            out.extend(code.to_bytes(2, "big"))
            res.used_codes[ch] = code
            continue
        code = tb.pick_code(ch, preferred_codes)
        if code is None:
            # 全角空格等仍按空格处理
            if ch in ("\u3000",):
                code = DEFAULT_SPACE_CODE
                for p in preferred_codes:
                    if p in SPACE_CODES:
                        code = p
                        break
                out.extend(code.to_bytes(2, "big"))
                res.used_codes[ch] = code
                continue
            res.missing.append(ch)
            continue
        out.extend(code.to_bytes(2, "big"))
        res.used_codes[ch] = code
    res.data = bytes(out)
    return res


def encode_display_string(seg: Segment, new_plain: str) -> EncodeResult:
    """按原槽结构重建：标签原样保留，只替换正文段。"""
    return encode_text(new_plain, seg.codes)


def rebuild_with_plain(seg: Segment, new_plain: str) -> Tuple[bytes, EncodeResult]:
    """把 Segment 的正文替换为 new_plain，标签顺序与字节完全保留。

    原槽只有一个正文段时直接替换；有多个正文段时按「先到先得」顺序分配，
    多出来的正文并入最后一段（并在 notes 里说明）。
    """
    res_all = EncodeResult()
    text_idx = 0
    text_positions = [i for i, t in enumerate(seg.tokens) if t[0] == "text"]
    pieces = split_plain_for_slots(seg, new_plain)
    out = bytearray()
    for i, (kind, val) in enumerate(seg.tokens):
        if kind == "tag":
            out.extend(val)  # type: ignore[arg-type]
            continue
        piece = pieces[text_idx] if text_idx < len(pieces) else ""
        text_idx += 1
        r = encode_text(piece, seg.codes)
        res_all.missing.extend(r.missing)
        res_all.used_codes.update(r.used_codes)
        out.extend(r.data)
    if len(pieces) > len(text_positions):
        res_all.notes.append("正文段数不足，多余内容已并回最后一段")
    res_all.data = bytes(out)
    return res_all.data, res_all


def split_plain_for_slots(seg: Segment, new_plain: str) -> List[str]:
    """按原槽正文段的数量切分新文本。

    单个正文段（绝大多数）→ 整串给它。
    多个正文段 → 按原各段长度比例保留切点（尽量不改变排版），
    若原段长度信息不足则全部给第一段。
    """
    positions = [i for i, t in enumerate(seg.tokens) if t[0] == "text"]
    if len(positions) <= 1:
        return [new_plain]
    lens = [len(seg.tokens[i][1]) for i in positions]  # type: ignore[arg-type]
    total = sum(lens) or 1
    out: List[str] = []
    consumed = 0
    for k, ln in enumerate(lens):
        if k == len(lens) - 1:
            out.append(new_plain[consumed:])
            break
        take = round(len(new_plain) * ln / total)
        take = max(0, min(take, len(new_plain) - consumed))
        out.append(new_plain[consumed : consumed + take])
        consumed += take
    return out


# ---------------------------------------------------------------- 标签

def tag_name(tag: bytes) -> str:
    s = tag.decode("latin-1")
    if s.startswith("<") and s.endswith(">"):
        s = s[1:-1]
    return s.split("(")[0]


def tag_args(tag: bytes) -> List[str]:
    s = tag.decode("latin-1")
    a = s.find("(")
    b = s.rfind(")")
    if a < 0 or b <= a:
        return []
    return [x.strip() for x in s[a + 1 : b].split(",")]


def tag_signature(tags: Sequence[bytes]) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    """标签序列指纹，用于写盘前后「控制标签完整性」比对。"""
    return tuple((tag_name(t), tuple(tag_args(t))) for t in tags)


def diff_chars(old: str, new: str) -> List[Tuple[str, str, str]]:
    """逐字符差异（简易 LCS 对齐），返回 [(op, a, b)]，op ∈ '=' '~' '+' '-'。"""
    import difflib

    sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    out: List[Tuple[str, str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                out.append(("=", old[i1 + k], new[j1 + k]))
        elif tag == "replace":
            a = old[i1:i2]
            b = new[j1:j2]
            for k in range(max(len(a), len(b))):
                out.append(("~", a[k] if k < len(a) else "", b[k] if k < len(b) else ""))
        elif tag == "delete":
            for ch in old[i1:i2]:
                out.append(("-", ch, ""))
        elif tag == "insert":
            for ch in new[j1:j2]:
                out.append(("+", "", ch))
    return out
