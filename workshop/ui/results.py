"""左栏：搜索结果（文字 / EID / 码位 / 偏移 四种检索）。

行渲染用 ``QStyledItemDelegate`` 自绘，而不是 ``QListWidget.setItemWidget``：
后者会把子控件高度压到行高以下（实测 item 高 31px、控件只有 20px），
第二行元信息被裁掉一半；而且几百条命中就要建几百个 QWidget，滚动发卡。
自绘一屏只画可见行，高度由 ``sizeHint`` 按真实字体算，不依赖控件布局。
"""
from __future__ import annotations

import html
from typing import List, Optional

from PySide6.QtCore import QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFontMetrics, QPainter, QTextLayout,
                           QTextOption)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QStyle,
                               QStyledItemDelegate, QVBoxLayout, QWidget)
from core import editplan, search as S

from . import theme

_ROLE = Qt.ItemDataRole.UserRole          # 命中 dict 存在这里
_ROLE_HL = Qt.ItemDataRole.UserRole + 1   # 该行的命中关键词
_ROLE_Q = Qt.ItemDataRole.UserRole + 2    # 质量等级 0/1/2


def _hit_fg() -> str:
    """命中的前景色：深色主题配白字，浅色主题配深字（否则黄底白字看不见）。"""
    return "#1e222a" if theme.theme_name() == "light" else "#ffffff"


def _badge(hit: dict) -> tuple:
    """返回 (文字, 颜色键)。图标 + 文字，不只靠颜色。"""
    q = hit.get("quality", 1)
    if q == 0:
        return ("✕ 疑似二进制", "bad")
    if q == 2:
        return ("! 含未知码", "warn")
    return ("✓ 干净", "text_dim")


def _meta_text(hit: dict) -> str:
    if hit.get("kind") == "name":
        # 名字块（CLM 指令表 / FNM 机体名）不在 VER 槽体系里，
        # 元信息换成「块名 + 块内下标 + 余量」，别让用户以为它是普通槽。
        bits = ["EID %d" % hit["eid"], "%s 块" % hit.get("mark", "名字"),
                "第 %d 条" % hit["slot"], "%d 字节" % hit.get("bytes", 0)]
        if hit.get("slack"):
            bits.append("余量 %d 字节" % hit["slack"])
        bits.append("走%s" % ("槽编辑" if hit.get("engine") == "slot"
                              else "名字块引擎"))
        return " · ".join(bits)
    bits = ["EID %d" % hit["eid"], "槽 %d" % hit["slot"]]
    if hit.get("compressed"):
        bits.append("压缩")
    bits.append("%d 字节" % hit.get("bytes", 0))
    if hit.get("jp_text"):
        bits.append("有日文对照")
    if hit.get("zh_authority"):
        bits.append("有正式译文")
    return " · ".join(bits)


