"""ACE3 汉化工坊 · 命令行入口（不起 GUI，供独立验证与自动化验收）。

用法
----
    ACE3汉化工坊.cmd find "关键词" --iso <盘>
    ACE3汉化工坊.cmd check --iso <盘> --plan <计划.json>
    ACE3汉化工坊.cmd build --iso <盘> --plan <计划.json> [--out 目录] [--md5 <父盘MD5>]
    ACE3汉化工坊.cmd index --iso <盘>

设计原则：与 GUI 走**同一套** core（pipeline / writer / editplan），
不另写一份写盘逻辑；这样 CLI 的 PASS 才是 GUI 的 PASS。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_here = os.path.abspath(os.path.dirname(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from core import editplan, env, pipeline, search, slots, writer  # noqa: E402


def _echo(msg: str) -> None:
    print(msg, flush=True)


def _progress(msg: str) -> None:
    print("  · %s" % msg, flush=True)


# ---------------------------------------------------------------- index
def cmd_index(args) -> int:
    iso = os.path.abspath(args.iso)
    if not os.path.exists(iso):
        _echo("错误：ISO 不存在 %s" % iso)
        return 2
    _echo("为 %s 建立检索索引…" % os.path.basename(iso))
    path = search.build_index(iso, progress=_progress)
    idx = search.Index(path)
    fp = idx.fingerprint()
    _echo("完成：%s" % path)
    _echo("  iso_md5=%s slots=%s code_table=%s"
          % (fp["iso_md5"], fp["slots"], fp["code_table_size"]))
    idx.close()
    return 0


# ---------------------------------------------------------------- find
def cmd_find(args) -> int:
    idx = search.open_index(search.find_index(args.iso))
    _echo("索引：%s" % idx.db_path)
    _echo("盘：%s（md5 %s）" % (idx.iso_path, idx.iso_md5))
    res = search.run_query(idx, args.query, limit=args.limit)
    hits = res.get("hits") or []
    _echo("模式 %s（%s）→ 命中 %d 条："
          % (res.get("mode"), res.get("value"), len(hits)))
    if res.get("usage") is not None:
        _echo("  该码位在全盘出现 %d 次" % res["usage"])
    if res.get("note"):
        _echo("  说明：%s" % res["note"])
    for i, h in enumerate(hits, 1):
        _echo("%3d. %-34s %s" % (i, h["key"], h["text"][:60].replace("\n", "\\n")))
        _echo("     码位 %s | 字节 %d | 压缩 %s | 质量 %s"
              % (h["codes_hex"][:48] or "-", h["bytes"],
                 "是" if h["compressed"] else "否", h["quality"]))
        if h["jp_text"]:
            _echo("     日文 %s" % h["jp_text"][:60].replace("\n", "\\n"))
        if args.verbose:
            _echo("     偏移 %s | 权威译文 %s"
                  % (h["file_off"], (h["zh_authority"] or "")[:50]))
    return 0


# ---------------------------------------------------------------- plan
def _load_plan(path: str) -> editplan.PlanQueue:
    q = editplan.PlanQueue()
    q.load_draft(path, verify_parent=False)
    return q


def cmd_check(args) -> int:
    q = _load_plan(args.plan)
    _echo("计划：%s（%d 项，父盘 %s）"
          % (args.plan, len(q.items), q.parent.get("md5")))
    ok, why = q.parent_ok(args.iso)
    if not ok:
        _echo("阻断：%s" % why)
        return 2
    rep = pipeline.inspect(args.iso, q.items, progress=_progress)
    _echo("总 %d 项：可写入 %d / 警告 %d / 阻断 %d；涉及 %d 个资源；耗时 %.1fs"
          % (rep["total"], rep["safe"], rep["warn"], rep["blocked"],
             len(rep["resources"]), rep["elapsed"]))
    for d in rep.get("blocked_detail") or []:
        _echo("  ✕ %s → %s" % (d["where"], d["why"]))
    for d in rep.get("warn_detail") or []:
        _echo("  ! %s → %s" % (d["where"], d["why"]))
    _echo("可以生成新 ISO：%s" % ("是" if rep["can_build"] else "否"))
    return 0 if rep["can_build"] else 1


def cmd_build(args) -> int:
    q = _load_plan(args.plan)
    _echo("父盘：%s" % args.iso)
    _echo("计划：%d 项" % len(q.items))
    try:
        man = pipeline.build_to(args.iso, q.items, out_dir=args.out,
                                expected_md5=args.md5 or q.parent.get("md5"),
                                name_hint=args.hint or "", progress=_progress)
    except pipeline.PipelineError as exc:
        _echo("失败：%s" % exc)
        _echo("父盘未改动，修改队列与草稿保留。")
        return 1
    _echo("成功：%s" % man["output"]["path"])
    _echo("  新盘 MD5 %s" % man["output"]["md5"])
    _echo("  父盘 MD5 %s（未改动）" % man["parent"]["md5"])
    _echo("  独立回读 %s | 整盘差分 %s | 受保护资源 %s"
          % ("PASS" if man["readback"]["ok"] else "FAIL",
             "PASS" if man["diff"]["ok"] else "FAIL",
             "PASS" if man["protected"]["ok"] else "FAIL"))
    _echo("  证据目录 %s" % man["stage_dir"])
    return 0


# ---------------------------------------------------------------- 诊断
def cmd_info(args) -> int:
    idx = search.open_index(search.find_index(args.iso))
    fp = idx.fingerprint()
    _echo("索引 %s" % idx.db_path)
    for k, v in fp.items():
        _echo("  %s = %s" % (k, v))
    _echo("索引清单：")
    for d in search.list_indexes():
        _echo("  %s | md5 %s | slots %s | %s"
              % (d["stem"], d["md5"], d["slots"], d["built_at"]))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ACE3汉化工坊",
                                 description="ACE3 汉化工坊命令行（与 GUI 同一核心）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("index", help="为 ISO 建立检索索引")
    p.add_argument("--iso", required=True)
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("find", help="检索文本")
    p.add_argument("query")
    p.add_argument("--iso")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("check", help="写盘前检查台")
    p.add_argument("--iso", required=True)
    p.add_argument("--plan", required=True)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("build", help="事务式生成新 ISO")
    p.add_argument("--iso", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--out")
    p.add_argument("--md5")
    p.add_argument("--hint", default="")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("info", help="索引与指纹信息")
    p.add_argument("--iso")
    p.set_defaults(func=cmd_info)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    env.ensure_dirs()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
