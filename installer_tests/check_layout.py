"""离屏检查 GUI 布局：按钮是否落在窗口可见区域内。"""
from __future__ import annotations

import sys
from pathlib import Path

import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "installer"))

from app import App  # noqa: E402


def check() -> int:
    root = tk.Tk()
    app = App(root)
    root.update_idletasks()
    root.update()
    win_w = root.winfo_width()
    win_h = root.winfo_height()
    print(f"window={win_w}x{win_h} req={root.winfo_reqwidth()}x{root.winfo_reqheight()}")
    problems = []
    widgets = {
        "start": app.start_button,
        "cancel": app.cancel_button,
        "open": app.open_button,
        "progress": app.progress,
    }
    for name, widget in widgets.items():
        x = widget.winfo_rootx() - root.winfo_rootx()
        y = widget.winfo_rooty() - root.winfo_rooty()
        w = widget.winfo_width()
        h = widget.winfo_height()
        visible = widget.winfo_ismapped() == 1
        inside = x >= 0 and y >= 0 and x + w <= win_w + 1 and y + h <= win_h + 1
        print(f"{name}: x={x} y={y} w={w} h={h} mapped={visible} inside={inside} text={widget.cget('text') if 'text' in widget.keys() else ''}")
        if not visible or not inside or w <= 1 or h <= 1:
            problems.append(name)
    root.destroy()
    if problems:
        print("FAIL:", ", ".join(problems))
        return 1
    print("PASS 所有关键控件可见且在窗口内")
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
