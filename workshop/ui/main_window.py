"""主窗口：三栏布局 + 顶部工具条 + 状态栏 + 快捷键 + 自动草稿。

设计纪律（任务书 §一/§五/增强需求 5/10）
-----------------------------------------
* 搜索、编辑、写盘三段彻底分离：编辑只进队列，写盘只在检查台里发生。
* 每次「加入队列」立即落草稿；关程序后可恢复。
* 草稿永远绑定父盘 MD5，跨版本不复用。
"""
from __future__ import annotations

import os
import traceback
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QSplitter, QStatusBar, QVBoxLayout,
                               QWidget)

from core import editplan, env, paths, search as S, settings

from . import theme
from .checkdesk import CheckDesk
from .editor import EditorPanel
from .indexmgr import IndexManager
from .queue import QueuePanel
from .results import ResultsPanel
from .widgets import Toast
from .workers import JobRunner


class MainWindow(QMainWindow):
    """ACE3 汉化工坊主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        env.ensure_dirs()
        self.setWindowTitle("ACE3 汉化工坊")
        self.resize(1560, 940)
        self.setMinimumSize(1180, 720)

        self.queue = editplan.PlanQueue()
        self.runner = JobRunner(self)
        self.runner.busy_changed.connect(self._on_busy)
        self.runner.progress.connect(self._on_progress)
        self._iso: str = ""
        self._editing_key: Optional[tuple] = None
        self._dirty = False
        self._pending_index: str = ""      # 排队中的建索引请求（当前任务结束后自动开始）

        # ---- 顶部工具条
        top = QFrame()
        top.setObjectName("PanelAlt")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(12, 7, 12, 7)
        tl.setSpacing(8)
        title = QLabel("ACE3 汉化工坊")
        title.setObjectName("PanelTitle")
        tl.addWidget(title)
        self.iso_label = QLabel("未选择 ISO")
        self.iso_label.setObjectName("Faint")
        tl.addWidget(self.iso_label, 1)

        self.btn_open = QPushButton("打开 ISO…")
        self.btn_open.setObjectName("Primary")
        self.btn_open.setToolTip("选择父盘（只读使用；所有产物写到别处）")
        self.btn_open.clicked.connect(self.open_iso)
        tl.addWidget(self.btn_open)

        self.btn_index = QPushButton("建索引")
        self.btn_index.setToolTip("为当前 ISO 建检索索引（只写 db 目录，不动 ISO）")
        self.btn_index.clicked.connect(self.build_index)
        self.btn_index.setEnabled(False)
        tl.addWidget(self.btn_index)

        self.btn_index_mgr = QPushButton("索引…")
        self.btn_index_mgr.setObjectName("Ghost")
        self.btn_index_mgr.setToolTip("查看 / 清理已建的索引缓存（可重建，不动 ISO）")
        self.btn_index_mgr.clicked.connect(self.open_index_manager)
        tl.addWidget(self.btn_index_mgr)

        self.btn_draft = QPushButton("草稿")
        self.btn_draft.setToolTip("保存 / 恢复 / 导出编辑计划")
        self.btn_draft.clicked.connect(self.show_draft_menu)
        tl.addWidget(self.btn_draft)

        self.btn_theme = QPushButton("浅色")
        self.btn_theme.setObjectName("Ghost")
        self.btn_theme.setToolTip("切换深色 / 浅色")
        self.btn_theme.clicked.connect(self.toggle_theme)
        tl.addWidget(self.btn_theme)

        self.btn_check = QPushButton("统一写盘检查  Ctrl+Shift+E")
        self.btn_check.setObjectName("Primary")
        self.btn_check.clicked.connect(self.open_checkdesk)
        tl.addWidget(self.btn_check)

        # ---- 三栏
        self.results = ResultsPanel()
        self.editor = EditorPanel()
        self.qpanel = QueuePanel()
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.results)
        split.addWidget(self.editor)
        split.addWidget(self.qpanel)
        split.setSizes([430, 660, 470])
        split.setChildrenCollapsible(False)

        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(top)
        cv.addWidget(split, 1)
        self.setCentralWidget(central)

        # ---- 状态栏
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.status = QLabel("就绪 · 全程离线")
        sb.addWidget(self.status, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setMaximumWidth(180)
        self.bar.setVisible(False)
        sb.addPermanentWidget(self.bar)

        # ---- 权限与写盘守则（常驻右下角，点一下能看详情）
        self.perm = QLabel("")
        self.perm.setObjectName("Faint")
        self.perm.setCursor(Qt.PointingHandCursor)
        self.perm.setToolTip("点击查看权限与写盘目录的详细情况")
        self.perm.mousePressEvent = lambda _e: self.show_path_status()   # noqa: ARG005
        sb.addPermanentWidget(self.perm)
        self._refresh_path_status()

        self.toast = Toast(central)

        # ---- 接线
        self.results.hitActivated.connect(self.on_hit)
        self.results.hitDropped.connect(lambda: self.on_hit(None))
        self.results.indexChanged.connect(self._on_index_changed)
        self.results.isoRequested.connect(self.open_iso)
        self.results.searchRequested.connect(self.run_search)
        self.editor.saveRequested.connect(self.on_save_requested)
        self.editor.revertRequested.connect(self.on_revert)
        self.qpanel.removeRequested.connect(self.on_remove)
        self.qpanel.recheckRequested.connect(self.recheck_all)
        self.qpanel.writeRequested.connect(self.open_checkdesk)
        self.qpanel.draftRequested.connect(lambda: self.save_draft())
        self.qpanel.openRequested.connect(self.on_open_item)
        self.qpanel.revertRequested.connect(self.on_remove)

        self._install_shortcuts()

        # ---- 自动草稿
        self._auto = QTimer(self)
        self._auto.setInterval(45_000)
        self._auto.timeout.connect(self._auto_save)
        self._auto.start()

        self.qpanel.set_queue(self.queue)
        self.set_iso(None)
        QTimer.singleShot(0, self._startup)

    # ------------------------------------------------------------ 快捷键
    def _install_shortcuts(self) -> None:
        def add(seq: str, fn) -> None:           # noqa: ANN001
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(fn)

        add("Ctrl+F", self.results.focus_search)
        add("Ctrl+S", lambda: self.save_draft())
        add("Ctrl+Enter", self._hotkey_commit)
        add("F5", self.recheck_all)
        add("Ctrl+Shift+E", self.open_checkdesk)
        add("Ctrl+Z", self._hotkey_undo)
        add("Ctrl+Y", self._hotkey_redo)

    def _hotkey_commit(self) -> None:
        if self.editor.isEnabled():
            self.editor._save()                  # noqa: SLF001 同一模块内的动作

    def _hotkey_undo(self) -> None:
        if self.editor.editor.hasFocus():
            self.editor.editor.undo()
        else:
            self.status.setText("撤销：编辑框获得焦点时可用于文本撤销（Ctrl+Z）")

    def _hotkey_redo(self) -> None:
        if self.editor.editor.hasFocus():
            self.editor.editor.redo()

    # ------------------------------------------------------------ 权限与路径守则
    def _refresh_path_status(self) -> None:
        """把「是不是管理员 / 能不能写 / 写到哪」钉在状态栏右下角。"""
        st = paths.status()
        self.perm.setText(("🔒 管理员" if st["admin"] else "⚠ 非管理员")
                          + " ｜ 不写 %s: 盘" % st["system_drive"])
        self.perm.setToolTip(paths.status_text())
        # 工具自己落在系统盘时：写盘一定失败，必须显眼提示（不是等用户点了才说）
        bad = paths.require_tool_off_system_drive()
        if bad:
            self.perm.setStyleSheet("color:#ff8a80;")
            self.status.setText("⚠ 工具位于系统盘，无法写盘：请把整个「ACE3汉化工坊」目录移到非系统盘")
        elif not st["admin"]:
            self.perm.setStyleSheet("color:#ffd54f;")
        else:
            self.perm.setStyleSheet("")

    def show_path_status(self) -> None:
        """点击右下角权限标记 → 详情（管理员、写盘根目录、临时目录）。"""
        st = paths.status()
        lines = [
            "权限：%s" % ("管理员（已提权）" if st["admin"] else "普通权限（未提权）"),
            "系统盘：%s:" % st["system_drive"],
            "可写根目录：%s" % st["write_root"],
            "工作目录：%s" % st["work_dir"],
            "临时目录：%s" % st["temp_dir"],
            "",
            "守则：一律不写 %s: 盘；所有产物（草稿/报告/新 ISO）只落在工具自身目录内。"
            % st["system_drive"],
        ]
        bad = paths.require_tool_off_system_drive()
        if bad:
            lines += ["", "⚠ " + bad]
        elif not st["admin"]:
            lines += ["", "提示：当前不是管理员权限。若某些目录写入被系统拒绝，"
                          "请右键 EXE →「以管理员身份运行」。"]
        if paths.last_redirect():
            lines += ["", "本进程内最近一次改道：", paths.last_redirect()]
        QMessageBox.information(self, "权限与写盘守则", "\n".join(lines))

    # ------------------------------------------------------------ 启动
    def _startup(self) -> None:
        infos = S.list_indexes()
        if not infos:
            self.status.setText("没有可用索引：先「打开 ISO…」再「建索引」")
            return
        # 优先：冻结工具默认索引 → 其余按 built_at 倒序 → 首个磁盘上存在的
        usable = [i for i in infos if i.get("iso_exists")]
        if not usable:
            self.status.setText("索引存在但对应 ISO 不在磁盘上：请重新打开 ISO 并建索引")
            return
        default_stem = getattr(S.Q, "DEFAULT_STEM", "")
        pick = next((i for i in usable if i["stem"] == default_stem), None)
        if pick is None:
            pick = max(usable, key=lambda i: i.get("built_at") or "")
        self._load_index(pick)

    def _load_index(self, info: dict) -> None:
        try:
            idx = S.open_index(info["db"])
        except Exception as exc:                 # noqa: BLE001
            QMessageBox.warning(self, "索引不可用", "%s" % exc)
            return
        self.results.set_index(idx)
        iso = idx.iso_path
        self.set_iso(iso, idx=idx)
        self.status.setText("已载入索引：%s（%d 槽）"
                            % (os.path.basename(info["db"]), idx.slot_total))

    def _on_index_changed(self, idx) -> None:     # noqa: ANN001
        """左栏版本下拉切换了索引：父盘、队列、编辑器必须一起跟着换。"""
        if idx is None:
            return
        iso = idx.iso_path
        if not iso:
            return
        self.on_hit(None)
        self.set_iso(iso, idx=idx)
        self.status.setText("已切换版本：%s（%d 槽）"
                            % (os.path.basename(idx.db_path), idx.slot_total))
        self.toast.show_message("机师，换盘完成！", 2000)

    # ------------------------------------------------------------ 检索
    def run_search(self, needle: str, with_names: bool) -> None:
        """把检索丢进后台。

        含名字块时首次要扫全盘 272 个 VMD（实测 19 秒），放在 GUI 线程里
        整窗会假死；所以统一走 JobRunner，结果回来再由左栏渲染。
        """
        idx = self.results._index              # noqa: SLF001
        if idx is None:
            self.results.hint.setText("尚未选择 ISO 或索引未就绪。")
            return

        def work(progress=None) -> dict:
            if progress is not None:
                progress("检索「%s」%s…" % (needle,
                                          "（含名字块）" if with_names else ""))
            return S.run_query(idx, needle, limit=300, with_names=with_names)

        def done(res) -> None:                  # noqa: ANN001
            self.results.show_result(res)
            self.status.setText("检索完成：%d 条" % len(res.get("hits") or []))

        def fail(msg: str, tb: str) -> None:
            self.results.hint.setText("检索失败：%s" % msg)
            self.status.setText("检索失败：%s" % msg)

        if not self.runner.start("检索", work, done, fail):
            self.results.hint.setText("当前有任务在跑（%s），等它结束再搜。"
                                      % (self.runner.label() or "未知任务"))

    # ------------------------------------------------------------ ISO
    def open_iso(self) -> None:
        # 记住上次选 ISO 的目录（存在 work/settings.json，不碰注册表）。
        # 目录失效（被删/移动）时 recall_dir 会自动退回工程根。
        start = settings.recall_dir("iso_dir", env.ROOT)
        path, _ = QFileDialog.getOpenFileName(
            self, "选择父盘 ISO（只读使用）", start, "ISO 镜像 (*.iso);;所有文件 (*.*)")
        if not path:
            return
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            QMessageBox.warning(self, "ISO 不存在", path)
            return
        size = os.path.getsize(path)
        if size < 1 << 20:
            QMessageBox.warning(self, "不是有效 ISO",
                                "文件只有 %d 字节，明显不是 ACE3 盘。" % size)
            return
        settings.remember_dir("iso_dir", os.path.dirname(path))
        # 索引目录是按文件名（stem）命名的：同一张盘搬了位置、或同名的换了一张盘，
        # 都会命中上届留下的索引。先试着「认回」——只有大小与 MD5 都与建库时
        # 一致才能改绑，内容不同就老老实实重建，绝不拿旧索引套新盘。
        info = S.adopt_index(path)
        if info is not None and info.get("iso_exists"):
            self._load_index(info)
            self.toast.show_message("机师，盘已就位！", 2200)
            return
        # 注意：必须用 find_info()（返回 dict），不是 find_index()（返回路径字符串）。
        # 传字符串给 _load_index(info: dict) 会报 string indices must be integers, not 'str'。
        info = S.find_info(path)
        if info is None:
            r = QMessageBox.question(
                self, "还没有索引",
                "这个 ISO 还没有索引。现在就建吗？\n\n"
                "建索引只读 ISO、只写检索缓存目录，不会改动盘面。\n全盘扫描约需 1–3 分钟。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if r == QMessageBox.Yes:
                self._build_for(path)
            return
        if not info.get("iso_exists"):
            # 同名索引记的是另一张盘（内容对不上），不能拿它来定位。
            r = QMessageBox.warning(
                self, "索引与这张盘对不上",
                "检索缓存目录里已有一份同名索引，但它记的是另一张盘：\n%s\n\n"
                "大小或 MD5 对不上，不能拿来定位，否则搜出来的槽会指错位置。\n\n"
                "要就地重建这份索引吗？（只覆盖检索缓存，不动 ISO）" % info["iso"],
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if r == QMessageBox.Yes:
                self._build_for(path)
            return
        self._load_index(info)
        self.toast.show_message("机师，盘已就位！", 2200)

    def open_index_manager(self) -> None:
        """索引缓存管理：单删 / 多选删 / 全删。"""
        cur_db = getattr(self.results, "_idx", None)
        cur_db = getattr(cur_db, "db_path", "") if cur_db is not None else ""
        dlg = IndexManager(self, current_db=cur_db)
        dlg.exec()
        gone = dlg.deleted()
        if not gone:
            return
        # 删完必须让版本下拉跟着刷新，否则界面上还挂着已经不存在的版本。
        self.results._refresh_versions()          # noqa: SLF001
        cur_iso = self._iso or ""
        cur_stem = os.path.splitext(os.path.basename(cur_iso))[0] if cur_iso else ""
        self.status.setText("已清理 %d 份索引缓存：%s" % (len(gone), "、".join(gone)))
        if cur_stem and cur_stem in gone:
            # 当前在用的那份被删了（正常情况下对话框不允许勾选，这里只是兜底）
            self.results.set_index(None)
            self.on_hit(None)
            self.status.setText("当前版本的索引已删除，请重新建索引或换一个版本")
        self.toast.show_message("索引清完了，空间回来了", 2400)

    def build_index(self) -> None:
        if not self._iso:
            return
        self._build_for(self._iso)

    def _build_for(self, iso: str) -> None:
        # ``busy`` 是方法，必须调用；写漏括号会变成「永远为真」，
        # 表现就是不管有没有任务都弹「当前有任务在跑」（真实踩过）。
        if self.runner.busy():
            # 不打断也不丢弃：记下来，等当前任务收尾后自动接着建。
            self._pending_index = iso
            self.status.setText(
                "当前有任务在跑（%s）…建索引已排队，结束后自动开始。"
                % (self.runner.label() or "未知任务"))
            self.toast.show_message("已排队，等当前任务收尾", 2400)
            return
        self.status.setText("正在建索引（只读 ISO，写检索缓存）…")

        def work(progress=None):
            return S.build_index(iso, progress=progress)

        def done(db: str) -> None:
            # 同 open_iso：这里要 dict，不能用返回路径字符串的 find_index()
            info = S.find_info(iso)
            if info is None:
                QMessageBox.warning(self, "索引结果缺失", "建完了但找不到索引文件。")
                return
            self._load_index(info)
            self.toast.show_message("索引就绪，可以开搜了", 2600)

        def fail(msg: str, tb: str) -> None:
            QMessageBox.critical(self, "建索引失败", msg)
            self.status.setText("建索引失败")

        self.runner.start("建索引", work, done, fail)

    def set_iso(self, iso: Optional[str], idx=None) -> None:   # noqa: ANN001
        self._iso = iso or ""
        self.editor.set_iso(self._iso)
        self.qpanel.set_iso(self._iso)
        if iso:
            self.iso_label.setText("%s" % os.path.basename(iso))
            self.iso_label.setToolTip(iso)
            self.btn_index.setEnabled(True)
            if self.queue.parent.get("path") != os.path.abspath(iso):
                self._bind_queue(iso)
        else:
            self.iso_label.setText("未选择 ISO")
            self.iso_label.setToolTip("")
            self.btn_index.setEnabled(False)

    def _bind_queue(self, iso: str) -> None:
        """绑定父盘；若存在同 MD5 的草稿则询问是否恢复。"""
        try:
            md5 = idx_md5 = None
            if self.results._index is not None and \
                    os.path.abspath(self.results._index.iso_path) == os.path.abspath(iso):
                md5 = self.results._index.iso_md5
        except Exception as exc:                 # noqa: BLE001
            self.status.setText("父盘指纹计算失败：%s" % exc)
            return
        # 换盘守卫：队列里的修改绑在**上一张盘**的槽容量与偏移上算出来，
        # 跨版本直接抱过来就是「复用旧偏移」——轻则检查台报原字节指纹不符，
        # 重则把字写进另一张盘的同号槽。所以换盘必须断开：先把旧盘草稿落盘
        # （草稿名按旧父盘 MD5），再清空队列，由用户在新盘上重做。
        old_md5 = (self.queue.parent or {}).get("md5") or ""
        carried = len(self.queue.items)
        if carried and old_md5 and md5 and old_md5 != md5:
            try:
                self.save_draft(quiet=True)
            except Exception:                   # noqa: BLE001
                pass
            self.queue.clear()
            self.qpanel.set_queue(self.queue)
            self.editor.load_hit(None)
            QMessageBox.information(
                self, "已换盘，队列已清空",
                "换到了另一张 ISO。上一张盘上的 %d 条修改绑的是旧盘的槽容量与偏移，"
                "不能跨版本沿用，已留在旧盘草稿里（换回那张盘可以再恢复）。\n\n"
                "请在当前这张盘上重新选择文本编辑。" % carried)
            self.toast.show_message("机师，换盘完毕，队列已清空", 2800)
        try:
            self.queue.bind(iso, md5)
        except Exception as exc:                 # noqa: BLE001
            self.status.setText("父盘指纹计算失败：%s" % exc)
            return
        self.qpanel.refresh()
        name = self.queue.draft_name()
        path = os.path.join(env.DRAFT_DIR, name)
        if not os.path.exists(path):
            return
        try:
            peek = editplan.PlanQueue()
            info = peek.load_draft(path, verify_parent=False)
        except Exception:                        # noqa: BLE001
            return
        r = QMessageBox.question(
            self, "发现上次的草稿",
            "这张盘上次留下了 %d 条未写盘的修改（草稿时间 %s）。\n\n现在恢复吗？"
            % (info.get("items", 0), info.get("saved_at", "?")),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r == QMessageBox.Yes:
            try:
                got = self.queue.load_draft(path, verify_parent=True)
                self.qpanel.set_queue(self.queue)
                self.toast.show_message("已恢复 %d 条修改" % got.get("items", 0), 2600)
            except Exception as exc:             # noqa: BLE001
                QMessageBox.warning(self, "草稿无法恢复", str(exc))

    # ------------------------------------------------------------ 检索事件
    def on_hit(self, hit: Optional[dict]) -> None:
        if not hit:
            self.editor.load_hit(None)
            return
        # 同槽在队列里已有修改时，直接用队列里的文本作为编辑起点
        key = editplan.hit_key(hit)
        it = self.queue.get(key)
        self.editor.load_hit(hit)
        if it is not None:
            self.editor.set_text(it.new_text)
            self.status.setText("该槽队列里已有修改，编辑框载入的是队列内容")
        self.qpanel.select(key)

    # ------------------------------------------------------------ 队列事件
    def on_save_requested(self, data: dict) -> None:
        hit = data["hit"]
        text = data["text"]
        key = editplan.hit_key(hit)
        is_name = hit.get("kind") == "name"

        # 镜像提示：同文句可能在其它 EID 出现，必须由用户确认，绝不自动同步
        if not self.queue.get(key):
            sib = self._siblings(hit, key)
            if sib:
                r = QMessageBox.question(
                    self, "发现相同的句子",
                    "这句话在另外 %d 个位置也一模一样（可能是镜像/复用槽）。\n\n"
                    "是「只修改当前这一句」，还是先「检查其他相同文本」？\n\n"
                    "（本工具不会默认同步所有同文槽，语境不同的对白往往不该一起改。）"
                    % len(sib),
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                    QMessageBox.Yes)
                if r == QMessageBox.Cancel:
                    return
        if is_name:
            item, missing = editplan.item_from_name(hit, text)
        else:
            item, missing = editplan.item_from_hit(hit, text, data["seg"].codes)
        if missing:
            r = QMessageBox.warning(
                self, "有字符没有游戏码位",
                "这些字在 ACE3 码表里找不到合法码位：%s\n\n"
                "写盘后必然显示异常（不会用 GBK/CP932 顶替）。仍然加入队列吗？"
                % "".join(missing),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        est = data.get("estimate")
        if est is not None:
            item.capacity = est.capacity or item.capacity
            item.compressed = bool(est.compressed)
        item.state = editplan.STATE_PENDING
        action = self.queue.upsert(item)
        self.qpanel.refresh()
        self.qpanel.select(key)
        self.save_draft(quiet=True)
        if action == "updated":
            self.toast.show_message("已更新这条修改", 1800)
        else:
            self.toast.show_message("机师，译文已就位！", 1800)
        self.status.setText("队列 %d 条 · 尚未写盘" % len(self.queue.items))
        self._dirty = True

    def _siblings(self, hit: dict, key: tuple) -> List[dict]:
        plain = (hit.get("text") or "").strip()
        if len(plain) < 3:
            return []
        if hit.get("kind") == "name":
            # 名字块不在检索索引里，必须回名字块引擎里找同文副本
            iso = self.results.iso_path()
            if not iso:
                return []
            try:
                from core import nametext as NT
                out = []
                for h in NT.search_hits(iso, plain, limit=60):
                    if editplan.hit_key(h) == tuple(key):
                        continue
                    if (h.get("text") or "").strip() != plain:
                        continue
                    out.append(h)
                return out[:12]
            except Exception:                    # noqa: BLE001
                return []
        idx = self.results._index
        if idx is None:
            return []
        try:
            return idx.siblings_same_text(plain, exclude=key, limit=12)
        except Exception:                        # noqa: BLE001
            return []

    def on_remove(self, keys: List[tuple]) -> None:
        if not keys:
            return
        n = 0
        for k in keys:
            if self.queue.remove(tuple(k)):
                n += 1
        self.qpanel.refresh()
        if n:
            self.save_draft(quiet=True)
            self.status.setText("已从队列移除 %d 条（父盘未改动）" % n)

    def on_revert(self) -> None:
        hit = self.results.current_hit()
        if hit:
            key = editplan.hit_key(hit)
            if self.queue.remove(key):
                self.qpanel.refresh()
                self.save_draft(quiet=True)
                self.status.setText("已恢复原文并从队列移除")

    def on_open_item(self, d: dict) -> None:
        # 队列项可能来自别的检索词，甚至可能不在当前结果列表里，
        # 所以先按队列身份在结果里找，找不到就现场重建命中。
        kind = d.get("kind") or "slot"
        if kind == "name":
            key = ("name", int(d["eid"]), int(d.get("name_block_pos") or 0),
                   int(d["slot"]))
        else:
            key = (int(d["eid"]), int(d["tag"]), int(d["k"]), int(d["slot"]))
        hit = None
        if self.results.select_by_plan_key(key):
            hit = self.results.current_hit()
        if hit is None and kind == "name" and self._iso:
            from core import nametext as NT
            try:
                for h in NT.hits_of(self._iso, int(d["eid"])):
                    if (int(h.get("block_pos", 0)) == key[2]
                            and int(h["slot"]) == key[3]):
                        hit = NT.as_hit(h)
                        break
            except Exception:                    # noqa: BLE001
                hit = None
        if hit is None and kind == "slot" and self._iso:
            idx = self.results._index
            if idx is not None:
                try:
                    hit = idx.slot(*key)
                except Exception:                # noqa: BLE001
                    hit = None
        if hit:
            self.editor.load_hit(hit)
            it = self.queue.get(key)
            if it is not None:
                self.editor.set_text(it.new_text)
        self.status.setText("重新编辑 %s" % d.get("where", ""))

    def recheck_all(self) -> None:
        if not self.queue.items:
            self.status.setText("队列是空的")
            return
        if not self._iso:
            return
        self.status.setText("正在检查 %d 条修改…" % len(self.queue.items))

        def work(progress=None):
            return _inspect(self._iso, self.queue.items, progress)

        def done(rep: dict) -> None:
            self.qpanel.refresh()
            msg = ("检查完成：可写 %d · 警告 %d · 阻断 %d（%.1fs）"
                   % (rep["safe"], rep["warn"], rep["blocked"], rep["elapsed"]))
            self.status.setText(msg)
            if rep["blocked"]:
                QMessageBox.warning(
                    self, "有阻断项",
                    "有 %d 条存在阻断，写盘会被拒绝：\n\n%s"
                    % (rep["blocked"],
                       "\n".join("%s → %s" % (d["where"], d["why"])
                                 for d in (rep.get("blocked_detail") or [])[:5])))
            else:
                self.toast.show_message("检查通过，可以写盘", 2000)

        def fail(msg: str, tb: str) -> None:
            QMessageBox.critical(self, "检查失败", msg)

        self.runner.start("检查", work, done, fail)

    # ------------------------------------------------------------ 检查台
    def open_checkdesk(self) -> None:
        if not self._iso:
            QMessageBox.information(self, "先选 ISO", "还没有绑定父盘。")
            return
        if not self.queue.items:
            QMessageBox.information(self, "队列是空的",
                                    "先把至少一条修改加入队列，再来检查台。")
            return
        dlg = CheckDesk(self, self._iso, self.queue.items, self.queue, self.runner)
        dlg.exec()
        man = dlg.result()
        if man:
            self.status.setText("写盘完成：%s" % man["output"]["path"])
            self.qpanel.refresh()

    # ------------------------------------------------------------ 草稿
    def save_draft(self, quiet: bool = False, auto: bool = False) -> None:
        if not self.queue.items:
            if not quiet:
                self.status.setText("队列是空的，没有可保存的草稿")
            return
        try:
            path = self.queue.save_draft(note="自动保存" if auto else "")
        except Exception as exc:                 # noqa: BLE001
            QMessageBox.warning(self, "草稿保存失败", str(exc))
            return
        self._dirty = False
        self.status.setText("草稿已保存：%s（%d 条）"
                            % (os.path.basename(path), len(self.queue.items)))
        if not quiet:
            self.toast.show_message("草稿已保存", 1600)

    def _auto_save(self) -> None:
        if self.queue.items and self._dirty:
            self.save_draft(quiet=True, auto=True)

    def show_draft_menu(self) -> None:
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        a1 = m.addAction("保存草稿  Ctrl+S")
        a2 = m.addAction("从草稿恢复…")
        m.addSeparator()
        a3 = m.addAction("导出编辑计划 JSON…")
        a4 = m.addAction("导入编辑计划 JSON…")
        m.addSeparator()
        a5 = m.addAction("清空队列")
        act = m.exec(self.btn_draft.mapToGlobal(self.btn_draft.rect().bottomLeft()))
        if act is a1:
            self.save_draft()
        elif act is a2:
            self._restore_draft()
        elif act is a3:
            self._export_plan()
        elif act is a4:
            self._import_plan()
        elif act is a5:
            if not self.queue.items:
                return
            r = QMessageBox.warning(self, "清空队列",
                                    "将丢弃当前 %d 条暂存修改（父盘不受影响）。\n"
                                    "已保存的草稿文件不会被删。确定？"
                                    % len(self.queue.items),
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r == QMessageBox.Yes:
                self.queue.clear()
                self.qpanel.refresh()
                self.status.setText("队列已清空")

    def _restore_draft(self) -> None:
        start = env.DRAFT_DIR
        path, _ = QFileDialog.getOpenFileName(
            self, "选择草稿 / 编辑计划", start, "JSON (*.json)")
        if not path:
            return
        try:
            info = self.queue.load_draft(path, verify_parent=True)
        except Exception as exc:                 # noqa: BLE001
            QMessageBox.critical(
                self, "草稿无法恢复",
                "%s\n\n（草稿损坏时不会覆盖上一份有效备份，也不会改动父盘。）" % exc)
            return
        self.qpanel.set_queue(self.queue)
        self.toast.show_message("已恢复 %d 条修改" % info.get("items", 0), 2400)
        for w in info.get("warnings") or []:
            self.status.setText(w)

    def _export_plan(self) -> None:
        if not self.queue.items:
            self.status.setText("队列是空的")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出编辑计划", os.path.join(env.DRAFT_DIR, "编辑计划.json"),
            "JSON (*.json)")
        if not path:
            return
        # 守则：导出文件也不落系统盘
        try:
            path = paths.check(path, "导出编辑计划")
        except paths.PathRuleError as exc:
            QMessageBox.warning(self, "保存位置不符合守则", str(exc))
            return
        try:
            self.queue.export_json(path)
        except Exception as exc:                 # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.toast.show_message("编辑计划已导出", 1800)

    def _import_plan(self) -> None:
        if not self._iso:
            QMessageBox.information(self, "先选 ISO", "导入的修改必须绑定父盘。")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "导入编辑计划", env.DRAFT_DIR, "JSON (*.json)")
        if not path:
            return
        try:
            res = self.queue.import_json(path, merge=True, verify_parent=True)
        except Exception as exc:                 # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self.qpanel.refresh()
        self.save_draft(quiet=True)
        msg = "导入：新增 %d、更新 %d" % (res["added"], res["updated"])
        if res.get("warning"):
            msg += "\n！%s" % res["warning"]
        QMessageBox.information(self, "导入完成", msg)

    # ------------------------------------------------------------ 主题
    def toggle_theme(self) -> None:
        name = "light" if theme.theme_name() == "dark" else "dark"
        theme.set_theme(name)
        theme.apply_theme(QApplication.instance(), name)
        self._restyle()
        self.btn_theme.setText("浅色" if name == "dark" else "深色")

    def _restyle(self) -> None:
        for w in (self.results, self.editor, self.qpanel):
            w.style().unpolish(w)
            w.style().polish(w)
            w.update()
        # 结果行与徽章的底色是行内样式，必须重建才能跟随主题
        self.results.restyle()
        self.qpanel.refresh()

    # ------------------------------------------------------------ 任务状态
    def _on_busy(self, busy: bool) -> None:
        self.bar.setVisible(busy)
        # 「索引…」也一起禁：任务跑着的时候删缓存目录，正在读那份索引的
        # 后台线程会拿到半张表。等任务结束再让用户动它。
        for b in (self.btn_open, self.btn_index, self.btn_index_mgr,
                  self.btn_check, self.btn_draft):
            b.setEnabled(not busy)
        if not busy and self._pending_index:
            iso = self._pending_index
            self._pending_index = ""
            # 让排队请求在当前信号的调用栈之后启动，避免重入 runner.start。
            QTimer.singleShot(0, lambda: self._build_for(iso))

    def _on_progress(self, msg: str) -> None:
        self.status.setText(msg)

    def closeEvent(self, ev) -> None:            # noqa: ANN001
        if self.queue.items and self._dirty:
            r = QMessageBox.question(
                self, "还有未保存的修改",
                "队列里有 %d 条修改还没存草稿。\n保存后退出吗？"
                % len(self.queue.items),
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save)
            if r == QMessageBox.Cancel:
                ev.ignore()
                return
            if r == QMessageBox.Save:
                try:
                    self.queue.save_draft(note="退出前保存")
                except Exception as exc:         # noqa: BLE001
                    QMessageBox.warning(self, "草稿保存失败", str(exc))
                    ev.ignore()
                    return
        self.runner.shutdown()
        S.close_all()
        ev.accept()


def _inspect(iso: str, items, progress=None) -> dict:   # noqa: ANN001
    from core import pipeline
    return pipeline.inspect(iso, items, progress=progress)
