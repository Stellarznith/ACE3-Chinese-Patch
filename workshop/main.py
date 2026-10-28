"""ACE3 汉化工坊 · GUI 入口。

用法（开发环境）::

    python workshop/main.py          # 发布仓库布局（tools/ 与 workshop/ 同级）

全程离线，不需要联网；只读父盘，所有产物写到用户选择的目录。

打包成 EXE 后的两种用法::

    ACE3汉化工坊.exe                 # 正常启动 GUI（无控制台窗口）
    ACE3汉化工坊.exe --selftest out.json   # 无头自测（搜索/修改/写盘/回读）
"""
from __future__ import annotations

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


# ---------------------------------------------------------------- 关键：先补 stdio
class _NullIO:
    """PyInstaller windowed 模式下 ``sys.stdout`` / ``sys.stderr`` 是 ``None``。

    工程里被复用的历史模块（``lz_encode`` / ``_r104_audit2`` / ``_r211_override``
    / ``locator_core`` …）在 import 时都会执行
    ``sys.stdout.reconfigure(encoding="utf-8")``，一旦 stdout 为 None 就直接
    AttributeError 崩在导入阶段。所以必须在**导入任何业务模块之前**把它们换成
    能接受 ``reconfigure`` / ``write`` / ``flush`` 的空实现。
    """

    encoding = "utf-8"
    errors = "replace"
    newlines = None

    def write(self, _s: str) -> int:             # noqa: D401
        return 0

    def writelines(self, _lines) -> None:        # noqa: ANN001
        return None

    def flush(self) -> None:
        return None

    def reconfigure(self, **_kw) -> None:        # noqa: ANN003
        return None

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("no fileno")

    def close(self) -> None:
        return None


def _ensure_stdio() -> None:
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, _NullIO())
    if getattr(sys, "stdin", None) is None:
        sys.stdin = _NullIO()


_ensure_stdio()


# ---------------------------------------------------------------- 路径与权限
def _bootstrap_paths() -> dict:
    """把临时目录钉到工具目录，并返回当前权限/路径体检结果。

    必须在使用任何 ``tempfile`` 之前调用（所以放在业务导入之前）。
    """
    from core import paths
    paths.install_temp_redirect()
    return paths.status()


def _try_elevate(argv) -> int:
    """没提权就自提升一次。返回 0 表示「已提权，继续跑」。

    用环境变量打防重入标记：新实例若因 UAC 被拒仍是普通权限，
    也不会无限递归弹窗，而是带着标记降级继续（并在状态栏说明）。
    """
    from core import paths

    if paths.is_admin():
        return 0
    if os.environ.get("ACE3_NO_ELEVATE") == "1":
        return 0                      # 上一轮已经试过，不再弹
    os.environ["ACE3_NO_ELEVATE"] = "1"
    ok, msg = paths.relaunch_as_admin(argv)
    if ok:
        return 1                      # 新实例接管，本进程退出
    # 提权被拒 / 系统不允许：继续跑，但把话说明白，不假装一切正常
    sys.stderr.write(msg + "\n")
    return 0


def _elevate_wait(argv):
    """无头自测专用：提权并**等子进程跑完**，返回它的退出码。

    返回 ``None`` 表示「本来就是管理员，自己接着跑」。GUI 分支不能等
    （等就等于同步阻塞界面），所以只有 ``--selftest`` 走这条。
    """
    from core import paths

    if paths.is_admin() or os.environ.get("ACE3_NO_ELEVATE") == "1":
        return None
    os.environ["ACE3_NO_ELEVATE"] = "1"
    ok, code, msg = paths.run_elevated_wait(argv)
    sys.stderr.write(msg + "\n")
    if ok:
        return code
    return None                       # 提权失败：降级自己跑（报告里会有记录）


def _excepthook(exc_type, exc, tb) -> None:      # noqa: ANN001
    """不让异常直接闪退：弹窗 + 写日志。"""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        from core import env
        env.ensure_dirs()
        path = os.path.join(env.LOG_DIR, "crash.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n===== %s =====\n%s" % (env_now(), text))
    except Exception:                            # noqa: BLE001
        path = "（日志写入失败）"
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is not None:
            QMessageBox.critical(
                None, "ACE3 汉化工坊 遇到错误",
                "程序遇到未处理的错误，已记录日志：\n%s\n\n"
                "父盘与草稿都未受影响。\n\n%s"
                % (path, text[-1400:]))
    except Exception:                            # noqa: BLE001
        pass
    sys.__excepthook__(exc_type, exc, tb)


