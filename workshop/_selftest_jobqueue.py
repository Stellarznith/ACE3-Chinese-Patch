"""回归自测：任务队列（建索引被挡住的真实故障）。

背景
----
用户实测反馈：点「建索引」永远弹「当前有任务在跑，等它结束再建索引」，
即使根本没有任何任务在跑。

根因（已修）
------------
``MainWindow._build_for`` 写的是 ``if self.runner.busy:`` —— ``busy`` 是**方法**，
取到的 bound method 恒为真值，于是这个分支每次都进。修法两处：

1. 改成 ``self.runner.busy()``；
2. 顺手把「被挡住就弹窗丢弃」改成「排队，等当前任务收尾后自动接着建」——
   真正的冲突场景（正在建索引时又点一次）也不该丢请求。

另加一层兜底（``ui/workers.py``）：``finished`` 是排队投递到 GUI 线程的信号，
若 ``run()`` 返回后 QRunnable 立刻被线程池销毁，``_clear`` 可能收不到而让
``busy`` 永久为真；因此 QRunnable 关闭 ``setAutoDelete``，并在 ``start()`` 里
检查上一个 job 的 ``_finished`` 标记。

本脚本只验证这三件事，不碰 ISO、不写盘。
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main as entry                                       # noqa: E402

entry._ensure_stdio()

from PySide6.QtWidgets import QApplication, QMessageBox    # noqa: E402

from core import search as S                               # noqa: E402
from ui import theme                                       # noqa: E402
from ui.main_window import MainWindow                      # noqa: E402

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  " + detail) if detail else ""))


def pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def pump_until(app, pred, timeout: float = 8.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


def main() -> int:
    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app, "dark")

    # 打桩：不弹任何模态框（脚本只关心队列逻辑）
    dialogs = []
    for fn in ("information", "warning", "critical", "question"):
        setattr(QMessageBox, fn,
                staticmethod(lambda *a, **kw: dialogs.append(a[1:2]) or QMessageBox.Ok))

    # 建索引换成秒回的假实现：本测试只验队列调度，不真扫 4.2 GB 盘。
    calls = []

    def fake_build(iso, progress=None):
        if progress:
            progress("假装建索引…")
        time.sleep(0.25)
        calls.append(iso)
        return "fake_db"

    real_build = S.build_index
    S.build_index = fake_build
    real_find = S.find_index
    S.find_index = lambda iso=None: None       # done() 里拿不到索引，只会警告

    mw = MainWindow()
    mw.show()
    pump(app, 1.2)                              # 让 _startup 的自动载入跑完

    iso_a = os.path.join(os.sep + "fake", "a.iso")
    iso_b = os.path.join(os.sep + "fake", "b.iso")

    check("初始状态没有任务在跑", mw.runner.busy() is False,
          "busy()=%r" % (mw.runner.busy(),))

    mw._build_for(iso_a)
    check("第一次请求正常开跑", mw.runner.busy() is True,
          "label=%s" % mw.runner.label())

    mw._build_for(iso_b)                        # 撞上正在跑的任务
    check("第二次请求被排队而不是丢弃", mw._pending_index == iso_b,
          "_pending_index=%s" % mw._pending_index)
    check("排队时不弹窗打断", not dialogs, "弹窗次数=%d" % len(dialogs))
    check("状态栏说明在排队", "排队" in mw.status.text(), mw.status.text())

    ok = pump_until(app, lambda: not mw.runner.busy() and len(calls) >= 2, 8.0)
    check("当前任务结束后自动接着建", ok, "已跑完 %d 次：%s" % (len(calls), calls))
    check("两次都真的执行了", calls == [iso_a, iso_b], "calls=%s" % (calls,))
    check("队列已清空", mw._pending_index == "", "_pending_index=%r" % (mw._pending_index,))
    check("跑完后 busy 复位", mw.runner.busy() is False, "busy()=%r" % (mw.runner.busy(),))
    check("按钮恢复可用", mw.btn_index.isEnabled(), "enabled=%r" % (mw.btn_index.isEnabled(),))
    check("进度条已隐藏", not mw.bar.isVisible(), "visible=%r" % (mw.bar.isVisible(),))

    mw._dirty = False
    mw.close()
    pump(app, 0.3)

    S.build_index = real_build
    S.find_index = real_find

    bad = [r for r in RESULTS if not r[1]]
    print("\nPASS %d / FAIL %d" % (len(RESULTS) - len(bad), len(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