class ResultDelegate(QStyledItemDelegate):
    """画一行结果：第一行摘要（命中词高亮，可换行），第二行元信息 + 右侧徽章。"""

    PAD_X = 4
    PAD_Y = 3

    def _fonts(self, opt) -> tuple:
        base = opt.font
        f_main = type(base)(base)
        f_meta = type(base)(base)
        # 元信息比正文小一号。注意 pointSizeF() 在「按像素设字号」的字体上返回 -1，
        # 直接减 1 会触发 Qt 的 "QFont::setPointSize: Point size <= 0" 告警，
        # 所以两种字号口径都要走一遍，都不能设就保持原字号（宁可同号也不报警）。
        ps = base.pointSizeF()
        if ps > 0:
            f_meta.setPointSizeF(max(6.5, ps - 1.0))
        else:
            px = base.pixelSize()
            if px > 0:
                f_meta.setPixelSize(max(9, px - 2))
        return f_main, f_meta

    def sizeHint(self, opt, index) -> QSize:                 # noqa: N802
        hit = index.data(_ROLE) or {}
        f_main, f_meta = self._fonts(opt)
        w = self._avail_width(opt)
        h_main = self._measure(hit.get("text") or "", f_main, w)
        h_meta = self._measure(_meta_text(hit), f_meta, w)
        fm_main = QFontMetrics(f_main)
        fm_meta = QFontMetrics(f_meta)
        # 兜底：即使测量因宽度为 0 失效，也必须容得下两行字 + 上下留白
        floor = fm_main.height() + fm_meta.height() + self.PAD_Y * 2 + 4
        return QSize(max(120, opt.rect.width()), max(floor, h_main + h_meta
                                                     + self.PAD_Y * 2 + 3))

    @staticmethod
    def _avail_width(opt) -> int:
        """一行文字可用的像素宽度：优先取列表 viewport，退回到 option 矩形。"""
        view = getattr(opt.widget, "viewport", None)
        if callable(view):
            w = view().width()
            if w > 60:
                return w - 8 - 96
        return max(80, (opt.rect.width() or 400) - 8 - 96)

    @staticmethod
    def _measure(text: str, font, width: int) -> int:
        if not text:
            return 0
        lay = QTextLayout(text, font)
        o = QTextOption()
        o.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        lay.setTextOption(o)
        lay.beginLayout()
        total = 0.0
        while True:
            line = lay.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(40, width))
            total += line.height()
        lay.endLayout()
        return int(total + 0.5)

    def _draw_wrapped(self, p, rect: QRect, text: str, font,
                      color: QColor, hl: str = "") -> int:
        """把 text 按宽度换行画进 rect；命中词加高亮底色。返回占用高度。"""
        if not text:
            return 0
        p.save()
        p.setFont(font)
        p.setPen(color)
        lay = QTextLayout(text, font)
        o = QTextOption()
        o.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        lay.setTextOption(o)

        # 命中词高亮：用 FormatRange 设底色，交给 QTextLayout 自己画
        if hl:
            ranges = []
            start = text.find(hl)
            while start >= 0:
                fr = QTextLayout.FormatRange()
                fr.start = start
                fr.length = len(hl)
                f = type(font)(font)
                f.setBold(True)
                fr.format.setFont(f)
                fr.format.setBackground(QColor(theme.colors()["hit"]))
                fr.format.setForeground(QColor(_hit_fg()))
                ranges.append(fr)
                start = text.find(hl, start + len(hl))
            if ranges:
                lay.setFormats(ranges)

        lay.beginLayout()
        y = 0.0
        width = max(40, rect.width())
        while True:
            line = lay.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QPointF(0.0, y))
            y += line.height()
        lay.endLayout()
        lay.draw(p, QPointF(float(rect.left()), float(rect.top())))
        p.restore()
        return int(y + 0.5)

    def paint(self, p, opt, index) -> None:                 # noqa: N802
        hit = index.data(_ROLE) or {}
        col = theme.colors()
        p.save()
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        rect = opt.rect
        if sel:
            p.fillRect(rect, QColor(col["sel"]))
        elif hover:
            p.fillRect(rect, QColor(col["panel_alt"]))
        f_main, f_meta = self._fonts(opt)
        inner = QRect(rect.left() + self.PAD_X, rect.top() + self.PAD_Y,
                      rect.width() - self.PAD_X * 2, rect.height())
        # 右侧徽章先量宽度，给主文本留位置
        badge_text, badge_key = _badge(hit)
        p.setFont(f_meta)
        bw = p.fontMetrics().horizontalAdvance(badge_text) + 6
        main_rect = QRect(inner.left(), inner.top(),
                          max(80, inner.width() - bw - 10),
                          inner.height())
        used = self._draw_wrapped(p, main_rect, hit.get("text") or "",
                                  f_main, QColor(col["text"]),
                                  index.data(_ROLE_HL) or "")
        meta_rect = QRect(inner.left(), inner.top() + used + 1,
                          max(80, inner.width() - bw - 10),
                          max(12, inner.height() - used - 1))
        self._draw_wrapped(p, meta_rect, _meta_text(hit), f_meta,
                           QColor(col["text_dim"]))
        # 徽章右对齐到第一行
        p.setFont(f_meta)
        fm = p.fontMetrics()
        bh = fm.height()
        bx = rect.right() - self.PAD_X - bw
        by = rect.top() + self.PAD_Y
        p.setPen(QColor(col[badge_key]))
        p.drawText(QRect(bx, by, bw, bh),
                   int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop),
                   badge_text)
        p.restore()