def env_now() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 无头自测
def headless_selftest(out_path: str, do_write: bool = True) -> int:
    """打包后 EXE 的关键流程自测（不建窗口，纯 core 链路）。

    覆盖：找索引 → 搜索 → 取槽 → 字库检查 → 容量估算 → 加入计划队列 →
    草稿落盘 → 统一写盘（可选，默认做）→ 独立回读 → 整盘差分白名单。

    全程只用已打包进去的代码，不调用工程目录里的 .py，所以它验证的是
    「EXE 里那套东西」而不是「开发环境的源码」。
    """
    import json
    import time

    from core import codec, editplan, env, estimate, fontcov, pipeline
    from core import search as S

    rep: dict = {"ok": False, "steps": [], "exe": sys.executable,
                 "frozen": bool(getattr(sys, "frozen", False)),
                 "root": env.ROOT}
    # 权限与路径守则也要写进报告：报告得能自证「我是以管理员跑的、
    # 我没往系统盘写」。否则「退出码 0」什么都证明不了。
    from core import paths
    rep["paths"] = paths.status()
    t0 = time.time()

    def step(name: str, ok: bool, detail: str, extra: dict | None = None) -> None:
        rep["steps"].append({"name": name, "ok": bool(ok), "detail": detail,
                             **(extra or {})})
        if not ok:
            rep["detail"] = "失败于「%s」：%s" % (name, detail)

    try:
        env.ensure_dirs()
        st = paths.status()
        step("管理员权限", st["admin"],
             "已提权" if st["admin"] else "未提权（守则要求管理员运行）")
        step("工具不在系统盘", not st["write_root_on_system_drive"],
             "工具目录 %s（%s: 盘）"
             % (st["write_root"], paths.drive_of(st["write_root"])))
        step("临时目录已改道到工具目录",
             os.path.abspath(st["temp_dir"]) == os.path.abspath(paths.temp_dir()),
             st["temp_dir"])
        infos = [i for i in S.list_indexes()
                 if i.get("iso") and os.path.exists(i["iso"])]
        step("找到可用索引", bool(infos),
             "共 %d 份，ISO 存在 %d 份" % (len(S.list_indexes()), len(infos)))
        if not infos:
            raise RuntimeError("没有可用索引：请先在 GUI 里为 ISO 建索引")
        default_stem = getattr(S.Q, "DEFAULT_STEM", "")
        pick = next((i for i in infos if i["stem"] == default_stem), None)
        if pick is None:
            pick = max(infos, key=lambda i: i.get("built_at") or "")
        idx = S.open_index(pick["db"])
        iso, md5 = idx.iso_path, idx.iso_md5
        rep["parent"] = {"path": iso, "md5": md5}
        step("打开索引", True, "%s（%d 槽）" % (pick["stem"], idx.slot_total))

        hits = idx.search("何？", limit=200)
        step("搜索「何？」", bool(hits), "命中 %d 槽" % len(hits))

        hit = idx.slot(4004643, 0, 0, 9)
        step("定位 eid=4004643 槽 9", hit is not None,
             "当前文本 %r、%d 字节、压缩=%s"
             % (hit["text"], hit["bytes"], hit["compressed"]) if hit else "无此槽")
        if hit is None:
            raise RuntimeError("索引里没有 eid=4004643 槽 9")

        new_text = "何？" if hit["text"] != "何？" else "什么？"
        item, missing = editplan.item_from_hit(hit, new_text)
        step("编码新文本", not missing,
             "%r → %d 字节（缺字 %s）"
             % (new_text, len(item.new_payload), missing or "无"))

        cov = fontcov.coverage()
        state, order, notes = cov.diagnose_text(new_text, preferred=hit["codes_hex"])
        step("字库检查", state != "BLOCK",
             "%s：%s" % (state, "；".join(notes) or "全部字符静态覆盖充分"))
        rep["font"] = {"state": state, "notes": notes,
                       "chars": [v.as_dict() for v in order]}

        est = estimate.estimate(iso, item.ref.eid, item.ref.tag, item.ref.k,
                                item.ref.slot, item.new_payload)
        step("容量估算", est.ok, "%s（%s）" % (est.reason, est.mode or "-"))

        q = editplan.PlanQueue()
        q.bind(iso, md5)
        q.upsert(item)
        draft = q.save_draft()
        step("草稿落盘", os.path.exists(draft),
             "%s（%d 字节）" % (os.path.basename(draft), os.path.getsize(draft)))
        back = editplan.PlanQueue()
        back.load_draft(draft)
        step("草稿回读", len(back.items) == 1 and back.items[0].new_text == new_text,
             "回读 %d 条、父盘指纹一致=%s"
             % (len(back.items), back.parent.get("md5") == md5))

        if not do_write:
            rep["ok"] = all(s["ok"] for s in rep["steps"])
            rep["elapsed"] = round(time.time() - t0, 1)
            return _dump(rep, out_path, 0 if rep["ok"] else 1)

        out_dir = os.path.join(env.WORK_DIR, "iso_out")
        man = pipeline.build_to(iso, q.items, out_dir=out_dir,
                                expected_md5=md5, name_hint="selftest")
        final = man["output"]["path"]
        rep["output"] = {"path": final, "md5": man["output"]["md5"]}
        step("统一写盘（检查台→构建→临时文件→提交）", True,
             "%s / %d 条 / %.1fs"
             % (os.path.basename(final), len(q.items),
                man.get("elapsed_write", man.get("elapsed", 0.0))))
        step("独立回读新 ISO", man["readback"]["ok"],
             "逐槽重读 %d 条：%s" % (man["readback"]["checked"],
                                     "全部一致" if man["readback"]["ok"] else "有差异"))
        step("整盘差分白名单", man["diff"]["outside"] == 0,
             "白名单外 %d 字节；允许区间 %d 段"
             % (man["diff"]["outside"], len(man["diff"]["allow_ranges"])))
        step("受保护资源守恒", man["protected"]["ok"],
             "%d 项（含 %d 个字体包）：%s"
             % (man["protected"]["checked"], man["protected"]["font_pkgs"],
                "无差异" if man["protected"]["ok"] else "有差异"))
        step("父盘未被覆盖", env.md5_file(iso) == md5, "父盘 MD5 一致=%s"
             % (env.md5_file(iso) == md5))
        rep["ok"] = all(s["ok"] for s in rep["steps"])
    except BaseException as exc:                 # noqa: BLE001
        rep["ok"] = False
        rep["detail"] = "%s：%s" % (type(exc).__name__, exc)
        rep["traceback"] = traceback.format_exc()[-2000:]
    rep["elapsed"] = round(time.time() - t0, 1)
    return _dump(rep, out_path, 0 if rep["ok"] else 1)


