"""写盘守则（core.paths）回归自测：不写系统盘 + 只写工具目录 + 提权接口。

为什么单独测这个：守则是硬约束，一旦被绕开，产物就会落到系统盘上，
而系统盘写入在本机是**会失败的**（见 core/paths.py 头部对照表）。
所以这里不测「写成功」，专门测「该拒的拒掉、该改道的改道」。

覆盖：
  A. 盘符解析：system_drive / drive_of / on_system_drive
  B. write_root 与 inside_write_root 边界（自身、子目录、兄弟目录、上级）
  C. check()：空路径 / 系统盘 / 目录外 三类拒绝，合法路径放行
  D. ensure_dir()：违规时**不建目录**（先判后建），合法时建得出来
  E. resolve_or()：违规退回 fallback 并记 last_redirect；合法原样返回
  F. install_temp_redirect()：tempfile.tempdir 与 TMP/TEMP/TMPDIR 全指向工具目录
  G. is_admin() / status() / status_text() / require_tool_off_system_drive() 形状
  H. relaunch_as_admin / run_elevated_wait 的可调用性（不真的提权）
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core import env, paths  # noqa: E402

PASS, FAIL = [], []


def ck(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append((name, detail))
    print(("  OK   " if cond else "  FAIL ") + name
          + (("　←　" + detail) if detail else ""))


def main() -> int:
    env.ensure_dirs()
    sysd = paths.system_drive()
    root = paths.write_root()

    print("== A. 盘符解析 ==")
    ck("system_drive 是单字母", len(sysd) == 1 and sysd.isalpha(), sysd)
    ck("drive_of 解析盘符", paths.drive_of("C:\\x\\y") == "C", paths.drive_of("C:\\x\\y"))
    ck("drive_of 大小写归一", paths.drive_of("e:\\ACE3") == "E", paths.drive_of("e:\\ACE3"))
    ck("drive_of 相对路径按当前盘", paths.drive_of("x") != "", paths.drive_of("x"))
    ck("drive_of UNC 返回空", paths.drive_of("\\\\srv\\share\\x") == "",
       repr(paths.drive_of("\\\\srv\\share\\x")))
    ck("on_system_drive 认系统盘", paths.on_system_drive(sysd + ":\\Windows"))
    ck("on_system_drive 不认工具盘", not paths.on_system_drive(root),
       "%s 在 %s: 上" % (root, paths.drive_of(root)))

    print("== B. write_root 边界 ==")
    ck("write_root 是绝对路径", os.path.isabs(root), root)
    ck("inside_write_root 认自身", paths.inside_write_root(root))
    ck("inside_write_root 认子目录",
       paths.inside_write_root(os.path.join(root, "work", "drafts")))
    ck("inside_write_root 拒兄弟目录",
       not paths.inside_write_root(os.path.dirname(root)))
    ck("inside_write_root 拒上级", not paths.inside_write_root(
        os.path.dirname(os.path.dirname(root))))
    ck("inside_write_root 拒空串", not paths.inside_write_root(""))
    # 前缀陷阱：<root>_bak 不能被当成 <root> 的子目录
    ck("inside_write_root 拒前缀相同但不同名",
       not paths.inside_write_root(root + "_bak"))

    print("== C. check() 拒绝清单 ==")
    try:
        paths.check("", "测试")
        ck("空路径被拒", False, "居然没抛")
    except paths.PathRuleError as exc:
        ck("空路径被拒", True, str(exc)[:40])
    try:
        paths.check(os.path.join(sysd + ":\\", "ace3_probe", "x.json"), "测试")
        ck("系统盘被拒", False, "居然没抛")
    except paths.PathRuleError as exc:
        ck("系统盘被拒", "系统盘" in str(exc), str(exc).splitlines()[0][:44])
    try:
        paths.check(os.path.join(os.path.dirname(root), "x.json"), "测试")
        ck("工具目录外被拒", False, "居然没抛")
    except paths.PathRuleError as exc:
        ck("工具目录外被拒", "不在工具自身目录内" in str(exc),
           str(exc).splitlines()[0][:44])
    ok_path = os.path.join(env.TMP_DIR, "check_ok.json")
    ck("合法路径放行并返回 abspath",
       paths.check(ok_path) == os.path.abspath(ok_path), paths.check(ok_path))
    ck("PathRuleError 是 OSError 子类", issubclass(paths.PathRuleError, OSError))

    print("== D. ensure_dir() 先判后建 ==")
    bad_dir = os.path.join(sysd + ":\\", "ace3_should_not_exist_%d" % os.getpid())
    try:
        paths.ensure_dir(bad_dir, "测试目录")
        ck("违规目录被拒", False, "居然没抛")
    except paths.PathRuleError:
        ck("违规目录被拒", True)
    ck("违规目录没有被真的建出来", not os.path.exists(bad_dir), bad_dir)
    good_dir = os.path.join(env.TMP_DIR, "_selftest_paths_dir")
    shutil.rmtree(good_dir, ignore_errors=True)
    got = paths.ensure_dir(good_dir, "测试目录")
    ck("合法目录建得出来", os.path.isdir(good_dir) and got == os.path.abspath(good_dir))
    shutil.rmtree(good_dir, ignore_errors=True)

    print("== E. resolve_or() 改道 ==")
    good = paths.resolve_or(os.path.join(env.TMP_DIR, "rr_ok"),
                            env.DRAFT_DIR, "测试输出")
    ck("合法目录原样返回", good == os.path.abspath(os.path.join(env.TMP_DIR, "rr_ok")),
       good)
    ck("合法时不记改道", paths.last_redirect() == "", repr(paths.last_redirect()))
    bad = paths.resolve_or(os.path.join(sysd + ":\\", "ace3_rr_bad"),
                           env.DRAFT_DIR, "测试输出")
    ck("违规目录改道到 fallback", bad == os.path.abspath(env.DRAFT_DIR), bad)
    ck("改道原因被记下", "系统盘" in paths.last_redirect(),
       paths.last_redirect().splitlines()[0][:44])
    ck("fallback 确实存在", os.path.isdir(env.DRAFT_DIR))
    empty = paths.resolve_or("", env.DRAFT_DIR, "测试输出")
    ck("空路径走 fallback", empty == os.path.abspath(env.DRAFT_DIR), empty)

    print("== F. 临时目录改道 ==")
    d = paths.install_temp_redirect()
    ck("tempfile.tempdir 指向工具目录", tempfile.tempdir == d, str(tempfile.tempdir))
    ck("TMP/TEMP/TMPDIR 全部指向工具目录",
       all(os.environ.get(k) == d for k in ("TMP", "TEMP", "TMPDIR")),
       "%s / %s / %s" % (os.environ.get("TMP"), os.environ.get("TEMP"),
                         os.environ.get("TMPDIR")))
    ck("改道后的临时目录不在系统盘", not paths.on_system_drive(d), d)
    fd, tmpname = tempfile.mkstemp(prefix="ace3_paths_", suffix=".tmp")
    os.close(fd)
    try:
        ck("mkstemp 真的落在工具目录",
           os.path.abspath(tmpname).startswith(os.path.abspath(d) + os.sep), tmpname)
    finally:
        try:
            os.remove(tmpname)
        except OSError:
            pass
    ck("temp_path 只在工具目录内",
       os.path.dirname(paths.temp_path("..\\..\\x.json")) == paths.temp_dir(),
       paths.temp_path("..\\..\\x.json"))

    print("== G. 体检信息 ==")
    st = paths.status()
    for k in ("admin", "system_drive", "write_root", "write_root_on_system_drive",
              "work_dir", "temp_dir", "frozen", "ok"):
        ck("status() 含 %s" % k, k in st, repr(st.get(k)))
    ck("status.ok 与两项条件一致",
       st["ok"] == ((not st["write_root_on_system_drive"]) and st["admin"]),
       "ok=%s admin=%s onsys=%s" % (st["ok"], st["admin"],
                                    st["write_root_on_system_drive"]))
    txt = paths.status_text()
    # 注意：非管理员时文案是「普通权限（未提权）」，所以不能硬找「管理员」三字
    ck("status_text 说明权限状态",
       ("管理员" in txt) or ("普通权限" in txt), txt[:70])
    ck("status_text 写明不写哪块盘", ("不写 %s: 盘" % sysd) in txt
       or ("系统盘 %s:" % sysd) in txt, txt[:70])
    need = paths.require_tool_off_system_drive()
    ck("工具在非系统盘时不抱怨", need == "" if not st["write_root_on_system_drive"]
       else bool(need), repr(need[:50]))

    print("== H. 提权接口形状（不真提权） ==")
    ck("is_admin 返回 bool", isinstance(paths.is_admin(), bool), str(paths.is_admin()))
    ck("relaunch_as_admin 可调用", callable(paths.relaunch_as_admin))
    ck("run_elevated_wait 可调用", callable(paths.run_elevated_wait))
    exe, args = paths._self_cmdline(["prog.exe", "--selftest", "a b.json"])
    ck("_self_cmdline 拆出 exe 与参数", bool(exe) and "--selftest" in args,
       "%s | %s" % (os.path.basename(exe), args))
    ck("_quote 给含空格的参数加引号",
       paths._quote(["a b.json"]) == '"a b.json"', paths._quote(["a b.json"]))

    print()
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    for n, dd in FAIL:
        print("  FAIL:", n, dd)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
