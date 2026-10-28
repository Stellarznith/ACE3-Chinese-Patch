"""可复用小控件：状态徽章、信息行、折叠区、差异视图、字符体检表。"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPlainTextEdit, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from . import theme


class Chip(QLabel):
    """状态徽章：图标 + 文字 + 颜色（三者同时表达，不只靠颜色）。"""

    def __init__(self, state: str = "PASS", text: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Chip")
        self._state = state
        self._text = text
        self.setAlignment(Qt.AlignCenter)
        self._apply()

    def set_state(self, state: str, text: str = "") -> None:
        self._state = state
        if text:
            self._text = text
        self._apply()

    def _apply(self) -> None:
        col = theme.state_color(self._state)
        label = theme.state_badge(self._state)
        shown = label if not self._text else "%s · %s" % (label, self._text)
        self.setText(shown)
        self.setStyleSheet(
            "QLabel#Chip { color: %s; border: 1px solid %s;"
            " border-radius: 9px; padding: 1px 9px; font-size: 12px;"
            " background: transparent; }" % (col, col))
        self.adjustSize()


class InfoRow(QWidget):
    """一行「标签：值」，值可选中复制。"""

    def __init__(self, label: str, value: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._lab = QLabel(label)
        self._lab.setObjectName("Hint")
        self._lab.setMinimumWidth(58)
        self._val = QLabel(value)
        self._val.setObjectName("Mono")
        self._val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._val.setWordWrap(True)
        lay.addWidget(self._lab, 0, Qt.AlignTop)
        lay.addWidget(self._val, 1)

    def set_value(self, text: str, color: Optional[str] = None) -> None:
        self._val.setText(text)
        if color:
            self._val.setStyleSheet("color: %s;" % color)
        else:
            self._val.setStyleSheet("")


class Section(QFrame):
    """带标题的可折叠区（技术信息默认折叠）。"""

    def __init__(self, title: str, parent: Optional[QWidget] = None,
                 collapsed: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(9, 7, 9, 8)
        outer.setSpacing(5)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(4)
        self._btn = QToolButton()
        self._btn.setObjectName("Ghost")
        self._btn.setCheckable(True)
        self._btn.setAutoRaise(True)
        self._btn.setStyleSheet(
            "QToolButton { border: none; color: %s; font-weight: 600;"
            " font-size: 12px; padding: 0; }" % theme.colors()["text_dim"])
        self._btn.setText(("▸ " if collapsed else "▾ ") + title)
        self._btn.clicked.connect(self._toggle)
        head.addWidget(self._btn)
        head.addStretch(1)
        self._extra = QLabel("")
        self._extra.setObjectName("Faint")
        head.addWidget(self._extra)
        outer.addLayout(head)

        self.body = QWidget()
        self._body_lay = QVBoxLayout(self.body)
        self._body_lay.setContentsMargins(0, 2, 0, 0)
        self._body_lay.setSpacing(5)
        outer.addWidget(self.body)
        self._title = title
        self._collapsed = collapsed
        self.body.setVisible(not collapsed)

    def _toggle(self) -> None:
        self._collapsed = not self._collapsed
        self.body.setVisible(not self._collapsed)
        self._btn.setText(("▸ " if self._collapsed else "▾ ") + self._title)

    def add(self, w: QWidget) -> None:
        self._body_lay.addWidget(w)

    def add_layout(self, lay) -> None:      # noqa: ANN001
        self._body_lay.addLayout(lay)

    def set_extra(self, text: str) -> None:
        self._extra.setText(text)

    def set_collapsed(self, on: bool) -> None:
        if on != self._collapsed:
            self._toggle()


class DiffView(QPlainTextEdit):
    """逐字符差异视图：删除红底删除线、新增绿底、相同灰色。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(theme.ui_font(10))
        self.setMinimumHeight(58)
        self.setMaximumHeight(120)
        self.setPlaceholderText("修改前后的逐字符差异会显示在这里")

    def show_diff(self, old: str, new: str) -> None:
        from core import codec
        self.clear()
        cur = self.textCursor()
        c = theme.colors()
        f_same = QTextCharFormat()
        f_same.setForeground(QColor(c["text_dim"]))
        f_del = QTextCharFormat()
        f_del.setForeground(QColor(c["bad"]))
        f_del.setBackground(QColor(c["bad_soft"]))
        f_del.setFontStrikeOut(True)
        f_ins = QTextCharFormat()
        f_ins.setForeground(QColor(c["ok"]))
        f_ins.setBackground(QColor(c["ok_soft"]))
        f_ins.setFontWeight(QFont.Bold)

        if not new and not old:
            return
        for op, a_ch, b_ch in codec.diff_chars(old or "", new or ""):
            if op == "=":
                cur.insertText(a_ch, f_same)
            elif op == "-":
                cur.insertText(a_ch, f_del)
            elif op == "+":
                cur.insertText(b_ch, f_ins)
            else:                       # '~' 替换：旧删 + 新插
                if a_ch:
                    cur.insertText(a_ch, f_del)
                if b_ch:
                    cur.insertText(b_ch, f_ins)
        self.setTextCursor(cur)