def _dump(rep: dict, out_path: str, code: int) -> int:
    import json

    from core import paths

    def _save(path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)

    # 守则：报告只能落在工具目录内，绝不写系统盘。用户传了 %TEMP% 之类的
    # 系统盘路径时**改道**到 work\tmp 并把原因记进报告，而不是让报告无声消失。
    target = out_path
    try:
        target = paths.check(out_path, "写自测报告")
    except paths.PathRuleError as exc:
        target = paths.temp_path(os.path.basename(out_path) or "selftest.json")
        rep["path_redirect"] = "%s（原定写入 %s，改写到 %s）" % (exc, out_path, target)

    try:
        _save(target)
        return code
    except OSError as exc:
        # 不许静默丢报告：把「目录当文件名 / 盘满 / 无权限 / 路径带空格被拆开」
        # 这类情况兜底写到 EXE 同目录，并在报告里留下原因（真实踩过：
        # 参数里的路径含空格被拆开后指向一个目录，报告无声无息没生成）。
        rest = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                            "exe_selftest.json")
        rep["dump_error"] = "%s：%s（原定写入 %s，改写 %s）" % (
            type(exc).__name__, exc, target, rest)
        try:
            _save(rest)
        except OSError:
            try:
                sys.stderr.write(rep["dump_error"] + "\n")
            except Exception:                     # noqa: BLE001
                pass
        return code


def main(argv=None) -> int:                      # noqa: ANN001
    sys.excepthook = _excepthook
    argv = list(sys.argv if argv is None else argv)

    # 先落守则再干别的：① 临时目录钉到工具目录（早于任何 tempfile 使用）
    # ② 没提权就提权（GUI 不等新实例；--selftest 必须等，否则报告还没写完
    #    父进程就退出了，验收脚本会读到「退出码 0 但报告不存在」）
    _bootstrap_paths()

    if "--selftest" in argv:
        code = _elevate_wait(argv)
        if code is not None:
            return code
        i = argv.index("--selftest")
        out = argv[i + 1] if i + 1 < len(argv) else os.path.join(
            os.path.dirname(os.path.abspath(sys.executable)), "exe_selftest.json")
        do_write = "--no-write" not in argv
        return headless_selftest(out, do_write=do_write)

    rc = _try_elevate(argv)
    if rc:
        return rc

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    # 高 DPI：Qt6 默认已开启缩放，这里只保证不因取整出偏差
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(argv)
    app.setApplicationName("ACE3 汉化工坊")
    app.setApplicationDisplayName("ACE3 汉化工坊")
    app.setOrganizationName("ACE3")

    # 窗口/任务栏图标：EXE 文件图标由 PyInstaller --icon 写进 PE 资源，
    # 这里补 Qt 侧（任务栏、Alt-Tab、窗口左上角）。找不到就静默跳过。
    from core import env as _env
    _icon = ""
    for _cand in (os.path.join(_env.GUI_DIR, "ace3.ico"),
                  os.path.join(HERE, "assets", "ace3.ico")):
        if os.path.isfile(_cand):
            _icon = _cand
            break
    if _icon:
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(_icon))

    from ui import theme
    theme.apply_theme(app, theme.theme_name())

    from ui.main_window import MainWindow
    win = MainWindow()
    win.show()

    if "--smoke" in argv:
        # 冒烟测试：起窗 → 2 秒后自动关闭（供打包验证用）
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
