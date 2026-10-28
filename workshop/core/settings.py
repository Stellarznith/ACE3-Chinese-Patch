"""轻量界面偏好存储（JSON 文件，不碰注册表）。

只存"下次打开还能用上"的小东西，例如上次选 ISO 的目录。
刻意**不存**任何盘面身份（父盘 MD5、偏移、槽号）——那些属于草稿与修改队列，
由 `editplan` 负责并与父盘绑定。

纪律：
* 文件损坏时不覆盖、不抛异常：改名 ``settings.json.corrupt_<时间戳>`` 留证后从空开始。
* 写盘走"临时文件 → 回读校验 → 原子替换"，中途断电不会留下半截 JSON。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from . import env

NAME = "settings.json"

_CACHE: Optional[Dict[str, Any]] = None


def path() -> str:
    """偏好文件的绝对路径（在工作区 work/ 下，与草稿同处）。"""
    return os.path.join(env.WORK_DIR, NAME)


def load(force: bool = False) -> Dict[str, Any]:
    """读取全部偏好；坏档改名留证并返回空 dict。"""
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    p = path()
    data: Dict[str, Any] = {}
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if isinstance(obj, dict):
                data = obj
        except (OSError, ValueError):
            stamp = time.strftime("%Y%m%d_%H%M%S")
            try:
                os.replace(p, p + ".corrupt_" + stamp)
            except OSError:
                pass
            data = {}
    _CACHE = data
    return _CACHE


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def save(data: Dict[str, Any]) -> bool:
    """原子保存；成功返回 True。写不进去不抛异常（偏好丢了不影响干活）。"""
    global _CACHE
    if not isinstance(data, dict):
        return False
    p = path()
    tmp = p + ".tmp"
    try:
        env.ensure_dirs()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        with open(tmp, "r", encoding="utf-8") as f:
            if json.load(f) != data:
                raise ValueError("回读不一致")
        os.replace(tmp, p)
    except (OSError, ValueError):
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False
    _CACHE = dict(data)
    return True


def put(key: str, value: Any) -> bool:
    d = dict(load())
    if d.get(key) == value:
        return True
    d[key] = value
    return save(d)


# ---------------------------------------------------------------- 目录记忆
DIR_KEYS = ("iso_dir",)


def remember_dir(key: str, folder: str) -> bool:
    """记住"上次用过的目录"。目录不存在就不记，免得下次打开弹到无效路径。

    返回 True 表示真的记下了，False 表示被拒（空路径 / 目录不存在 / 写盘失败）。
    """
    if not folder:
        return False
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        return False
    return bool(put(key, folder))


def recall_dir(key: str, fallback: str = "") -> str:
    """取回上次的目录；失效（被删/改名）时退回 caller 给的兜底目录。"""
    saved = get(key) or ""
    if isinstance(saved, str) and saved and os.path.isdir(saved):
        return saved
    return fallback
