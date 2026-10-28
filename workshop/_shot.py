"""真实平台起窗并截图，用于确认界面确实渲染出来（非离屏）。

用法：python _shot.py [输出目录]
会起真实窗口 → 等 2.5 秒 → 抓图 → 再切浅色抓一张 → 退出。
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main as M                                    # noqa: E402

M._ensure_stdio()

from PySide6.QtCore import Qt, QTimer               # noqa: E402
from PySide6.QtWidgets import QApplication          # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    HERE, "work", "shots")
os.makedirs(OUT, exist_ok=True)

QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
app = QApplication(sys.argv[:1])
app.setApplicationName("ACE3 汉化工坊")

from ui import theme                                # noqa: E402
theme.apply_theme(app, "dark")

from ui.main_window import MainWindow               # noqa: E402
win = MainWindow()
win.resize(1600, 960)
win.show()
win.raise_()


def _wait(ms: int) -> None:
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


shots = []


def grab_dark() -> None:
    _wait(2500)                       # 等 _startup 载入索引
    p = os.path.join(OUT, "gui_dark.png")
    win.grab().save(p)
    shots.append(p)
    # 顺手搜一个词，看结果列表真的填上
    if win.results is not None:
        win.results.edit.setText("何？")
        win.results._do_search()
        _wait(1200)
        p2 = os.path.join(OUT, "gui_dark_search.png")
        win.grab().save(p2)
        shots.append(p2)
        # 选中第一条，看中栏编辑器是否真的加载
        if win.results.list.count():
            win.results.list.setCurrentRow(0)
        _wait(1500)
        p2b = os.path.join(OUT, "gui_dark_editor.png")
        win.grab().save(p2b)
        shots.append(p2b)
    theme.set_theme("light")
    theme.apply_theme(app, "light")
    _wait(600)
    p3 = os.path.join(OUT, "gui_light.png")
    win.grab().save(p3)
    shots.append(p3)
    app.quit()


QTimer.singleShot(200, grab_dark)
app.exec()

for p in shots:
    print("%s  %d 字节" % (p, os.path.getsize(p)))
