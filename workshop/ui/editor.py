"""中栏：一站式文本编辑（同屏显示日文、当前中文、前后文、可编辑新中文）。

设计要点（任务书 §三 + 增强需求 1/2/3）
----------------------------------------
* 默认只编辑中文正文；控制标签以「受保护标签」形式展示，普通输入改不到它们。
* 输入即判定：字形覆盖（PASS/WARN/BLOCK）+ 容量（原位/移位/紧凑/BLOCK）。
* 技术信息（EID/码位/偏移/字节）默认折叠，需要时展开。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QSpinBox,
                               QSplitter, QVBoxLayout, QWidget)

from core import codec, estimate, fontcov

from . import theme
from .preview import DialogPreview, EmBar, LINE_EM, text_em
from .widgets import CharTable, Chip, DiffView, InfoRow, Section


class TagStrip(QWidget):
    """控制标签只读展示（受保护，不允许普通输入破坏）。"""

    tagClicked = Signal(bytes)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(4)
        self._chips: List[QWidget] = []
        self._empty = QLabel("（本槽没有控制标签）")
        self._empty.setObjectName("Faint")
        self._lay.addWidget(self._empty)
        self._lay.addStretch(1)

    def set_tags(self, tags: Sequence[bytes]) -> None:
        for w in self._chips:
            w.setParent(None)
            w.deleteLater()
        self._chips.clear()
        self._empty.setVisible(not tags)
        for tag in tags:
            name = codec.tag_name(tag)
            args = codec.tag_args(tag)
            chip = QPushButton(name or "?")
            chip.setObjectName("Ghost")
            chip.setToolTip("受保护标签 %s\n参数：%s\n按下可查看含义，不会改动它"
                            % (name, ", ".join(args) or "无"))
            chip.setStyleSheet(
                "QPushButton { border: 1px solid %s; border-radius: 8px;"
                " padding: 1px 8px; font-size: 11px; color: %s; }"
                % (theme.colors()["warn"], theme.colors()["warn"]))
            chip.clicked.connect(lambda _=False, t=bytes(tag):
                                 self.tagClicked.emit(t))
            self._lay.insertWidget(len(self._chips), chip)
            self._chips.append(chip)


class EditorPanel(QFrame):
    """中栏编辑器。"""

    saveRequested = Signal(dict)         # 请求加入修改队列（携带构造好的信息）
    revertRequested = Signal()
    dirtyChanged = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._hit: Optional[dict] = None
        self._seg: Optional[codec.Segment] = None
        self._iso: str = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(110)
        self._timer.timeout.connect(self._validate)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 9)
        outer.setSpacing(6)

        # ---- 标题行
        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = QLabel("文本编辑")
        self.title.setObjectName("PanelTitle")
        head.addWidget(self.title)
        self.where = QLabel("")
        self.where.setObjectName("Faint")
        head.addWidget(self.where, 1)
        self.chip_font = Chip("PASS", "字库")
        self.chip_cap = Chip("PASS", "容量")
        self.chip_tag = Chip("PASS", "标签")
        for c in (self.chip_font, self.chip_cap, self.chip_tag):
            head.addWidget(c)
        outer.addLayout(head)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)

        # ---- 上：原文 / 译文对照
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)
        self.row_jp = InfoRow("原版日文")
        self.row_cur = InfoRow("当前中文")
        self.row_ctx = InfoRow("前后文")
        for w in (self.row_jp, self.row_cur, self.row_ctx):
            tl.addWidget(w)
        self.tags = TagStrip()
        self.tags.tagClicked.connect(self._explain_tag)
        tl.addWidget(self.tags)
        split.addWidget(top)

        # ---- 中：编辑框 + 预览
        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(4)
        cap = QHBoxLayout()
        cap.setSpacing(6)
        lab = QLabel("新中文（只改正文，控制标签受保护）")
        lab.setObjectName("Hint")
        cap.addWidget(lab)
        cap.addStretch(1)
        self.font_size = QSpinBox()
        self.font_size.setRange(9, 26)
        self.font_size.setValue(15)
        self.font_size.setSuffix(" pt")
        self.font_size.setToolTip("预览字号（只影响预览，不影响写盘）")
        self.font_size.valueChanged.connect(lambda v: self.preview.set_font_size(v))
        cap.addWidget(self.font_size)
        ml.addLayout(cap)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("在此输入中文……")
        self.editor.setMinimumHeight(96)
        self.editor.textChanged.connect(self._on_text)
        self.editor.setTabChangesFocus(True)
        ml.addWidget(self.editor, 1)

        self.em = EmBar()
        ml.addWidget(self.em)

        self.preview = DialogPreview()
        self.preview.set_font_size(15)
        ml.addWidget(self.preview)
        split.addWidget(mid)

        # ---- 下：判定 + 差异 + 技术信息
        bot = QWidget()
        bl = QVBoxLayout(bot)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)
        self.verdict = QLabel("选择一条搜索结果开始编辑")
        self.verdict.setObjectName("Hint")
        self.verdict.setWordWrap(True)
        bl.addWidget(self.verdict)
        self.diff = DiffView()
        bl.addWidget(self.diff)
        self.chars = CharTable()
        bl.addWidget(self.chars)

        self.tech = Section("技术信息（默认折叠）", collapsed=True)
        self.row_eid = InfoRow("位置", "")
        self.row_off = InfoRow("偏移", "")
        self.row_bytes = InfoRow("字节", "")
        self.row_code = InfoRow("码位", "")
        self.row_comp = InfoRow("资源", "")
        for w in (self.row_eid, self.row_off, self.row_bytes,
                  self.row_code, self.row_comp):
            self.tech.add(w)
        bl.addWidget(self.tech)
        split.addWidget(bot)

        split.setSizes([110, 320, 170])
        outer.addWidget(split, 1)

        # ---- 底部按钮
        btns = QHBoxLayout()
        btns.setSpacing(6)
        btns.addStretch(1)
        self.btn_revert = QPushButton("恢复原文")
        self.btn_revert.clicked.connect(self._revert)
        self.btn_clear = QPushButton("撤销编辑")
        self.btn_clear.setObjectName("Ghost")
        self.btn_clear.clicked.connect(self._clear_edit)
        self.btn_save = QPushButton("加入修改队列  Ctrl+Enter")
        self.btn_save.setObjectName("Primary")
        self.btn_save.clicked.connect(self._save)
        for b in (self.btn_revert, self.btn_clear, self.btn_save):
            btns.addWidget(b)
        outer.addLayout(btns)

        self._set_enabled(False)
        self._last_est: Optional[estimate.Estimate] = None
        self._last_font_state = "PASS"
        self._auto_text = ""
        self._is_name = False
        self._name_slack = 0
        self._name_cap = 0

    # ---------------------------------------------------------------- 载入
    def set_iso(self, iso: str) -> None:
        self._iso = iso

    def load_hit(self, hit: Optional[dict], keep_text: bool = False) -> None:
        self._hit = hit
        if hit is None:
            self.title.setText("文本编辑")
            self.where.setText("")
            self.row_jp.set_value("")
            self.row_cur.set_value("")
            self.row_ctx.set_value("")
            self.tags.set_tags([])
            self.editor.setPlainText("")
            self.diff.show_diff("", "")
            self.chars.set_verdicts([])
            self.preview.set_payload(b"")
            self.em.set_values(0.0, "")
            self.verdict.setText("选择一条搜索结果开始编辑")
            self._set_enabled(False)
            return

        self._set_enabled(True)
        self._is_name = hit.get("kind") == "name"
        payload = bytes.fromhex(hit.get("seg_hex") or "")
        self._seg = codec.Segment(payload)
        if self._is_name:
            self.title.setText("名字编辑（%s 块）" % hit.get("mark", "名字"))
            self.where.setText("EID %d · %s 块 @0x%X · 第 %d 条 · 来源 ISO：%s"
                               % (hit["eid"], hit.get("mark", ""),
                                  hit.get("block_pos", 0), hit["slot"],
                                  self._iso or "（未绑定）"))
        else:
            self.title.setText("文本编辑")
            self.where.setText("EID %d · %s · 槽 %d · 来源 ISO：%s"
                               % (hit["eid"], hit["key_text"], hit["slot"],
                                  self._iso or "（未绑定）"))
        jp = hit.get("jp_text") or "（该槽没有原版日文缓存）"
        self.row_jp.set_value(jp)
        cur_text = self._seg.plain
        self.row_cur.set_value(cur_text or "（空槽）")
        zh = hit.get("zh_authority") or ""
        if zh:
            try:
                zh_plain = codec.split_tags(zh.encode("latin-1", "ignore"))[1]
            except Exception:                       # noqa: BLE001
                zh_plain = zh
            self.row_cur.setToolTip("正式译文（权威链）：%s" % zh_plain)
        else:
            self.row_cur.setToolTip("该槽没有正式译文记录")

        self._show_context(hit.get("context") or [], hit["slot"])

        self.tags.set_tags(self._seg.tags)

        if not keep_text:
            self.editor.blockSignals(True)
            self.editor.setPlainText(cur_text)
            self.editor.blockSignals(False)
            self._auto_text = cur_text
        self.diff.show_diff(cur_text, self.editor.toPlainText())
        self._fill_tech(hit)
        self._validate()

    def _show_context(self, ctx: Sequence[dict], slot: int) -> None:
        if not ctx:
            self.row_ctx.set_value("（无前后文）")
            return
        parts = []
        for item in ctx:
            mark = "▶" if item.get("is_target") else "　"
            txt = (item.get("text") or "").replace("\n", "⏎")
            parts.append("%s %s" % (mark, txt[:46]))
        self.row_ctx.set_value(" ｜ ".join(parts[:6]))

    def _fill_tech(self, hit: dict) -> None:
        if hit.get("kind") == "name":
            self.row_eid.set_value(
                "EID %d · %s 块 @0x%X · 第 %d 条 / 共 %d 条 · 引擎：%s"
                % (hit["eid"], hit.get("mark", ""), hit.get("block_pos", 0),
                   hit["slot"], hit.get("nslots", 0),
                   ("槽编辑（该块本身是 VER 块）"
                    if hit.get("engine") == "slot" else "名字块引擎（块内重排）")))
        else:
            self.row_eid.set_value("EID %d · tag=%d · k=%d · 逻辑槽 %d / %d"
                                   % (hit["eid"], hit["tag"], hit["k"],
                                      hit["slot"], hit.get("nslots", 0)))
        self.row_off.set_value("file_off=%s  file_end=%s  abs_off=%s  base=%s"
                               % (hex(hit.get("file_off", 0)),
                                  hex(hit.get("file_end", 0)),
                                  hex(hit.get("abs_off", 0)),
                                  hit.get("base", 0)))
        self.row_bytes.set_value("%d 字节 · %s"
                                 % (hit.get("bytes", 0),
                                    (hit.get("seg_hex") or "")[:96]))
        self.row_code.set_value(hit.get("codes_hex") or "—")
        if hit.get("kind") == "name":
            self.row_comp.set_value(
                "非压缩资源（名字块：串间/块尾 padding 可吸收 %d 字节）"
                % hit.get("slack", 0))
        else:
            self.row_comp.set_value(
                "压缩资源（整条解压→改字→重压）" if hit.get("compressed")
                else "非压缩资源（块内重排）")

    # ---------------------------------------------------------------- 输入
    def _on_text(self) -> None:
        self._timer.start()
        self.dirtyChanged.emit(True)
        txt = self.editor.toPlainText()
        self.diff.show_diff(self._seg.plain if self._seg else "", txt)
        em = text_em(txt.replace("\n", ""))
        self.em.set_values(em / LINE_EM, "%.1f / %.0f 字宽" % (em, LINE_EM))
        self.preview.set_payload(self._build_payload(txt) if self._seg else b"")

    def _build_payload(self, text: str) -> bytes:
        if self._seg is None:
            return b""
        if self._is_name:
            # 名字块里没有控制标签（实测 CLM/FNM 全是纯文本 + NUL 结尾），
            # 直接编码；不要走 rebuild_with_plain，那套逻辑是为带标签的槽写的。
            return codec.encode_text(text, self._seg.codes).data
        data, _res = codec.rebuild_with_plain(self._seg, text)
        return data

    def _validate(self) -> None:
        if self._hit is None or self._seg is None:
            return
        text = self.editor.toPlainText()
        payload = self._build_payload(text)

        # 1) 字库覆盖
        cov = fontcov.coverage()
        state, order, notes = cov.diagnose_text(text, preferred=self._seg.codes)
        self._last_font_state = state
        self.chars.set_verdicts(order)
        self.chip_font.set_state(state, _short_counts(order))

        # 2) 编码是否掉字
        enc = codec.encode_text(text, self._seg.codes)
        missing = enc.missing

        # 3) 容量：名字块与 VER 槽是两套口径，不能混用
        if self._is_name:
            old_len = len(bytes.fromhex(self._hit.get("seg_hex") or ""))
            slack = int(self._hit.get("slack", 0))
            room = old_len + slack
            new_len = len(enc.data)
            ok = new_len <= room
            mode = "inplace" if new_len <= old_len else ("reflow" if ok else "")
            reason = ("原位覆盖（块内偏移不动）" if mode == "inplace"
                      else "块内重排（吃掉串间/块尾 padding，块外零位移）"
                      if mode == "reflow" else
                      "块内装不下：要长 %d 字节，这块只剩 %d 字节余量"
                      % (new_len - old_len, slack))
            est = estimate.Estimate()
            est.ok = ok
            est.state = "PASS" if ok else "BLOCK"
            est.mode = mode
            est.reason = reason
            est.capacity = room
            est.compressed = False
            est.limit = room
            est.detail = {"old_len": old_len, "slack": slack, "room": room,
                          "kind": "name", "engine": self._hit.get("engine")}
            self._last_est = est
            self.chip_cap.set_state("PASS" if ok else "BLOCK", mode)
        else:
            est = estimate.estimate(self._iso, self._hit["eid"], self._hit["tag"],
                                    self._hit["k"], self._hit["slot"], payload)
            self._last_est = est
            self.chip_cap.set_state("PASS" if est.ok else "BLOCK", est.mode or "")

        # 4) 标签完整性
        new_seg = codec.Segment(payload)
        tags_ok = (codec.tag_signature(new_seg.tags)
                   == codec.tag_signature(self._seg.tags))
        self.chip_tag.set_state("PASS" if tags_ok else "BLOCK",
                                "%d 个" % len(new_seg.tags))

        # 汇总一句话
        bits: List[str] = []
        if missing:
            bits.append("✕ 这些字没有合法游戏码位：%s（不会用 GBK/CP932 顶替）"
                        % "".join(missing))
        if state == "BLOCK":
            bits.append("✕ 字库阻断：%s" % "；".join(notes or ["存在无法渲染的字"]))
        elif state == "WARN":
            bits.append("! 字库提醒：%s" % "；".join(notes or ["部分字体包缺字"]))
        if not est.ok:
            bits.append("✕ 容量阻断：%s" % est.reason)
        else:
            bits.append("✓ 容量：%s（%d→%d 字节，%s）"
                        % (est.reason, est.detail.get("old_len", 0),
                           len(payload), est.mode))
        if not tags_ok:
            bits.append("✕ 控制标签序列被改变（正常输入不应出现，请撤销）")
        if est.compressed:
            bits.append("⚠ 该资源是压缩成员：将整条重压并独立回读校验")
        if not text.strip():
            bits.append("！正文为空：写盘后会变成空串")
        self.verdict.setText("\n".join(bits))
        col = theme.colors()
        worst = ("bad" if (not est.ok or state == "BLOCK" or missing
                           or not tags_ok)
                 else "warn" if state == "WARN" else "ok")
        self.verdict.setStyleSheet("color: %s;" % col[worst])

        self.preview.set_payload(payload)
        st = self.preview.stats()
        self.verdict.setToolTip(
            "预览估算：%d 行（上限 %d），总宽 %.1f em / 每行 %.0f em"
            % (st["lines"], st["max_lines"], st["total_em"], st["line_em"]))

    # ---------------------------------------------------------------- 动作
    def _set_enabled(self, on: bool) -> None:
        for w in (self.editor, self.btn_save, self.btn_revert, self.btn_clear,
                  self.font_size):
            w.setEnabled(on)

    def _clear_edit(self) -> None:
        if self._seg is None:
            return
        self.editor.setPlainText(self._seg.plain)

    def _revert(self) -> None:
        self._clear_edit()
        self.revertRequested.emit()

    def _explain_tag(self, tag: bytes) -> None:
        name = codec.tag_name(tag)
        args = codec.tag_args(tag)
        from PySide6.QtWidgets import QMessageBox
        desc = _TAG_HELP.get(name, "该标签用于控制对话框表现，正文编辑不应改动它。")
        QMessageBox.information(
            self, "受保护标签：%s" % name,
            "标签：%s\n参数：%s\n\n%s\n\n"
            "说明：控制标签由「显式标签操作」维护，普通输入不会修改它；"
            "本次编辑只替换中文正文，标签字节原样保留。"
            % (name, ", ".join(args) or "无", desc))

    def _save(self) -> None:
        if self._hit is None or self._seg is None:
            return
        if self._last_est is None or not self._last_est.ok:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "还不能加入队列",
                                "当前这一条存在阻断项，先把问题解决：\n\n%s"
                                % (self._last_est.reason if self._last_est else
                                   "未完成检查"))
            return
        if self._last_font_state == "BLOCK":
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "字库阻断",
                                "存在没有合法游戏码位（或全部字体包缺字）的字符，"
                                "写盘后必然显示异常。请先换字。")
            return
        text = self.editor.toPlainText()
        payload = self._build_payload(text)
        self.saveRequested.emit({"hit": self._hit, "text": text,
                                 "payload": payload, "seg": self._seg,
                                 "estimate": self._last_est,
                                 "font_state": self._last_font_state})

    # ---------------------------------------------------------------- 外部
    def current_text(self) -> str:
        return self.editor.toPlainText()

    def set_text(self, text: str) -> None:
        self.editor.setPlainText(text)


def _short_counts(order: Sequence) -> str:
    n_ok = sum(1 for v in order if v.state == "PASS")
    n_w = sum(1 for v in order if v.state == "WARN")
    n_b = sum(1 for v in order if v.state == "BLOCK")
    if n_b:
        return "阻断 %d 字" % n_b
    if n_w:
        return "提醒 %d 字" % n_w
    return "%d 字齐备" % n_ok


_TAG_HELP: Dict[str, str] = {
    "operator": "说话人/头像标识。参数第一位是角色编号。",
    "speed": "文字显示速度（打字机速度）。",
    "color": "后续文字颜色档位。",
    "ce": "颜色结束，恢复默认色。",
    "book": "术语/词条高亮开始（实机显示为下划线或强调色）。",
    "endbook": "术语高亮结束。",
    "thumbnail": "对话框角标小图（人物头像缩略）。",
    "thumbdown": "角标小图收下。",
    "sp": "等待停顿（毫秒或帧），不是空格。",
    "ab": "自动推进等待（对话结束前停留）。",
    "on": "对话框开始（开框）。",
    "off": "对话框结束（关框）。",
    "op": "开口型对白开始。",
    "ed": "对白结束标记。",
    "cs": "场景切换标记。",
    "marker": "分支/事件标记。",
    "move": "位移控制。",
    "picture": "插入插图。",
    "delpic": "移除插图。",
    "refer": "引用其它文本块。",
    "freezemouth": "冻结口型动画。",
    "topic": "标题条。",
    "line": "分隔线。",
}
