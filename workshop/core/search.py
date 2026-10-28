"""检索适配层：只调用冻结 Text Locator V3 的只读能力，不复制、不改写。

分工
----
* **定位/解码/排序** 全部委托 ``tools/ace3_text_locator``：``locator_core`` 负责
  盘面解析与解码口径，``query`` 负责倒排检索与排序，``build_index`` 负责建缓存。
  本层一句判定逻辑都不自创——两套口径就会得出两套结论（这是工程纪律）。
* **本层只做三件事**：① 把 ISO / 索引目录的选取与「缺索引就建」串起来；
  ② 把命中行整理成 GUI 直接可用的结构（含原版日文、权威译文、字形覆盖）；
  ③ 提供槽级上下文与码位/偏移/EID 三种反查。

**只读保证**：索引一律 ``mode=ro`` 打开；建索引只写 ``db/<stem>/`` 缓存目录，
不碰 ISO 本身（``build_index.build`` 的实现就是扫盘 + 写 sqlite）。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import codec, env

env.ensure_sys_path()

import build_index as BI          # noqa: E402  冻结工具的建索引模块
import locator_core as L          # noqa: E402  冻结工具的只读解析层
import query as Q                 # noqa: E402  冻结工具的检索层


class SearchError(RuntimeError):
    """检索不可用（索引缺失/版本不符/索引与盘不符）。"""


DB_NAME = BI.DB_NAME

# 比这更小的文件不可能是 ACE3 盘（真盘 4.1 GB）。只用来挡明显不是盘的东西。
MIN_ISO_SIZE = 1 << 20


# ------------------------------------------------------------------ 索引选取
def index_dir_for(iso: str) -> str:
    stem = os.path.splitext(os.path.basename(iso))[0]
    return os.path.join(env.LOCATOR_DB, stem)


def index_path_for(iso: str) -> str:
    return os.path.join(index_dir_for(iso), DB_NAME)


def find_index(iso: Optional[str] = None) -> Optional[str]:
    """按 ISO 找索引；未给 ISO 时退回工具默认盘。"""
    if iso:
        p = index_path_for(iso)
        return p if os.path.exists(p) else None
    p = os.path.join(env.LOCATOR_DB, Q.DEFAULT_STEM, DB_NAME)
    return p if os.path.exists(p) else None


def adopt_index(iso: str) -> Optional[dict]:
    """索引目录名对得上、但它记的盘不是这张时，把索引**改绑**到这张盘。

    只在 ISO 的**大小和 MD5 都与索引建库时一致**时才动手（同内容换个路径，
    比如从旧工程目录搬到现在的位置）。内容不一致就返回 None——那说明同名的
    是另一张盘，旧索引只能重建，不能糊上去。

    改写的只有 ``meta`` 里的 ``iso_path``／``iso_mtime`` 两个键，槽表、码表、
    字形缓存一个字节都不碰，所以改绑之后原索引仍然可用。
    """
    if not iso or not iso_usable(iso):
        return None
    stem = os.path.splitext(os.path.basename(iso))[0]
    info = info_of(stem)
    if info is None or not info["readable"]:
        return None
    old = info["iso"]
    if os.path.abspath(old) == os.path.abspath(iso):
        return info
    try:
        if os.path.getsize(iso) != os.path.getsize(old):
            return None
    except OSError:
        pass
    try:
        if info["md5"] and (env.md5_file(iso) != info["md5"]):
            return None
    except OSError:
        return None
    try:
        conn = sqlite3.connect(info["db"])
        try:
            conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)",
                         ("iso_path", os.path.abspath(iso)))
            conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)",
                         ("iso_mtime", str(int(os.path.getmtime(iso)))))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return None
    return info_of(stem)


def iso_usable(path: str) -> bool:
    """这条路径上是否躺着一张像样的 ACE3 盘（存在、不是目录、不是空壳）。"""
    if not path or not os.path.isfile(path):
        return False
    try:
        return os.path.getsize(path) >= MIN_ISO_SIZE
    except OSError:
        return False


def info_of(stem: str) -> Optional[dict]:
    """读单个索引目录的元信息；目录或库不存在返回 None。

    ``iso_exists`` 表示索引里记的那张盘**现在还在不在原处**。索引目录是按
    ISO 文件名（stem）命名的，所以同一张盘被挪走、改名或被删之后重名的
    另一张盘会「认领」旧索引——那时 ``iso`` 指向老路径，界面必须据此提醒，
    不能让用户对着一条读不到资源的索引继续编辑（v1.0.3 鼠标验收真实踩过：
    版本下拉切到一份指向上届工程目录的索引，中栏满屏「资源读取失败」）。
    """
    p = os.path.join(env.LOCATOR_DB, stem, DB_NAME)
    if not os.path.exists(p):
        return None
    info = {"stem": stem, "dir": os.path.join(env.LOCATOR_DB, stem), "db": p,
            "iso": "", "md5": "", "built_at": "", "slots": 0,
            "size": os.path.getsize(p), "readable": True, "iso_exists": False}
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
        try:
            m = {r[0]: r[1] for r in conn.execute("SELECT k,v FROM meta")}
            info["iso"] = m.get("iso_path", "")
            info["md5"] = m.get("iso_md5", "")
            info["built_at"] = m.get("built_at", "")
            info["slots"] = int(m.get("slots", 0) or 0)
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        info["iso"] = "(索引不可读)"
        info["readable"] = False
    info["iso_exists"] = info["readable"] and iso_usable(info["iso"])
    return info


def find_info(iso: Optional[str] = None) -> Optional[dict]:
    """按 ISO 找索引信息（dict，结构与 ``list_indexes()`` 的元素一致）。

    .. warning::
       ``find_index()`` 返回的是**路径字符串**，``find_info()`` 返回的是 dict。
       主窗口载入索引用的是 ``_load_index(info: dict)``，传字符串进去就会报
       ``string indices must be integers, not 'str'``（v1.0.1 真实踩过：
       "建索引"成功后立刻弹「索引不可用」就是这个原因）。
    """
    if iso:
        stem = os.path.splitext(os.path.basename(iso))[0]
    else:
        stem = Q.DEFAULT_STEM
    return info_of(stem)


def list_indexes() -> List[dict]:
    """已建好的索引清单（供「切换 ISO 版本」下拉框与索引缓存管理）。"""
    base = env.LOCATOR_DB
    out = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        if name == SHARED_STEM:
            continue
        info = info_of(name)
        if info is not None:
            out.append(info)
    return out


SHARED_STEM = "_shared"          # 字形/日文共享缓存，不是某张盘的索引，不许删


def index_is_shared(stem: str) -> bool:
    """是否是共享缓存目录（字形/日文缓存，不能当某张盘的索引删掉）。

    只认精确的 ``_shared``。曾经写成 ``stem.startswith("_")`` 太宽，
    把任何下划线开头的目录都当共享缓存拒掉（自测里一个临时目录就被误拒）。
    删除安全性由 ``info_of()`` 兜住：目录里没有 ``text_index.sqlite`` 就不认。
    """
    return stem == SHARED_STEM


def delete_index(stem: str) -> dict:
    """删掉一个索引缓存目录（可重建；不碰 ISO、不碰冻结工具源码）。

    拒绝：共享缓存（``_shared``）、路径穿越、非索引目录。
    返回 ``{"ok": bool, "stem": str, "dir": str, "freed": int, "msg": str}``。
    """
    res = {"ok": False, "stem": stem, "dir": "", "freed": 0, "msg": ""}
    if not stem or os.sep in stem or "/" in stem or ".." in stem:
        res["msg"] = "可疑的名字，拒绝删除：%r" % (stem,)
        return res
    if index_is_shared(stem):
        res["msg"] = "%s 是字形/日文共享缓存，不是某张盘的索引，不受理。" % stem
        return res
    info = info_of(stem)
    if info is None:
        res["msg"] = "没有这个索引：%s" % stem
        return res
    folder = info["dir"]
    res["dir"] = folder
    if not os.path.isdir(folder):
        res["msg"] = "目录不存在：%s" % folder
        return res
    freed = 0
    for root, _dirs, files in os.walk(folder):
        for fn in files:
            try:
                freed += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    try:
        shutil.rmtree(folder)
    except OSError as exc:
        res["msg"] = "删除失败：%s" % exc
        return res
    res.update({"ok": True, "freed": freed,
                "msg": "已删除 %s（释放 %s）" % (stem, env.human_size(freed))})
    return res


def build_index(iso: str, progress=None, quiet=True) -> str:
    """给指定 ISO 建索引（只写缓存目录，不碰 ISO）。返回 db 路径。"""
    if not os.path.exists(iso):
        raise SearchError("ISO 不存在：%s" % iso)
    out_dir = index_dir_for(iso)
    if progress:
        progress("开始建索引：%s（首次会较慢，之后复用缓存）"
                 % os.path.basename(iso))
    BI.build(iso, out_dir=out_dir, use_md5=True, quiet=quiet)
    p = os.path.join(out_dir, DB_NAME)
    if not os.path.exists(p):
        raise SearchError("建索引失败：%s" % p)
    if progress:
        progress("索引已建好：%s" % p)
    return p


# ------------------------------------------------------------------ 索引对象
class Index:
    """一个索引的只读句柄。

    ★ 线程规则：``sqlite3`` 默认禁止跨线程使用同一个连接
    （``ProgrammingError: SQLite objects created in a thread can only be used
    in that same thread``）。GUI 里检索走 ``JobRunner`` 线程池，所以
    ``search`` / ``by_eid`` 这些查询方法内部统一用 ``_conn()`` 取连接——
    当前线程不是建连线程时，就在**本线程**另开一条只读连接并缓存，
    线程结束自然释放。绝不把 GUI 线程的连接借给后台线程用。
    """

    def __init__(self, db_path: str) -> None:
        if not os.path.exists(db_path):
            raise SearchError("索引不存在：%s" % db_path)
        try:
            self.conn, self.db_path = Q.open_db(db_path=db_path)
        except SystemExit as exc:                 # 冻结工具用 SystemExit 报错
            raise SearchError(str(exc))
        self.meta = Q.meta(self.conn)
        self.c2c, self.code_note = L.load_code_table()
        self._tid = threading.get_ident()         # 建连线程
        self._extra: Dict[int, Any] = {}          # 其他线程各自的连接

    def _conn(self):
        """取本线程可用的连接（必要时现开一条只读连接）。"""
        tid = threading.get_ident()
        if tid == self._tid:
            return self.conn
        c = self._extra.get(tid)
        if c is None:
            c, _ = Q.open_db(db_path=self.db_path)
            self._extra[tid] = c
        return c

    # -- 基础信息 --------------------------------------------------
    @property
    def iso_path(self) -> str:
        return self.meta.get("iso_path", "")

    @property
    def iso_md5(self) -> str:
        return self.meta.get("iso_md5", "")

    @property
    def built_at(self) -> str:
        return self.meta.get("built_at", "")

    @property
    def slot_total(self) -> int:
        try:
            return int(self.meta.get("slots", 0) or 0)
        except ValueError:
            return 0

    def fingerprint(self) -> dict:
        return {"db": self.db_path, "iso_path": self.iso_path,
                "iso_md5": self.iso_md5, "built_at": self.built_at,
                "schema_version": self.meta.get("schema_version", ""),
                "slots": self.slot_total,
                "code_table_size": self.meta.get("code_table_size", "")}

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
        for c in self._extra.values():
            try:
                c.close()
            except Exception:
                pass
        self._extra.clear()

    # -- 检索 ------------------------------------------------------
    def search(self, needle: str, limit: int = 200,
               with_ctx: bool = False) -> List[dict]:
        """文字检索。空 needle 返回空表（不返回全盘）。"""
        needle = (needle or "").strip()
        if not needle:
            return []
        ids = Q.search_ids(self._conn(), needle, limit=max(20000, limit * 8))
        rows = Q.rank_hits(self._conn(), needle, ids, limit=limit)
        return [self._hit(r, needle, with_ctx) for r in rows]

    def by_eid(self, eid: int, limit: int = 500) -> List[dict]:
        rows = Q.locate_slot(self._conn(), eid)[:limit]
        return [self._hit(r, "", False) for r in rows]

    def by_code(self, code: int, limit: int = 200) -> List[dict]:
        rows = Q.code_hits(self._conn(), code, limit=limit)
        return [self._hit(r, "", False) for r in rows]

    def by_offset(self, off: int, limit: int = 40) -> dict:
        res = Q.by_offset(self._conn(), off, limit=limit)
        out = {"off": off, "note": res["note"], "hits": [], "prev": [], "next": []}
        for k in ("hits", "prev", "next"):
            out[k] = [self._hit(r, "", False) for r in res[k]]
        return out

    def slot(self, eid: int, tag: int, k: int, slot: int) -> Optional[dict]:
        rows = Q.locate_slot(self._conn(), eid, tag=tag, k=k, slot=slot)
        if not rows:
            return None
        return self._hit(rows[0], "", True)

    def context(self, eid: int, tag: int, k: int, slot: int,
                span: int = 3) -> List[dict]:
        rows = Q.context(self._conn(), eid, tag, k, slot, span=span)
        out = []
        for r in rows:
            out.append({"slot": r["slot"], "text": r["plain"],
                        "codes": r["codes"], "quality": r["q"],
                        "is_target": r["slot"] == slot})
        return out

    def container(self, eid: int) -> List[dict]:
        return [dict(r) for r in Q.container_at(self._conn(), eid)]

    def code_usage(self, code: int) -> int:
        return Q.code_count(self._conn(), code)

    def siblings_same_text(self, plain: str, exclude: Tuple[int, int, int, int] = (),
                           limit: int = 30) -> List[dict]:
        """同文句（疑似镜像）候选：按文字精确匹配，**只列出，不自动同步**。"""
        plain = (plain or "").strip()
        if not plain:
            return []
        ids = Q.search_ids(self._conn(), plain, limit=4000)
        rows = Q.rank_hits(self._conn(), plain, ids, limit=limit * 2)
        out = []
        for r in rows:
            key = (r["eid"], r["tag"], r["k"], r["slot"])
            if key == exclude:
                continue
            if r["plain"].strip() != plain:        # 只留完全同文
                continue
            out.append(self._hit(r, "", False))
            if len(out) >= limit:
                break
        return out

    # -- 行转换 ----------------------------------------------------
    def _jp_of(self, eid: int, tag: int, k: int, slot: int) -> Tuple[str, str]:
        try:
            r = self._conn().execute(
                "SELECT jp_text,status FROM jp WHERE eid=? AND tag=? AND k=? AND slot=?",
                (eid, tag, k, slot)).fetchone()
        except sqlite3.DatabaseError:
            return "", ""
        if not r:
            return "", ""
        return r["jp_text"] or "", r["status"] or ""

    def _trans_of(self, eid: int, tag: int, k: int, slot: int) -> List[dict]:
        try:
            rows = self._conn().execute(
                "SELECT status,rank,scope,src,src_line,zh,match,note FROM trans "
                "WHERE eid=? AND tag=? AND k=? AND slot=? ORDER BY rank",
                (eid, tag, k, slot)).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [dict(r) for r in rows]

    def _hit(self, r, needle: str, with_ctx: bool) -> dict:
        seg = bytes(r["seg"])
        plain = r["plain"] or ""
        eid, tag, k, slot = r["eid"], r["tag"], r["k"], r["slot"]
        jp, jp_status = self._jp_of(eid, tag, k, slot)
        trans = self._trans_of(eid, tag, k, slot)
        zh = ""
        for t in trans:
            if t["zh"] and t["status"] != "NO_SOURCE":
                zh = t["zh"]
                break
        d = {
            "eid": eid, "tag": tag, "k": k, "slot": slot,
            "nslots": r["nslots"],
            "key": "EID %d · %s · 槽%d" % (eid, d_key(tag, k), slot),
            "key_short": "%d/%d,%d/%d" % (eid, tag, k, slot),
            "key_text": "(tag=%d, k=%d)" % (tag, k),
            "text": plain,
            "bytes": len(seg),
            "seg_hex": seg.hex().upper(),
            "codes_hex": r["codes"],
            "compressed": bool(r["comp"]),
            "quality": r["q"],
            "unknown_codes": r["n_unk"],
            "control_bytes": r["n_ctrl"],
            "file_off": r["file_off"],
            "file_end": r["file_end"],
            "abs_off": r["abs_off"],
            "base": r["base"],
            "jp_text": jp,
            "jp_status": jp_status,
            "zh_authority": zh,
            "trans": trans,
            "scene": "%s / %s / 槽%d" % (eid, d_key(tag, k), slot),
            "match_at": [],
        }
        if needle:
            p = plain.find(needle)
            while p >= 0:
                d["match_at"].append((p, p + len(needle)))
                p = plain.find(needle, p + 1)
        if with_ctx:
            d["context"] = self.context(eid, tag, k, slot, span=3)
        return d


def d_key(tag: int, k: int) -> str:
    return "tag%d/k%d" % (tag, k)


# ------------------------------------------------------------------ 便捷入口
_INDEX_CACHE: Dict[str, Index] = {}
_LOCK = threading.Lock()


def open_index(db_path: str) -> Index:
    key = os.path.abspath(db_path).lower()
    with _LOCK:
        idx = _INDEX_CACHE.get(key)
        if idx is None:
            idx = Index(db_path)
            _INDEX_CACHE[key] = idx
    return idx


def close_all() -> None:
    with _LOCK:
        for idx in _INDEX_CACHE.values():
            idx.close()
        _INDEX_CACHE.clear()


def parse_query(text: str) -> Tuple[str, object]:
    """把用户输入解释成 (模式, 值)。

    支持：``eid:4004643`` / ``code:0x89A8`` / ``0x3ED439F8`` / 裸数字当 EID /
    ``name:关键词``（只搜 VMD 名字块）/ 其余一律当文字。裸 8 位以上十六进制串
    按偏移理解。
    """
    s = (text or "").strip()
    low = s.lower()
    if low.startswith("eid:"):
        return "eid", int(low[4:].strip(), 0)
    if low.startswith("code:"):
        return "code", int(low[5:].strip(), 0)
    if low.startswith("offset:"):
        return "offset", int(low[7:].strip(), 0)
    if low.startswith("name:"):
        return "name", s[5:].strip()
    if low.startswith("0x"):
        try:
            v = int(low, 16)
        except ValueError:
            return "text", s
        return ("offset", v) if v >= 0x1000 else ("code", v)
    if s.isdigit() and 1 <= int(s) <= 9999999:
        return "eid", int(s)
    return "text", s


def run_query(idx: Index, text: str, limit: int = 200,
              with_names: bool = False) -> dict:
    """执行一次检索。

    ``with_names=True`` 时，文字检索会**额外**扫 VMD 名字块（CLM 指令表 /
    FNM 机体名），命中的行带 ``kind="name"``。名字块不在 Text Locator 索引里
    （实测索引只收了 VMD 里一处 ``tag=-1`` 的文本块），所以要在这里补上，
    否则「冥墓方舟」这类机体名/武器名永远搜不到。

    返回值里的 ``name_note`` 说明名字块这一趟的结果，便于界面如实交代
    「搜了但没命中」与「根本没搜」的区别。
    """
    mode, val = parse_query(text)
    if mode == "name":
        from . import nametext as NT
        hits = NT.search_hits(idx.iso_path, val, limit=limit) if val else []
        return {"mode": "name", "value": val, "hits": hits,
                "note": ("名字块命中 %d 条（CLM 指令表 / FNM 机体名）" % len(hits))
                        if hits else "名字块里没有匹配的串。",
                "name_note": "已搜索 VMD 名字块"}
    if mode == "text":
        hits = idx.search(val, limit=limit)
        name_note = "本次未搜索名字块"
        if with_names and val:
            from . import nametext as NT
            try:
                nh = NT.search_hits(idx.iso_path, val, limit=max(20, limit // 4))
            except Exception as exc:                 # noqa: BLE001
                name_note = "名字块检索失败：%s" % exc
            else:
                hits = hits + nh
                name_note = ("名字块命中 %d 条（CLM 指令表 / FNM 机体名）"
                             % len(nh)) if nh else "名字块无命中"
        return {"mode": mode, "value": val, "hits": hits,
                "name_note": name_note}
    if mode == "eid":
        return {"mode": mode, "value": val, "hits": idx.by_eid(val)}
    if mode == "code":
        return {"mode": mode, "value": val,
                "hits": idx.by_code(val, limit=limit),
                "usage": idx.code_usage(val)}
    res = idx.by_offset(val)
    return {"mode": mode, "value": val, "hits": res["hits"],
            "note": res["note"], "prev": res["prev"], "next": res["next"]}
