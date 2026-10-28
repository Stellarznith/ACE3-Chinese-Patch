"""浏览器目录记忆与对话框前置的自动测试（无交互）。

运行：
  python installer_tests\\test_browse_memory.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "installer"))

import app as app_module  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{extra}]" if extra else ""))
    if not ok:
        FAILS.append(name)


def make_dirs(base: Path) -> tuple[Path, Path, Path]:
    a = base / "补丁目录 甲"
    b = base / "G盘模拟 乙"
    c = base / "输出目录 丙"
    for d in (a, b, c):
        d.mkdir(parents=True, exist_ok=True)
    return a, b, c


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        settings_file = base / "settings.json"
        app_module.settings_path = lambda: settings_file  # type: ignore[assignment]
        d_patch, d_source, d_output = make_dirs(base)

        root = tk.Tk()
        root.withdraw()
        ui = app_module.App(root)

        # 1. 三个键独立记忆
        ui.settings.set_dir("patch_dir", d_patch)
        ui.settings.set_dir("source_dir", d_source)
        ui.settings.set_dir("output_dir", d_output)
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        check("三个目录键互相独立",
              data.get("patch_dir") == str(d_patch)
              and data.get("source_dir") == str(d_source)
              and data.get("output_dir") == str(d_output),
              json.dumps(data, ensure_ascii=False))

        # 2. 重新构造后仍能读回（模拟下次启动）
        again = app_module.Settings()
        check("重启后仍记得目录",
              again.get_dir("patch_dir") == str(d_patch)
              and again.get_dir("source_dir") == str(d_source)
              and again.get_dir("output_dir") == str(d_output))

        # 3. 浏览时把 initialdir 传进去，并在对话框期间临时置顶主窗口
        seen: list[dict] = []
        topmost_during: list[bool] = []

        def fake_open(**kwargs):
            seen.append(dict(kwargs))
            topmost_during.append(bool(root.attributes("-topmost")))
            return str(d_patch / "demo.xdelta")

        def fake_save(**kwargs):
            seen.append(dict(kwargs))
            topmost_during.append(bool(root.attributes("-topmost")))
            return str(d_output / "ACE3_中文版.iso")

        app_module.filedialog.askopenfilename = fake_open          # type: ignore[assignment]
        app_module.filedialog.asksaveasfilename = fake_save        # type: ignore[assignment]

        ui.browse_patch()
        check("补丁对话框带 initialdir", seen[-1].get("initialdir") == str(d_patch), str(seen[-1]))
        check("补丁对话框带 parent", seen[-1].get("parent") is root)
        check("对话框打开期间主窗口置顶", topmost_during[-1] is True)
        check("对话框关闭后取消置顶", bool(root.attributes("-topmost")) is False)

        ui.source.set(str(d_source / "原版.iso"))
        ui.browse_output()
        check("输出对话框带 initialdir", seen[-1].get("initialdir") == str(d_output), str(seen[-1]))
        check("输出路径已写回输入框", ui.output.get() == str(d_output / "ACE3_中文版.iso"), ui.output.get())
        check("输出对话框也置顶且复位", topmost_during[-1] is True and bool(root.attributes("-topmost")) is False)

        # 3b. 对话框里取消（返回空串）不能破坏已有路径
        app_module.filedialog.asksaveasfilename = lambda **kw: ""  # type: ignore[assignment]
        before = ui.output.get()
        ui.browse_output()
        check("取消选择时保留原输出路径", ui.output.get() == before, ui.output.get())

        # 4. 目录被删除后不应把坏路径当成 initialdir
        ui.settings.set_dir("patch_dir", d_patch)
        import shutil
        shutil.rmtree(d_patch)
        gone = app_module.Settings()
        check("已删除的目录不再记忆", gone.get_dir("patch_dir") == "", gone.get_dir("patch_dir"))

        # 5. 设置文件损坏时不崩溃
        settings_file.write_text("{ not json", encoding="utf-8")
        broken = app_module.Settings()
        check("设置文件损坏时安全降级", broken.get_dir("patch_dir") == "")

        # 6. 保存失败不影响程序
        app_module.settings_path = lambda: Path("Z:/不存在的盘/settings.json")  # type: ignore[assignment]
        ro = app_module.Settings()
        ro.set_dir("patch_dir", str(d_output))
        check("写入失败不抛异常", True)

        root.destroy()

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}: {FAILS}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
