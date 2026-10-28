"""写盘路径守则 + 管理员权限守卫（不依赖 Qt）。

两条硬规则（用户 2026-10-26 当面要求）：

1. **强制管理员运行**：EXE 用 PyInstaller ``--uac-admin`` 写进 PE manifest，
   双击即提权；万一 manifest 没生效（从源码跑、被别的启动器降权），
   启动时用 ``ShellExecuteW("runas")`` 自提升一次。
2. **绝不写系统盘**：所有产物（草稿、报告、日志、临时文件、新 ISO）只能落在
   **工具自身目录**内；任何指向系统盘（通常是 ``C:``）的写入一律拒绝，
   能改道的改道到 ``work/tmp``，不能改道的直接报错。

第 2 条不是洁癖，是本机实测出来的：

======  ==========================================  ========================
编号    操作                                         结果
======  ==========================================  ========================
1       E: 上的 EXE 写 ``%TEMP%\\x.json``            PermissionError[13]
2       E: 上的 EXE 写 ``%USERPROFILE%\\Documents\\x``  PermissionError[13]
3       E: 上的 EXE 在 ``C:\\_ace3p\\`` 新建目录        WinError 5
4       同一个 EXE 复制到 ``C:\\_ace3_copy_test\\`` 后写 ``%TEMP%``  成功
5       ``C:\\…\\python.exe`` 写 ``%TEMP%``           成功
======  ==========================================  ========================

同一份代码、同一个用户、同一时刻，只有「程序在 E:、目标在 C:」这一种组合
失败 ⇒ 跨盘写被拦，与具体路径长短、中文、空格都无关。根因（Defender / 应用
控制策略 / 跨卷重定向）**尚未定位，标 UNKNOWN**；本模块不去猜它，而是把
「不写系统盘」变成代码里的硬约束，顺带把这类失败从「报告无声消失」变成
「启动就明确告知」。
"""
from __future__ import annotations

import os
import sys

from . import env


# ---------------------------------------------------------------- 盘符与根

def system_drive() -> str:
    """系统盘盘符（大写单字母，如 ``C``）。"""
    raw = os.environ.get("SystemDrive") or "C:"
    return raw[0].upper()


def drive_of(path: str) -> str:
    """路径所在盘符（大写单字母）。UNC / 无盘符返回空串。"""
    p = os.path.abspath(path) if path else ""
    if len(p) >= 2 and p[1] == ":":
        return p[0].upper()
    return ""


def on_system_drive(path: str) -> bool:
    d = drive_of(path)
    return bool(d) and d == system_drive()


def write_root() -> str:
    """允许写入的根目录 = 工具自身目录。

    * 打包后：EXE 所在目录（``ACE3汉化工坊\\01_程序``）
    * 源码运行：``ACE3汉化工坊\\02_源码``

    两者都在 ``ACE3汉化工坊\\`` 下面，等于「工具自己的目录」。
    """
    return os.path.abspath(env.GUI_DIR)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def inside_write_root(path: str) -> bool:
    """``path`` 是否落在工具自身目录内（含子孙目录）。"""
    if not path:
        return False
    root = _norm(write_root())
    p = _norm(path)
    if p == root:
        return True
    return p.startswith(root + os.sep)


# ---------------------------------------------------------------- 拒绝与改道

class PathRuleError(OSError):
    """违反「不写系统盘 / 只写工具目录」守则。"""


def explains(path: str) -> str:
    """给人看的拒绝原因（不带异常类名，直接可放进弹窗）。"""
    if on_system_drive(path):
        return ("目标在系统盘 %s: 上。本工具按守则一律不写系统盘——"
                "实测跨盘写会被系统拒绝，也可能弹「拒绝访问」。"
                "请把产物目录放在工具所在盘，或直接用默认目录：\n%s"
                % (system_drive(), env.WORK_DIR))
    return ("目标不在工具自身目录内：\n%s\n允许写入的根目录只有：\n%s"
            % (os.path.abspath(path), write_root()))


def check(path: str, what: str = "写盘") -> str:
    """校验写盘目标，通过则返回规范化绝对路径，否则抛 :class:`PathRuleError`。"""
    if not path:
        raise PathRuleError("%s：目标路径为空" % what)
    ap = os.path.abspath(path)
    if on_system_drive(ap):
        raise PathRuleError("%s被拒绝：%s" % (what, explains(ap)))
    if not inside_write_root(ap):
        raise PathRuleError("%s被拒绝：%s" % (what, explains(ap)))
    return ap


def ensure_dir(path: str, what: str = "创建目录") -> str:
    """校验 + 建目录（校验在前，绝不先建再骂）。"""
    ap = check(path, what)
    os.makedirs(ap, exist_ok=True)
    return ap


