"""编辑计划管理器：暂存修改、草稿、冲突检测、JSON 导入导出。

铁律（来自任务书 §五与工程纪律）
--------------------------------
1. **每条修改都绑定父盘身份**（路径 + 大小 + MD5）。父盘一变，旧计划不得直接复用。
2. **槽身份只用逻辑口径** ``(eid, tag, k, slot)``，绝不存 ``file_off``——
   改过字的盘整块偏移会平移，物理地址跨版本无效（r1354→r1356 已实证）。
3. **草稿与正式写盘严格分离**：本模块只写 ``work/drafts/`` 下的 JSON，
   永不触碰 ISO。
4. **草稿损坏不得覆盖上一份有效备份**：写入走 临时文件 → 校验 → 备份旧版 →
   原子替换；读失败时只报错，不回写。
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import os
import shutil
import tempfile
from typing import Dict, Iterable, List, Optional, Tuple

from . import codec, env, paths

PLAN_VERSION = 1

STATE_PENDING = "待修改"
STATE_OK = "可写入"
STATE_WARN = "警告"
STATE_BLOCK = "阻断"


class PlanError(RuntimeError):
    """计划文件不可用。"""


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclasses.dataclass
class SlotRef:
    """槽的稳定身份（不含任何物理地址）。"""

    eid: int
    tag: int
    k: int
    slot: int

    def key(self) -> Tuple[int, int, int, int]:
        return (self.eid, self.tag, self.k, self.slot)

    def label(self) -> str:
        return "EID %d · tag%d/k%d · 槽 %d" % (self.eid, self.tag, self.k, self.slot)

    def as_dict(self) -> dict:
        return {"eid": self.eid, "tag": self.tag, "k": self.k, "slot": self.slot}

    @classmethod
    def of(cls, d: dict) -> "SlotRef":
        return cls(int(d["eid"]), int(d["tag"]), int(d["k"]), int(d["slot"]))


@dataclasses.dataclass
class PlanItem:
    """一条编辑计划（暂存修改）。

    ``kind`` 区分两类编辑对象：

    * ``"slot"`` —— 普通文本槽，身份 ``(eid, tag, k, slot)``，走 ``writer.Edit``
    * ``"name"`` —— CLM/FNM 名字块里的某一条，身份
      ``("name", eid, block_pos, index)``，走 ``namewriter.NameEdit``

    **槽编辑的 ``key()`` 形状一个字都没改**（仍是 4 元组 int），
    否则 ``pipeline`` 里 ``by_key[v.edit.key()]`` 的映射会整片失效。
    """

    ref: SlotRef
    new_text: str                      # 用户输入的中文正文（不含控制标签）
    new_payload_hex: str               # 编码后的完整字节（含控制标签）
    old_payload_hex: str               # 原盘字节指纹（防串位）
    old_text: str = ""                 # 原盘可读文本
    jp_text: str = ""                  # 原版日文对照
    zh_current: str = ""               # 当前正式译文
    old_tags: List[list] = dataclasses.field(default_factory=list)
    new_tags: List[list] = dataclasses.field(default_factory=list)
    state: str = STATE_PENDING
    reasons: List[str] = dataclasses.field(default_factory=list)
    warnings: List[str] = dataclasses.field(default_factory=list)
    capacity: int = 0
    compressed: bool = False
    created: str = dataclasses.field(default_factory=_now)
    modified: str = dataclasses.field(default_factory=_now)
    note: str = ""
    screenshot: str = ""
    history: List[dict] = dataclasses.field(default_factory=list)
    # -- 名字块编辑专用（kind == "name" 时有效） -----------------------
    kind: str = "slot"
    name_block_pos: int = 0            # 名字块在资源内的偏移
    name_index: int = 0                # 块内第几条
    mark: str = ""                     # "CLM" / "FNM"
    name_slack: int = 0                # 该块可吸收字节（块内重排上限）
    name_mode: str = ""                # inplace / reflow

    # -- 便捷 --------------------------------------------------
    def key(self):
        """稳定身份。槽编辑返回 4 元组（与历史一致），名字编辑返回带前缀的 4 元组。"""
        if self.kind == "name":
            return ("name", self.ref.eid, self.name_block_pos, self.name_index)
        return self.ref.key()

    @property
    def is_name(self) -> bool:
        return self.kind == "name"

    @property
    def new_payload(self) -> bytes:
        return bytes.fromhex(self.new_payload_hex)

    @property
    def old_payload(self) -> bytes:
        return bytes.fromhex(self.old_payload_hex)

    def label(self) -> str:
        """一行人类可读身份（队列里显示用）。"""
        if self.kind == "name":
            return ("EID %d · %s 块 @0x%X · 第 %d 条"
                    % (self.ref.eid, self.mark or "名字", self.name_block_pos,
                       self.name_index))
        return self.ref.label()

    def as_dict(self) -> dict:
        return {
            "eid": self.ref.eid, "tag": self.ref.tag,
            "k": self.ref.k, "slot": self.ref.slot,
            "new_text": self.new_text,
            "new_payload_hex": self.new_payload_hex,
            "old_payload_hex": self.old_payload_hex,
            "old_text": self.old_text,
            "jp_text": self.jp_text,
            "zh_current": self.zh_current,
            "old_tags": self.old_tags,
            "new_tags": self.new_tags,
            "state": self.state,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "capacity": self.capacity,
            "compressed": self.compressed,
            "created": self.created,
            "modified": self.modified,
            "note": self.note,
            "screenshot": self.screenshot,
            "history": list(self.history),
            "kind": self.kind,
            "name_block_pos": self.name_block_pos,
            "name_index": self.name_index,
            "mark": self.mark,
            "name_slack": self.name_slack,
            "name_mode": self.name_mode,
        }

    @classmethod
    def of(cls, d: dict) -> "PlanItem":
        r = SlotRef(int(d["eid"]), int(d["tag"]), int(d["k"]), int(d["slot"]))
        return cls(
            ref=r, new_text=d.get("new_text", ""),
            new_payload_hex=d.get("new_payload_hex", ""),
            old_payload_hex=d.get("old_payload_hex", ""),
            old_text=d.get("old_text", ""), jp_text=d.get("jp_text", ""),
            zh_current=d.get("zh_current", ""),
            old_tags=d.get("old_tags") or [], new_tags=d.get("new_tags") or [],
            state=d.get("state", STATE_PENDING),
            reasons=list(d.get("reasons") or []),
            warnings=list(d.get("warnings") or []),
            capacity=int(d.get("capacity") or 0),
            compressed=bool(d.get("compressed")),
            created=d.get("created") or _now(),
            modified=d.get("modified") or _now(),
            note=d.get("note", ""), screenshot=d.get("screenshot", ""),
            history=list(d.get("history") or []),
            kind=d.get("kind", "slot"),
            name_block_pos=int(d.get("name_block_pos") or 0),
            name_index=int(d.get("name_index") or 0),
            mark=d.get("mark", ""),
            name_slack=int(d.get("name_slack") or 0),
            name_mode=d.get("name_mode", ""),
        )


# ---------------------------------------------------------------- 父盘身份
def parent_identity(iso: str, md5: Optional[str] = None) -> dict:
    if not os.path.exists(iso):
        raise PlanError("父盘不存在：%s" % iso)
    st = os.stat(iso)
    return {"path": os.path.abspath(iso), "size": st.st_size,
            "mtime": int(st.st_mtime),
            "md5": (md5 or env.md5_file(iso)).lower()}


# ---------------------------------------------------------------- 队列
class PlanQueue:
    """内存里的修改队列 + 草稿持久化。"""

    def __init__(self) -> None:
        self.items: List[PlanItem] = []
        self.parent: dict = {}
        self.draft_path: str = ""
        self.dirty: bool = False

    # -- 父盘 --------------------------------------------------
    def bind(self, iso: str, md5: Optional[str] = None) -> dict:
        self.parent = parent_identity(iso, md5)
        return self.parent

    def parent_ok(self, iso: str, md5: Optional[str] = None) -> Tuple[bool, str]:
        if not self.parent:
            return True, ""
        cur = parent_identity(iso, md5)
        if cur["md5"] != self.parent.get("md5"):
            return False, ("父盘 MD5 已变：计划绑定 %s，当前 %s。"
                           "跨版本不得复用旧计划，请重建或导出后另存。"
                           % (self.parent.get("md5"), cur["md5"]))
        return True, ""

    # -- 增删改 --------------------------------------------------
    def upsert(self, item: PlanItem) -> str:
        """加入或更新一条。返回动作：``added`` / ``updated`` / ``unchanged``。"""
        for i, it in enumerate(self.items):
            if it.key() == item.key():
                if it.new_payload_hex == item.new_payload_hex \
                        and it.note == item.note:
                    return "unchanged"
                item.created = it.created
                item.screenshot = it.screenshot or it.screenshot
                item.history = list(it.history)
                item.history.append({"at": _now(), "from": it.new_text,
                                     "to": item.new_text})
                item.modified = _now()
                self.items[i] = item
                self.dirty = True
                return "updated"
        self.items.append(item)
        self.dirty = True
        return "added"

    def remove(self, key: Tuple[int, int, int, int]) -> bool:
        for i, it in enumerate(self.items):
            if it.key() == key:
                del self.items[i]
                self.dirty = True
                return True
        return False

    def get(self, key: Tuple[int, int, int, int]) -> Optional[PlanItem]:
        for it in self.items:
            if it.key() == key:
                return it
        return None

    def clear(self) -> None:
        self.items.clear()
        self.dirty = True

    def revert(self, key: Tuple[int, int, int, int]) -> bool:
        """恢复原文：把该项标记为回原文并移出队列。"""
        return self.remove(key)

    # -- 冲突与统计 ----------------------------------------------
    def duplicates(self) -> Dict[Tuple[int, int, int, int], int]:
        seen: Dict[Tuple[int, int, int, int], int] = {}
        for it in self.items:
            seen[it.key()] = seen.get(it.key(), 0) + 1
        return {k: n for k, n in seen.items() if n > 1}

    def same_resource_groups(self) -> Dict[int, List[PlanItem]]:
        out: Dict[int, List[PlanItem]] = {}
        for it in self.items:
            out.setdefault(it.ref.eid, []).append(it)
        return out

    def stats(self) -> dict:
        n = {STATE_PENDING: 0, STATE_OK: 0, STATE_WARN: 0, STATE_BLOCK: 0}
        for it in self.items:
            n[it.state] = n.get(it.state, 0) + 1
        return {"total": len(self.items), "by_state": n,
                "blocked": n.get(STATE_BLOCK, 0),
                "warn": n.get(STATE_WARN, 0),
                "by_state_ok": n.get(STATE_OK, 0),
                "resources": len(self.same_resource_groups()),
                "duplicates": len(self.duplicates()),
                "dirty": self.dirty}

    def by_state(self, state: str) -> List[PlanItem]:
        return [it for it in self.items if it.state == state]

    # -- 草稿 --------------------------------------------------
    def draft_name(self) -> str:
        stem = "draft"
        if self.parent.get("path"):
            stem = os.path.splitext(os.path.basename(self.parent["path"]))[0]
        md5 = (self.parent.get("md5") or "")[:8] or "noparent"
        return "%s_%s.json" % (stem, md5)

    def save_draft(self, directory: Optional[str] = None,
                   note: str = "") -> str:
        """保存草稿（原子写 + 旧版备份；损坏时绝不覆盖好档）。"""
        # 守则：草稿只落工具自身目录。用户指定了系统盘目录（例如桌面）时
        # 改道到默认草稿目录，并记下原因——草稿丢在系统盘等于丢了修改。
        d = paths.resolve_or(directory or env.DRAFT_DIR, env.DRAFT_DIR, "草稿目录")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, self.draft_name())
        payload = {
            "plan_version": PLAN_VERSION,
            "saved_at": _now(),
            "note": note,
            "parent": self.parent,
            "items": [it.as_dict() for it in self.items],
        }
        blob = json.dumps(payload, ensure_ascii=False, indent=1)
        # 1) 写临时文件并回读校验
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".draft_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            with open(tmp, "r", encoding="utf-8") as f:
                chk = json.load(f)
            if len(chk.get("items", [])) != len(self.items):
                raise PlanError("草稿回读条数不符")
        except Exception:
            try:
                os.remove(tmp)
            finally:
                raise
        # 2) 旧档先备份，再原子替换
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    json.load(f)
                shutil.copyfile(path, path + ".bak")
            except Exception:
                # 旧档已损坏：**不要**覆盖它，改名为 .corrupt 留证
                bad = "%s.corrupt_%s" % (path, datetime.datetime.now()
                                         .strftime("%Y%m%d_%H%M%S"))
                try:
                    shutil.move(path, bad)
                except Exception:
                    pass
        os.replace(tmp, path)
        self.draft_path = path
        self.dirty = False
        return path

    def load_draft(self, path: str, verify_parent: bool = True) -> dict:
        if not os.path.exists(path):
            raise PlanError("草稿不存在：%s" % path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as exc:
            raise PlanError("草稿损坏（不覆盖、不回写）：%s\n%s" % (path, exc))
        ver = int(payload.get("plan_version") or 0)
        if ver > PLAN_VERSION:
            raise PlanError("草稿版本 %d 高于程序支持的 %d" % (ver, PLAN_VERSION))
        self.parent = dict(payload.get("parent") or {})
        self.items = [PlanItem.of(d) for d in payload.get("items") or []]
        self.draft_path = path
        self.dirty = False
        warn = ""
        if verify_parent and self.parent.get("path"):
            try:
                ok, why = self.parent_ok(self.parent["path"])
                if not ok:
                    warn = why
            except Exception as exc:
                warn = "无法核对父盘：%s" % exc
        return {"items": len(self.items), "parent": self.parent,
                "saved_at": payload.get("saved_at", ""), "warning": warn}

    def list_drafts(self, directory: Optional[str] = None) -> List[dict]:
        d = directory or env.DRAFT_DIR
        out = []
        if not os.path.isdir(d):
            return out
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            p = os.path.join(d, name)
            info = {"path": p, "name": name, "size": os.path.getsize(p),
                    "saved_at": "", "items": 0, "parent_md5": "", "parent": "",
                    "ok": True, "error": ""}
            try:
                with open(p, "r", encoding="utf-8") as f:
                    d2 = json.load(f)
                info["saved_at"] = d2.get("saved_at", "")
                info["items"] = len(d2.get("items") or [])
                par = d2.get("parent") or {}
                info["parent_md5"] = par.get("md5", "")
                info["parent"] = par.get("path", "")
            except Exception as exc:
                info["ok"] = False
                info["error"] = str(exc)
            out.append(info)
        return out

    # -- 导入导出（显式 JSON 计划） ------------------------------
    def export_json(self, path: str, include_history: bool = True) -> str:
        payload = {
            "plan_version": PLAN_VERSION,
            "kind": "ace3_editor_plan",
            "exported_at": _now(),
            "parent": self.parent,
            "items": [it.as_dict() for it in self.items],
        }
        if not include_history:
            for d in payload["items"]:
                d.pop("history", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        return path

    def import_json(self, path: str, merge: bool = True,
                    verify_parent: bool = True) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("kind") not in (None, "ace3_editor_plan"):
            raise PlanError("不是编辑计划文件：kind=%r" % payload.get("kind"))
        par = payload.get("parent") or {}
        warn = ""
        if verify_parent and par.get("md5") and self.parent.get("md5"):
            if par["md5"] != self.parent["md5"]:
                warn = ("计划来自另一张盘（%s vs 当前 %s），已按槽身份导入但"
                        "原字节指纹会逐项复核" % (par["md5"], self.parent["md5"]))
        if not merge:
            self.items.clear()
        added = updated = 0
        for d in payload.get("items") or []:
            it = PlanItem.of(d)
            act = self.upsert(it)
            if act == "added":
                added += 1
            elif act == "updated":
                updated += 1
        self.dirty = True
        return {"added": added, "updated": updated, "warning": warn,
                "items": len(self.items)}


# ---------------------------------------------------------------- 从命中构造
def hit_key(hit: dict) -> tuple:
    """由检索命中算出队列身份（与 ``PlanItem.key()`` 同一口径）。

    名字块命中要看它属于哪个引擎（``ver_k`` 是不是 None）：

    * FNM 块本身就是 VER 块（实测 ``VER.start == FNM.base``、``cnt==1``），
      它走槽编辑引擎，身份必须是 ``(eid, tag, k, slot)`` 四个 int——
      与 ``item_from_name()`` 产出的 ``kind="slot"`` 计划完全一致。
    * CLM 块不在 VER 体系里，走名字块引擎，身份是
      ``("name", eid, block_pos, block_index)``。

    存在的理由：GUI 里 ``on_hit`` / ``on_save_requested`` 要拿 key 去队列查重，
    以前那里写死 ``(hit["eid"], hit["tag"], hit["k"], hit["slot"])``，
    名字块命中没有 tag/k（是 -1/-1），会与真实槽位撞车或查不到。

    2026-10-27 修正：以前这里只看 ``kind``，把 FNM 也算成 ``("name", …)``，
    而 ``item_from_name()`` 对 FNM 产出的是 4 个 int ⇒ 同一句 FNM 命中
    算出来的 key 与入队后的 key 对不上，队列查重永远查不到、
    「重新编辑」也定位不回原项。现在两处都按 ``ver_k`` 分流。
    """
    if hit.get("kind") == "name":
        ver_k = hit.get("ver_k")
        if ver_k is not None:
            return (int(hit["eid"]), int(hit.get("mem_tag", -1)),
                    int(ver_k), int(hit["slot"]))
        return ("name", int(hit["eid"]), int(hit.get("block_pos", 0)),
                int(hit["slot"]))
    return (int(hit["eid"]), int(hit["tag"]), int(hit["k"]), int(hit["slot"]))


def item_from_hit(hit: dict, new_text: str,
                  preferred_codes: Iterable[int] = ()) -> Tuple[PlanItem, list]:
    """由检索命中 + 用户新文本构造一条计划（含编码与标签校验）。

    返回 ``(PlanItem, 缺字列表)``。缺字不在这里阻断，交给 ``writer.check``
    统一出 PASS/WARN/BLOCK，避免两套判定口径。
    """
    seg_old = bytes.fromhex(hit["seg_hex"])
    seg = codec.Segment(seg_old)
    data, er = codec.rebuild_with_plain(seg, new_text)
    ref = SlotRef(int(hit["eid"]), int(hit["tag"]), int(hit["k"]), int(hit["slot"]))
    item = PlanItem(
        ref=ref,
        new_text=new_text,
        new_payload_hex=data.hex(),
        old_payload_hex=seg_old.hex(),
        old_text=seg.plain,
        jp_text=hit.get("jp_text", ""),
        zh_current=hit.get("zh_authority", ""),
        old_tags=[[t.hex() if isinstance(t, bytes) else t
                   for t in codec.Segment(seg_old).tags]],
        new_tags=[[t.hex() if isinstance(t, bytes) else t
                   for t in codec.Segment(data).tags]],
        capacity=int(hit.get("bytes") or 0),
        compressed=bool(hit.get("compressed")),
    )
    return item, sorted(er.missing)


def item_from_name(hit, new_text: str) -> Tuple[PlanItem, list]:
    """由名字块命中（``nametext.NameHit``）+ 用户新文本构造一条计划。

    名字块的字符串**没有控制标签**（实测 CLM/FNM 里全是纯文本 + NUL 结尾），
    所以这里直接 ``codec.encode_text``，不需要 ``rebuild_with_plain``。

    **按引擎自动分流**（实测结论）：

    * ``hit.ver_k is not None`` —— 这是 FNM 块，它本身就是 VER 块
      （``VER.start == FNM.base``、``cnt == 1``），所以直接产出一条**槽编辑**
      计划（``kind="slot"``，身份 ``(eid, mem_tag, ver_k, index)``），
      走 ``writer`` 那套已经验证过的 relayout 引擎；
    * ``hit.ver_k is None`` —— CLM 指令/武器名表，不在 VER 体系里，
      产出**名字块编辑**计划（``kind="name"``），走 ``namewriter`` 的块内重排。

    这样队列里两类计划混排也安全：槽计划的 ``key()`` 仍是 4 个 int，
    历史代码（pipeline / queue / 草稿）一个字都不用改。

    返回 ``(PlanItem, 缺字列表)``。缺字不在这里阻断，交给预检统一出
    PASS/WARN/BLOCK，避免两套判定口径。

    入参 ``hit`` 既接受 ``core.nametext.NameHit`` 对象，也接受
    ``core.nametext.as_hit()`` 产出的 dict（GUI 走的是 dict）。
    """
    g = (hit.get if isinstance(hit, dict) else (lambda k, d=None: getattr(hit, k, d)))
    eid = int(g("eid"))
    index = int(g("slot", g("index", 0)))
    block_pos = int(g("block_pos", 0))
    raw = bytes.fromhex(g("seg_hex") or "") if isinstance(hit, dict) else hit.raw
    plain = (g("text") or "") if isinstance(hit, dict) else hit.plain
    mark = g("mark") or ""
    slack = int(g("slack", 0))
    cap = int(g("capacity", 0))
    ver_k = g("ver_k")
    mem_tag = int(g("mem_tag", -1))
    new_raw = codec.encode_text(new_text)
    if ver_k is not None:
        item = PlanItem(
            ref=SlotRef(eid, mem_tag, int(ver_k), index),
            new_text=new_text,
            new_payload_hex=new_raw.data.hex(),
            old_payload_hex=raw.hex(),
            old_text=plain,
            capacity=cap,
            compressed=False,
        )
        return item, sorted(new_raw.missing)
    item = PlanItem(
        ref=SlotRef(eid, -1, -1, index),
        new_text=new_text,
        new_payload_hex=new_raw.data.hex(),
        old_payload_hex=raw.hex(),
        old_text=plain,
        capacity=cap,
        compressed=False,
        kind="name",
        name_block_pos=block_pos,
        name_index=index,
        mark=mark,
        name_slack=slack,
    )
    return item, sorted(new_raw.missing)


def name_edits_of(items) -> list:
    """从计划队列里挑出名字块编辑，转成 ``namewriter.NameEdit``。"""
    from . import namewriter as NW
    out = []
    for it in items:
        if not getattr(it, "is_name", False):
            continue
        out.append(NW.NameEdit(eid=it.ref.eid, block_pos=it.name_block_pos,
                               index=it.name_index,
                               old_raw=it.old_payload,
                               new_raw=it.new_payload,
                               mark=it.mark))
    return out


def slot_items_of(items) -> list:
    """从计划队列里挑出普通槽编辑。"""
    return [it for it in items if not getattr(it, "is_name", False)]