class CharTable(QWidget):
    """字符体检表：每字一行，状态 + 码位 + 覆盖包数（不只靠颜色）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._lay = QGridLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setHorizontalSpacing(8)
        self._lay.setVerticalSpacing(3)
        self._rows: List[QWidget] = []
        self._empty = QLabel("尚未输入中文")
        self._empty.setObjectName("Hint")
        self._lay.addWidget(self._empty, 0, 0)

    def clear_rows(self) -> None:
        for w in self._rows:
            w.setParent(None)
            w.deleteLater()
        self._rows.clear()

    def set_verdicts(self, order: Sequence) -> None:
        self.clear_rows()
        if not order:
            self._empty.setVisible(True)
            return
        self._empty.setVisible(False)
        for i, v in enumerate(order):
            self._lay.addWidget(self._row(v), i // 2, i % 2)

    def _row(self, v) -> QWidget:           # noqa: ANN001
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        g = QLabel(theme.state_glyph(v.state))
        g.setStyleSheet("color: %s; font-weight: 700; min-width: 12px;"
                        % theme.state_color(v.state))
        ch = QLabel(v.char if v.char.strip() else "␣")
        f = theme.dialog_font(11)
        ch.setFont(f)
        ch.setStyleSheet("min-width: 18px;")
        txt = QLabel(v.short())
        txt.setObjectName("Hint")
        txt.setStyleSheet("color: %s;" % theme.state_color(v.state))
        txt.setToolTip(v.reason or "")
        lay.addWidget(g)
        lay.addWidget(ch)
        lay.addWidget(txt, 1)
        self._rows.append(w)
        return w


class Toast(QLabel):
    """轻量操作反馈（趣味提示只用于成功；错误走状态栏 + 对话框）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAlignment(Qt.AlignCenter)
        self.setVisible(False)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._timer = None

    def show_message(self, text: str, ms: int = 2600,
                     kind: str = "ok") -> None:
        c = theme.colors()
        col = c.get(kind, c["accent"])
        self.setStyleSheet(
            "QLabel#Toast { background: %s; color: %s; border: 1px solid %s;"
            " border-radius: 11px; padding: 5px 14px; font-size: 12px; }"
            % (c["panel_alt"], col, col))
        self.setText(text)
        self.adjustSize()
        self._reposition()
        self.setVisible(True)
        self.raise_()
        from PySide6.QtCore import QTimer
        if self._timer is not None:
            self._timer.stop()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self._timer.start(ms)

    def _reposition(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        self.move(max(8, (p.width() - self.width()) // 2),
                  max(8, p.height() - self.height() - 26))
