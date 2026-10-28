"""运行环境与常量（不依赖 Qt）。

本模块只负责：工程根定位、冻结工具可见性、盘面常量、路径约定。
不做任何业务判断，不写任何文件。
"""
from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------- 工程根

def _is_project_root(p: str) -> bool:
    """工程根判据：必须有 ``tools/``。

    第二个条件二选一：
    * ``工程档案库/`` —— 作者本机的完整工程目录；
    * ``workshop/``   —— 公开发布仓库的 publish 布局（``publish/tools`` +
      ``publish/workshop``），发布版没有工程档案库。
    """
    if not p or not os.path.isdir(os.path.join(p, "tools")):
        return False
    return os.path.isdir(os.path.join(p, "工程档案库")) or os.path.isdir(
        os.path.join(p, "workshop")
    )


def _search_up(start: str, levels: int = 8) -> str:
    cur = os.path.abspath(start)
    for _ in range(levels):
        if _is_project_root(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return ""


def _frozen_root() -> str:
    """打包后的 EXE 怎么找工程根。

    优先顺序（都是"实测能落地"的方式，不做魔法猜测）：

    1. 环境变量 ``ACE3_ROOT``（运维/测试用）；
    2. EXE 同目录的 ``ace3_root.txt``（一行绝对路径，用户可自行改）；
    3. 从 EXE 所在目录向上找含 ``tools/`` + ``工程档案库/`` 的目录
       （把 EXE 放在工程内任意层级都能找到）；
    4. 从 EXE 同目录的 ``work/last_root.txt`` 读上次成功用过的根。
    """
    root = os.environ.get("ACE3_ROOT") or ""
    if _is_project_root(root):
        return root

    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    cfg = os.path.join(exe_dir, "ace3_root.txt")
    if os.path.isfile(cfg):
        try:
            with open(cfg, "r", encoding="utf-8-sig") as f:
                cand = f.read().strip().strip('"')
            if _is_project_root(cand):
                return cand
        except OSError:
            pass

    found = _search_up(exe_dir, 10)
    if found:
        return found

    for base in (exe_dir, os.path.dirname(exe_dir)):
        memo = os.path.join(base, "work", "last_root.txt")
        if os.path.isfile(memo):
            try:
                with open(memo, "r", encoding="utf-8-sig") as f:
                    cand = f.read().strip()
                if _is_project_root(cand):
                    return cand
            except OSError:
                pass
    return ""


def _find_root() -> str:
    """从本文件位置向上找含 tools/ 与 工程档案库/ 的目录。

    源码运行时走文件位置；PyInstaller 打包后 ``__file__`` 落在 ``_internal``
    里，改走 ``_frozen_root()``（EXE 位置 + 配置文件 + 记忆文件）。
    """
    if getattr(sys, "frozen", False):
        root = _frozen_root()
        if root:
            return root
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        root = _search_up(here, 8)
        if root:
            return root
    # 兜底：环境变量 → 程序所在目录的上一级 → 当前工作目录。
    # 不写死任何本机绝对路径：发布版把 tools/ 与 workshop/ 放在同一层，
    # 上一级就是工程根；再不行就退回当前工作目录，至少不会指向别人的磁盘。
    root = os.environ.get("ACE3_ROOT") or ""
    if root:
        return root
    if getattr(sys, "frozen", False):
        cand = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        cand = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return cand or os.getcwd()


ROOT = _find_root()
TOOLS = os.path.join(ROOT, "tools")
LOCATOR_DIR = os.path.join(TOOLS, "ace3_text_locator")
LOCATOR_DB = os.path.join(LOCATOR_DIR, "db")
LOCATOR_SHARED = os.path.join(LOCATOR_DB, "_shared")
FONT_CACHE = os.path.join(LOCATOR_SHARED, "font_cache.sqlite")
JP_CACHE = os.path.join(LOCATOR_SHARED, "jp_cache.sqlite")

if getattr(sys, "frozen", False):
    # 打包后不往 Program Files 之类只读目录写，改为 EXE 同目录的 work/
    GUI_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    # 源码目录 = 本包（core/）的上一级目录。不写死 tools/ace3_editor_gui，
    # 这样封版整理把源码搬到 ACE3汉化工坊\02_源码\ 之后仍然自洽。
    GUI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK_DIR = os.path.join(GUI_DIR, "work")          # 草稿 / 报告 / 产物清单
DRAFT_DIR = os.path.join(WORK_DIR, "drafts")
REPORT_DIR = os.path.join(WORK_DIR, "reports")
LOG_DIR = os.path.join(WORK_DIR, "logs")
ISO_DIR = os.path.join(WORK_DIR, "iso_out")
# 临时目录也钉在工具目录里：本机实测普通权限下 %TEMP%（在 C 盘）会
# PermissionError，且守则禁止往系统盘写，见 core/paths.py
TMP_DIR = os.path.join(WORK_DIR, "tmp")

ORIG_JP_ISO = os.path.join(
    ROOT, r"A.C.E. - Another Century's Episode 3 - The Final [NTSC-J] [SLPS-25784].iso"
)

# ---------------------------------------------------------------- 盘面常量

SECTOR = 2048
DATA_LBA = 2117
ISOBASE = 0x422800          # DATA.BIN 在 ISO 内的起始偏移（_r104_glyphfix2 同值）
VER_MAGIC = b"\x00\x00\x01\x00"
BND_MAGIC = b"BND\x00"
LZ_MAGIC = b"\x00\x01\x00\x00"
LZ_HEADER = 12

# 15 包 FontMap（权威列表，与 locator_core.FONT_PKGS 一致）
FONT_PKGS = (
    1200000, 1200001, 1200002, 1200003, 1200004, 1200005,
    1200006, 1200007, 1200011,
    4002050, 4002051, 4002052, 4002053, 4002054, 4002057,
)
# 这两包是 512 小图集，设计上只装 1814 码位，缺它们不算异常（coverage.py 口径）
SMALL_PKGS = frozenset({1200002, 1200003})
MAIN_PKGS = tuple(p for p in FONT_PKGS if p not in SMALL_PKGS)

# 保护性资源：写盘后必须逐字节未变
PROTECTED_EIDS = tuple(FONT_PKGS)


class EnvError(RuntimeError):
    pass


def ensure_sys_path() -> None:
    """让冻结工具与底层模块可被 import。只加路径，不改其内容。"""
    for p in (TOOLS, LOCATOR_DIR):
        if p and p not in sys.path:
            sys.path.insert(0, p)


def ensure_dirs() -> None:
    for d in (WORK_DIR, DRAFT_DIR, REPORT_DIR, LOG_DIR, TMP_DIR):
        os.makedirs(d, exist_ok=True)


def python_exe() -> str:
    """当前解释器（打包成 EXE 时返回 sys.executable，仅供诊断显示）。"""
    return sys.executable


def md5_file(path: str, chunk: int = 1 << 22, progress=None) -> str:
    import hashlib

    h = hashlib.md5()
    size = os.path.getsize(path)
    done = 0
    with open(path, "rb") as f:
        while True:
            # 必须读满：低速盘上 read(chunk) 会短读，只按「读到空就停」虽然
            # 不会算错 MD5，但会让进度条跳变；这里统一按块读满再退出。
            b = f.read(chunk)
            while b and len(b) < chunk and done + len(b) < size:
                more = f.read(chunk - len(b))
                if not more:
                    break
                b += more
            if not b:
                break
            h.update(b)
            done += len(b)
            if progress:
                progress(done, size)
    return h.hexdigest()


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
    return "%d B" % n
