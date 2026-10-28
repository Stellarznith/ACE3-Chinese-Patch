"""索引缓存管理 + 目录记忆 的回归自测（只读 + 可控删除，不动任何 ISO）。

覆盖：
  A. find_info() 返回 dict（而是 find_index() 返回 str）——v1.0.1「索引不可用」的根因回归
  B. info_of() 字段齐备；list_indexes() 不含 _shared
  C. delete_index() 拒绝 _shared / 路径穿越 / 不存在的名字
  D. delete_index() 真删一个自己造的空壳索引目录，并正确统计 freed
  E. core.settings 往返 / 坏档留证 / recall_dir 失效回落
  F. iso_exists 判定 + adopt_index() 改绑（同内容换路径可认回、内容不符必须拒绝）
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core import env, search as S, settings  # noqa: E402

env.ensure_dirs()          # 含 TMP_DIR：下面 mkdtemp(dir=env.TMP_DIR) 要用

PASS, FAIL = [], []


def ck(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append((name, detail))
    print(("  OK   " if cond else "  FAIL ") + name + (("　←　" + detail) if detail else ""))


def main() -> int:
    # 上一轮若在 D 段中途崩掉，会留下自造目录；先清掉，免得污染 B 段的清单断言
    shutil.rmtree(os.path.join(env.LOCATOR_DB, "__selftest_fake_index__"),
                  ignore_errors=True)
    print("== A. find_index 返回 str / find_info 返回 dict ==")
    infos = S.list_indexes()
    ck("至少有一份索引", bool(infos), "%d 份" % len(infos))
    if not infos:
        return 1
    mine = None
    for it in infos:
        if it["stem"] == S.Q.DEFAULT_STEM:
            mine = it
    mine = mine or infos[0]
    iso = mine["iso"]
    a = S.find_index(iso)
    b = S.find_info(iso)
    ck("find_index() 是 str", isinstance(a, str), type(a).__name__)
    ck("find_info() 是 dict", isinstance(b, dict), type(b).__name__)
    ck("find_info() 的 db 与 find_index() 一致",
       isinstance(b, dict) and os.path.normcase(b["db"]) == os.path.normcase(a))
    ck("find_info() 带 stem/dir/readable", isinstance(b, dict) and
       all(k in b for k in ("stem", "dir", "readable")),
       ",".join(sorted(b.keys())) if isinstance(b, dict) else "-")
    # 回归：把 str 当 dict 用会怎样（复现旧 bug 的异常类型，确认根因描述准确）
    try:
        a["db"]  # type: ignore[index]
        ck("对 str 用 ['db'] 应抛 TypeError", False, "居然没抛")
    except TypeError as exc:
        ck("对 str 用 ['db'] 抛 TypeError", "indices must be integers" in str(exc), str(exc))

    print("== B. info_of / list_indexes 字段 ==")
    ck("list_indexes() 不含 _shared",
       all(it["stem"] != S.SHARED_STEM for it in infos),
       ",".join(it["stem"] for it in infos))
    ck("每项都有 size>0", all(int(it.get("size") or 0) > 0 for it in infos))
    ck("每项都有 built_at", all(it.get("built_at") for it in infos))
    ck("info_of 不存在的名字返回 None", S.info_of("__no_such_index__") is None)
    ck("index_is_shared('_shared')", S.index_is_shared("_shared"))
    ck("index_is_shared('r1356_lenton_what') 为 False",
       not S.index_is_shared("r1356_lenton_what"))

    print("== C. delete_index 拒绝清单 ==")
    r = S.delete_index(S.SHARED_STEM)
    ck("拒绝 _shared", (not r["ok"]) and ("共享缓存" in r["msg"]), r["msg"])
    r = S.delete_index("")
    ck("拒绝空名", not r["ok"], r["msg"])
    r = S.delete_index("..\\evil")
    ck("拒绝路径穿越", not r["ok"], r["msg"])
    r = S.delete_index("a/b")
    ck("拒绝带斜杠", not r["ok"], r["msg"])
    r = S.delete_index("__no_such_index__")
    ck("不存在则拒绝", (not r["ok"]) and ("没有这个索引" in r["msg"]), r["msg"])
    ck("被拒时 _shared 目录仍在", os.path.isdir(os.path.join(env.LOCATOR_DB, S.SHARED_STEM)))

    print("== D. delete_index 真删自己造的空壳索引 ==")
    fake = "__selftest_fake_index__"
    fdir = os.path.join(env.LOCATOR_DB, fake)
    shutil.rmtree(fdir, ignore_errors=True)
    os.makedirs(fdir, exist_ok=True)
    payload = b"x" * 4096
    with open(os.path.join(fdir, S.DB_NAME), "wb") as f:
        f.write(payload)
    # 造一个能读 meta 的最小库，让 info_of 认得它
    dbp = os.path.join(fdir, S.DB_NAME)
    os.remove(dbp)
    conn = sqlite3.connect(dbp)
    conn.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO meta VALUES(?,?)", [
        ("iso_path", "X:\\fake.iso"), ("iso_md5", "0" * 32),
        ("built_at", "2026-01-01 00:00:00"), ("slots", "1")])
    conn.commit()
    conn.close()
    info = S.info_of(fake)
    ck("自造索引可被 info_of 识别", info is not None and info["slots"] == 1,
       json.dumps(info, ensure_ascii=False) if info else "-")
    r = S.delete_index(fake)
    ck("自造索引删除成功", r["ok"], r["msg"])
    ck("删除后目录消失", not os.path.isdir(fdir))
    ck("freed 大于 0", int(r["freed"]) > 0, str(r["freed"]))

    print("== E. settings 往返 / 坏档留证 / 失效回落 ==")
    p = settings.path()
    bak = None
    if os.path.isfile(p):
        bak = p + ".selftest_bak"
        shutil.copy2(p, bak)
    try:
        ck("put 成功", settings.put("__selftest_key__", "中文 路径 ok"))
        ck("get 读回", settings.get("__selftest_key__") == "中文 路径 ok")
        ck("落盘是合法 JSON", isinstance(settings.load(force=True), dict))
        # 坏档：写坏内容后 load 必须改名留证并从空 dict 开始
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        d = settings.load(force=True)
        ck("坏档不抛异常，返回 dict", isinstance(d, dict))
        corrupt = [n for n in os.listdir(os.path.dirname(p)) if ".corrupt_" in n]
        ck("坏档改名为 .corrupt_* 留证", bool(corrupt), ",".join(corrupt))
        for n in corrupt:
            try:
                os.remove(os.path.join(os.path.dirname(p), n))
            except OSError:
                pass
        # 失效目录回落（临时目录也必须在工具目录内：守则不写系统盘）
        tmpd = tempfile.mkdtemp(prefix="ace3_selftest_", dir=env.TMP_DIR)
        settings.put("iso_dir", tmpd)
        ck("remember_dir/recall_dir 正常往返", settings.recall_dir("iso_dir", "FB") == tmpd)
        shutil.rmtree(tmpd, ignore_errors=True)
        ck("目录被删后 recall_dir 回落", settings.recall_dir("iso_dir", "FB") == "FB",
           settings.recall_dir("iso_dir", "FB"))
        ck("remember_dir 拒绝不存在的目录",
           (settings.remember_dir("iso_dir", "Z:\\no\\such\\dir") is False))
    finally:
        try:
            if os.path.isfile(p):
                os.remove(p)
            if bak and os.path.isfile(bak):
                shutil.move(bak, p)
        except OSError:
            pass

    print("== F. iso_exists 与 adopt_index（索引指向的老盘没了 / 换位置） ==")
    # 索引目录按 stem 命名，盘搬家或重名换盘都会命中原索引。GUI 曾因此在版本
    # 下拉里切到一条指向旧工程目录的索引，中栏满屏「资源读取失败」。
    fake_iso = os.path.join(env.TMP_DIR, "__selftest_adopt__.iso")
    with open(fake_iso, "wb") as f:
        f.write(b"\0" * (S.MIN_ISO_SIZE + 4096))
    real_md5 = env.md5_file(fake_iso)
    ck("自造盘够大（过得了 iso_usable）", S.iso_usable(fake_iso),
       env.human_size(os.path.getsize(fake_iso)))
    ck("iso_usable 拒绝不存在的路径", not S.iso_usable("X:\\no\\such.iso"))
    ck("iso_usable 拒绝空串", not S.iso_usable(""))
    ck("iso_usable 拒绝太小的文件", not S.iso_usable(env.ROOT))

    stem = "__selftest_adopt__"
    fdir = os.path.join(env.LOCATOR_DB, stem)
    shutil.rmtree(fdir, ignore_errors=True)
    os.makedirs(fdir, exist_ok=True)

    def _mkindex(path: str, iso_path: str, iso_md5: str) -> None:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO meta VALUES(?,?)", [
            ("iso_path", iso_path), ("iso_md5", iso_md5),
            ("built_at", "2026-01-01 00:00:00"), ("slots", "2")])
        conn.commit()
        conn.close()

    _mkindex(os.path.join(fdir, S.DB_NAME),
             os.path.join("PATH", "TO", "ISO", stem + ".iso"),
             real_md5)
    info = S.info_of(stem)
    ck("盘不在原处时 iso_exists=False", info is not None and not info["iso_exists"],
       (info or {}).get("iso", "-"))

    # 内容一致（大小+MD5 都对）→ 允许改绑，只改 meta 的 iso_path/iso_mtime
    got = S.adopt_index(fake_iso)
    ck("内容一致时 adopt_index 改绑成功", got is not None and got["iso_exists"],
       (got or {}).get("iso", "-"))
    ck("改绑后 iso 指向新位置",
       got is not None and os.path.abspath(got["iso"]) == os.path.abspath(fake_iso))
    ck("改绑没有丢槽数", got is not None and int(got["slots"]) == 2, str((got or {}).get("slots")))
    ck("改绑是真正落库的（重读仍指向新位置）",
       (S.info_of(stem) or {}).get("iso") == os.path.abspath(fake_iso))

    # 内容不一致 → 绝不能把旧索引糊上去
    shutil.rmtree(fdir, ignore_errors=True)
    os.makedirs(fdir, exist_ok=True)
    _mkindex(os.path.join(fdir, S.DB_NAME), "X:\\gone\\" + stem + ".iso", "0" * 32)
    got = S.adopt_index(fake_iso)
    ck("MD5 不符时 adopt_index 拒绝改绑", got is None)
    ck("被拒后 iso_exists 仍是 False",
       (S.info_of(stem) or {}).get("iso_exists") is False)
    ck("盘不存在时 adopt_index 返回 None", S.adopt_index("X:\\no\\such.iso") is None)
    ck("adopt_index 对空串返回 None", S.adopt_index("") is None)

    shutil.rmtree(fdir, ignore_errors=True)
    try:
        os.remove(fake_iso)
    except OSError:
        pass

    print()
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    for n, d in FAIL:
        print("  FAIL:", n, d)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
