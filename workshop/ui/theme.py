"""深色/浅色主题与通用视觉元素（Qt 样式表 + 少量绘制）。

风格约定（任务书 §二 / 界面风格）
--------------------------------
* 默认深色，可切换浅色；不引入复杂皮肤系统，不做无关动画。
* 状态用 **图标 + 文字 + 颜色** 三者同时表达，绝不只靠颜色（无障碍）。
* 字体清晰、行高足够；技术信息可折叠。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QStyle

# ---------------------------------------------------------------- 色彩
DARK = {
    "bg": "#1b1e24",
    "bg_alt": "#22262e",
    "panel": "#262b34",
    "panel_alt": "#2d333d",
    "border": "#3a4150",
    "border_hi": "#4a566b",
    "text": "#d7dce5",
    "text_dim": "#8e97a8",
    "text_faint": "#646d7d",
    "accent": "#4b9fd8",
    "accent_soft": "#2f4a63",
    "ok": "#4caf7d",
    "ok_soft": "#243a30",
    "warn": "#d9a03c",
    "warn_soft": "#3d3220",
    "bad": "#d9615a",
    "bad_soft": "#3e2626",
    "edit": "#2a313b",
    "sel": "#31536e",
    "hit": "#8a6a1f",
}

LIGHT = {
    "bg": "#f3f4f6",
    "bg_alt": "#e9ebef",
    "panel": "#ffffff",
    "panel_alt": "#f7f8fa",
    "border": "#c9cdd6",
    "border_hi": "#a9b0bf",
    "text": "#1e222a",
    "text_dim": "#5c6472",
    "text_faint": "#8b93a2",
    "accent": "#1f6fa8",
    "accent_soft": "#d6e6f3",
    "ok": "#22794f",
    "ok_soft": "#dcefe4",
    "warn": "#9a6a10",
    "warn_soft": "#f5ecd6",
    "bad": "#a8332c",
    "bad_soft": "#f6dedd",
    "edit": "#ffffff",
    "sel": "#cfe2f2",
    "hit": "#ffe08a",
}

_current = "dark"


def colors() -> dict:
    return DARK if _current == "dark" else LIGHT


def set_theme(name: str) -> None:
    global _current
    _current = "light" if str(name).lower().startswith("light") else "dark"


def theme_name() -> str:
    return _current


# ---------------------------------------------------------------- 样式表
def _qss(c: dict) -> str:
    return f"""
* {{
    font-size: 13px;
}}
QWidget {{
    background: {c['bg']};
    color: {c['text']};
}}
QMainWindow, QDialog {{
    background: {c['bg']};
}}
QToolTip {{
    background: {c['panel_alt']};
    color: {c['text']};
    border: 1px solid {c['border_hi']};
    padding: 5px 8px;
}}

/* ---- 面板 ---- */
QFrame#Panel {{
    background: {c['panel']};
    border: 1px solid {c['border']};
    border-radius: 7px;
}}
QFrame#PanelAlt {{
    background: {c['panel_alt']};
    border: 1px solid {c['border']};
    border-radius: 7px;
}}
QLabel#PanelTitle {{
    color: {c['text']};
    font-weight: 600;
    font-size: 13px;
    padding: 1px 2px;
}}
QLabel#Hint {{
    color: {c['text_dim']};
    font-size: 12px;
}}
QLabel#Faint {{
    color: {c['text_faint']};
    font-size: 11px;
}}
QLabel#Mono {{
    color: {c['text_dim']};
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
}}

/* ---- 按钮 ---- */
QPushButton {{
    background: {c['panel_alt']};
    color: {c['text']};
    border: 1px solid {c['border_hi']};
    border-radius: 5px;
    padding: 5px 12px;
    min-height: 20px;
}}
QPushButton:hover {{
    background: {c['accent_soft']};
    border-color: {c['accent']};
}}
QPushButton:pressed {{
    background: {c['sel']};
}}
QPushButton:disabled {{
    color: {c['text_faint']};
    border-color: {c['border']};
    background: {c['bg_alt']};
}}
QPushButton#Primary {{
    background: {c['accent']};
    border-color: {c['accent']};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: {c['border_hi']};
}}
QPushButton#Danger {{
    border-color: {c['bad']};
    color: {c['bad']};
}}
QPushButton#Danger:hover {{
    background: {c['bad_soft']};
}}
QPushButton#Ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {c['text_dim']};
    padding: 3px 7px;
}}
QPushButton#Ghost:hover {{
    border-color: {c['border_hi']};
    color: {c['text']};
}}

/* ---- 输入 ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {c['edit']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 5px;
    padding: 4px 7px;
    selection-background-color: {c['sel']};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{
    border-color: {c['accent']};
}}
QLineEdit#Search {{
    padding: 6px 9px;
    font-size: 14px;
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background: {c['panel']};
    border: 1px solid {c['border_hi']};
    selection-background-color: {c['sel']};
    outline: none;
}}

/* ---- 列表 / 表格 ---- */
QListWidget, QTableWidget, QTreeWidget {{
    background: {c['panel']};
    border: 1px solid {c['border']};
    border-radius: 5px;
    outline: none;
}}
QListWidget::item {{
    padding: 5px 6px;
    border-bottom: 1px solid {c['bg_alt']};
}}
QListWidget::item:selected {{
    background: {c['sel']};
    color: {c['text']};
}}
QListWidget::item:hover {{
    background: {c['panel_alt']};
}}
QTableWidget {{
    gridline-color: {c['border']};
}}
QTableWidget::item:selected {{
    background: {c['sel']};
}}
QHeaderView::section {{
    background: {c['panel_alt']};
    color: {c['text_dim']};
    border: none;
    border-right: 1px solid {c['border']};
    border-bottom: 1px solid {c['border']};
    padding: 4px 6px;
    font-weight: 600;
}}

