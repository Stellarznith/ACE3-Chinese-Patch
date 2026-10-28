from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from engine import InstallCancelled, InstallError, PatchManifest, install

APP_TITLE = "ACE3 中文化补丁安装工具"
APP_VERSION = "开发版 0.2.0"
MIN_WIDTH = 620
SETTINGS_DIR_KEYS = ("patch_dir", "source_dir", "output_dir")


def configure_logging() -> None:
    try:
        if getattr(sys, "frozen", False):
            log_dir = Path(os.getenv("LOCALAPPDATA", Path.home())) / "ACE3_CN_Patcher" / "logs"
        else:
            log_dir = Path(__file__).resolve().parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=log_dir / "patcher.log", level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    except OSError:
        logging.basicConfig(level=logging.CRITICAL)


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def settings_path() -> Path:
    """浏览目录记忆写在用户目录，开发运行与打包运行共用同一份，不污染源码树。"""
    base = Path(os.getenv("LOCALAPPDATA", Path.home())) / "ACE3_CN_Patcher"
    return base / "settings.json"


class Settings:
    """三个路径各自独立记住上次浏览的目录，写在用户目录，失败不影响安装。"""

    def __init__(self) -> None:
        self.path = settings_path()
        self.data: dict[str, str] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key in SETTINGS_DIR_KEYS:
                    value = raw.get(key)
                    if isinstance(value, str) and Path(value).is_dir():
                        self.data[key] = value
        except (OSError, ValueError):
            self.data = {}

    def get_dir(self, key: str) -> str:
        return self.data.get(key, "")

    def set_dir(self, key: str, directory: str | Path) -> None:
        directory = str(directory)
        if not Path(directory).is_dir():
            return
        if self.data.get(key) == directory:
            return
        self.data[key] = directory
        self.save()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            logging.debug("保存浏览目录失败", exc_info=True)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        icon_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        icon_path = icon_root / "ace3_patcher.ico"
        if icon_path.is_file():
            try:
                self.root.iconbitmap(str(icon_path))
            except tk.TclError:
                pass
        self._wrap_labels: list[ttk.Label] = []
        self.cancel_event = threading.Event()
        self.busy = False
        self.settings = Settings()
        self.patch = tk.StringVar()
        self.source = tk.StringVar()
        self.output = tk.StringVar()
        self.stage = tk.StringVar(value="等待开始")
        self.detail = tk.StringVar(value="请选择独立补丁、对应的输入 ISO 和输出位置。补丁元数据从同目录同名 JSON 自动读取。")
        self.patch_type = tk.StringVar(value="")
        self._selected_manifest: PatchManifest | None = None
        self._configure_dpi()
        self._build()
        self._fit_window()
        self.root.bind("<Configure>", self._on_configure)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _configure_dpi(self) -> None:
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError):
                pass

    def _fit_window(self) -> None:
        self.root.update_idletasks()
        width = max(self.root.winfo_reqwidth(), MIN_WIDTH)
        height = self.root.winfo_reqheight()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(width, screen_w - 80)
        height = min(height, screen_h - 120)
        x = max((screen_w - width) // 2, 0)
        y = max((screen_h - height) // 2 - 20, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(MIN_WIDTH, width), min(self.root.winfo_reqheight(), height))
        self._resize_wraps(width)

    def _resize_wraps(self, width: int) -> None:
        self._wrap_width = max(width - 60, 320)
        for label in self._wrap_labels:
            label.configure(wraplength=self._wrap_width)

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        width = event.width
        if abs(width - getattr(self, "_wrap_width", 0)) > 12:
            self._resize_wraps(width)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        footer = ttk.Frame(outer)
        footer.pack(side="bottom", fill="x")
        ttk.Label(footer, textvariable=self.stage).pack(anchor="w", pady=(10, 4))
        self.progress = ttk.Progressbar(footer, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        actions = ttk.Frame(footer)
        actions.pack(pady=13)
        self.start_button = ttk.Button(actions, text="开始安装汉化补丁", command=self.start)
        self.start_button.pack(side="left", padx=5)
        self.cancel_button = ttk.Button(actions, text="取消", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=5)
        self.open_button = ttk.Button(actions, text="打开输出目录", command=self.open_output, state="disabled")
        self.open_button.pack(side="left", padx=5)
        note = ttk.Label(footer, text="本工具仅用于安装非官方中文化补丁，不包含游戏本体。\n请自行准备合法持有的对应版本原版游戏。")
        note.pack(anchor="w", pady=(4, 0))
        version = ttk.Label(footer, text=f"版本 {APP_VERSION}", foreground="#555555")
        version.pack(anchor="e", pady=(6, 0))
        self._wrap_labels.extend((note,))

        body = ttk.Frame(outer)
        body.pack(side="top", fill="both", expand=True)
        ttk.Label(body, text=APP_TITLE, font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="center")
        ttk.Label(body, text="Another Century's Episode 3: THE FINAL").pack(pady=(2, 0))
        ttk.Label(body, text="中文化制作：伐妹儿    GitHub: Stellaznith").pack(pady=(1, 12))
        ttk.Separator(body).pack(fill="x", pady=(0, 14))

        self._path_row(body, "汉化补丁（.xdelta）", self.patch, self.browse_patch)
        self._path_row(body, "对应的游戏 ISO", self.source, self.browse_source)
        self._path_row(body, "输出 ISO 保存位置", self.output, self.browse_output)
        ttk.Label(body, textvariable=self.patch_type, wraplength=590).pack(anchor="w", pady=(6, 0))
        self.detail_label = ttk.Label(body, textvariable=self.detail, wraplength=590, justify="left")
        self.detail_label.pack(anchor="w", pady=(4, 4))
        self._wrap_labels.append(self.detail_label)

    def _path_row(self, parent: ttk.Frame, label: str, variable: tk.StringVar, command) -> None:
        ttk.Label(parent, text=label).pack(anchor="w")
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(4, 2))
        entry = ttk.Entry(row, textvariable=variable)
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览…", command=command).pack(side="left", padx=(7, 0))

    def _prepare_dialog(self) -> None:
        """文件对话框弹出前先把主窗口提到前台并临时置顶。

        Windows 的前台锁会让非前台进程新建的窗口拿不到焦点，对话框就藏在主窗口后面，
        表现成“点了浏览却改不了路径”。这里临时置顶，对话框作为子窗口会跟着显示在最前。
        """
        self.root.update_idletasks()
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        try:
            self.root.focus_force()
        except tk.TclError:
            pass

    def _after_dialog(self) -> None:
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass
        self.root.lift()

    def _initial_dir(self, key: str, fallback: Path | None = None) -> str:
        saved = self.settings.get_dir(key)
        if saved:
            return saved
        if fallback is not None:
            try:
                if fallback.parent.is_dir():
                    return str(fallback.parent)
            except OSError:
                pass
        for candidate in (Path.home() / "Desktop", Path.home() / "Downloads", Path.home()):
            try:
                if candidate.is_dir():
                    return str(candidate)
            except OSError:
                continue
        return ""

    def _remember_dir(self, key: str, path: str) -> None:
        directory = Path(path).parent
        self.settings.set_dir(key, directory)

    def _ask_open(self, title: str, key: str, patterns: list[tuple[str, str]]) -> str:
        self._prepare_dialog()
        options = {"parent": self.root, "title": title, "filetypes": patterns}
        initial = self._initial_dir(key)
        if initial:
            options["initialdir"] = initial
        try:
            path = filedialog.askopenfilename(**options)
        finally:
            self._after_dialog()
        logging.info("浏览补丁：initialdir=%r -> %r", options.get("initialdir"), path)
        if path:
            self._remember_dir(key, path)
        return path

    def browse_patch(self) -> None:
        path = self._ask_open("选择汉化补丁", "patch_dir", [("Xdelta 补丁", "*.xdelta"), ("所有文件", "*.*")])
        if path:
            self.patch.set(path)
            self._load_selected_manifest(Path(path))

    def _load_selected_manifest(self, patch: Path) -> None:
        try:
            manifest = PatchManifest.load_for_patch(patch)
            if patch.name != manifest.patch_filename:
                raise InstallError("选择的补丁文件名与元数据 patch.filename 不一致。")
        except InstallError as exc:
            self._selected_manifest = None
            self.patch_type.set("")
            self.detail.set(str(exc))
            return
        self._selected_manifest = manifest
        kind = "完整补丁（FULL）" if manifest.patch_type == "FULL" else "版本更新（UPDATE）"
        self.patch_type.set(f"{kind}：{manifest.source.version} → {manifest.target.version}")
        self.detail.set(f"补丁元数据已读取：{patch.with_suffix('.json').name}。SHA-256 用于文件完整性校验，不代表来源认证。")

    def browse_source(self) -> None:
        fallback = Path(self.output.get()) if self.output.get() else None
        self._prepare_dialog()
        options = {
            "parent": self.root,
            "title": "选择与补丁匹配的输入 ISO",
            "filetypes": [("ISO 镜像", "*.iso"), ("所有文件", "*.*")],
        }
        initial = self._initial_dir("source_dir", fallback)
        if initial:
            options["initialdir"] = initial
        try:
            path = filedialog.askopenfilename(**options)
        finally:
            self._after_dialog()
        logging.info("浏览输入 ISO：initialdir=%r -> %r", options.get("initialdir"), path)
        if path:
            self.source.set(path)
            self._remember_dir("source_dir", path)

    def browse_output(self) -> None:
        current = self.output.get()
        default_name = Path(current).name if current else "ACE3_输出.iso"
        self._prepare_dialog()
        options = {
            "parent": self.root,
            "title": "选择输出 ISO 保存位置",
            "initialfile": default_name,
            "defaultextension": ".iso",
            "filetypes": [("ISO 镜像", "*.iso")],
        }
        initial = self._initial_dir("output_dir", Path(self.source.get()) if self.source.get() else None)
        if initial:
            options["initialdir"] = initial
        try:
            path = filedialog.asksaveasfilename(**options)
        finally:
            self._after_dialog()
        logging.info("浏览输出 ISO：initialdir=%r -> %r", options.get("initialdir"), path)
        if path:
            self.output.set(path)
            self._remember_dir("output_dir", path)

    def start(self) -> None:
        if self.busy:
            return
        if not self.patch.get() or not self.source.get() or not self.output.get():
            messagebox.showwarning(APP_TITLE, "请选择汉化补丁、对应的输入 ISO 和输出位置。")
            return
        patch = Path(self.patch.get())
        try:
            manifest = PatchManifest.load_for_patch(patch)
            if patch.name != manifest.patch_filename:
                raise InstallError("选择的补丁文件名与元数据 patch.filename 不一致。")
        except InstallError as exc:
            self._selected_manifest = None
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self._selected_manifest = manifest
        self.busy = True
        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)
        args = (patch, Path(self.source.get()), Path(self.output.get()))
        threading.Thread(target=self._worker, args=args, daemon=True).start()

    def _worker(self, patch: Path, source: Path, destination: Path) -> None:
        try:
            executable = resource_dir() / "xdelta3.exe"
            result = install(patch, source, destination, self.cancel_event, self._progress, executable)
            self.root.after(0, self._finished, None, result)
        except Exception as exc:
            logging.exception("安装失败：%s", exc)
            self.root.after(0, self._finished, exc, None)

    def _progress(self, stage: str, percent: int | None, detail: str) -> None:
        self.root.after(0, self._update_progress, stage, percent, detail)

    def _update_progress(self, stage: str, percent: int | None, detail: str) -> None:
        self.stage.set(stage)
        if detail:
            self.detail.set(detail)
        if percent is not None:
            if str(self.progress["mode"]) != "determinate":
                self.progress.stop()
                self.progress.configure(mode="determinate")
            self.progress["value"] = percent
        elif str(self.progress["mode"]) != "indeterminate":
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)

    def _finished(self, error: Exception | None, result: Path | None) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.busy = False
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        if error:
            if isinstance(error, InstallCancelled):
                self.stage.set("安装已取消")
                messagebox.showinfo(APP_TITLE, "安装已取消，临时文件已清理。")
            else:
                self.stage.set("安装失败")
                self.detail.set(str(error))
                messagebox.showerror(APP_TITLE, str(error))
            return
        self.stage.set("安装完成，输出已通过 SHA-256 校验。")
        self.detail.set(str(result))
        self.open_button.configure(state="normal")
        messagebox.showinfo(APP_TITLE, f"安装成功，中文版 ISO 已通过 SHA-256 校验：\n{result}")

    def cancel(self) -> None:
        if self.busy:
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.stage.set("正在取消并清理临时文件……")

    def open_output(self) -> None:
        path = Path(self.output.get()).parent
        if os.name == "nt":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], shell=False)
        else:
            subprocess.Popen(["xdg-open", str(path)], shell=False)

    def close(self) -> None:
        if self.busy:
            if not messagebox.askyesno(APP_TITLE, "安装仍在运行。取消安装并退出吗？"):
                return
            self.cancel_event.set()
            self.root.after(100, self._wait_close)
            return
        self.root.destroy()

    def _wait_close(self) -> None:
        if self.busy:
            self.root.after(100, self._wait_close)
        else:
            self.root.destroy()


def main() -> None:
    configure_logging()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
