"""GUI 功能自测：在离屏模式下把「搜索→编辑→入队→检查→草稿」整条链走一遍。

不弹任何真实对话框（QMessageBox 全部打桩），不写 ISO。
"""
from __future__ import annotations

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox      # noqa: E402

PASS_N = 0
FAIL_N = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS_N, FAIL_N
    if cond:
        PASS_N += 1
        print("  PASS  %s%s" % (name, ("  " + extra) if extra else ""))
    else:
        FAIL_N += 1
        print("  FAIL  %s%s" % (name, ("  " + extra) if extra else ""))


def stub_dialogs() -> None:
    """把模态对话框全部打桩，避免自测卡死。"""
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)      # type: ignore
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Yes)       # type: ignore
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)    # type: ignore
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)       # type: ignore


def _allowed(paths, path: str) -> bool:
    """``paths.check()`` 是否放行（不抛即放行）。"""
    try:
        paths.check(path, "自测")
        return True
    except paths.PathRuleError:
        return False


def _wait_hits(win, app, timeout=120.0) -> int:      # noqa: ANN001
    """等后台搜索把结果送回左栏，返回行数。

    v1.0.4 起搜索走 ``JobRunner`` 线程池（含名字块时全盘扫 VMD 要十几秒，
    放 GUI 线程会整窗卡死）。``_do_search()`` 只发信号，所以自测必须 pump
    事件循环等结果，不能像以前那样立刻 ``list.count()``（那样永远读到 0）。
    """
    import time
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        n = win.results.list.count()
        if n:
            return n
        time.sleep(0.05)
    return win.results.list.count()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    stub_dialogs()

    from core import editplan, env, pipeline, search as S
    from ui import theme
    from ui.main_window import MainWindow

    theme.apply_theme(app, "dark")
    env.ensure_dirs()

    # 草稿目录隔离：真跑留下的草稿会通过「同父盘自动恢复」污染自测
    # （实测过一次：编辑框初值变成上一轮残留的「何？」）。这里把草稿写到
    # work/selftest_drafts 并清空，保证自测从干净状态开始。
    import shutil
    iso_drafts = os.path.join(env.WORK_DIR, "selftest_drafts")
    shutil.rmtree(iso_drafts, ignore_errors=True)
    os.makedirs(iso_drafts, exist_ok=True)
    env.DRAFT_DIR = iso_drafts

    print("== 1. 主窗口组装")
    win = MainWindow()
    app.processEvents()          # 触发 singleShot(0) 的启动钩子
    app.processEvents()
    check("窗口创建", win is not None)
    check("三栏都存在", all(w is not None for w in
                            (win.results, win.editor, win.qpanel)))
    idx = win.results._index
    check("索引已载入", idx is not None,
          (os.path.basename(idx.iso_path) if idx else "无"))
    if idx is None:
        print("  索引不可用，后续用例跳过")
        return 1
    iso = idx.iso_path
    check("父盘已绑定", bool(win.queue.parent.get("md5")),
          win.queue.parent.get("md5", "")[:12])

    print("== 2. 搜索「何？」")
    win.results.edit.setText("何？")
    win.results._do_search()
    # v1.0.4 起搜索走后台线程（_do_search 只发信号，主窗口 run_search 里
    # 才真正查询），所以要 pump 事件等结果回来，不能立刻读 list.count()。
    n = _wait_hits(win, app)
    check("搜索有结果", n > 0, "%d 条" % n)

    print("== 3. 载入 4004643 槽 9（任务书验收第 1 项）")
    hit = None
    for i in range(n):
        h = win.results.list.item(i).data(0x0100)
        if h and h["eid"] == 4004643 and h["slot"] == 9:
            hit = h
            break
    if hit is None:
        hit = idx.slot(4004643, 0, 0, 9)
    check("定位到 eid=4004643 slot9", hit is not None)
    if hit is None:
        return 1
    check("该槽当前文本", hit["text"] in ("什么？", "何？"), repr(hit["text"]))
    check("原版日文可读", "何" in (hit.get("jp_text") or ""),
          repr(hit.get("jp_text"))[:40])

    win.on_hit(hit)
    check("编辑器载入命中", win.editor._hit is not None)
    check("编辑框初值 = 当前文本",
          win.editor.current_text() == hit["text"],
          repr(win.editor.current_text()))

    print("== 4. 输入「什么？」后实时判定")
    win.editor.set_text("什么？")
    win.editor._validate()
    est = win.editor._last_est
    check("容量可写", est is not None and est.ok,
          (est.reason if est else "no estimate"))
    check("字库非阻断", win.editor._last_font_state != "BLOCK",
          win.editor._last_font_state)
    check("标签胶囊已渲染", len(win.editor.tags._chips) == len(win.editor._seg.tags),
          "%d 个" % len(win.editor._seg.tags))

    print("== 5. 加入修改队列")
    payload = win.editor._build_payload("什么？")
    win.editor.saveRequested.emit({"hit": hit, "text": "什么？",
                                   "payload": payload, "seg": win.editor._seg,
                                   "estimate": est,
                                   "font_state": win.editor._last_font_state})
    app.processEvents()
    check("队列有 1 条", len(win.queue.items) == 1, "%d 条" % len(win.queue.items))
    check("队列界面显示 1 行", win.qpanel.tree.topLevelItemCount() == 1)
    check("草稿文件已落盘",
          os.path.exists(os.path.join(env.DRAFT_DIR, win.queue.draft_name())))

    print("== 6. 无码位字符必须 BLOCK（验收第 5 项）")
    win.editor.set_text("黄")
    win.editor._validate()
    check("「黄」判为 BLOCK", win.editor._last_font_state == "BLOCK",
          win.editor._last_font_state)

    print("== 7. 部分包缺字必须 WARN（验收第 6 项）")
    from core import codec, fontcov
    cov = fontcov.coverage()
    tbl = codec.table()
    warn_ch = None
    for ch in sorted(tbl.char2codes.keys()):
        s, _o, _n = cov.diagnose_text(ch)
        if s == "WARN" and "\u4e00" <= ch <= "\u9fff":
            warn_ch = ch
            break
    check("找到汉字 WARN 字符", warn_ch is not None, repr(warn_ch))
    if warn_ch:
        st, order, notes2 = cov.diagnose_text(warn_ch)
        v = order[0]
        check("WARN 带缺包清单", bool(v.missing), "%d 包有字形，缺 %d 包"
              % (len(v.have), len(v.missing)))
        check("WARN 不阻断写盘", not v.blocks_write, v.state)
        check("WARN 保留 UNKNOWN 说明",
              any("UNKNOWN" in n for n in v.notes),
              "；".join(v.notes))

    print("== 8. 草稿往返")
    win.save_draft(quiet=True)
    q2 = editplan.PlanQueue()
    info = q2.load_draft(os.path.join(env.DRAFT_DIR, win.queue.draft_name()),
                         verify_parent=True)
    check("草稿回读条数", info["items"] == len(win.queue.items),
          "%d" % info["items"])
    check("父盘 MD5 一致", q2.parent.get("md5") == win.queue.parent.get("md5"))
    check("回读无告警", not info.get("warning"), info.get("warning", ""))
    it = q2.items[0]
    check("回读的新文本", it.new_text == "什么？", repr(it.new_text))
    check("回读的旧字节指纹保留", len(it.old_payload) > 0,
          "%d 字节" % len(it.old_payload))

    print("== 9. 同槽重复修改识别")
    item2, _miss = editplan.item_from_hit(hit, "何？", win.editor._seg.codes)
    act = win.queue.upsert(item2)
    check("识别为 updated", act == "updated", act)
    check("历史记录留痕", bool(win.queue.items[0].history))
    check("仍是 1 条", len(win.queue.items) == 1)
    win.queue.upsert(editplan.item_from_hit(hit, "什么？",
                                           win.editor._seg.codes)[0])

    print("== 10. 检查台 API（不弹窗、不写盘）")
    rep = pipeline.inspect(iso, win.queue.items)
    check("检查台返回", isinstance(rep, dict) and rep["total"] == 1,
          "total=%s safe=%s warn=%s block=%s"
          % (rep.get("total"), rep.get("safe"), rep.get("warn"),
             rep.get("blocked")))
    check("可写入", rep["safe"] == 1, str(rep.get("blocked_detail") or ""))
    check("无阻断", rep["blocked"] == 0)
    check("状态回写队列", win.queue.items[0].state == editplan.STATE_OK,
          win.queue.items[0].state)

    print("== 11. 边界：过期 MD5 必须拒绝（验收第 14 项）")
    bad = editplan.PlanQueue()
    bad.parent = {"path": iso, "md5": "0" * 32, "size": 0, "mtime": 0}
    ok, why = bad.parent_ok(iso)
    check("MD5 不符被识别", not ok and "MD5" in why, why[:40])
    try:
        pipeline.build_to(iso, win.queue.items,
                          out_dir=os.path.join(env.WORK_DIR, "iso_out"),
                          expected_md5="0" * 32, name_hint="badmd5")
        check("错误 MD5 被拒绝", False, "居然没抛异常")
    except Exception as exc:                     # noqa: BLE001
        check("错误 MD5 被拒绝", "MD5" in str(exc), str(exc)[:60])

    print("== 12. 主题切换")
    win.toggle_theme()
    check("切到浅色", theme.theme_name() == "light", theme.theme_name())
    win.toggle_theme()
    check("切回深色", theme.theme_name() == "dark", theme.theme_name())

    # ---- 13/14：v1.0.2 新增（索引 dtype 修复 + 目录记忆 + 索引缓存管理）
    print("== 13. 索引句柄类型：_load_index 必须收到 dict（v1.0.1 的崩溃回归）")
    info = S.find_info(iso)
    check("find_info() 是 dict", isinstance(info, dict), type(info).__name__)
    check("find_info() 的 db 可用于载入",
          isinstance(info, dict) and os.path.isfile(info.get("db", "")))
    before = win.results._index
    win._load_index(info)                     # 走真实载入路径，不应抛 TypeError
    check("_load_index(dict) 不抛异常", win.results._index is not None)
    check("载入后父盘一致",
          os.path.normcase(win.results._index.iso_path or "") ==
          os.path.normcase(before.iso_path or "") if before else True)

    print("== 14. 打开 ISO 记忆目录（settings）")
    from core import settings
    d = os.path.dirname(iso)
    settings.put("iso_dir", d)
    check("recall_dir 读回上次目录", settings.recall_dir("iso_dir", "FB") == d,
          settings.recall_dir("iso_dir", "FB"))
    check("recall_dir 失效回落到兜底",
          settings.recall_dir("__no_such_key__", "FB") == "FB")
    check("remember_dir 拒绝不存在的目录",
          settings.remember_dir("iso_dir", "Z:\\no\\such\\dir") is False)
    check("remember_dir 对真实目录返回 True",
          settings.remember_dir("iso_dir", d) is True)

    print("== 15. 索引缓存管理（对话框构造 / 当前索引受保护 / 删除生效）")
    from PySide6.QtCore import Qt
    from ui.indexmgr import IndexManager
    cur_db = getattr(win.results._index, "db_path", "")
    dlg = IndexManager(win, current_db=cur_db)
    rows = dlg.tree.topLevelItemCount()
    check("对话框列出索引", rows >= 1, "%d 行" % rows)
    cur_stem = os.path.basename(os.path.dirname(cur_db))
    stems = [dlg.tree.topLevelItem(i).data(0, Qt.UserRole)
             for i in range(rows)]
    check("当前索引在列表里", cur_stem in stems, cur_stem)
    cur_item = dlg.tree.topLevelItem(stems.index(cur_stem)) if cur_stem in stems else None
    # flags() 返回的是 Qt.ItemFlag 枚举，与 int 直接 & 会 TypeError
    check("当前索引不可勾选",
          cur_item is not None and
          not bool(cur_item.flags() & Qt.ItemIsUserCheckable),
          str(cur_item.flags()) if cur_item else "-")
    dlg._set_checks(True)
    got = dlg._checked()
    check("全选不会勾到当前索引", cur_stem not in got, ",".join(got) or "（空）")
    dlg.close()

    # 真删一个自造的空壳索引，走 UI 的删除路径
    fake = "__selftest_ui_fake__"
    fdir = os.path.join(env.LOCATOR_DB, fake)
    shutil.rmtree(fdir, ignore_errors=True)
    os.makedirs(fdir, exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(os.path.join(fdir, S.DB_NAME))
    conn.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO meta VALUES(?,?)",
                     [("iso_path", "X:\\fake.iso"), ("iso_md5", "0" * 32),
                      ("built_at", "2026-01-01 00:00:00"), ("slots", "1")])
    conn.commit()
    conn.close()
    dlg2 = IndexManager(win, current_db=cur_db)
    fake_item = None
    for i in range(dlg2.tree.topLevelItemCount()):
        it = dlg2.tree.topLevelItem(i)
        if it.data(0, Qt.UserRole) == fake:
            fake_item = it
    check("自造索引出现在对话框", fake_item is not None, fake)
    if fake_item is not None:
        fake_item.setCheckState(0, Qt.Checked)
        check("勾选到自造索引", fake in dlg2._checked())
        dlg2._delete()
        check("对话框报告已删除", fake in dlg2.deleted(), ",".join(dlg2.deleted()))
        check("自造索引目录已消失", not os.path.isdir(fdir))
    dlg2.close()

    print("== 16. 权限与写盘守则（状态栏 / 详情 / 拒绝）")
    from core import paths
    check("状态栏权限标记已就位", win.perm.text() != "", win.perm.text())
    check("权限标记含盘符", paths.system_drive() + ":" in win.perm.text(),
          win.perm.text())
    st = paths.status()
    check("status() 可用", st["write_root"] == paths.write_root(), st["write_root"])
    # 详情弹窗（QMessageBox.information 已打桩）不应抛
    try:
        win.show_path_status()
        check("权限详情可打开", True)
    except Exception as exc:                     # noqa: BLE001
        check("权限详情可打开", False, "%s：%s" % (type(exc).__name__, exc))
    # 守则：检查台选到系统盘时应当被拦下来（打桩后会自动选「是」→ 改道默认目录）
    check("系统盘被 paths 拒绝",
          not _allowed(paths, os.path.join(paths.system_drive() + ":\\", "x.iso")))
    check("工具目录内被允许", _allowed(paths, os.path.join(env.REPORT_DIR, "x.iso")))
    # 导出编辑计划：选到系统盘时应弹「不符合守则」而不是真的写下去
    check("导出计划路径守则生效",
          not _allowed(paths, os.path.join(paths.system_drive() + ":\\", "p.json")))

    print("== 17. 版本下拉标出「盘不在原处」")
    # 鼠标验收真实踩过：下拉切到一份指向旧工程目录的索引，中栏满屏「资源读取失败」。
    gone = [i for i in S.list_indexes() if not i.get("iso_exists", True)]
    check("iso_exists 字段存在",
          all("iso_exists" in i for i in S.list_indexes()),
          "%d 份索引" % len(S.list_indexes()))
    combo = win.results.version
    labels = [combo.itemText(i) for i in range(combo.count())]
    if gone:
        check("读不到盘的行已标注", any("盘不在原处" in s for s in labels),
              "、".join(s for s in labels if "盘不在原处" in s)[:160])
        check("标注行不等于全部行", any("盘不在原处" not in s for s in labels))
        # 切过去要被拦下并提示。注意两点踩过的坑：
        #  ① setCurrentIndex 自己就会发 currentIndexChanged → _pick_version，
        #     不能再手动调一次（会问两遍，第二次就真的去弹文件选择框卡死）；
        #  ② 打桩的 question 返回 Yes 时，提示里那句「要现在去选吗？」会走到
        #     主窗口 open_iso() → QFileDialog 真模态框 ⇒ 本组临时改成返回 No。
        cur = win.results._index
        bad = next((i for i in range(combo.count())
                    if combo.itemText(i).endswith("（盘不在原处）")), -1)
        if bad >= 0 and cur is not None:
            before = win.results._index.db_path
            keep = QMessageBox.question
            QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)  # type: ignore
            try:
                combo.setCurrentIndex(bad)
            finally:
                QMessageBox.question = keep                              # type: ignore
            check("切到失效索引被拦下，仍用原索引",
                  win.results._index is not None
                  and win.results._index.db_path == before,
                  win.results._index.db_path if win.results._index else "-")
            check("左栏提示说明了原因",
                  "不在原处" in win.results.hint.text(), win.results.hint.text())
    else:
        check("当前没有失效索引（跳过标注断言）", True, "本机所有索引的盘都在")

    win.close()
    print()
    print("PASS %d / FAIL %d" % (PASS_N, FAIL_N))
    return 0 if FAIL_N == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
