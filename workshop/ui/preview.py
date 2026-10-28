"""ACE3 对话框实时预览（近似）。

定位
----
* **辅助**：用真实字形度量（``font_cache.sqlite`` 的 w/h）估算每字推进宽度，
  按游戏对话框的「每行 39 个全角字」换算，提示可能的溢出、裁切与自动换行。
* **不是 PS2 渲染模拟器**：不做像素级一致保证，也不假装知道实机由哪个字体包渲染。
  界面上必须写明这一点。
* 支持的视觉要素：正常文字、``<color(N)>`` 颜色、``<book(N)>…<endbook()>``
  术语下划线、``<sp(N)>``/``<ab(N)`` 等待机间隔（按停顿像素近似）、手动换行。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QPainter,
                           QPen, QTextLayout, QTextOption)
from PySide6.QtWidgets import QSizePolicy, QWidget

from core import codec, fontcov

# 游戏对话框实测：一行标准宽度约 39 个全角字（eid=701009 压缩台词表实证）
LINE_EM = 39.0

# <color(N)> 的 N → 颜色（N 来自原文，取常见 4 档 + 兜底）
COLOR_PALETTE = {
    0: "#dfe6f0",   # 默认白
    1: "#e3e3e3",
    2: "#8ec7f0",   # 蓝
    3: "#f0c674",   # 金
    4: "#e0736a",   # 红
    5: "#8fe08a",   # 绿
    6: "#c9a0f0",   # 紫
    7: "#f0a0c0",   # 粉
}

# 未知码位按全角算；这些 ASCII 类按半角算
_HALF_ASCII = set(" .,:;'\"!|/\\()[]{}<>-_=+*&^%$#@~`")


class _Metrics:
    """字符推进宽度（单位：全角 em）。"""

    _cache: Dict[str, float] = {}

    @classmethod
    def of(cls, ch: str) -> float:
        v = cls._cache.get(ch)
        if v is not None:
            return v
        v = cls._compute(ch)
        cls._cache[ch] = v
        return v

    @classmethod
    def _compute(cls, ch: str) -> float:
        c2c = codec.table()
        codes = c2c.codes_of(ch)
        cov = fontcov.coverage()
        best = 0.0
        for code in codes:
            for rows in (cov.rows_of(code) or {}).values():
                for row in rows:
                    if row.valid and row.page == 1 and row.w:
                        best = max(best, float(row.w))
        if best:
            # 全角汉字实测 w=17 → 1.0 em
            return max(0.30, best / 17.0)
        # 无字形记录：按字符类别给保守值
        if ord(ch) < 0x80:
            return 0.52
        if 0xFF61 <= ord(ch) <= 0xFF9F:
            return 0.55
        return 1.0


def text_em(text: str) -> float:
    return sum(_Metrics.of(c) for c in text)


class Run:
    """一段同色同装饰的显示文本。"""

    __slots__ = ("text", "color", "book", "em_before")

    def __init__(self, text: str, color: str, book: bool) -> None:
        self.text = text
        self.color = color
        self.book = book
        self.em_before = 0.0


def parse_runs(payload: bytes) -> List[Run]:
    """把槽字节解析成显示分段（保留颜色与 book 状态）。"""
    seg = codec.Segment(payload)
    runs: List[Run] = []
    color_idx = 0
    book = False
    for kind, val in seg.tokens:
        if kind == "tag":
            name = codec.tag_name(val)
            args = codec.tag_args(val)
            if name == "color":
                try:
                    color_idx = int(args[0]) if args else 0
                except (TypeError, ValueError):
                    color_idx = 0
            elif name in ("ce", "cc"):
                color_idx = 0
            elif name == "book":
                book = True
            elif name == "endbook":
                book = False
            elif name == "sp":
                # <sp(N)> 是等待，不是空格；仅留 0 宽度
                pass
            continue
        if not val:
            continue
        runs.append(Run(val, COLOR_PALETTE.get(color_idx, COLOR_PALETTE[0]), book))
    return runs


def layout_lines(runs: List[Run], max_em: float = LINE_EM) -> List[List[tuple]]:
    """按「最大全角宽」折行，返回 ``[[(run, 片段), …], …]``。

    手动换行（``\\n``）优先；超宽自动折行并在末尾标记。
    """
    lines: List[List[tuple]] = [[]]
    used = 0.0
    for run in runs:
        buf = ""
        for ch in run.text:
            if ch == "\n":
                if buf:
                    lines[-1].append((run, buf))
                    buf = ""
                lines.append([])
                used = 0.0
                continue
            w = _Metrics.of(ch)
            if used + w > max_em + 1e-6:
                if buf:
                    lines[-1].append((run, buf))
                    buf = ""
                lines.append([])
                used = 0.0
            buf += ch
            used += w
        if buf:
            lines[-1].append((run, buf))
    if lines and not lines[-1]:
        lines.pop()
    return lines or [[]]


class DialogPreview(QWidget):
    """对话框预览控件（自绘：暗底、边框、彩色文字、术语下划线）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._payload = b""
        self._runs: List[Run] = []
        self._lines: List[List[tuple]] = []
        self._font_pt = 15
        self._max_lines = 4
        self._overflow: List[int] = []
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setToolTip(
            "对话框预览（近似）\n"
            "按真实字形宽度与「每行 39 个全角字」估算，仅供排版参考；\n"
            "不保证与 PS2 实机像素一致，实机渲染包恒为 UNKNOWN。")

    # -- 数据 --------------------------------------------------
    def set_payload(self, payload: bytes, max_lines: int = 4) -> None:
        self._payload = payload or b""
        self._runs = parse_runs(self._payload)
        self._max_lines = max(1, int(max_lines))
        self._relayout()

    def set_font_size(self, pt: int) -> None:
        self._font_pt = max(9, min(28, int(pt)))
        self._relayout()

    def font_size(self) -> int:
        return self._font_pt

    def stats(self) -> dict:
        """供编辑器读取的排版结论。"""
        total_em = sum(text_em(r.text.replace("\n", "")) for r in self._runs)
        manual = sum(1 for r in self._runs if "\n" in r.text)
        return {"lines": len(self._lines), "total_em": round(total_em, 1),
                "line_em": LINE_EM, "manual_breaks": manual,
                "overflow_lines": list(self._overflow),
                "max_lines": self._max_lines}

    def _relayout(self) -> None:
        self._lines = layout_lines(self._runs, LINE_EM)
        self._overflow = [i for i in range(self._max_lines, len(self._lines))]
        self.updateGeometry()
        self.update()

    # -- 绘制 --------------------------------------------------
    def sizeHint(self) -> QSize:            # noqa: N802
        return QSize(520, 110 + 26 * max(1, min(self._max_lines, len(self._lines))))

    def paintEvent(self, ev) -> None:       # noqa: N802, ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor("#5b6b84"), 1))
        p.setBrush(QBrush(QColor("#0d1218")))
        p.drawRoundedRect(rect, 8, 8)

        inner = rect.adjusted(14, 12, -14, -10)
        p.setPen(QPen(QColor("#26313f"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(inner, 5, 5)

        font = QFont()
        font.setFamilies(["Microsoft YaHei", "Microsoft YaHei UI", "SimHei"])
        font.setPointSizeF(self._font_pt)
        p.setFont(font)
        fm = QFontMetricsF(font)
        em_px = fm.height() * 0.0 + fm.averageCharWidth() * 2.0
        # 用「全角字宽」定标：把 39 em 映射到内框可用宽度
        avail = inner.width() - 22
        em_px = avail / LINE_EM
        line_h = fm.height() * 1.22

        y = inner.top() + 10
        max_lines = self._max_lines
        shown = self._lines[:max_lines]
        for i, line in enumerate(shown):
            x = inner.left() + 11
            for run, chunk in line:
                w = text_em(chunk) * em_px
                p.setPen(QColor(run.color))
                base = QPointF(x, y + fm.ascent())
                p.drawText(base, chunk)
                if run.book:
                    p.setPen(QPen(QColor("#d9b45a"), max(1.0, em_px / 17.0)))
                    p.drawLine(QPointF(x, y + fm.ascent() + 2.5),
                               QPointF(x + w, y + fm.ascent() + 2.5))
                x += w
            y += line_h

        # 溢出提示：超出对话框可见行数的部分画在右下角
        if len(self._lines) > max_lines:
            p.setPen(QColor("#e0736a"))
            f2 = QFont(font)
            f2.setPointSizeF(max(8.0, self._font_pt * 0.72))
            p.setFont(f2)
            p.drawText(QRectF(inner.left(), inner.bottom() - 18,
                              inner.width(), 18),
                       Qt.AlignRight | Qt.AlignVCenter,
                       "▼ 还有 %d 行显示不下（溢出）"
                       % (len(self._lines) - max_lines))

        # 行宽标尺（淡）
        p.setPen(QPen(QColor("#1d2836"), 1, Qt.DotLine))
        for i in range(len(shown) + 1):
            yy = inner.top() + 10 + line_h * i
            if yy > inner.bottom():
                break
            p.drawLine(QPointF(inner.left() + 8, yy),
                       QPointF(inner.right() - 8, yy))
        p.end()


class EmBar(QWidget):
    """单行宽度标尺：显示当前行占用多少 em / 上限。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ratio = 0.0
        self._text = ""
        self.setFixedHeight(20)

    def set_values(self, ratio: float, text: str = "") -> None:
        self._ratio = max(0.0, float(ratio))
        self._text = text
        self.update()

    def paintEvent(self, ev) -> None:       # noqa: N802, ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 5, -1, -5)
        # 轨道与文字跟随主题；填充色用主题的 ok/warn/bad
        from . import theme as _theme
        c = _theme.colors()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["bg_alt"]))
        p.drawRoundedRect(r, 3, 3)
        f = min(1.0, self._ratio)
        col = c["ok"] if self._ratio <= 0.98 else (
            c["warn"] if self._ratio <= 1.15 else c["bad"])
        if f > 0:
            p.setBrush(QColor(col))
            p.drawRoundedRect(QRectF(r.left(), r.top(), r.width() * f, r.height()),
                              3, 3)
        if self._text:
            p.setPen(QColor(c["text"]))
            p.drawText(r, Qt.AlignCenter, self._text)
        p.end()