class ResultsPanel(QFrame):
    """搜索结果面板。"""

    hitActivated = Signal(dict)      # 单击选中
    hitDropped = Signal()            # 清空
    indexChanged = Signal(object)    # 切换了索引（参数是新的 S.Index）
    isoRequested = Signal()          # 用户要重新挑一张 ISO（盘被挪走时用）
    searchRequested = Signal(str, bool)   # (关键词, 是否含名字块)：交给主窗口丢进后台

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._hits: List[dict] = []
        self._index: Optional[S.Index] = None
        self._iso: str = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(9, 8, 9, 9)
        outer.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(5)
        title = QLabel("搜索结果")
        title.setObjectName("PanelTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.count = QLabel("—")
        self.count.setObjectName("Faint")
        head.addWidget(self.count)
        outer.addLayout(head)

        # 搜索行
        srow = QHBoxLayout()
        srow.setSpacing(5)
        self.edit = QLineEdit()
        self.edit.setObjectName("Search")
        self.edit.setPlaceholderText(
            "搜索文字 / EID / 码位 / 偏移（例：何？ · eid:4004643 · code:0x89A8）")
        self.edit.setClearButtonEnabled(True)
        self.edit.returnPressed.connect(self._do_search)
        srow.addWidget(self.edit, 1)
        self.btn = QPushButton("搜索")
        self.btn.setObjectName("Primary")
        self.btn.clicked.connect(self._do_search)
        srow.addWidget(self.btn)
        outer.addLayout(srow)

        # 筛选行
        frow = QHBoxLayout()
        frow.setSpacing(5)
        self.mode = QComboBox()
        self.mode.addItems(["命中优先", "按 EID 升序", "按 EID 降序",
                            "按字节长度", "仅压缩资源", "仅非压缩"])
        self.mode.currentIndexChanged.connect(self._apply_filter)
        frow.addWidget(self.mode, 1)
        self.version = QComboBox()
        self.version.setToolTip("切换 ISO 版本（对应不同索引）")
        self.version.currentIndexChanged.connect(self._pick_version)
        frow.addWidget(self.version, 1)
        outer.addLayout(frow)

        # 名字块（CLM 指令表 / FNM 机体名）不在检索索引里，要单独扫盘。
        # 首次扫描约 19 秒（全盘 272 个 VMD），之后有缓存是毫秒级，
        # 所以做成可关的开关，并且默认开——用户搜「冥墓方舟」时它必须能命中。
        self.chk_names = QCheckBox("含名字块（武器/机体名）")
        self.chk_names.setChecked(True)
        self.chk_names.setToolTip(
            "同时搜索 DATA.BIN 里 VMD 容器的名字块（CLM 指令/武器名表、FNM 机体名）。\n"
            "这些串不在 Text Locator 索引里，所以要现场扫盘：\n"
            "首次约 19 秒（全盘 272 个 VMD），之后有缓存是毫秒级。")
        self.chk_names.toggled.connect(
            lambda on: self.hint.setText(
                "已包含名字块检索（首次约 19 秒）" if on
                else "只搜标准文本槽（名字块未参与）"))
        outer.addWidget(self.chk_names)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(False)
        self.list.setUniformItemSizes(False)
        self.list.setMouseTracking(True)
        self.list.setWordWrap(True)
        self.list.setSpacing(0)
        self.list.setItemDelegate(ResultDelegate(self.list))
        self.list.currentItemChanged.connect(self._on_current)
        self.list.itemActivated.connect(self._on_activated)
        outer.addWidget(self.list, 1)

        self.hint = QLabel("输入关键词后回车。全程只读检索，不碰 ISO。")
        self.hint.setObjectName("Faint")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

    # -- 索引 --------------------------------------------------
    def set_index(self, idx: Optional[S.Index]) -> None:
        self._index = idx
        self._iso = idx.iso_path if idx else ""
        self._refresh_versions()

    def iso_path(self) -> str:
        """当前索引绑定的 ISO 路径（名字块检索要直接读盘，不能走索引库）。"""
        return self._iso or ""

    def _refresh_versions(self) -> None:
        self.version.blockSignals(True)
        self.version.clear()
        cur = self._index.iso_path if self._index else ""
        for info in S.list_indexes():
            # 盘被挪走/删掉的索引必须标出来：否则用户切过去，中栏每次判定都是
            # 「资源读取失败」，看着像程序坏了，其实是这条索引指向的老路径没了。
            label = "%s%s" % (info["stem"],
                              "（当前）" if info["iso"] == cur else "")
            if not info.get("iso_exists", True):
                label += "（盘不在原处）"
            self.version.addItem(label, info["db"])
            if info["iso"] == cur:
                self.version.setCurrentIndex(self.version.count() - 1)
            i = self.version.count() - 1
            if not info.get("iso_exists", True):
                self.version.setItemData(
                    i, "索引记的盘现在读不到：\n%s\n\n换个位置打开这张盘即可自动认回"
                       "（大小与 MD5 都对得上时），或重建索引。" % (info["iso"] or "（未记录）"),
                    Qt.ToolTipRole)
                self.version.setItemData(i, QBrush(QColor(theme.state_color("警告"))),
                                         Qt.ForegroundRole)
        if self.version.count() == 0:
            self.version.addItem("（无索引）", "")
        self.version.blockSignals(False)

    def _pick_version(self, i: int) -> None:
        if i < 0 or not self._index:
            return
        db = self.version.itemData(i)
        if db and db != self._index.db_path:
            self.hitDropped.emit()
            try:
                idx = S.open_index(db)
                if idx.iso_path and not S.iso_usable(idx.iso_path):
                    # 这条索引记的盘现在读不到。切过去只会让中栏每次判定都报
                    # 「资源读取失败」——看着像程序坏了，其实是盘被挪走了。
                    # 留着当前索引，直接告诉用户怎么救回来。
                    self._refresh_versions()
                    self.hint.setText(
                        "这个版本的盘不在原处，先不切：%s" % idx.iso_path)
                    keep = QMessageBox.question(
                        self, "这张盘现在读不到",
                        "「%s」这条索引记的盘现在是这个路径：\n%s\n\n"
                        "文件不在了。用工具栏「打开 ISO…」找到这张盘的当前位置即可——"
                        "大小和 MD5 都对得上时会自动认回这份索引，不用重建。\n\n"
                        "要现在去选吗？" % (self.version.itemText(i), idx.iso_path),
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                    if keep == QMessageBox.Yes:
                        self.isoRequested.emit()
                    return
                self.set_index(idx)
                self.hint.setText("已切换索引：%s" % db)
                self.indexChanged.emit(idx)      # 让主窗口同步父盘与队列
            except Exception as exc:                # noqa: BLE001
                self.hint.setText("切换失败：%s" % exc)

    # -- 动作 --------------------------------------------------
    def focus_search(self) -> None:
        self.edit.setFocus()
        self.edit.selectAll()

    def _do_search(self) -> None:
        if self._index is None:
            self.hint.setText("尚未选择 ISO 或索引未就绪。")
            return
        q = self.edit.text().strip()
        if not q:
            return
        with_names = self.chk_names.isChecked()
        if with_names:
            self.hint.setText("检索中…（含名字块，首次要扫全盘 VMD，约 19 秒）")
        else:
            self.hint.setText("检索中…")
        # 真正干活的是主窗口的后台任务：含名字块时首次要 19 秒，
        # 放在 GUI 线程里会整窗卡死（实测）。
        self.searchRequested.emit(q, with_names)

    def show_result(self, res: dict) -> None:
        """接收后台检索的结果（``core.search.run_query`` 的返回）。"""
        self._raw = res.get("hits") or []
        self._needle = res.get("value") or self.edit.text().strip()
        self._apply_filter()
        extra = []
        if res.get("usage") is not None:
            extra.append("该码位全盘出现 %d 次" % res["usage"])
        if res.get("note"):
            extra.append(res["note"])
        if res.get("name_note"):
            extra.append(res["name_note"])
        if not self._raw:
            extra.append("没有命中。换个关键词，或用 eid:/code:/offset:/name: 直查。")
        self.hint.setText("；".join(extra) or
                          "点击任一行查看原版日文、当前中文与前后文。")

    def _apply_filter(self) -> None:
        rows = list(getattr(self, "_raw", []))
        m = self.mode.currentText()
        if m == "按 EID 升序":
            rows.sort(key=lambda h: (h["eid"], h["slot"]))
        elif m == "按 EID 降序":
            rows.sort(key=lambda h: (-h["eid"], h["slot"]))
        elif m == "按字节长度":
            rows.sort(key=lambda h: -h.get("bytes", 0))
        elif m == "仅压缩资源":
            rows = [h for h in rows if h.get("compressed")]
        elif m == "仅非压缩":
            rows = [h for h in rows if not h.get("compressed")]

        needle = getattr(self, "_needle", "")
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        for h in rows:
            it = QListWidgetItem()
            it.setData(_ROLE, h)
            it.setData(_ROLE_HL, needle)
            it.setData(_ROLE_Q, h.get("quality", 1))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.list.addItem(it)
        self.list.setUpdatesEnabled(True)
        self._hits = rows
        self.count.setText("%d 条" % len(rows) if rows else "—")

    def _on_current(self, cur: Optional[QListWidgetItem],
                    _prev: Optional[QListWidgetItem]) -> None:
        if cur is None:
            return
        hit = cur.data(_ROLE)
        if hit:
            self.hitActivated.emit(hit)

    def _on_activated(self, it: QListWidgetItem) -> None:
        hit = it.data(_ROLE) if it else None
        if hit:
            self.hitActivated.emit(hit)

    def current_hit(self) -> Optional[dict]:
        it = self.list.currentItem()
        return it.data(_ROLE) if it else None

    def select_by_key(self, eid: int, tag: int, k: int, slot: int) -> bool:
        for i in range(self.list.count()):
            h = self.list.item(i).data(_ROLE)
            if (h["eid"], h["tag"], h["k"], h["slot"]) == (eid, tag, k, slot):
                self.list.setCurrentRow(i)
                self.list.scrollToItem(self.list.item(i))
                return True
        return False

    def select_by_plan_key(self, key: tuple) -> bool:
        """按队列身份（``PlanItem.key()``）选中结果行，名字块也支持。"""
        for i in range(self.list.count()):
            h = self.list.item(i).data(_ROLE)
            if h and self._key_of(h) == tuple(key):
                self.list.setCurrentRow(i)
                self.list.scrollToItem(self.list.item(i))
                return True
        return False

    @staticmethod
    def _key_of(h: dict) -> tuple:
        # 口径只有一处：core.editplan.hit_key()。这里以前自己又写了一遍，
        # FNM 被算成 ("name", …) 而入队的是 4 个 int，两边对不上。
        return editplan.hit_key(h)

    def restyle(self) -> None:
        """主题切换后重画（自绘颜色来自 theme.colors()）。"""
        self.list.viewport().update()
