"""索引缓存管理：看清单、单删 / 多选删 / 全删（可重建，不是盘面数据）。

边界（重要）
------------
* 只删 ``tools/ace3_text_locator/db/<盘名>/`` 这一层**检索缓存**；
  不碰 ISO、不碰冻结工具的源码与 ``_shared`` 字形/日文共享缓存。
* 正在使用的那份索引**不删**（删了当前会话就搜不了，且 Windows 上 sqlite 还开着）。
* 删完必须让左栏版本下拉跟着刷新，否则界面上还挂着一个已经不存在的版本。
"""
from __future__ import annotations

import os
from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout)

from core import env, search as S

from . import theme


def _mb(n: int) -> str:
    return "%.1f MB" % (n / (1024.0 * 1024.0)) if n >= 1 << 20 else "%d KB" % max(1, n // 1024)


class IndexManager(QDialog):
    """索引缓存管理对话框。"""

    def __init__(self, parent=None, current_db: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("索引缓存管理 - ACE3 汉化工坊")
        self.resize(860, 520)
        self._current_db = os.path.abspath(current_db) if current_db else ""
        self._deleted: List[str] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(9)

        head = QLabel("这里列出的是**检索缓存**（可随时重建），不是游戏盘，也不是译文。\n"
                      "删除后该版本会从左栏「版本」下拉里消失；想看它需要重新建索引（1–3 分钟）。")
        head.setObjectName("Hint")
        head.setWordWrap(True)
        lay.addWidget(head)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["版本（索引目录）", "对应 ISO", "槽数", "建成时间", "大小"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        for i, w in enumerate((230, 520, 70, 140, 80)):
            self.tree.setColumnWidth(i, w)
        lay.addWidget(self.tree, 1)

        self.info = QLabel("")
        self.info.setObjectName("Faint")
        lay.addWidget(self.info)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_all = QPushButton("全选")
        self.btn_none = QPushButton("全不选")
        self.btn_orphan = QPushButton("仅选 ISO 已不在的")
        self.btn_del = QPushButton("删除选中")
        self.btn_del.setObjectName("Danger")
        self.btn_del.setToolTip("只删检索缓存目录，ISO 与冻结工具源码不受影响")
        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("Primary")
        for b in (self.btn_all, self.btn_none, self.btn_orphan):
            b.setObjectName("Ghost")
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.btn_del)
        bar.addWidget(self.btn_close)
        lay.addLayout(bar)

        self.btn_all.clicked.connect(lambda: self._set_checks(True, None))
        self.btn_none.clicked.connect(lambda: self._set_checks(False, None))
        self.btn_orphan.clicked.connect(self._check_orphans)
        self.btn_del.clicked.connect(self._delete)
        self.btn_close.clicked.connect(self.accept)
        self.tree.itemChanged.connect(lambda *_: self._update_info())

        self.reload()

    # ------------------------------------------------------------ 数据
    def reload(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        infos = S.list_indexes()
        cur_stem = ""
        for info in infos:
            db = os.path.abspath(info["db"])
            is_cur = bool(self._current_db) and db == self._current_db
            if is_cur:
                cur_stem = info["stem"]
            iso = info.get("iso") or ""
            # 统一用 info_of() 的口径（iso_usable：存在且有盘的大小），
            # 免得这里说「在」、左栏说「不在原处」两套说法。
            iso_exists = bool(info.get("iso_exists"))
            it = QTreeWidgetItem([
                info["stem"] + ("（当前使用）" if is_cur else ""),
                iso or "(无记录)",
                str(info.get("slots") or 0),
                info.get("built_at") or "",
                _mb(int(info.get("size") or 0)),
            ])
            it.setData(0, Qt.UserRole, info["stem"])
            it.setData(1, Qt.UserRole + 1, iso_exists)
            it.setToolTip(1, iso)
            flags = it.flags() | Qt.ItemIsUserCheckable
            if is_cur:
                flags &= ~Qt.ItemIsUserCheckable
                it.setCheckState(0, Qt.Unchecked)
                # 注意：setForeground 收 QBrush/QColor，不收颜色字符串
                # （直接传 "#rrggbb" 会报 called with wrong argument types）
                it.setForeground(0, QBrush(QColor(theme.state_color("可写入"))))
                for c in range(5):
                    it.setToolTip(c, "当前正在使用的索引，不能在这里删除")
            else:
                it.setCheckState(0, Qt.Unchecked)
            it.setFlags(flags)
            if not iso_exists:
                it.setForeground(1, QBrush(QColor(theme.state_color("警告"))))
                it.setToolTip(1, (iso or "(空)") + "\n（这张盘现在不在磁盘上，索引可以删）")
            self.tree.addTopLevelItem(it)
        self.tree.blockSignals(False)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.tree.setFont(mono)
        self._update_info(cur_stem)

    def _update_info(self, cur_stem: str = "") -> None:
        total = self.tree.topLevelItemCount()
        picked = self._checked()
        size = 0
        for info in S.list_indexes():
            if info["stem"] in picked:
                size += int(info.get("size") or 0)
        tips = ["共 %d 份索引" % total]
        if picked:
            tips.append("已勾选 %d 份（约 %s）" % (len(picked), _mb(size)))
        else:
            tips.append("勾选要删的版本，或点「仅选 ISO 已不在的」")
        tips.append("共享缓存 _shared 不在列表里，不会被删")
        self.info.setText(" · ".join(tips))
        self.btn_del.setEnabled(bool(picked))

    def _checked(self) -> List[str]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == Qt.Checked:
                out.append(str(it.data(0, Qt.UserRole) or ""))
        return [s for s in out if s]

    def _set_checks(self, on: bool, only_missing: bool = None) -> None:
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if not (it.flags() & Qt.ItemIsUserCheckable):
                continue
            if only_missing and it.data(1, Qt.UserRole + 1):
                continue
            it.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._update_info()

    def _check_orphans(self) -> None:
        self._set_checks(False, None)
        self._set_checks(True, only_missing=True)

    # ------------------------------------------------------------ 删除
    def _delete(self) -> None:
        picked = self._checked()
        if not picked:
            return
        lines = ["即将删除以下 %d 份索引缓存：" % len(picked), ""]
        lines += ["　· " + s for s in picked]
        lines += ["", "删除的是检索缓存目录（db/<版本>/），可以随时重建。",
                  "ISO 镜像、译文、冻结工具源码都不会被改动。继续吗？"]
        r = QMessageBox.warning(self, "确认删除索引缓存", "\n".join(lines),
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        ok, bad, freed = 0, [], 0
        for stem in picked:
            res = S.delete_index(stem)
            if res["ok"]:
                ok += 1
                freed += int(res.get("freed") or 0)
                self._deleted.append(stem)
            else:
                bad.append("%s：%s" % (stem, res["msg"]))
        self.reload()
        msg = "已删除 %d 份，释放 %s。" % (ok, env.human_size(freed))
        if bad:
            QMessageBox.warning(self, "部分没删掉",
                               msg + "\n\n" + "\n".join(bad))
        else:
            QMessageBox.information(self, "清完了", msg)
        self.info.setText(msg)

    def deleted(self) -> List[str]:
        return list(self._deleted)
