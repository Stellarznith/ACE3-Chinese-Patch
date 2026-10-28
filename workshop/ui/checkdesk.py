"""统一写盘前的检查台（任务书 §七 / 增强需求 7）。

只做两件事：把 ``pipeline.inspect`` 的结论如实摆出来，以及在**没有任何阻断项**
时放行到 ``pipeline.build_to``。任何"构建函数返回成功"都不等于写盘完成——
完成的口径由 ``build_to`` 里的独立回读 + 整盘白名单 + 受保护资源守恒三件事共同定义。
"""
from __future__ import annotations

import os
from typing import List, Optional, Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from core import editplan, env, paths, pipeline

from . import theme


class CheckDesk(QDialog):
    """检查台对话框。"""

    def __init__(self, parent: Optional[QWidget], iso: str,
                 items: Sequence[editplan.PlanItem], queue: editplan.PlanQueue,
                 runner=None) -> None:                # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("统一写盘检查台")
        self.resize(940, 700)
        self._iso = iso
        self._items = list(items)
        self._queue = queue
        self._runner = runner
        self._report: dict = {}
        self._busy = False
        self._result: dict = {}
        self._pending_build = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        head = QLabel("写盘前完整检查")
        head.setObjectName("PanelTitle")
        outer.addWidget(head)

        self.sub = QLabel("")
        self.sub.setObjectName("Hint")
        self.sub.setWordWrap(True)
        outer.addWidget(self.sub)

        # 汇总卡
        self.cards = QFrame()
        self.cards.setObjectName("Panel")
        cl = QHBoxLayout(self.cards)
        cl.setContentsMargins(10, 8, 10, 8)
        cl.setSpacing(14)
        self.lab_total = QLabel("—")
        self.lab_safe = QLabel("—")
        self.lab_warn = QLabel("—")
        self.lab_block = QLabel("—")
        self.lab_res = QLabel("—")
        self.lab_names = QLabel("—")
        self.lab_names.setToolTip(
            "名字块（CLM 指令表 / FNM 机体名）编辑条数。\n"
            "这类文本不在检索索引里，容量口径是块内 padding 而不是槽跨度。")
        for text, lab in (("总修改", self.lab_total), ("可安全写入", self.lab_safe),
                          ("需确认", self.lab_warn), ("阻断", self.lab_block),
                          ("涉及资源", self.lab_res),
                          ("其中名字块", self.lab_names)):
            box = QVBoxLayout()
            box.setSpacing(0)
            t = QLabel(text)
            t.setObjectName("Faint")
            lab.setStyleSheet("font-size: 19px; font-weight: 700;")
            box.addWidget(t)
            box.addWidget(lab)
            cl.addLayout(box)
        cl.addStretch(1)
        outer.addWidget(self.cards)

        # 明细树
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["类别", "位置", "说明", "处理建议"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        outer.addWidget(self.tree, 1)

        # 清单预览
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(theme.mono_font(10))
        self.log.setMaximumHeight(120)
        self.log.setPlaceholderText("写盘过程日志会显示在这里")
        outer.addWidget(self.log)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setVisible(False)
        outer.addWidget(self.bar)

        # 按钮
        brow = QHBoxLayout()
        brow.setSpacing(6)
        self.btn_recheck = QPushButton("重新检查 F5")
        self.btn_recheck.clicked.connect(self.recheck)
        self.btn_export = QPushButton("导出检查报告")
        self.btn_export.setObjectName("Ghost")
        self.btn_export.clicked.connect(self._export)
        brow.addWidget(self.btn_recheck)
        brow.addWidget(self.btn_export)
        brow.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_build = QPushButton("生成新 ISO")
        self.btn_build.setObjectName("Primary")
        self.btn_build.setEnabled(False)
        self.btn_build.clicked.connect(self._build)
        brow.addWidget(self.btn_cancel)
        brow.addWidget(self.btn_build)
        outer.addLayout(brow)

        self.recheck()

    # ------------------------------------------------------------ 检查
    def recheck(self) -> None:
        if self._busy:
            return
        self._set_busy(True, "正在检查 %d 条修改…" % len(self._items))

        def work(progress=None):
            return pipeline.inspect(self._iso, self._items,
                                    progress=progress)

        def done(rep) -> None:                   # noqa: ANN001
            self._report = rep
            self._fill(rep)
            self._set_busy(False, "")

        def fail(msg: str, tb: str) -> None:
            self._set_busy(False, "")
            self.log.appendPlainText("检查失败：%s" % msg)
            QMessageBox.critical(self, "检查失败", msg)

        self._run(work, done, fail)

    def _fill(self, rep: dict) -> None:
        col = theme.colors()
        self.lab_total.setText(str(rep["total"]))
        self.lab_safe.setText(str(rep["safe"]))
        self.lab_safe.setStyleSheet("font-size: 19px; font-weight: 700; color: %s;"
                                    % col["ok"])
        self.lab_warn.setText(str(rep["warn"]))
        self.lab_warn.setStyleSheet("font-size: 19px; font-weight: 700; color: %s;"
                                    % col["warn"])
        self.lab_block.setText(str(rep["blocked"]))
        self.lab_block.setStyleSheet("font-size: 19px; font-weight: 700; color: %s;"
                                     % col["bad"])
        self.lab_res.setText(str(len(rep["resources"])))
        n_name = int(rep.get("name_items") or 0)
        self.lab_names.setText(str(n_name))
        self.lab_names.setStyleSheet(
            "font-size: 19px; font-weight: 700; color: %s;"
            % (col["accent"] if n_name else col["text_dim"]))
        self.lab_names.setToolTip(
            "名字块（CLM 指令表 / FNM 机体名）编辑 %d 条。\n"
            "FNM 机体名块本身就是 VER 块，走槽编辑引擎；\n"
            "CLM 指令表不在 VER 体系里，走名字块引擎（块内重排）。" % n_name)

        self.tree.clear()
        blocked = QTreeWidgetItem(self.tree, ["✕ 阻断", "", "%d 项"
                                              % rep["blocked"],
                                              "必须先解决才能写盘"])
        blocked.setForeground(0, Qt.GlobalColor.red)
        for d in rep.get("blocked_detail") or []:
            it = QTreeWidgetItem(blocked, ["", d["where"], d["why"], ""])
            it.setForeground(2, Qt.GlobalColor.red)
        warned = QTreeWidgetItem(self.tree, ["! 需人工确认", "", "%d 项"
                                             % rep["warn"],
                                             "可以写入，但请逐条确认风险"])
        for d in rep.get("warn_detail") or []:
            QTreeWidgetItem(warned, ["", d["where"], d["why"], ""])
        conflicts = QTreeWidgetItem(
            self.tree, ["≡ 同资源合并", "", "%d 组" % len(rep.get("conflicts") or []),
                        "同块多条会整体重排、偏移重算，属正常规划"])
        for c in rep.get("conflicts") or []:
            # 冲突项有两类：槽编辑冲突带 tag/k，名字块冲突带 block_pos；
            # 混资源冲突（同资源既有槽又有名字）两者都不带。以前这里写死
            # c["tag"]，一遇到名字块冲突就 KeyError，整个 _fill 中断，
            # 树里只剩前三个节点、写盘按钮永远不可用（实测踩到）。
            if "block_pos" in c:
                where = "EID %d · 名字块 @0x%X" % (c["eid"], c["block_pos"])
            elif "tag" in c and "k" in c:
                where = "EID %d tag%d/k%d" % (c["eid"], c["tag"], c["k"])
            else:
                where = "EID %d（整资源）" % c["eid"]
            QTreeWidgetItem(conflicts, ["", where, c["note"], ""])
        dups = QTreeWidgetItem(self.tree, ["⎘ 同槽重复", "",
                                           "%d 组" % len(rep.get("duplicates") or []),
                                           "后者覆盖前者，最终只写一条"])
        for d in rep.get("duplicates") or []:
            QTreeWidgetItem(dups, ["", d["where"],
                                   "第 %s 条" % "、".join(str(x) for x in d["rows"]),
                                   "可保留最后一条或删除重复"])
        comp = [v for v in rep["verdicts"] if v.get("compressed")]
        cn = QTreeWidgetItem(self.tree, ["⇩ 压缩资源", "", "%d 条" % len(comp),
                                         "整条解压→改字→重压，需独立解压回读"])
        for v in comp[:80]:
            QTreeWidgetItem(cn, ["", v["where"], v.get("reason", ""), ""])
        nv = rep.get("name_verdicts") or []
        if nv:
            nblk = QTreeWidgetItem(
                self.tree,
                ["▤ 名字块（武器/机体名）", "", "%d 条" % len(nv),
                 "不在检索索引里；容量按块内 padding 算，回读走名字块引擎"])
            for v in nv[:80]:
                # NameVerdict.as_dict() 是平铺的（eid/block_pos/index/mark 与
                # ok/mode/... 同级），不是嵌套在 "edit" 里。以前这里读
                # v.get("edit")，永远拿 None，明细行全显示成
                # 「EID 0 · 名字 块 @0x0 · 第 0 条」（实测踩到）。
                e = v.get("edit") if isinstance(v.get("edit"), dict) else v
                where = "EID %d · %s 块 @0x%X · 第 %d 条" % (
                    e.get("eid", 0), e.get("mark") or "名字",
                    e.get("block_pos", 0), e.get("index", 0))
                if not v.get("ok"):
                    why = v.get("reason", "")
                elif v.get("font_state") == "WARN":
                    why = v.get("font_detail", "") or "字库告警"
                else:
                    why = "可写（%s，%d→%d 字节，块内余量 %d）" % (
                        v.get("mode", ""), v.get("capacity", 0),
                        v.get("need", 0), v.get("slack", 0))
                item = QTreeWidgetItem(nblk, ["", where, why, ""])
                if not v.get("ok"):
                    item.setForeground(2, Qt.GlobalColor.red)
                elif v.get("font_state") == "WARN":
                    item.setForeground(2, Qt.GlobalColor.yellow)
        ok = QTreeWidgetItem(self.tree, ["✓ 可直接写入", "", "%d 条" % rep["safe"], ""])
        for v in rep["verdicts"]:
            if v["state"] == "PASS":
                QTreeWidgetItem(ok, ["", v["where"], v.get("reason", ""), ""])
        for i in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(i)

        can = bool(rep["can_build"])
        self.btn_build.setEnabled(can)
        if can:
            self.sub.setText(
                "阻断项已清零，可以生成新 ISO。写盘会在独立临时文件里构建，"
                "父盘只读；完成后自动做独立回读、整盘白名单差分与受保护资源守恒核对。"
                "耗时约 20–60 秒。")
            self.sub.setStyleSheet("color: %s;" % col["ok"])
        else:
            self.sub.setText(
                "存在 %d 项阻断，禁止生成新 ISO。父盘与修改队列都会保持原样。"
                % rep["blocked"])
            self.sub.setStyleSheet("color: %s;" % col["bad"])

    # ------------------------------------------------------------ 写盘
    def _build(self) -> None:
        if self._busy:
            # 检查台一打开就在后台跑 inspect，用户很可能在「正在检查」时
            # 就点了「生成新 ISO」。不能静默吞掉这次点击（实测过：点击被
            # 丢弃后界面毫无反应，用户只会以为程序卡死），改为记下意图，
            # 等当前后台任务结束后自动继续。
            self._pending_build = True
            self.sub.setText("正在检查…检查结束后会自动继续生成新 ISO。")
            self.sub.setStyleSheet("color: %s;" % theme.colors()["accent"])
            self.log.appendPlainText("已记下「生成新 ISO」请求，检查完成后自动开始。")
            return
        if not self._report.get("can_build"):
            # 检查已经结束且存在阻断项：明确告诉用户为什么不能写
            self.log.appendPlainText(
                "无法生成：检查结果存在阻断项（%d 项），请先处理。"
                % (self._report.get("blocked") or 0))
            return
        out_dir = QFileDialog.getExistingDirectory(
            self, "选择新 ISO 的输出目录", env.REPORT_DIR)
        if not out_dir:
            return
        # 守则：产物不能落在系统盘，也不能落在工具目录之外。
        # 这里先拦下来问一句，而不是等到写盘中途才失败（那时已经复制了 4GB）。
        try:
            paths.check(out_dir, "新 ISO 输出目录")
        except paths.PathRuleError as exc:
            r = QMessageBox.warning(
                self, "输出目录不符合守则",
                "%s\n\n要改用默认目录吗？\n%s"
                % (exc, env.REPORT_DIR),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if r != QMessageBox.Yes:
                return
            out_dir = env.REPORT_DIR
        if not self._confirm(out_dir):
            return
        self._set_busy(True, "正在写盘…（父盘只读，产物写进独立临时文件）")
        self.log.clear()
        md5 = (self._queue.parent or {}).get("md5")

        def work(progress=None):
            return pipeline.build_to(self._iso, self._items, out_dir=out_dir,
                                     expected_md5=md5,
                                     name_hint="gui",
                                     progress=progress)

        def done(man) -> None:                   # noqa: ANN001
            self._set_busy(False, "")
            self._result = man
            out = man["output"]
            self.log.appendPlainText("完成：%s" % out["path"])
            self.log.appendPlainText("MD5：%s" % out["md5"])
            self.log.appendPlainText("父盘 MD5：%s" % man["parent"]["md5"])
            self.log.appendPlainText(
                "独立回读 %s / 整盘差分 %s（白名单外 %d 字节）/ 受保护资源 %s"
                % ("PASS" if man["readback"]["ok"] else "FAIL",
                   "PASS" if man["diff"]["ok"] else "FAIL",
                   man["diff"]["outside"],
                   "PASS" if man["protected"]["ok"] else "FAIL"))
            QMessageBox.information(
                self, "写盘完成",
                "机师，译文已就位！\n\n"
                "新 ISO：%s\nMD5：%s\n\n"
                "独立回读 PASS、整盘差分白名单外 0 字节、15 包字库与受保护资源守恒 PASS。\n"
                "修改清单与回读报告已写入同一目录。"
                % (out["path"], out["md5"]))
            self.accept()

        def fail(msg: str, tb: str) -> None:
            self._set_busy(False, "")
            self.log.appendPlainText("写盘失败：%s" % msg)
            QMessageBox.critical(
                self, "写盘失败",
                "写盘已中止，父盘与修改队列均未改动。\n\n原因：%s\n\n"
                "已保留检查台 / 构建计划 / 回读报告等证据文件。" % msg)

        self._run(work, done, fail)

    def _confirm(self, out_dir: str) -> bool:
        rep = self._report
        comp = [v for v in rep["verdicts"] if v.get("compressed")]
        dups = rep.get("duplicates") or []
        text = (
            "即将生成一张新的 ISO 副本。\n\n"
            "父盘：%s\n父盘 MD5：%s\n输出目录：%s\n\n"
            "本次修改：%d 条，涉及 %d 个资源，其中压缩资源 %d 条、名字块 %d 条。\n"
            "需人工确认（WARN）：%d 条。\n"
            "%s\n"
            "保证：父盘不会被修改或覆盖；任何一步失败都不会产生正式输出。\n\n"
            "注意：预览与静态检查都不能保证 PS2 实机像素完全一致，"
            "写盘完成后仍建议在模拟器里实机确认。\n\n确定继续？"
            % (os.path.basename(self._iso), (self._queue.parent or {}).get("md5", "?"),
               out_dir, rep["total"], len(rep["resources"]), len(comp),
               int(rep.get("name_items") or 0),
               rep["warn"],
               ("检测到 %d 组同槽重复修改，将只写入最后一条。\n" % len(dups))
               if dups else ""))
        r = QMessageBox.warning(self, "确认写盘", text,
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No)
        return r == QMessageBox.Yes

    # ------------------------------------------------------------ 工具
    def _run(self, fn, done, fail) -> None:      # noqa: ANN001
        try:
            if self._runner is not None:
                self._runner.start("检查/写盘", fn, done, fail)
            else:
                done(fn(progress=self._log_progress))
        except Exception as exc:                 # noqa: BLE001
            fail("%s：%s" % (type(exc).__name__, exc), "")

    def _log_progress(self, msg: str) -> None:
        self.log.appendPlainText(msg)

    def _set_busy(self, on: bool, msg: str) -> None:
        self._busy = on
        self.bar.setVisible(on)
        self.btn_recheck.setEnabled(not on)
        self.btn_cancel.setEnabled(not on)
        self.btn_build.setEnabled(not on and bool(self._report.get("can_build")))
        self.btn_export.setEnabled(not on)
        if msg:
            self.sub.setText(msg)
            self.sub.setStyleSheet("color: %s;" % theme.colors()["accent"])
        elif not on and self._pending_build:
            # 用户在「正在检查」期间点过生成：现在检查结束，接着办。
            # 用 singleShot 排队，避免在 done() 信号处理途中重入 _build。
            QTimer.singleShot(0, self._resume_build)

    def _resume_build(self) -> None:
        if not self._pending_build:
            return
        self._pending_build = False
        if self._result:
            return                     # 已经写过了
        if not (self._report or {}).get("can_build"):
            self.log.appendPlainText(
                "检查结束：存在阻断项，取消先前记下的生成请求。")
            return
        self._build()

    def _export(self) -> None:
        if not self._report:
            return
        import datetime
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出检查报告",
            os.path.join(env.REPORT_DIR, "检查台_%s.json" % stamp),
            "JSON (*.json)")
        if not path:
            return
        # 守则：报告同样不写系统盘（用户可能从桌面另存）
        try:
            path = paths.check(path, "导出检查报告")
        except paths.PathRuleError as exc:
            QMessageBox.warning(self, "保存位置不符合守则", str(exc))
            return
        try:
            pipeline._write_json(path, self._report)
        except Exception as exc:                 # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "已导出", path)

    def result(self) -> dict:
        return self._result