def resolve_or(path: str, fallback: str, what: str = "输出目录") -> str:
    """能用就用，违反守则就退回 ``fallback``（fallback 本身必须合法）。

    返回实际使用的绝对路径；被改道时通过 :func:`last_redirect` 可取原因。
    """
    global _LAST_REDIRECT
    try:
        return ensure_dir(path, what) if path else ensure_dir(fallback, what)
    except PathRuleError as exc:
        _LAST_REDIRECT = str(exc)
        return ensure_dir(fallback, what)


_LAST_REDIRECT = ""


def last_redirect() -> str:
    """最近一次被改道的原因（空串表示没改道）。"""
    return _LAST_REDIRECT


# ---------------------------------------------------------------- 临时目录

def install_temp_redirect() -> str:
    """把 ``tempfile`` 与 ``TMP``/``TEMP`` 环境变量都指到工具目录。

    这样即便某个底层模块偷偷用 ``tempfile``，产物也不会落到系统盘；
    子进程（例如写盘用的辅助进程）继承 ``TMP`` 后同样留在工具目录。
    """
    import tempfile

    d = os.path.join(env.WORK_DIR, "tmp")
    os.makedirs(d, exist_ok=True)
    tempfile.tempdir = d
    os.environ["TMP"] = d
    os.environ["TEMP"] = d
    os.environ["TMPDIR"] = d
    return d


def temp_dir() -> str:
    d = os.path.join(env.WORK_DIR, "tmp")
    os.makedirs(d, exist_ok=True)
    return d


def temp_path(name: str) -> str:
    """工具目录内的临时文件名（``name`` 只取文件名部分）。"""
    return os.path.join(temp_dir(), os.path.basename(name) or "tmp.bin")


# ---------------------------------------------------------------- 管理员权限