/* ---- 滚动条 ---- */
QScrollBar:vertical {{
    background: {c['bg']};
    width: 11px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {c['border_hi']};
    border-radius: 5px;
    min-height: 26px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c['accent']};
}}
QScrollBar:horizontal {{
    background: {c['bg']};
    height: 11px;
}}
QScrollBar::handle:horizontal {{
    background: {c['border_hi']};
    border-radius: 5px;
    min-width: 26px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0; width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ---- 选项卡 ---- */
QTabWidget::pane {{
    border: 1px solid {c['border']};
    border-radius: 6px;
    background: {c['panel']};
}}
QTabBar::tab {{
    background: {c['bg_alt']};
    color: {c['text_dim']};
    padding: 6px 13px;
    margin-right: 2px;
    border: 1px solid {c['border']};
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
}}
QTabBar::tab:selected {{
    background: {c['panel']};
    color: {c['text']};
}}
QTabBar::tab:hover:!selected {{
    color: {c['text']};
}}

/* ---- 分隔条 ---- */
QSplitter::handle {{
    background: {c['bg']};
}}
QSplitter::handle:horizontal {{
    width: 5px;
}}
QSplitter::handle:vertical {{
    height: 5px;
}}
QSplitter::handle:hover {{
    background: {c['accent_soft']};
}}

/* ---- 分组框 ---- */
QGroupBox {{
    border: 1px solid {c['border']};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 8px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 9px;
    padding: 0 4px;
    color: {c['text_dim']};
}}

/* ---- 菜单 / 状态栏 ---- */
QMenuBar {{
    background: {c['bg_alt']};
    border-bottom: 1px solid {c['border']};
}}
QMenuBar::item {{
    padding: 5px 10px;
    background: transparent;
}}
QMenuBar::item:selected {{
    background: {c['accent_soft']};
}}
QMenu {{
    background: {c['panel']};
    border: 1px solid {c['border_hi']};
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 22px 5px 18px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background: {c['sel']};
}}
QStatusBar {{
    background: {c['bg_alt']};
    border-top: 1px solid {c['border']};
    color: {c['text_dim']};
}}
QStatusBar QLabel {{
    color: {c['text_dim']};
    padding: 0 8px;
}}
QCheckBox, QRadioButton {{
    spacing: 6px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px; height: 14px;
}}
QProgressBar {{
    background: {c['bg_alt']};
    border: 1px solid {c['border']};
    border-radius: 4px;
    text-align: center;
    color: {c['text']};
    height: 14px;
}}
QProgressBar::chunk {{
    background: {c['accent']};
    border-radius: 3px;
}}
"""


def apply_theme(app: QApplication, name: str = "dark") -> None:
    set_theme(name)
    app.setStyleSheet(_qss(colors()))
    from PySide6.QtGui import QPalette
    pal = app.palette()
    c = colors()
    role = QPalette.ColorRole
    pal.setColor(role.Window, QColor(c["bg"]))
    pal.setColor(role.Base, QColor(c["edit"]))
    pal.setColor(role.AlternateBase, QColor(c["panel_alt"]))
    pal.setColor(role.Button, QColor(c["panel"]))
    pal.setColor(role.Text, QColor(c["text"]))
    pal.setColor(role.WindowText, QColor(c["text"]))
    pal.setColor(role.ButtonText, QColor(c["text"]))
    pal.setColor(role.Highlight, QColor(c["sel"]))
    pal.setColor(role.HighlightedText, QColor(c["text"]))
    pal.setColor(role.ToolTipBase, QColor(c["panel_alt"]))
    pal.setColor(role.ToolTipText, QColor(c["text"]))
    app.setPalette(pal)


# ---------------------------------------------------------------- 状态图标
STATE_COLORS = {
    "PASS": "ok", "OK": "ok", "可写入": "ok",
    "WARN": "warn", "警告": "warn",
    "BLOCK": "bad", "阻断": "bad",
    "待修改": "accent",
}

STATE_GLYPH = {
    "PASS": "✓", "OK": "✓", "可写入": "✓",
    "WARN": "!", "警告": "!",
    "BLOCK": "✕", "阻断": "✕",
    "待修改": "•",
}


def state_color(state: str) -> str:
    return colors().get(STATE_COLORS.get(state, "text_dim"), "#888")


def state_glyph(state: str) -> str:
    return STATE_GLYPH.get(state, "•")


def state_badge(state: str) -> str:
    """带图标的文字标（GUI 里到处用，保证不只靠颜色）。"""
    return "%s %s" % (state_glyph(state), state)


def dot_icon(state: str, size: int = 10) -> QIcon:
    """小圆点图标（列表项左侧用）。"""
    col = QColor(state_color(state))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(col)
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, size - 1, size - 1)
    p.end()
    return QIcon(pm)


def mono_font(size: int = 11) -> QFont:
    f = QFont("Cascadia Mono")
    if not f.exactMatch():
        f = QFont("Consolas")
    f.setStyleHint(QFont.Monospace)
    f.setPointSize(size)
    return f


def ui_font(size: int = 10) -> QFont:
    """界面字体：优先 Windows 中文界面字体，保证汉字清晰。"""
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"):
        f = QFont(name)
        if f.exactMatch():
            f.setPointSize(size)
            return f
    f = QFont()
    f.setPointSize(size)
    return f


def dialog_font(size: int = 12) -> QFont:
    """对话框/预览用的游戏风格字体（尽量像 PS2 中文对话框）。"""
    for name in ("Microsoft YaHei", "Microsoft YaHei UI", "SimHei"):
        f = QFont(name)
        if f.exactMatch():
            f.setPointSize(size)
            return f
    f = QFont()
    f.setPointSize(size)
    return f
