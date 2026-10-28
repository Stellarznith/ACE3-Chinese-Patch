"""右栏：修改队列（暂存修改、逐项状态、冲突原因、统一写盘入口）。"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QMenu,
                               QMessageBox, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from core import editplan

from . import theme

_COLS = ("状态", "位置", "日文 / 原文", "新中文", "字节", "说明")


class QueuePanel(QFrame):
    """修改队列面板。"""

    openRequested = Signal(dict)        # 重新编辑该条
    removeRequested = Signal(list)      # 删除若干 key
    recheckRequested = Signal()
    writeRequested = Signal()
    draftRequested = Signal()
    revertRequested = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._queue: Optional[editplan.PlanQueue] = None
        self._iso: str = ""
        self._rows: Dict[tuple, QTreeWidgetItem] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(9, 8, 9, 9)
        outer.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(5)
        t = QLabel("修改队列")
        t.setObjectName("PanelTitle")
        head.addWidget(t)
        head.addStretch(1)
        self.stats = QLabel("0 条")
        self.stats.setObjectName("Faint")
        head.addWidget(self.stats)
        outer.addLayout(head)

        frow = QHBoxLayout()
        frow.setSpacing(5)
        self.filter = QComboBox()
        self.filter.addItems(["全部", editplan.STATE_PENDING, editplan.STATE_OK,
                              editplan.STATE_WARN, editplan.STATE_BLOCK,
                              "仅压缩资源"])
        self.filter.currentIndexChanged.connect(self.refresh)
        frow.addWidget(self.filter, 1)
        outer.addLayout(frow)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(_COLS))
        self.tree.setHeaderLabels(list(_COLS))
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.itemDoubleClicked.connect(self._on_open)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.Stretch)
        outer.addWidget(self.tree, 1)

        self.hint = QLabel("每条修改先暂存为编辑计划，不立即改 ISO。")
        self.hint.setObjectName("Faint")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        btns = QHBoxLayout()
        btns.setSpacing(5)
        self.btn_draft = QPushButton("保存草稿 Ctrl+S")
        self.btn_draft.clicked.connect(self.draftRequested.emit)
        self.btn_recheck = QPushButton("重新检查 F5")
        self.btn_recheck.clicked.connect(self.recheckRequested.emit)
        self.btn_del = QPushButton("删除")
        self.btn_del.setObjectName("Ghost")
        self.btn_del.clicked.connect(self._delete_selected)
        self.btn_write = QPushButton("统一写盘  Ctrl+Shift+E")
        self.btn_write.setObjectName("Primary")
        self.btn_write.clicked.connect(self.writeRequested.emit)
        for b in (self.btn_del, self.btn_draft, self.btn_recheck, self.btn_write):
            btns.addWidget(b)
        outer.addLayout(btns)

        self.set_queue(None)

    # -------------------------------------------------------------- 绑定
    def set_queue(self, q: Optional[editplan.PlanQueue]) -> None:
        self._queue = q
        self.refresh()

    def set_iso(self, iso: str) -> None:
        self._iso = iso

    # -------------------------------------------------------------- 刷新
    def refresh(self) -> None:
        self.tree.clear()
        self._rows.clear()
        q = self._queue
        if q is None:
            self.stats.setText("0 条")
            return
        want = self.filter.currentText()
        items = list(q.items)
        if want == "仅压缩资源":
            items = [it for it in items if it.compressed]
        elif want != "全部":
            items = [it for it in items if it.state == want]

        for it in items:
            row = QTreeWidgetItem(self.tree)
            row.setText(0, "%s %s" % (theme.state_glyph(it.state), it.state))
            from PySide6.QtGui import QBrush, QColor
            row.setForeground(0, QBrush(QColor(theme.state_color(it.state))))
            row.setText(1, it.label())
            jp = (it.jp_text or "").replace("\n", "⏎")
            old = (it.old_text or "").replace("\n", "⏎")
            row.setText(2, "%s ／ %s" % (jp[:40], old[:40]) if jp else old[:60])
            row.setText(3, (it.new_text or "").replace("\n", "⏎")[:80])
            row.setText(4, "%d→%d" % (len(it.old_payload), len(it.new_payload)))
            why = "；".join(list(it.reasons) + list(it.warnings))[:120]
            row.setText(5, why or ("压缩资源" if it.compressed else ""))
            row.setToolTip(2, "原版日文：%s\n当前中文：%s" % (it.jp_text, it.old_text))
            row.setToolTip(5, why)
            row.setData(0, Qt.UserRole, it.key())
            self._rows[it.key()] = row

        s = q.stats()
        self.stats.setText(
            "%d 条 · 可写 %d · 警告 %d · 阻断 %d · %d 个资源"
            % (s["total"], s["by_state"].get(editplan.STATE_OK, 0),
               s["by_state"].get(editplan.STATE_WARN, 0),
               s["by_state"].get(editplan.STATE_BLOCK, 0), s["resources"]))

        if q.parent.get("md5"):
            self.hint.setText("父盘：%s  ·  MD5 %s…"
                              % (os.path.basename(q.parent["path"]),
                                 q.parent["md5"][:16]))
        else:
            self.hint.setText("还没绑定父盘：先选一张 ISO。")

    # -------------------------------------------------------------- 选择
    def selected_keys(self) -> List[tuple]:
        out = []
        for row in self.tree.selectedItems():
            k = row.data(0, Qt.UserRole)
            if k:
                out.append(tuple(k))
        return out

    def _delete_selected(self) -> None:
        keys = self.selected_keys()
        if keys:
            self.removeRequested.emit(keys)

    def _on_open(self, row: QTreeWidgetItem, _col: int) -> None:
        k = row.data(0, Qt.UserRole)
        if not k or self._queue is None:
            return
        it = self._queue.get(tuple(k))
        if it:
            self.openRequested.emit(it.as_dict())

    def _menu(self, pos) -> None:            # noqa: ANN001
        row = self.tree.itemAt(pos)
        if row is None:
            return
        if not row.isSelected():
            self.tree.setCurrentItem(row)
        keys = self.selected_keys()
        m = QMenu(self)
        a1 = m.addAction("重新编辑这一条")
        a2 = m.addAction("恢复原文（从队列移除）")
        a3 = m.addAction("删除 %d 条" % len(keys) if keys else "删除")
        act = m.exec(self.tree.viewport().mapToGlobal(pos))
        if act is a1:
            k = row.data(0, Qt.UserRole)
            it = self._queue.get(tuple(k)) if self._queue else None
            if it:
                self.openRequested.emit(it.as_dict())
        elif act is a2:
            self.revertRequested.emit(keys)
        elif act is a3:
            self.removeRequested.emit(keys)

    def select(self, key: tuple) -> None:
        row = self._rows.get(tuple(key))
        if row is not None:
            self.tree.setCurrentItem(row)
            self.tree.scrollToItem(row)
