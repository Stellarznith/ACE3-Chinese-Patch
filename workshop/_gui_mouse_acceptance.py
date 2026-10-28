"""GUI 鼠标操作验收（真实窗口 + 真实 QTest 鼠标点击 + 测试 ISO 副本）。

任务书收口第 4 条要求「一次实际 GUI 鼠标操作验收」：
搜索 → 编辑 → 入队 → 保存 / 恢复草稿 → 统一写盘 → 重新打开产物并查询。

纪律
----
* **只用测试 ISO 副本**：先把冻结父盘整份复制到 ``work/mouse/``，GUI 打开的是副本。
  父盘在前后各记一次 MD5 与 mtime，必须完全一致。
* 走**真实窗口**与**真实鼠标事件**（``QTest.mouseClick``），不用离屏、不直接调
  ``pipeline`` 绕过界面。对话框（选目录 / 确认框 / 完成提示）按验收需要打桩，
  打桩只替换「人点按钮」这一步，不替换任何判定逻辑。
* 每一步都留可复核证据：控件文本、队列条数、草稿路径与字节数、产物路径与 MD5、
  以及**用冻结检索层独立回读**产物盘面的结果。
* 中文输入只能走「聚焦后写入控件」的方式（模拟 IME 不在范围内），
  脚本会记录实际用了 keyClicks 还是 setText，并在报告里注明。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main as entry                                        # noqa: E402

entry._ensure_stdio()                                       # noqa: SLF001

from PySide6.QtCore import Qt, QTimer                       # noqa: E402
from PySide6.QtGui import QKeySequence                      # noqa: E402
from PySide6.QtTest import QTest                            # noqa: E402
from PySide6.QtWidgets import (QApplication, QFileDialog,    # noqa: E402
                               QMessageBox)

from core import editplan, env, search as S                  # noqa: E402
import ui.main_window as MW                                 # noqa: E402
from ui import theme                                        # noqa: E402

_T0 = __import__("time").time()   # 全程计时起点（finish() 里写进报告）
MOUSE_DIR = os.path.join(env.WORK_DIR, "mouse")
ISO_DIR = os.path.join(MOUSE_DIR, "iso_out")
REPORT_JSON = os.path.join(MOUSE_DIR, "鼠标验收报告.json")
REPORT_MD = os.path.join(MOUSE_DIR, "鼠标验收报告.md")

RESULTS: list = []
NOTES: list = []
DIALOGS: list = []

# 目标槽与文本。方向刻意选**正向**（日文原词 → 正式中文），与十五项验收第 1–3 项
# 同向，避免报告里把「测试写回日文用词」误读成正式译文被改。
BASE_STEM = "r1354_grayline_clamp_test"   # 该盘该槽仍是「何？」（尚未汉化）
TARGET = (4004643, 0, 0, 9)               # EID / tag / k / slot
OLD_TEXT = "何？"                          # 起点文本（搜索用）
NEW_TEXT = "什么？"                        # 目标文本（正式译法方向）


# ------------------------------------------------------------------ 基础工具
def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 22)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def record(no: int, name: str, ok: bool, detail: str, evidence=None) -> None:
    RESULTS.append({"no": no, "name": name, "ok": bool(ok),
                    "detail": detail, "evidence": evidence or {}})
    print("  [%s] %d. %s — %s" % ("PASS" if ok else "FAIL", no, name, detail))


def pump(ms: int = 120) -> None:
    """推进事件循环 ms 毫秒（真实窗口下必须靠这个让后台线程结果回到界面）。"""
    app = QApplication.instance()
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def pump_until(pred, timeout: float = 60.0, step: int = 100) -> bool:  # noqa: ANN001
    end = time.time() + timeout
    app = QApplication.instance()
    while time.time() < end:
        app.processEvents()
        try:
            if pred():
                return True
        except BaseException:                    # noqa: BLE001
            pass
        time.sleep(0.01)
    app.processEvents()
    try:
        return bool(pred())
    except BaseException:                        # noqa: BLE001
        return False


def click(widget, label: str = "") -> None:      # noqa: ANN001
    """真实鼠标左键点击控件中心。"""
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, widget.rect().center())
    pump(60)
    if label:
        print("      点击 %s" % label)


def double_click(widget, pos=None) -> None:      # noqa: ANN001
    QTest.mouseDClick(widget, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier,
                      pos or widget.rect().center())
    pump(60)


# ------------------------------------------------------------------ 对话框打桩
# 引用计数：main() 一进来就装一层（全程有效），个别步骤再套一层；
# 只有最外层 remove 才真正还原。原因：真实窗口里任何一次未打桩的模态框都会
# 把脚本无限期挂在 QMessageBox.exec 里（实测卡死 39 分钟，CPU 只走了 11 秒）。
_STUB_STATE = {"st": None, "depth": 0}


def install_stubs(out_dir: str) -> dict:
    """把「人点对话框」换成脚本代答；不替换任何业务判定。"""
    if _STUB_STATE["st"] is not None:
        _STUB_STATE["depth"] += 1
        return _STUB_STATE["st"]
    st = {"dir": out_dir, "asked": 0}

    def q_file(*a, **kw):                        # noqa: ANN001
        st["asked"] += 1
        DIALOGS.append({"kind": "filedir", "args": [str(x) for x in a][:3]})
        # PySide6 的 QFileDialog.getExistingDirectory() 返回**单个 str**（选中目录），
        # 不是 (dir, filter) 元组。返回元组会让调用方 os.path.abspath(out_dir) 抛
        # TypeError: expected str, bytes or os.PathLike object, not tuple。
        return out_dir

    def q_question(parent, title, text, *a, **kw):   # noqa: ANN001
        DIALOGS.append({"kind": "question", "title": title, "text": text[:400]})
        return QMessageBox.StandardButton.Yes

    def q_warning(parent, title, text, *a, **kw):    # noqa: ANN001
        DIALOGS.append({"kind": "warning", "title": title, "text": text[:400]})
        btns = a[0] if a else kw.get("buttons")
        if btns is not None:
            # 「有字符没有码位」「确认写盘」都取 Yes；其余取默认
            return QMessageBox.StandardButton.Yes
        return QMessageBox.StandardButton.Ok

    def q_info(parent, title, text, *a, **kw):       # noqa: ANN001
        DIALOGS.append({"kind": "info", "title": title, "text": text[:400]})
        return QMessageBox.StandardButton.Ok

    def q_critical(parent, title, text, *a, **kw):   # noqa: ANN001
        DIALOGS.append({"kind": "critical", "title": title, "text": text[:400]})
        return QMessageBox.StandardButton.Ok

    saved = {
        "filedir": QFileDialog.getExistingDirectory,
        "question": QMessageBox.question,
        "warning": QMessageBox.warning,
        "information": QMessageBox.information,
        "critical": QMessageBox.critical,
    }
    QFileDialog.getExistingDirectory = q_file
    QMessageBox.question = q_question
    QMessageBox.warning = q_warning
    QMessageBox.information = q_info
    QMessageBox.critical = q_critical
    st["saved"] = saved
    _STUB_STATE["st"] = st
    _STUB_STATE["depth"] = 1
    return st


def remove_stubs(st: dict) -> None:
    if _STUB_STATE["st"] is not st:
        return
    _STUB_STATE["depth"] -= 1
    if _STUB_STATE["depth"] > 0:
        return
    _STUB_STATE["st"] = None
    QFileDialog.getExistingDirectory = st["saved"]["filedir"]
    QMessageBox.question = st["saved"]["question"]
    QMessageBox.warning = st["saved"]["warning"]
    QMessageBox.information = st["saved"]["information"]
    QMessageBox.critical = st["saved"]["critical"]


# ------------------------------------------------------------------ 界面辅助
def pick_version(mw, stem: str) -> bool:         # noqa: ANN001
    """在左栏版本下拉里选到指定索引（先尝试真实弹出+点击，失败再退回程序化选择）。

    匹配用索引库目录名**精确相等**，不能用子串——否则
    ``r1354_grayline_clamp_test`` 会把 ``r1354_grayline_clamp_test_mouse_test``
    也一起匹配上，点到错的盘。
    """
    combo = mw.results.version
    idx = -1
    for i in range(combo.count()):
        db = str(combo.itemData(i) or "")
        if db and os.path.basename(os.path.dirname(db)) == stem:
            idx = i
            break
    if idx < 0:
        return False
    # 真实交互：点开下拉 → 点中条目 → 收起
    ok = False
    try:
        combo.showPopup()
        pump(80)
        view = combo.view()
        rect = view.visualRect(combo.model().index(idx, 0))
        if rect.isValid():
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier, rect.center())
            pump(120)
            ok = combo.currentIndex() == idx
        combo.hidePopup()
        pump(60)
    except BaseException:                        # noqa: BLE001
        ok = False
    if not ok:
        combo.setCurrentIndex(idx)
        pump(200)
        NOTES.append("版本下拉：弹出点击未生效，退回 setCurrentIndex(%d)" % idx)
        return combo.currentIndex() == idx
    NOTES.append("版本下拉：通过真实弹出+鼠标点击选中第 %d 项" % idx)
    return True


def find_row(panel, key) -> int:                 # noqa: ANN001
    for i in range(panel.list.count()):
        try:
            hit = panel.list.item(i).data(0x0100)    # Qt.UserRole
        except BaseException:                    # noqa: BLE001
            hit = None
        if isinstance(hit, dict) and \
                (hit["eid"], hit["tag"], hit["k"], hit["slot"]) == tuple(key):
            return i
    return -1


def click_row(panel, i: int) -> None:            # noqa: ANN001
    it = panel.list.item(i)
    panel.list.scrollToItem(it)
    pump(60)
    rect = panel.list.visualItemRect(it)
    QTest.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, rect.center())
    pump(200)


def _ascii_only(text: str) -> bool:
    try:
        text.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def type_line(widget, text: str) -> str:         # noqa: ANN001
    """往 QLineEdit 里输入文本。

    QTest.keyClicks 只能送 ASCII（Qt 的 qasciikey 对非 ASCII 直接 assert 崩溃），
    所以纯 ASCII 走真实键盘事件，含中文则走「聚焦 + 写入控件」并如实记录。
    """
    widget.setFocus()
    pump(60)
    QTest.keyClick(widget, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    pump(40)
    QTest.keyClick(widget, Qt.Key.Key_Delete)
    pump(40)
    if _ascii_only(text):
        QTest.keyClicks(widget, text)
        pump(100)
        if widget.text().strip() == text:
            return "keyClicks"
    widget.setText(text)
    pump(100)
    return "setText"


def type_text(widget, text: str) -> str:         # noqa: ANN001
    """往 QPlainTextEdit 里输入文本。非 ASCII 用 insertPlainText 模拟 IME 上屏。"""
    widget.setFocus()
    pump(60)
    QTest.keyClick(widget, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    pump(40)
    QTest.keyClick(widget, Qt.Key.Key_Delete)
    pump(40)
    if _ascii_only(text):
        QTest.keyClicks(widget, text)
        pump(120)
        if widget.toPlainText().strip() == text:
            return "keyClicks"
    widget.selectAll()
    widget.insertPlainText(text)
    pump(120)
    return "insertPlainText"


# ------------------------------------------------------------------ 冻结层回读
def frozen_read(iso: str, eid: int, tag: int, k: int, slot: int):
    """用冻结检索层（不经本项目写盘代码）从盘面重新读出该槽。"""
    from ace3_text_locator import locator_core as L
    from ace3_text_locator import query as Q
    c2c, _note = L.load_code_table()
    seg, err = Q.reread(iso, eid, tag, k, slot)
    if seg is None:
        return None, err
    plain, _codes, _stats = L.decode_seg(seg, c2c)
    return {"seg": seg, "plain": plain}, err


# ------------------------------------------------------------------ 主流程
def main() -> int:
    os.makedirs(MOUSE_DIR, exist_ok=True)
    os.makedirs(ISO_DIR, exist_ok=True)

    # 卡死定位：每 45 秒把主线程栈打到 stderr（stderr 是 console 重定向文件）
    import faulthandler
    faulthandler.dump_traceback_later(45, repeat=True)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("ACE3 汉化工坊")
    theme.apply_theme(app, "dark")

    # 全程打桩：真实窗口里凡是模态对话框（选目录/确认/完成提示）都要人点一下，
    # 脚本代答并按 kind 记录到报告。只代替「人点按钮」，不代替任何判定逻辑。
    install_stubs(ISO_DIR)
    NOTES.append("对话框全程代答（选目录/确认/提示），只代替点击，不代替判定")

    # 草稿目录隔离：不与真实工作草稿混住，避免「同父盘自动恢复」互相污染
    # （实测吃过一次亏：真实草稿被恢复进队列，第 1 步就带着一条错位的修改）。
    env.DRAFT_DIR = os.path.join(env.WORK_DIR, "mouse_drafts")
    shutil.rmtree(env.DRAFT_DIR, ignore_errors=True)
    os.makedirs(env.DRAFT_DIR, exist_ok=True)
    NOTES.append("草稿目录隔离到 work/mouse_drafts，开跑前清空")

    # ---- 0) 准备测试 ISO 副本 -------------------------------------
    print("\n== 0) 准备测试 ISO 副本（起点文本「%s」）" % OLD_TEXT)
    cands = [i for i in S.list_indexes() if i.get("iso") and os.path.exists(i["iso"])]
    src_info = next((i for i in cands if i["stem"] == BASE_STEM), None)
    if src_info is None:
        raise SystemExit("找不到起点盘索引 %s" % BASE_STEM)
    parent_iso = src_info["iso"]
    parent_md5 = src_info["md5"]
    parent_st_md5 = md5_file(parent_iso)
    parent_mtime = os.path.getmtime(parent_iso)
    parent_size = os.path.getsize(parent_iso)

    copy_iso = os.path.join(MOUSE_DIR, "%s_mouse_test.iso" % BASE_STEM)
    if not os.path.exists(copy_iso) or os.path.getsize(copy_iso) != parent_size:
        print("    复制父盘 → %s（%.1f GB，需要一点时间）"
              % (copy_iso, parent_size / (1 << 30)))
        t0 = time.time()
        shutil.copyfile(parent_iso, copy_iso)
        print("    复制完成 %.1f 秒" % (time.time() - t0))
    copy_md5 = md5_file(copy_iso)
    record(1, "准备测试 ISO 副本（不动冻结父盘）",
           copy_md5 == parent_md5 and os.path.getsize(copy_iso) == parent_size,
           "父盘 `%s` → 副本 `%s`，MD5=%s（与父盘一致=%s，%s）"
           % (os.path.basename(parent_iso), os.path.relpath(copy_iso, env.ROOT),
              copy_md5, copy_md5 == parent_md5, env.human_size(parent_size)),
           {"copy": copy_iso, "md5": copy_md5, "parent": parent_iso})

    # ---- 0b) 给副本建索引（GUI 打开副本需要） ---------------------
    # 清掉副本上一轮残留的草稿：否则第 1 步绑定父盘就会弹「恢复上次草稿」，
    # 恢复出错位的起点，后面「入队 1 条」「草稿 1 条」全部失真。
    _peek = editplan.PlanQueue()
    _peek.bind(copy_iso, copy_md5)
    _old_draft = os.path.join(env.DRAFT_DIR, _peek.draft_name())
    if os.path.exists(_old_draft):
        os.remove(_old_draft)
        print("    清掉上一轮残留草稿：%s" % os.path.basename(_old_draft))
        NOTES.append("开跑前清掉副本残留草稿，保证起点干净")

    # 索引目录按 stem 命名，上一轮留下的同名索引可能记着已经不存在的老路径。
    # 先让产品自己的 adopt_index() 认回（大小+MD5 都对才改绑），认不回就重建。
    info = S.adopt_index(copy_iso)
    if info is None or not info.get("iso_exists"):
        print("    为副本建索引（只写 db 目录，约 2 分钟）…")
        t0 = time.time()
        db = S.build_index(copy_iso)
        print("    索引完成 %.1f 秒：%s" % (time.time() - t0, db))
    else:
        print("    复用同名索引并改绑到副本：%s" % info["db"])
    copy_stem = os.path.splitext(os.path.basename(copy_iso))[0]

    # ---- 1) 起窗口并切到副本版本 ---------------------------------
    print("\n== 1) 起窗口 / 鼠标切换到副本版本")
    mw = MW.MainWindow()
    mw.resize(1600, 960)
    mw.show()
    pump_until(lambda: mw.results.version.count() > 0, timeout=20)
    pump(400)
    rec = []
    ok_ver = pick_version(mw, copy_stem)
    pump(500)
    rec.append(("切换版本", ok_ver, mw.results.version.currentText()))
    record(2, "真实窗口启动并用鼠标切换到测试 ISO 副本的索引",
           ok_ver and os.path.abspath(mw._iso) == os.path.abspath(copy_iso),
           "版本下拉当前项=%r；绑定父盘=%r（应为副本）"
           % (mw.results.version.currentText(), mw._iso),
           {"stem": copy_stem, "bound": mw._iso})

    # ---- 2) 鼠标：点搜索框 → 键入 → 点搜索 -----------------------
    print("\n== 2) 鼠标搜索")
    rp = mw.results
    click(rp.edit, "搜索框")
    way = type_line(rp.edit, OLD_TEXT)
    NOTES.append("搜索框输入方式：%s" % way)
    click(rp.btn, "搜索按钮")
    pump_until(lambda: rp.list.count() > 0, timeout=25)
    n_hits = rp.list.count()
    row9 = find_row(rp, TARGET)
    record(3, "鼠标点搜索并列出命中（输入方式 %s）" % way,
           n_hits > 0 and row9 >= 0,
           "命中 %d 行，目标槽 EID %d tag%d/k%d 槽%d 位于第 %d 行"
           % (n_hits, TARGET[0], TARGET[1], TARGET[2], TARGET[3], row9),
           {"hits": n_hits, "row": row9, "input": way})
    if row9 < 0:
        return finish(mw, copy_iso, parent_iso, parent_st_md5, parent_mtime, None)

    # ---- 3) 鼠标点结果行 → 中栏载入 ------------------------------
    print("\n== 3) 鼠标点结果行")
    click_row(rp, row9)
    pump(400)
    hit = rp.current_hit()
    ed = mw.editor
    n_tags = len(getattr(ed, "_seg").tags) if getattr(ed, "_seg", None) else 0
    record(4, "鼠标点选结果行后中栏一次性载入（日文/中文/标签/预览/逐字）",
           bool(hit) and ed.editor.isEnabled()
           and bool(ed.where.text()) and n_tags >= 1,
           "标题=%r；编辑框现值=%r；判定=%r"
           % (ed.where.text()[:80], ed.current_text()[:40],
              (ed.verdict.text() or "")[:80]),
           {"where": ed.where.text(), "text": ed.current_text()})

    # ---- 4) 鼠标点编辑框 → 输入新文本 ----------------------------
    print("\n== 4) 鼠标点编辑框并输入新中文")
    click(ed.editor, "中栏编辑框")
    used = type_text(ed.editor, NEW_TEXT)
    NOTES.append("编辑框输入方式：%s" % used)
    pump_until(lambda: ed.current_text().strip() == NEW_TEXT, timeout=10)
    pump(300)
    # 判据说明：中栏 ``verdict`` 是中文人话（如「✓ 容量：需后移 1 个槽…（33→35 字节，shift）」），
    # 不是英文状态枚举。此处按「无阻断标记 + 容量结论 + 字节数变化正确」判定，
    # 不去匹配英文字样，避免判据与界面文案耦合。
    vtext = ed.verdict.text() or ""
    cap_ok = ("✓" in vtext or "原位" in vtext or "后移" in vtext
              or "紧凑" in vtext) and ("容量" in vtext)
    no_block = ("✕" not in vtext) and ("阻断" not in vtext)
    bytes_ok = ("33→35" in vtext) or ("35" in vtext)
    record(5, "编辑框输入新文本并触发实时检查（%s）" % used,
           ed.current_text().strip() == NEW_TEXT and cap_ok and no_block and bytes_ok,
           "编辑框=%r；判定=%r" % (ed.current_text(), vtext[:120]),
           {"text": ed.current_text(), "verdict": vtext})

    # ---- 5) 鼠标点「加入修改队列」 -------------------------------
    print("\n== 5) 鼠标点「加入修改队列」")
    st = install_stubs(ISO_DIR)
    try:
        click(ed.btn_save, "加入修改队列")
        pump_until(lambda: len(mw.queue.items) > 0, timeout=20)
        pump(400)
        items = mw.queue.items
        record(6, "鼠标点「加入修改队列」后队列新增 1 条",
               len(items) == 1 and items[0].new_text == NEW_TEXT,
               "队列 %d 条；第 1 条 %s → %r，状态=%s"
               % (len(items), items[0].ref.label(), items[0].new_text,
                  items[0].state),
               {"items": len(items),
                "first": items[0].as_dict() if items else None})

        # ---- 6) 鼠标点「保存草稿」 -------------------------------
        print("\n== 6) 鼠标点「保存草稿」")
        click(mw.qpanel.btn_draft, "保存草稿")
        pump(500)
        dpath = os.path.join(env.DRAFT_DIR, mw.queue.draft_name())
        ok_draft = os.path.exists(dpath)
        dsize = os.path.getsize(dpath) if ok_draft else 0
        d_items = 0
        if ok_draft:
            with open(dpath, "r", encoding="utf-8") as f:
                d_items = len(json.load(f).get("items") or [])
        record(7, "鼠标点「保存草稿」落盘（绑定父盘 MD5）",
               ok_draft and d_items == 1,
               "草稿 `%s`（%d 字节，%d 条，父盘 MD5 %s）"
               % (os.path.basename(dpath), dsize, d_items,
                  (mw.queue.parent or {}).get("md5", "")[:8]),
               {"draft": dpath, "size": dsize, "items": d_items})
    finally:
        remove_stubs(st)

    # ---- 7) 关程序 → 重开 → 恢复草稿 -----------------------------
    print("\n== 7) 关闭窗口后重开并恢复草稿")
    mw._dirty = False                             # noqa: SLF001 草稿已存，免去退出询问
    mw.close()
    pump(300)
    mw2 = MW.MainWindow()
    mw2.resize(1600, 960)
    mw2.show()
    pump_until(lambda: mw2.results.version.count() > 0, timeout=20)
    pump(400)
    st = install_stubs(ISO_DIR)
    try:
        pick_version(mw2, copy_stem)
        pump(1200)                                # 触发 _bind_queue 的草稿询问
        n2 = len(mw2.queue.items)
        record(8, "关程序再重开，草稿被识别并恢复",
               n2 == 1 and mw2.queue.items[0].new_text == NEW_TEXT,
               "重开后队列 %d 条；首条新文本=%r；草稿询问=%s"
               % (n2, mw2.queue.items[0].new_text if n2 else None,
                  "已触发" if any(d["kind"] == "question" for d in DIALOGS)
                  else "未触发"),
               {"items": n2,
                "questions": [d["title"] for d in DIALOGS if d["kind"] == "question"]})
        if n2 == 0:
            print("    （草稿未恢复，改走菜单手动恢复路径）")
            mw2.queue.load_draft(os.path.join(env.DRAFT_DIR,
                                              mw2.queue.draft_name()))
            mw2.qpanel.set_queue(mw2.queue)
            pump(300)
            NOTES.append("草稿未自动恢复，改用 queue.load_draft 手动恢复")
    finally:
        remove_stubs(st)

    # ---- 8) 打开检查台 → 生成新 ISO ------------------------------
    print("\n== 8) 统一写盘检查台 → 鼠标点「生成新 ISO」")
    holder = {}
    orig_init = MW.CheckDesk.__init__

    def patched_init(self, *a, **kw):              # noqa: ANN001
        orig_init(self, *a, **kw)
        holder["dlg"] = self

    MW.CheckDesk.__init__ = patched_init
    orig_exec = MW.CheckDesk.exec
    MW.CheckDesk.exec = lambda self: 0             # 不阻塞脚本，其余按钮照常真点
    st = install_stubs(ISO_DIR)
    try:
        click(mw2.qpanel.btn_write, "统一写盘")
        pump_until(lambda: "dlg" in holder, timeout=10)
        dlg = holder.get("dlg")
        if dlg is not None:
            dlg.show()
            pump(300)
            pump_until(lambda: bool(dlg._report), timeout=90)   # noqa: SLF001
            pump(300)
            rep = dlg._report or {}                                  # noqa: SLF001
            record(9, "检查台列出汇总（总修改 / 可写入 / 阻断 / 资源）",
                   rep.get("total") == 1 and rep.get("blocked") == 0
                   and rep.get("can_build"),
                   "总修改=%s 可安全写入=%s 需确认=%s 阻断=%s 资源=%s"
                   % (rep.get("total"), rep.get("safe"), rep.get("warn"),
                      rep.get("blocked"), len(rep.get("resources") or [])),
                   {"total": rep.get("total"), "safe": rep.get("safe"),
                    "warn": rep.get("warn"), "blocked": rep.get("blocked")})
            click(dlg.btn_build, "生成新 ISO")
            t0 = time.time()
            done = pump_until(lambda: bool(dlg._result), timeout=300)  # noqa
            man = dlg._result or {}                                    # noqa
            out_iso = ((man.get("output") or {}).get("path") or "")
            record(10, "鼠标点「生成新 ISO」并完成事务式写盘",
                   done and bool(out_iso) and os.path.exists(out_iso),
                   "耗时 %.1f 秒；产物 `%s`；MD5=%s；回读=%s；白名单外=%s 字节；受保护=%s"
                   % (time.time() - t0,
                      os.path.relpath(out_iso, env.ROOT) if out_iso else "(无)",
                      (man.get("output") or {}).get("md5", "?"),
                      "PASS" if (man.get("readback") or {}).get("ok") else "FAIL",
                      (man.get("diff") or {}).get("outside"),
                      "PASS" if (man.get("protected") or {}).get("ok") else "FAIL"),
                   {"out": out_iso,
                    "md5": (man.get("output") or {}).get("md5"),
                    "readback": man.get("readback"), "diff": man.get("diff"),
                    "protected_ok": (man.get("protected") or {}).get("ok")})
        else:
            out_iso = ""
            record(9, "检查台打开", False, "检查台未创建", {})
            record(10, "鼠标点「生成新 ISO」并完成事务式写盘", False,
                   "检查台未创建", {})
    finally:
        remove_stubs(st)
        MW.CheckDesk.__init__ = orig_init
        MW.CheckDesk.exec = orig_exec

    mw2._dirty = False                            # noqa: SLF001
    mw2.close()
    pump(200)

    # ---- 9) 重新打开产物并查询 ----------------------------------
    print("\n== 9) 重新打开产物 GUI 并查询新译文")
    res = {}
    if out_iso and os.path.exists(out_iso):
        print("    为产物建索引（只写 db 目录，约 2 分钟）…")
        t0 = time.time()
        try:
            S.build_index(out_iso)
            print("    索引完成 %.1f 秒" % (time.time() - t0))
        except BaseException as exc:              # noqa: BLE001
            NOTES.append("产物建索引失败：%s" % exc)
        out_stem = os.path.splitext(os.path.basename(out_iso))[0]
        mw3 = MW.MainWindow()
        mw3.resize(1600, 960)
        mw3.show()
        pump_until(lambda: mw3.results.version.count() > 0, timeout=20)
        pump(400)
        okv = pick_version(mw3, out_stem)
        pump(600)
        rp3 = mw3.results
        click(rp3.edit, "搜索框（产物）")
        way3 = type_line(rp3.edit, NEW_TEXT)
        NOTES.append("产物搜索框输入方式：%s" % way3)
        click(rp3.btn, "搜索按钮（产物）")
        pump_until(lambda: rp3.list.count() > 0, timeout=25)
        row3 = find_row(rp3, TARGET)
        got = None
        if row3 >= 0:
            click_row(rp3, row3)
            pump(400)
            h3 = rp3.current_hit()
            got = (h3 or {}).get("text")
        record(11, "重新打开 GUI 载入产物 ISO 并能查到新译文",
               okv and row3 >= 0 and got == NEW_TEXT,
               "版本=%r；搜「%s」命中 %d 行；目标槽文本=%r"
               % (rp3.version.currentText(), NEW_TEXT, rp3.list.count(), got),
               {"stem": out_stem, "row": row3, "text": got})
        mw3._dirty = False                        # noqa: SLF001
        mw3.close()
        pump(200)
        fr, err = frozen_read(out_iso, *TARGET)
        res["frozen"] = fr
        record(12, "用冻结检索层独立回读产物盘面（不经本项目写盘代码）",
               bool(fr) and fr.get("plain") == NEW_TEXT,
               "盘面读出 %r（错误=%s）" % (fr.get("plain") if fr else None, err),
               {"plain": (fr or {}).get("plain"),
                "seg_hex": (fr or {}).get("seg", b"").hex()})
    else:
        record(11, "重新打开 GUI 载入产物 ISO 并能查到新译文", False,
               "没有产物 ISO", {})
        record(12, "用冻结检索层独立回读产物盘面", False, "没有产物 ISO", {})

    return finish(mw, copy_iso, parent_iso, parent_st_md5, parent_mtime, out_iso)


def finish(mw, copy_iso, parent_iso, parent_st_md5, parent_mtime, out_iso) -> int:  # noqa: ANN001
    """收尾：核对冻结父盘未被改动、写报告。"""
    print("\n== 收尾）核对冻结父盘未被改动")
    now_md5 = md5_file(parent_iso)
    now_mtime = os.path.getmtime(parent_iso)
    record(13, "冻结父盘 MD5 与 mtime 前后完全一致（未被本次操作改动）",
           now_md5 == parent_st_md5 and now_mtime == parent_mtime,
           "父盘 MD5 %s（前后一致=%s）；mtime %s（前后一致=%s）"
           % (now_md5[:16], now_md5 == parent_st_md5,
              int(now_mtime), now_mtime == parent_mtime),
           {"parent": parent_iso, "md5": now_md5,
            "mtime": int(now_mtime)})

    ok = sum(1 for r in RESULTS if r["ok"])
    rep = {
        "kind": "ace3_editor_gui_mouse_acceptance",
        "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed": round(time.time() - _T0, 1),
        "summary": {"pass": ok, "fail": len(RESULTS) - ok, "items": len(RESULTS)},
        "results": RESULTS,
        "notes": NOTES,
        "dialogs": DIALOGS,
        "copy_iso": copy_iso,
        "parent_iso": parent_iso,
        "output_iso": out_iso,
    }
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("# ACE3 汉化工坊 · GUI 鼠标操作验收报告\n\n")
        f.write("- 时间：%s\n- 结论：**通过 %d / %d**\n"
                "- 测试 ISO 副本：`%s`\n- 冻结父盘（只读）：`%s`\n\n"
                % (rep["ran_at"], ok, len(RESULTS), copy_iso, parent_iso))
        f.write("- 耗时：%.1f 秒\n" % rep["elapsed"])
        f.write("| # | 操作 | 结果 | 实测 |\n| --- | --- | --- | --- |\n")
        for r in RESULTS:
            f.write("| %d | %s | %s | %s |\n"
                    % (r["no"], r["name"], "PASS" if r["ok"] else "FAIL",
                       (r["detail"] or "").replace("|", "\\|")[:320]))
        if NOTES:
            f.write("\n## 交互实现说明\n\n")
            for n in dict.fromkeys(NOTES):
                f.write("- %s\n" % n)
        f.write("\n## 对话框交互记录（打桩只代替「人点按钮」）\n\n")
        for d in DIALOGS:
            f.write("- `%s` %s：%s\n" % (d["kind"], d.get("title", ""),
                                         (d.get("text") or "")[:160]))
        f.write("\n## 产物\n\n- 新 ISO：`%s`\n" % (out_iso or "（无）"))
    print("\n" + "=" * 60)
    print("通过 %d / %d" % (ok, len(RESULTS)))
    for r in RESULTS:
        if not r["ok"]:
            print("  FAIL %d. %s — %s" % (r["no"], r["name"], r["detail"]))
    print("报告：%s" % REPORT_MD)
    try:
        mw.close()
    except BaseException:                        # noqa: BLE001
        pass
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        raise SystemExit(2)
