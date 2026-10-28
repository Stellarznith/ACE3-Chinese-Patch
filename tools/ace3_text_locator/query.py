#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询层：读 text_index.sqlite，返回槽级命中与前后文（只读）。

搜索策略（关键设计）
    1 字   -> n2(kind=1) 单字倒排
    2 字   -> n2(kind=2) 双字倒排
    3+ 字  -> 取查询内**所有相邻双字**的倒排求交集（第一个双字为基准），
              再逐条做真子串验证
    ※ 不依赖 FTS5 trigram：FTS5 的 trigram 对 1~2 字查询直接返回空，
      方案第三节已明确禁止"只建一个 trigram FTS 表就声称支持单字搜索"。
      FTS5 表仅作长句候选的辅助加速（--fts），不进主路径。

排序（方案第三节）
    1 当前盘面完整文本精确匹配
    2 当前盘面部分文本匹配（越靠前/越短越优先）
    3 同槽前后文匹配
    4+ 由第二阶段接译文链后补
"""

import json
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import locator_core as L                                        # noqa: E402
from build_index import varint_decode, DB_NAME                  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# 不带 --iso 时的默认盘（当前最新汉化盘）。只影响"挑哪个索引目录"，
# 不改变任何数据；找不到它的索引就退回"唯一索引"或报错列候选。
DEFAULT_STEM = "r1356_lenton_what"

# 一张槽的行字段全集：凡是喂给 fmt_row 的 SELECT 必须用这一份，
# 少一个字段就会在 fmt_row 里 KeyError（踩过一次）。
SLOT_COLS = ("sid,eid,tag,k,slot,nslots,abs_off,base,comp,seg,codes,plain,"
             "n_unk,n_ctrl,q,n_code,file_off,file_end")


# ------------------------------------------------------------------ 打开
def open_db(db_path=None, iso_path=None, out_dir=None):
    if db_path is None:
        out_dir = out_dir or _pick_dir(iso_path)
        db_path = os.path.join(out_dir, DB_NAME)
    if not os.path.exists(db_path):
        raise SystemExit("索引不存在：%s\n先执行：python ace3_text_find.py index --iso <ISO>" % db_path)
    conn = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
    conn.row_factory = sqlite3.Row
    # 索引结构版本必须与当前代码一致，否则拒绝静默复用旧盘结果
    try:
        ver = conn.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise SystemExit("索引不可读：%s\n%s" % (db_path, exc))
    got = int(ver["v"]) if ver else 0
    if got != L.SCHEMA_VERSION:
        conn.close()
        raise SystemExit(
            "索引结构版本不符（库=%d，代码=%d），请重建：\n"
            "  python ace3_text_find.py index --iso <ISO>\n  索引：%s"
            % (got, L.SCHEMA_VERSION, db_path))
    return conn, db_path


def _pick_dir(iso_path):
    """选索引目录。

    未指定 --iso 时的顺序：
      1) 默认盘（r1356_lenton_what，当前最新）的索引存在 → 用它，输出里注明
      2) db/ 下恰好只有一个索引 → 用它
      3) 否则报错并列出候选，绝不静默挑一个查错盘
    """
    if iso_path:
        stem = os.path.splitext(os.path.basename(iso_path))[0]
        d = os.path.join(HERE, "db", stem)
        if os.path.exists(os.path.join(d, DB_NAME)):
            return d
        return HERE
    d = os.path.join(HERE, "db", DEFAULT_STEM)
    if os.path.exists(os.path.join(d, DB_NAME)):
        return d
    base = os.path.join(HERE, "db")
    cands = sorted(
        os.path.join(base, n) for n in os.listdir(base)
        if os.path.exists(os.path.join(base, n, DB_NAME))) if os.path.isdir(base) else []
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        raise SystemExit(
            "找到 %d 个索引，请用 --iso <ISO> 或 --db <sqlite> 指定：\n%s"
            % (len(cands), "\n".join("  " + c for c in cands)))
    return HERE


def meta(conn):
    return {r["k"]: r["v"] for r in conn.execute("SELECT k,v FROM meta")}


# ------------------------------------------------------------------ 检索核心
def term_ids(conn, term):
    kind = 1 if len(term) == 1 else 2
    r = conn.execute("SELECT ids FROM n2 WHERE term=? AND kind=?",
                     (term, kind)).fetchone()
    return set(varint_decode(r["ids"])) if r else set()


def search_ids(conn, needle, limit=20000):
    """返回候选 sid 集合（已做真子串验证）。"""
    if not needle:
        return set()
    if len(needle) == 1:
        cand = term_ids(conn, needle)
    else:
        grams = [needle[i:i + 2] for i in range(len(needle) - 1)]
        sets = [term_ids(conn, g) for g in grams]
        sets.sort(key=len)
        if not sets or not sets[0]:
            return set()
        cand = sets[0]
        for s in sets[1:]:
            cand &= s
            if not cand:
                return set()
    if not cand:
        return set()
    out = set()
    ids = sorted(cand)
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = "SELECT sid, plain FROM slots WHERE sid IN (%s)" % ",".join("?" * len(chunk))
        for r in conn.execute(q, chunk):
            if needle in r["plain"]:
                out.add(r["sid"])
        if len(out) >= limit:
            break
    return out


def rank_hits(conn, needle, sids, limit=50):
    """按方案第三节的优先级排序。"""
    if not sids:
        return []
    rows = []
    ids = sorted(sids)
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = ("SELECT %s FROM slots WHERE sid IN (%s)"
             % (SLOT_COLS, ",".join("?" * len(chunk))))
        rows.extend(conn.execute(q, chunk).fetchall())

    n = len(needle)

    def key(r):
        p = r["plain"]
        if p == needle:
            tier = 0                      # 完整精确
        elif p.strip() == needle:
            tier = 1                      # 去首尾空白后精确
        elif p.startswith(needle):
            tier = 2
        elif p.endswith(needle):
            tier = 3
        else:
            tier = 4
        # 同级里：覆盖面越大（命中长度/文本长度）越优先，文本越短越优先
        cover = -float(n) / max(1, len(p))
        return (tier, r["q"] == 0, round(cover, 4), len(p), r["eid"], r["slot"])

    rows.sort(key=key)
    return rows[:limit]


def context(conn, eid, tag, k, slot, span=2):
    """同 (eid,tag,k) 内 slot±span 的槽。"""
    rows = conn.execute(
        "SELECT slot,plain,codes,n_unk,q FROM slots "
        "WHERE eid=? AND tag=? AND k=? AND slot BETWEEN ? AND ? ORDER BY slot",
        (eid, tag, k, slot - span, slot + span)).fetchall()
    return rows


def code_count(conn, code):
    """该码位在盘面上的总引用槽数（不受 limit 影响）。"""
    r = conn.execute("SELECT ids FROM codes WHERE code=?", (code,)).fetchone()
    return len(varint_decode(r["ids"])) if r else 0


def code_hits(conn, code, limit=200):
    r = conn.execute("SELECT ids FROM codes WHERE code=?", (code,)).fetchone()
    if not r:
        return []
    ids = varint_decode(r["ids"])
    out = []
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = ("SELECT %s FROM slots WHERE sid IN (%s)"
             % (SLOT_COLS, ",".join("?" * len(chunk))))
        out.extend(conn.execute(q, chunk).fetchall())
    out.sort(key=lambda r: (r["eid"], r["slot"]))
    return out[:limit]


def locate_slot(conn, eid, tag=None, k=None, slot=None):
    q = "SELECT * FROM slots WHERE eid=?"
    args = [eid]
    if tag is not None:
        q += " AND tag=?"
        args.append(tag)
    if k is not None:
        q += " AND k=?"
        args.append(k)
    if slot is not None:
        q += " AND slot=?"
        args.append(slot)
    q += " ORDER BY tag,k,slot"
    return conn.execute(q, args).fetchall()


def by_offset(conn, off, limit=20):
    """绝对文件偏移 → 覆盖该偏移的槽（方案第三节「资源定位搜索」）。

    返回 {"off": off, "hits": [slot 行], "note": 说明,
          "prev": [更近的前几条], "next": [更近的后几条]}。
    """
    rows = conn.execute(
        "SELECT * FROM slots WHERE file_off<=? AND file_end>? "
        "ORDER BY file_off LIMIT ?", (off, off, limit)).fetchall()
    if rows:
        return {"off": off, "hits": rows, "note": "命中：偏移落在该槽的字节区间内",
                "prev": [], "next": []}
    prev = conn.execute(
        "SELECT * FROM slots WHERE file_off<=? ORDER BY file_off DESC LIMIT 3",
        (off,)).fetchall()
    nxt = conn.execute(
        "SELECT * FROM slots WHERE file_off>? ORDER BY file_off ASC LIMIT 3",
        (off,)).fetchall()
    return {"off": off, "hits": [], "prev": prev, "next": nxt,
            "note": "未命中：该偏移不是被索引的槽起点/内部（可能是资源头、"
                    "压缩块体或未扫描的 eid），下方给出最近的槽"}


def container_at(conn, eid):
    """该 eid 在索引中的所有块（tag,k）与槽数，用于看资源边界。"""
    return conn.execute(
        "SELECT tag,k,MAX(nslots) nslots,COUNT(*) nrows,MIN(file_off) f0,"
        "MAX(file_end) f1 FROM slots WHERE eid=? GROUP BY tag,k ORDER BY tag,k",
        (eid,)).fetchall()


# ------------------------------------------------------------------ 回读验证
def reread(iso_path, eid, tag, k, slot):
    """从 ISO 重新读该槽的原始字节，用于证明索引与当前盘一致。"""
    from _r223_apply_plan import locate
    toc = L.databin_toc(iso_path)
    res = locate(iso_path, toc, eid)
    key = (tag, k)
    if key not in res:
        return None, "该 eid 无此块 (tag=%d,k=%d)" % (tag, k)
    abs_off, data, base, offs, comp = res[key]
    if slot >= len(offs):
        return None, "槽号超界（该块 nslots=%d）" % len(offs)
    return L.seg_of(data, base, offs, slot), None


# ------------------------------------------------------------------ 输出
def fmt_row(conn, r, c2c, span=0, with_ctx=False, iso=None):
    """把一行 slots 记录变成人类可读块 + 结构化 dict。"""
    seg = bytes(r["seg"])
    plain, codes, st = L.decode_seg(seg, c2c)
    d = {
        "eid": r["eid"], "tag": r["tag"], "k": r["k"], "slot": r["slot"],
        "nslots": r["nslots"],
        "abs_off": "0x%X" % r["abs_off"],
        "rel_off": r["base"],
        "file_off": "0x%X" % r["file_off"],
        "file_end": "0x%X" % r["file_end"],
        "compressed": bool(r["comp"]),
        "bytes_hex": seg.hex().upper(),
        "codes_hex": r["codes"],
        "text": plain,
        "quality": r["q"],
        "unknown_codes": r["n_unk"],
        "control_bytes": r["n_ctrl"],
        "cp932_reference": L.cp932_view(seg),
    }
    if with_ctx:
        ctx = context(conn, r["eid"], r["tag"], r["k"], r["slot"], span)
        prev = [c["plain"] for c in ctx if c["slot"] < r["slot"]]
        nxt = [c["plain"] for c in ctx if c["slot"] > r["slot"]]
        d["prev"] = prev
        d["next"] = nxt
    if iso:
        seg2, err = reread(iso, r["eid"], r["tag"], r["k"], r["slot"])
        d["reread_ok"] = (seg2 == seg) if err is None else None
        d["reread_error"] = err
    return d
