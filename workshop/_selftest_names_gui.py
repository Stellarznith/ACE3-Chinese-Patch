# -*- coding: utf-8 -*-
"""名字块检索在真实 GUI 线程模型下的端到端验证（离屏）。

为什么单独做这一项：v1.0.4 修过「后台线程查 SQLite 必失败」这个产品级缺陷
（``ProgrammingError: SQLite objects created in a thread can only be used in
that same thread``，导致搜索永远 0 条）。名字块检索虽然不走 SQLite，
但同样经 ``JobRunner`` 线程池 + ``QThreadPool``，而且首次要扫全盘 VMD 约 19 秒。
所以必须真的把「左栏开关 → 后台线程 → 结果回填 → 中栏可编辑 → 入队」跑一遍，
不能只靠单元自测里直接调 ``nametext.search()`` 就宣布通。

跑法：
    python _selftest_names_gui.py
"""
from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox     # noqa: E402

from core import editplan, env, nametext as N               # noqa: E402

ISO = os.environ.get("ACE3_TEST_ISO") or r"PATH\TO\TEST.iso"
NEEDLE = "冥墓方舟"

_rows: list = []


def ck(name: str, cond: bool, detail: str = "") -> bool:
    _rows.append({"name": name, "ok": bool(cond), "detail": detail})
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" — " + detail) if detail else ""))
    return bool(cond)


def pump(app, secs: float) -> None:
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_hits(win, app, timeout: float = 180.0) -> int:
    """pump 事件循环直到左栏出现行（v1.0.4 起搜索走 JobRunner 线程池）。"""
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        n = win.results.list.count()
        if n:
            pump(app, 0.3)          # 让 hint / 状态栏也刷完
            return n
        time.sleep(0.02)
    return win.results.list.count()


def main() -> int:
    # 卡死诊断：离屏 GUI 一旦被模态框挂住就没有任何输出（实测 CPU 归零、
    # 跑多久都不返回），必须靠 faulthandler 定期把栈打到文件里才能定位。
    import faulthandler
    _fh = open(os.path.join(env.TMP_DIR if hasattr(env, "TMP_DIR") else os.environ.get("TEMP", "."),
                            "nsg_trace.log"), "w", encoding="utf-8", buffering=1)
    faulthandler.dump_traceback_later(30, repeat=True, file=_fh)
    try:
        return _main()
    finally:
        faulthandler.cancel_dump_traceback_later()
        _fh.close()


def _main() -> int:
    if not os.path.isfile(ISO):
        print("找不到测试盘：%s" % ISO)
        return 2
    env.ensure_dirs()
    # 草稿目录必须隔离，而且每轮必须清空：
    #   1) 这张盘在真实使用中留下过草稿，主窗口 _bind_queue() 会弹
    #      「发现上次的草稿，现在恢复吗？」模态框，离屏自测里没人点它，
    #      进程会一直挂着（实测 CPU 归零、永不返回）。
    #   2) 上一轮自测自己也会往隔离目录写草稿，于是「第二轮起必挂」——
    #      这个坑实测踩过：首轮跑绿，重跑就死。
    draft_dir = os.path.join(env.WORK_DIR, "selftest_names_drafts")
    shutil.rmtree(draft_dir, ignore_errors=True)
    os.makedirs(draft_dir, exist_ok=True)
    env.DRAFT_DIR = draft_dir
    app = QApplication.instance() or QApplication(sys.argv[:1])

    # 兜底打桩：任何没预料到的询问框都答「No」，绝不留给离屏进程去等。
    # 第 7 组自己会临时换成「Yes」并在 finally 里还原成这个 No。
    real_question = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    try:
        return _body(app, real_question)
    finally:
        QMessageBox.question = real_question