def is_admin() -> bool:
    """当前进程是否已提权。取不到信息时按「否」处理（宁可多提一次）。"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                            # noqa: BLE001
        return False


def _self_cmdline(argv=None) -> tuple:
    """(可执行文件, 参数字符串)。frozen 时参数不含 argv[0]，否则含。"""
    import sys as _sys
    argv = list(_sys.argv if argv is None else argv)
    if getattr(_sys, "frozen", False):
        return _sys.executable, argv[1:]
    return _sys.executable, argv


def _quote(args) -> str:
    return " ".join('"%s"' % a if " " in a else a for a in args)


def relaunch_as_admin(argv=None) -> tuple:
    """用 ``runas`` 重新拉起自己（UAC 已关时静默生效），不等它结束。

    返回 ``(ok, message)``；``ok`` 为 True 表示新实例已经起来了，
    调用方应当立即退出当前实例，避免两个窗口同时改草稿。
    """
    exe, args = _self_cmdline(argv)
    try:
        import ctypes
        from ctypes import wintypes

        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.restype = wintypes.HINSTANCE
        shell32.ShellExecuteW.argtypes = [
            wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
        rc = shell32.ShellExecuteW(None, "runas", exe, _quote(args),
                                   os.path.dirname(os.path.abspath(exe)), 1)
        if rc > 32:
            return True, "已请求管理员权限重新启动"
        return False, ("提权失败（ShellExecuteW 返回 %d）。"
                       "请右键 EXE →「以管理员身份运行」。" % rc)
    except Exception as exc:                     # noqa: BLE001
        return False, "提权失败：%s：%s" % (type(exc).__name__, exc)


def run_elevated_wait(argv=None, show: int = 0) -> tuple:
    """以管理员身份跑**自己**并等它结束，返回 ``(ok, code, message)``。

    无头自测（``--selftest``）必须用这个：报告是子进程写的，父进程若不等就
    退出，调用方（验收脚本）会读到「退出码 0 但报告不存在」这种假象。
    """
    exe, args = _self_cmdline(argv)
    return run_exe_as_admin_wait(exe, args, show=show)


def run_exe_as_admin_wait(exe: str, args=None, show: int = 0,
                          timeout: float = 0) -> tuple:
    """以管理员身份跑**任意** EXE 并等它结束。

    验收脚本需要这个：带 ``requireAdministrator`` 清单的 EXE 无法用
    ``subprocess``/``CreateProcess`` 直接启动——会拿到
    ``OSError [WinError 740] 请求的操作需要提升``。只能走 ShellExecuteEx。

    ``timeout`` 秒（0 = 无限等）。超时会 **终止** 子进程并返回 ``(False, -2, …)``：
    调用方是自动化验收脚本，不能因为子进程卡住就永远挂在
    ``WaitForSingleObject`` 上（实测踩过，十几分钟无输出）。
    """
    import ctypes
    from ctypes import wintypes

    args = list(args or [])

    class _SEI(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_NOASYNC = 0x00000100
    INFINITE = 0xFFFFFFFF

    sei = _SEI()
    sei.cbSize = ctypes.sizeof(_SEI)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    sei.lpVerb = "runas"
    sei.lpFile = exe
    sei.lpParameters = _quote(args)
    sei.lpDirectory = os.path.dirname(os.path.abspath(exe))
    sei.nShow = show

    try:
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32
        ok = shell32.ShellExecuteExW(ctypes.byref(sei))
        if not ok:
            err = ctypes.get_last_error() or kernel32.GetLastError()
            if err == 1223:                      # ERROR_CANCELLED：用户点了「否」
                return False, -1, "提权被取消（用户在 UAC 对话框选择了「否」）"
            return False, -1, "提权启动失败（错误码 %d）" % err
        if not sei.hProcess:
            return False, -1, "提权启动后没有拿到进程句柄"
        if timeout and timeout > 0:
            ms = int(timeout * 1000)
            waited = kernel32.WaitForSingleObject(sei.hProcess, ms)
            if waited == 0x00000102:             # WAIT_TIMEOUT
                kernel32.TerminateProcess(sei.hProcess, 1)
                kernel32.CloseHandle(sei.hProcess)
                return False, -2, ("管理员子进程 %.0f 秒未结束，已终止"
                                   % timeout)
        else:
            kernel32.WaitForSingleObject(sei.hProcess, INFINITE)
        code = wintypes.DWORD(0)
        kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
        kernel32.CloseHandle(sei.hProcess)
        return True, int(code.value), "管理员子进程已结束（退出码 %d）" % code.value
    except Exception as exc:                     # noqa: BLE001
        return False, -1, "提权失败：%s：%s" % (type(exc).__name__, exc)


def spawn_elevated(exe: str, args=None) -> tuple:
    """以管理员身份启动任意 EXE 但**不等**，返回 ``(ok, pid, message)``。

    ``--smoke`` 这类会自己起窗退出的用例用它：等它也行，但验收脚本要给
    超时兜底，所以把等待交给调用方，这里只负责过掉 WinError 740。
    """
    import ctypes
    from ctypes import wintypes

    args = list(args or [])

    class _SEI(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    sei = _SEI()
    sei.cbSize = ctypes.sizeof(_SEI)
    sei.fMask = 0x00000040 | 0x00000100          # NOCLOSEPROCESS | NOASYNC
    sei.lpVerb = "runas"
    sei.lpFile = exe
    sei.lpParameters = _quote(args)
    sei.lpDirectory = os.path.dirname(os.path.abspath(exe))
    sei.nShow = 0
    try:
        ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
        if not ok:
            err = ctypes.get_last_error() or ctypes.windll.kernel32.GetLastError()
            if err == 1223:
                return False, 0, "提权被取消（用户点了「否」）"
            return False, 0, "提权启动失败（错误码 %d）" % err
        pid = 0
        if sei.hProcess:
            pid = int(ctypes.windll.kernel32.GetProcessId(sei.hProcess) or 0)
            ctypes.windll.kernel32.CloseHandle(sei.hProcess)
        return True, pid, "已提权启动（pid %d）" % pid
    except Exception as exc:                     # noqa: BLE001
        return False, 0, "提权失败：%s：%s" % (type(exc).__name__, exc)



# ---------------------------------------------------------------- 体检信息

def status() -> dict:
    """一次性汇总，供标题栏 / 状态栏 / 自测 / 报告使用。"""
    root = write_root()
    return {
        "admin": is_admin(),
        "system_drive": system_drive(),
        "write_root": root,
        "write_root_on_system_drive": on_system_drive(root),
        "work_dir": env.WORK_DIR,
        "temp_dir": os.path.join(env.WORK_DIR, "tmp"),
        "frozen": bool(getattr(sys, "frozen", False)),
        "ok": (not on_system_drive(root)) and is_admin(),
    }


def status_text() -> str:
    """一行中文说明（状态栏用，长但不啰嗦）。"""
    s = status()
    perm = "管理员" if s["admin"] else "普通权限（未提权）"
    disk = ("写盘目录：%s" % s["write_root"])
    if s["write_root_on_system_drive"]:
        return "%s ｜ %s ｜ ⚠ 工具位于系统盘 %s:，按守则不能写盘" % (
            perm, disk, s["system_drive"])
    return "%s ｜ %s ｜ 不写 %s: 盘 ｜ 临时文件走 work\\tmp" % (
        perm, disk, s["system_drive"])


def require_tool_off_system_drive() -> str:
    """工具自己被放在系统盘时返回一句人话（空串表示没问题）。

    这种情况下任何写入都等于写系统盘，只能让用户把整个目录搬走。
    """
    root = write_root()
    if not on_system_drive(root):
        return ""
    return ("ACE3 汉化工坊 现在位于系统盘：\n%s\n\n"
            "本工具按守则一律不写系统盘，因此无法保存草稿、报告和新 ISO。\n"
            "请把整个「ACE3汉化工坊」目录移到非系统盘（如 E:）后再运行。"
            % root)
