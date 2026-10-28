"""验收第 15 项：用**打包后的 EXE** 复跑关键搜索/修改/写盘流程。

三项证据：

1. ``ACE3汉化工坊.exe --selftest <json>``：无头跑完整链路（找索引 → 搜索 →
   取槽 → 字库/容量 → 草稿 → 统一写盘 → 独立回读 → 白名单差分 → 父盘不变），
   全程只用 EXE 里打包进去的代码。
2. ``ACE3汉化工坊.exe --smoke``：真的起窗（验证无控制台窗口、Qt 平台插件、
   高 DPI、中文路径都能工作），2 秒后自动退出。
3. 独立验证：用**开发环境的冻结检索层**重新读 EXE 产出的新 ISO，证明回读不是
   EXE 自说自话。

结果写 ``work/acc/exe_验收.json``（供 ``_acceptance.py`` 第 15 项读取）。
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core import env, paths                             # noqa: E402

EXE_NAME = "ACE3汉化工坊.exe"
EXE = os.path.join(os.path.dirname(HERE), "01_程序", EXE_NAME)   # HERE=02_源码 -> ../01_程序
ACC = os.path.join(env.WORK_DIR, "acc")
# EXE 自己的可写根是 01_程序（见 core/paths.py 的 write_root()）。报告路径必须
# 落在这里面，否则会被守则改道到 01_程序\work\tmp\，父进程按原路径找就找不到。
EXE_ACC = os.path.join(os.path.dirname(EXE), "work", "acc")


def _read_report(path: str) -> tuple:
    """读报告；原路径没有就按守则找改道后的位置。

    返回 ``(报告 dict 或 None, 实际路径, 改道说明)``。
    """
    cands = [path,
             paths.temp_path(os.path.basename(path)),
             os.path.join(os.path.dirname(EXE), "exe_selftest.json")]
    for p in cands:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    rep = json.load(f)
            except (OSError, ValueError):
                continue
            note = "" if p == path else "（守则改道：%s）" % p
            return rep, p, note
    return None, "", ""


def _run(exe: str, args: list, timeout: float) -> dict:
    """以管理员身份跑 EXE 并等它结束（带硬超时）。

    不能用 ``subprocess``：发布版 EXE 带 ``requireAdministrator`` 清单，
    ``CreateProcess`` 会直接报 ``OSError [WinError 740] 请求的操作需要提升``。
    """
    t0 = time.time()
    ok, code, msg = paths.run_exe_as_admin_wait(exe, args, timeout=timeout)
    took = round(time.time() - t0, 1)
    return {"ok": ok, "returncode": code, "message": msg,
            "elapsed": took, "timed_out": code == -2}


def main() -> int:
    os.makedirs(ACC, exist_ok=True)
    rep = {"ok": False, "exe": EXE, "steps": [], "ran_at":
           time.strftime("%Y-%m-%d %H:%M:%S")}

    def step(name: str, ok: bool, detail: str, extra=None) -> None:  # noqa: ANN001
        rep["steps"].append({"name": name, "ok": bool(ok), "detail": detail,
                             **(extra or {})})
        print("  [%s] %s — %s" % ("PASS" if ok else "FAIL", name, detail))

    if not os.path.exists(EXE):
        rep["detail"] = "EXE 不存在：%s" % EXE
        _dump(rep)
        print(rep["detail"])
        return 1
    rep["exe_size"] = os.path.getsize(EXE)

    # ---- 1. 无头自测（含真写盘）
    # 报告要写在 EXE 自己的可写根内，否则会被守则改道
    os.makedirs(EXE_ACC, exist_ok=True)
    st_json = os.path.join(EXE_ACC, "exe_selftest.json")
    for p in (st_json, paths.temp_path("exe_selftest.json"),
              os.path.join(os.path.dirname(EXE), "exe_selftest.json")):
        if os.path.exists(p):
            os.remove(p)
    t0 = time.time()
    run1 = _run(EXE, ["--selftest", st_json], 1800)
    took = run1["elapsed"]
    got, got_path, got_note = _read_report(st_json)
    step("EXE 无头自测（搜索→修改→写盘→回读）",
         bool(got and got.get("ok")),
         ("退出码 %d / %.1fs / %d 步%s"
          % (run1["returncode"], took,
             len(got.get("steps", [])) if got else 0, got_note))
         if got else "没有产出报告（%s，退出码 %d）"
         % (run1["message"], run1["returncode"]),
         {"selftest": got, "report": got_path})
    rep["selftest_elapsed"] = took
    rep["selftest_report"] = got_path

    # ---- 2. 独立验证 EXE 产出的 ISO
    out = ((got or {}).get("output") or {})
    new_iso, new_md5 = out.get("path", ""), out.get("md5", "")
    if new_iso and os.path.exists(new_iso):
        step("EXE 产出新 ISO 存在", True,
             "%s（%.1f MB）" % (os.path.basename(new_iso),
                                os.path.getsize(new_iso) / 1048576.0))
        try:
            sys.path.insert(0, env.LOCATOR_DIR)
            import locator_core as L
            import query as Q
            c2c, _note = L.load_code_table()
            seg, err = Q.reread(new_iso, 4004643, 0, 0, 9)
            plain = L.decode_seg(seg, c2c)[0] if seg is not None else None
            step("独立回读（开发环境冻结检索层）", seg is not None,
                 "eid=4004643 槽 9 → %r%s"
                 % (plain, "" if seg is not None else "（%s）" % err))
            rep["independent_readback"] = {"plain": plain,
                                           "hex": seg.hex().upper() if seg else None}
        except BaseException as exc:              # noqa: BLE001
            step("独立回读（开发环境冻结检索层）", False,
                 "%s：%s" % (type(exc).__name__, exc))
    else:
        step("EXE 产出新 ISO 存在", False, "自测没有给出产物路径")

    # ---- 3. 起窗冒烟（无控制台 / 中文路径 / 高DPI）
    t0 = time.time()
    run2 = _run(EXE, ["--smoke"], 120)
    ok2 = run2["ok"] and run2["returncode"] == 0 and not run2["timed_out"]
    detail2 = "退出码 %d / %.1fs（窗口正常起停）" % (run2["returncode"],
                                                    time.time() - t0)
    if not ok2:
        detail2 += "；%s" % run2["message"]
        if run2["timed_out"]:
            detail2 += "；--smoke 超时（窗口可能卡住）"
    step("EXE 起窗冒烟（--smoke）", ok2, detail2)

    # ---- 4. 脱离工程目录 + 中文/空格路径实测
    offsite_info = _offsite_check(step)
    rep["offsite"] = offsite_info

    rep["ok"] = (bool(got and got.get("ok"))
                 and all(s["ok"] for s in rep["steps"])
                 and bool(offsite_info.get("ok")))
    rep["detail"] = ("EXE %s：无头自测 %d/%d 步通过，%s"
                     % ("通过" if rep["ok"] else "未通过",
                        sum(1 for s in (got or {}).get("steps", []) if s["ok"]),
                        len((got or {}).get("steps", [])),
                        "；".join(s["name"] for s in rep["steps"] if not s["ok"])
                        or "全部通过"))
    _dump(rep)
    print("\n%s" % rep["detail"])
    return 0 if rep["ok"] else 1


def _offsite_check(step) -> dict:                        # noqa: ANN001
    """把发布目录整体拷到「工程外 + 中文 + 空格」路径，再跑一次全链路。

    副本必须放在**非系统盘**：一是本工具守则不写系统盘，二是本机实测
    「程序在 E:、目标在 C:」写入会被拒（根因 `UNKNOWN`，见 `core/paths.py`）。
    """
    import shutil
    base = os.path.join(_offsite_drive(), "ACE3 汉化工坊 脱机测试")
    copy = os.path.join(base, "ACE3 汉化工坊 副本")
    info = {"target": copy, "ok": False}
    src_dir = os.path.dirname(EXE)
    try:
        if os.path.exists(base):
            shutil.rmtree(base, ignore_errors=True)
        os.makedirs(base, exist_ok=True)
        # 必须**复制时就跳过** work：那里有历次自测产出的 4.24 GB ISO，
        # 先 copytree 再 rmtree 会白搬几十 GB（实测卡了十几分钟）。
        shutil.copytree(src_dir, copy,
                        ignore=shutil.ignore_patterns("work", "__pycache__"))
        exe2 = os.path.join(copy, EXE_NAME)
        report = os.path.join(copy, "work", "acc", "selftest_offsite.json")
        t0 = time.time()
        run = _run(exe2, ["--selftest", report], 1800)
        got, got_path, got_note = _read_report(report)
        info.update({"exe": exe2, "elapsed": round(time.time() - t0, 1),
                     "returncode": run["returncode"],
                     "selftest_ok": bool(got and got.get("ok")),
                     "report": got_path,
                     "output": (got or {}).get("output")})
        step("脱离工程 + 中文/空格路径全链路",
             bool(got and got.get("ok")),
             "%s（%.1fs，%d 步，退出码 %d）%s"
             % (copy, info["elapsed"],
                len((got or {}).get("steps", [])), run["returncode"], got_note),
             {"offsite": info})
        # 独立回读该副本产出的 ISO
        out = ((got or {}).get("output") or {}).get("path", "")
        if out and os.path.exists(out):
            sys.path.insert(0, env.LOCATOR_DIR)
            import locator_core as L
            import query as Q
            c2c, _n = L.load_code_table()
            seg, err = Q.reread(out, 4004643, 0, 0, 9)
            plain = L.decode_seg(seg, c2c)[0] if seg is not None else None
            step("副本产物独立回读", seg is not None,
                 "eid=4004643 槽 9 → %r%s" % (plain, "" if seg else err))
            info["readback"] = plain
        crash = os.path.join(copy, "work", "logs", "crash.log")
        has_crash = os.path.exists(crash)
        step("副本运行无崩溃日志", not has_crash,
             "crash.log 存在" if has_crash else "无 crash.log")
        info["ok"] = (bool(got and got.get("ok")) and not has_crash
                      and info.get("readback") is not None)
    except BaseException as exc:                        # noqa: BLE001
        import traceback as _tb
        info["error"] = _tb.format_exc()[-800:]
        step("脱离工程 + 中文/空格路径全链路", False,
             "%s：%s" % (type(exc).__name__, exc))
    return info


def _offsite_drive() -> str:
    """挑一个**非系统盘**的可写盘符根目录（形如 ``<盘符>:\\``）做脱离工程测试。

    优先工程所在盘，其次其他固定盘；都不行就退回工作目录
    （虽然不算「脱离工程」，但至少不违反不写系统盘的守则）。

    注意：`paths.drive_of()` 返回的是**不带冒号**的盘符（`"E"`），
    拼成 `"E" + os.sep` 会得到 Windows 的**驱动相对路径** `"E\\"`，
    `os.path.join("E\\", "x")` 会被解析成「当前盘的 E 子目录」
    （实测把副本建到了 `02_源码\\E\\…`）。根目录必须写成 `"E:" + os.sep`。
    """
    def _root(letter: str) -> str:
        return letter + ":" + os.sep

    cands = []
    root_drive = paths.drive_of(env.ROOT)
    if root_drive and not paths.on_system_drive(env.ROOT):
        cands.append(_root(root_drive))
    for letter in "DEFGHIJK":
        p = _root(letter)
        if letter == paths.system_drive():
            continue
        if os.path.isdir(p) and p not in cands:
            cands.append(p)
    for p in cands:
        probe = os.path.join(p, "_ace3_offsite_probe")
        try:
            os.makedirs(probe, exist_ok=True)
            with open(os.path.join(probe, "x"), "w") as f:
                f.write("ok")
            os.remove(os.path.join(probe, "x"))
            os.rmdir(probe)
            return p
        except OSError:
            continue
    return env.WORK_DIR


def _dump(rep: dict) -> None:
    path = os.path.join(ACC, "exe_验收.json")
    os.makedirs(ACC, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print("报告：%s" % path)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:                         # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(2)