def _body(app, real_question) -> int:

    from core import search as S
    from ui.main_window import MainWindow

    win = MainWindow()
    # 不要 show()：offscreen 平台下 show() 会卡在 propagateSizeHints()
    # （实测无 CPU、无输出地挂住，跑多久都不返回）。
    # 离屏自测只需要控件树可用，与 _selftest_gui.py 保持一致。
    pump(app, 0.6)

    # ---- 1. 装载测试盘的索引
    print("== 1. 装载索引")
    info = S.find_info(ISO)
    if info is None or not info.get("iso_exists"):
        # 索引目录按 stem 命名，本机可能残留同名旧索引，先试改绑
        info = S.adopt_index(ISO)
    ck("1 找到并可用该盘的索引", info is not None and bool(info.get("iso_exists")),
       (info or {}).get("db", ""))
    if info is None or not info.get("iso_exists"):
        return _finish()
    win._load_index(info)
    pump(app, 0.6)
    ck("1 主窗口已切到该索引", win.results._index is not None,
       os.path.basename(info["db"]))

    # ---- 2. 左栏开关默认打开
    print("\n== 2. 左栏「含名字块」开关")
    ck("2 开关存在且默认打开", win.results.chk_names.isChecked(),
       "chk_names.isChecked()=%s" % win.results.chk_names.isChecked())

    # ---- 3. 真实走后台线程检索
    print("\n== 3. 后台线程检索「%s」（首次要扫全盘 VMD，约 19 秒）" % NEEDLE)
    t0 = time.time()
    win.results.edit.setText(NEEDLE)
    win.results._do_search()
    n = wait_hits(win, app)
    elapsed = time.time() - t0
    ck("3 左栏出结果（线程池没吞掉）", n > 0, "%d 行 / %.1fs" % (n, elapsed))
    ck("3 状态栏报告了条数", "检索完成" in win.status.text(),
       win.status.text())

    # ---- 4. 结果里含名字块命中
    print("\n== 4. 命中内容")
    raw = win.results._raw or []
    kinds = {}
    for h in raw:
        kinds[h.get("kind", "slot")] = kinds.get(h.get("kind", "slot"), 0) + 1
    ck("4 命中里有名字块条目", kinds.get("name", 0) > 0, str(kinds))
    # 注意：kind=="name" 表示「这条命中来自名字块扫描」，不代表最终走哪个引擎。
    # 引擎由 ver_k 决定：FNM 块本身就是 VER 块（ver_k 非 None）→ 槽引擎；
    # CLM 块不在 VER 体系（ver_k 为 None）→ 名字块引擎。
    name_hits = [h for h in raw if h.get("kind") == "name"]
    clm_hits = [h for h in name_hits if h.get("ver_k") is None]
    fnm_hits = [h for h in name_hits if h.get("ver_k") is not None]
    ck("4 CLM 命中 ver_k 为 None（走名字块引擎）",
       bool(clm_hits) and all(h.get("mark") == "CLM" for h in clm_hits),
       "CLM %d 条" % len(clm_hits))
    ck("4 FNM 命中带 ver_k（走槽引擎）",
       bool(fnm_hits) and all(h.get("mark") == "FNM" for h in fnm_hits),
       "FNM %d 条" % len(fnm_hits))

    # ---- 5. 点一条名字块命中 → 中栏进入名字编辑
    print("\n== 5. 中栏编辑")
    # 优先挑 CLM（走名字块引擎那条路），没有才退回第一条
    target = clm_hits[0] if clm_hits else (raw[0] if raw else None)
    ck("5 有可点的命中", target is not None)
    if target is None:
        return _finish()
    win.on_hit(target)
    pump(app, 0.5)
    ed = win.editor
    ck("5 中栏标题切到名字编辑",
       "名字编辑" in ed.title.text() or "名字" in ed.title.text(),
       ed.title.text())
    ck("5 技术行给出块位置与引擎",
       "@0x" in ed.row_eid._val.text() and "引擎" in ed.row_eid._val.text(),
       ed.row_eid._val.text())
    ck("5 编辑框可写", not ed.editor.isReadOnly(),
       "readOnly=%s" % ed.editor.isReadOnly())

    # ---- 6. 改一个字 → 实时判定
    print("\n== 6. 实时判定")
    old_text = ed.editor.toPlainText()
    ed.editor.setPlainText(old_text + "改")
    pump(app, 0.6)
    verdict = ed.verdict.text()
    ck("6 判定给出了容量结论", "容量" in verdict, verdict)

    # ---- 7. 入队（走中栏真实按钮路径，不直接调 on_save_requested）
    print("\n== 7. 加入修改队列")
    before = len(win.queue.items)
    # 镜像提示会弹模态框：本组把它打桩成「只改当前句」（Yes），
    # 并记录是否被问过——名字块命中要求必须问，不能默认同步。
    asked = {"n": 0}
    real_q = QMessageBox.question
    QMessageBox.question = staticmethod(
        lambda *a, **k: (asked.__setitem__("n", asked["n"] + 1), QMessageBox.Yes)[1])
    try:
        ed._save()          # 中栏「加入队列」按钮走的就是这里
        pump(app, 0.6)
    finally:
        QMessageBox.question = real_q
    after = len(win.queue.items)
    ck("7 队列条目增加", after == before + 1, "%d → %d" % (before, after))
    if after > before:
        it = win.queue.items[-1]
        ck("7 入队项 key 与命中一致",
           it.key() == editplan.hit_key(target),
           "key=%r kind=%s" % (it.key(), it.kind))
        ck("7 入队项标签指出 CLM 块",
           "CLM" in it.label() and "@0x" in it.label(), it.label())

    win.close()
    pump(app, 0.3)
    return _finish()


def _finish() -> int:
    ok = sum(1 for r in _rows if r["ok"])
    bad = [r for r in _rows if not r["ok"]]
    print("\n" + "=" * 60)
    print("PASS %d / FAIL %d" % (ok, len(bad)))
    for r in bad:
        print("  失败：%s  %s" % (r["name"], r["detail"]))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
