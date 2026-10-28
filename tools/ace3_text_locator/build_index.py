#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""索引构建器：从一张 ISO 生成 text_index.sqlite（只读 ISO，可删可重建）。

索引结构
    meta(key, value)              指纹、来源、耗时、口径说明
    slots                         槽级主表（含原始字节 hex，支持回读验证）
    n2(term, kind, ids)           单字/双字倒排（varint delta 压缩），解决 FTS5 trigram
                                  无法处理 1~2 字查询的硬限制
    codes(code, ids)              码位倒排（code 0x89A8 检索）
    slots_fts                     3 字及以上用 FTS5 trigram 做子串匹配
"""

import os
import sqlite3
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import locator_core as L                                        # noqa: E402

DB_NAME = "text_index.sqlite"
BATCH = 4000


# ------------------------------------------------------------------ varint
def varint_encode(nums):
    """delta + LEB128。nums 必须升序去重。"""
    out = bytearray()
    prev = 0
    for n in nums:
        d = n - prev
        prev = n
        while True:
            b = d & 0x7F
            d >>= 7
            if d:
                out.append(b | 0x80)
            else:
                out.append(b)
                break
    return bytes(out)


def varint_decode(buf):
    out = []
    prev = 0
    i = 0
    v = 0
    shift = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        v |= (b & 0x7F) << shift
        if b & 0x80:
            shift += 7
            continue
        prev += v
        out.append(prev)
        v = 0
        shift = 0
    return out


# ------------------------------------------------------------------ schema
DDL = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE slots(
    sid     INTEGER PRIMARY KEY,
    eid     INTEGER NOT NULL,
    tag     INTEGER NOT NULL,
    k       INTEGER NOT NULL,
    slot    INTEGER NOT NULL,
    nslots  INTEGER NOT NULL,
    abs_off INTEGER NOT NULL,
    base    INTEGER NOT NULL,
    comp    INTEGER NOT NULL,
    seg     BLOB    NOT NULL,
    codes   TEXT    NOT NULL,
    plain   TEXT    NOT NULL,
    n_unk   INTEGER NOT NULL,
    n_ctrl  INTEGER NOT NULL,
    q       INTEGER NOT NULL,
    n_code  INTEGER NOT NULL,
    file_off INTEGER NOT NULL,
    file_end INTEGER NOT NULL
);
CREATE TABLE n2(term TEXT, kind INTEGER, ids BLOB, n INTEGER, PRIMARY KEY(term, kind));
CREATE TABLE codes(code INTEGER PRIMARY KEY, ids BLOB, n INTEGER);
CREATE VIRTUAL TABLE slots_fts USING fts5(plain, content='', tokenize='trigram');
"""


def build(iso_path, out_dir=None, use_md5=True, quiet=False, limit_eids=None,
          keep_font_pkgs=False):
    if out_dir is None:
        stem = os.path.splitext(os.path.basename(iso_path))[0]
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", stem)
    os.makedirs(out_dir, exist_ok=True)
    db_path = os.path.join(out_dir, DB_NAME)
    tmp_path = db_path + ".building"
    for p in (tmp_path, tmp_path + "-journal", tmp_path + "-wal", tmp_path + "-shm"):
        if os.path.exists(p):
            os.remove(p)

    t0 = time.time()
    c2c, note = L.load_code_table()
    if not quiet:
        print("[1/5] 权威码表 %d 个码位  (%s)" % (len(c2c), note))

    fp = L.iso_fingerprint(iso_path, use_md5=use_md5)
    if not quiet:
        print("[2/5] ISO 指纹 size=%d md5=%s  %s"
              % (fp["size"], fp["md5"] or "(跳过)", fp["path"]))

    conn = sqlite3.connect(tmp_path)
    conn.executescript(DDL)

    # ---------- 主扫描 ----------
    n2_char = {}
    n2_bi = {}
    code_map = {}
    n_slot = n_text = n_err = n_unk = 0
    qdist = {0: 0, 1: 0, 2: 0}
    t_scan = time.time()
    rows = []
    fts_rows = []
    sid = 0

    def flush():
        nonlocal rows, fts_rows
        if rows:
            conn.executemany(
                "INSERT INTO slots(sid,eid,tag,k,slot,nslots,abs_off,base,comp,"
                "seg,codes,plain,n_unk,n_ctrl,q,n_code,file_off,file_end) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows)
            conn.executemany("INSERT INTO slots_fts(rowid, plain) VALUES(?,?)", fts_rows)
            rows, fts_rows = [], []

    for row in L.scan_iso(iso_path, c2c, limit_eids=limit_eids,
                          skip_eids=None if keep_font_pkgs else L.FONT_PKGS,
                          progress=None if quiet else _prog()):
        if "error" in row:
            n_err += 1
            continue
        sid += 1
        n_slot += 1
        plain = row["plain"]
        if row["unknown"]:
            n_unk += 1
        q = row["q"]
        qdist[q] = qdist.get(q, 0) + 1
        if plain.strip():
            n_text += 1
        rows.append((sid, row["eid"], row["tag"], row["k"], row["slot"],
                     row["nslots"], row["abs_off"], row["base"], row["comp"],
                     row["seg"], " ".join("%04X" % c for c in row["codes"]),
                     plain, row["unknown"], row["ctrl"], q, len(row["codes"]),
                     row["file_off"], row["file_end"]))
        # 只有 q>0（非二进制段）才进倒排，避免把数据表当文本命中
        if q:
            fts_rows.append((sid, plain))
            for ch in set(plain):
                n2_char.setdefault(ch, []).append(sid)
            seen = set()
            for i in range(len(plain) - 1):
                bg = plain[i:i + 2]
                if bg in seen:
                    continue
                seen.add(bg)
                n2_bi.setdefault(bg, []).append(sid)
        for c in set(row["codes"]):
            code_map.setdefault(c, []).append(sid)
        if len(rows) >= BATCH:
            flush()
    flush()
    t_scan = time.time() - t_scan

    # ---------- 倒排落库 ----------
    t_inv = time.time()
    conn.executemany("INSERT INTO n2(term,kind,ids,n) VALUES(?,?,?,?)",
                     [(k, 1, varint_encode(v), len(v)) for k, v in n2_char.items()])
    conn.executemany("INSERT INTO n2(term,kind,ids,n) VALUES(?,?,?,?)",
                     [(k, 2, varint_encode(v), len(v)) for k, v in n2_bi.items()])
    conn.executemany("INSERT INTO codes(code,ids,n) VALUES(?,?,?)",
                     [(c, varint_encode(v), len(v)) for c, v in code_map.items()])
    conn.commit()
    t_inv = time.time() - t_inv

    # ---------- meta ----------
    meta = {
        "schema_version": str(L.SCHEMA_VERSION),
        "iso_path": fp["path"],
        "iso_size": str(fp["size"]),
        "iso_mtime": str(fp["mtime"]),
        "iso_md5": fp["md5"],
        "code_table": note,
        "code_table_size": str(len(c2c)),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "slots": str(n_slot),
        "text_slots": str(n_text),
        "q1_clean": str(qdist[1]),
        "q2_suspect": str(qdist[2]),
        "q0_binary": str(qdist[0]),
        "error_eids": str(n_err),
        "slots_with_unknown_codes": str(n_unk),
        "distinct_chars": str(len(n2_char)),
        "distinct_bigrams": str(len(n2_bi)),
        "distinct_codes": str(len(code_map)),
        "elapsed_scan_s": "%.2f" % t_scan,
        "elapsed_invert_s": "%.2f" % t_inv,
        "elapsed_total_s": "%.2f" % (time.time() - t0),
        "decode_note": "盘面码位用工程权威码表 _r211_cmap 解码；CP932 仅作对照列出现",
        "offset_note": "file_off = abs_off + base + offs[slot]（abs_off 已含 ISOBASE），"
                       "file_end 为下一槽起点或块尾；即 ISO 文件真实偏移区间",
    }
    conn.executemany("INSERT INTO meta(k,v) VALUES(?,?)", list(meta.items()))
    conn.commit()

    t_idx = time.time()
    conn.execute("CREATE INDEX ix_slots_pos ON slots(eid, tag, k, slot)")
    conn.execute("CREATE INDEX ix_slots_code ON slots(n_code)")
    conn.execute("CREATE INDEX ix_slots_file ON slots(file_off)")
    conn.commit()
    conn.execute("INSERT INTO slots_fts(slots_fts) VALUES('optimize')")
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    t_idx = time.time() - t_idx

    if os.path.exists(db_path):
        os.remove(db_path)
    os.rename(tmp_path, db_path)
    meta["elapsed_index_s"] = "%.2f" % t_idx
    meta["db_bytes"] = str(os.path.getsize(db_path))
    meta["elapsed_wall_s"] = "%.2f" % (time.time() - t0)
    if not quiet:
        print("[5/5] 完成 %s  %.2fs  槽=%d 文本槽=%d 单字=%d 双字=%d 码位=%d"
              % (db_path, time.time() - t0, n_slot, n_text, len(n2_char),
                 len(n2_bi), len(code_map)))
    return meta


def _prog():
    st = {"t": time.time(), "n": 0}

    def cb(idx, total, eid):
        if idx and idx % 1000 == 0:
            el = time.time() - st["t"]
            print("    扫描 %d/%d  eid=%d  %.1fs" % (idx, total, eid, el),
                  flush=True)
    return cb


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("用法: python -X utf8 build_index.py <ISO_PATH>")
    iso = sys.argv[1]
    m = build(iso)
    for k, v in m.items():
        print("%-28s %s" % (k, v))
