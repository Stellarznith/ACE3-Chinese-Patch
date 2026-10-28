"""后台任务：把耗时操作（建索引、检索、预检、写盘）挪到工作线程。

原则
----
* GUI 线程只做界面；任何超过几十毫秒的操作都走这里。
* 任务可取消（协作式）：worker 定期检查 ``is_cancelled()``。
* 异常一律转成 ``failed`` 信号里的可读文本，**不允许静默吞掉**，
  也不允许让 GUI 直接闪退。
"""
from __future__ import annotations

import traceback
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str, str)          # (摘要, 详细堆栈)
    progress = Signal(str)
    finished = Signal()


class Job(QRunnable):
    """把一个无参函数丢进线程池执行。

    ``fn`` 可接受一个 ``emit`` 回调作为可选参数用于汇报进度：
    用 ``Job(fn)`` 时若 fn 声明了 ``progress`` 关键字即自动注入。
    """

    def __init__(self, fn: Callable[..., Any], *args, **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._cancel = False
        self._finished = False
        self.signals = _Signals()
        # 不交给线程池自动 delete：``finished`` 是排队投递到 GUI 线程的，
        # 若 run() 返回后 C++ 侧立刻销毁 QRunnable，``signals`` 可能一起没了，
        # 收不到 ``finished`` 就会永远显示「有任务在跑」（真实踩过）。
        # 生命周期改由 JobRunner 持有：_clear() 里把引用放掉即回收。
        self.setAutoDelete(False)

    # -- 取消 --------------------------------------------------
    def cancel(self) -> None:
        self._cancel = True

    def is_cancelled(self) -> bool:
        return self._cancel

    def _emit(self, msg: str) -> None:
        self.signals.progress.emit(str(msg))

    # -- 执行 --------------------------------------------------
    def run(self) -> None:
        try:
            fn = self._fn
            kwargs = dict(self._kwargs)
            try:
                import inspect as _inspect

                params = _inspect.signature(fn).parameters
                if "progress" in params and "progress" not in kwargs:
                    kwargs["progress"] = self._emit
                if "job" in params and "job" not in kwargs:
                    kwargs["job"] = self
            except (TypeError, ValueError):
                pass
            result = fn(*self._args, **kwargs)
            if self._cancel:
                self.signals.failed.emit("已取消", "")
            else:
                self.signals.done.emit(result)
        except Exception as exc:                      # noqa: BLE001
            summary = "%s：%s" % (type(exc).__name__, exc)
            self.signals.failed.emit(summary, traceback.format_exc())
        finally:
            self._finished = True
            self.signals.finished.emit()


class JobRunner(QObject):
    """管理当前活动的 Job（同一时刻只允许一个重任务，避免抢占磁盘）。"""

    busy_changed = Signal(bool)
    progress = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._job: Optional[Job] = None
        self._label = ""

    # -- 状态 --------------------------------------------------
    def busy(self) -> bool:
        return self._job is not None

    def label(self) -> str:
        return self._label

    def cancel(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self.progress.emit("正在取消「%s」…" % self._label)

    # -- 启动 --------------------------------------------------
    def start(self, label: str, fn: Callable[..., Any],
              on_done: Callable[[Any], None],
              on_fail: Optional[Callable[[str, str], None]] = None,
              *args, **kwargs) -> bool:
        # 兜底：上一个任务其实已经跑完，只是 ``finished`` 这条排队信号还没送到，
        # 此时不该继续把新任务挡在门外（否则界面会永远显示「有任务在跑」）。
        if self._job is not None and getattr(self._job, "_finished", False):
            self._clear()
        if self._job is not None:
            return False
        job = Job(fn, *args, **kwargs)
        self._job = job
        self._label = label
        job.signals.progress.connect(self.progress.emit)
        job.signals.done.connect(on_done)
        if on_fail is not None:
            job.signals.failed.connect(on_fail)
        job.signals.finished.connect(self._clear)
        self.busy_changed.emit(True)
        self.progress.emit("开始：%s" % label)
        self._pool.start(job)
        return True

    def _clear(self) -> None:
        self._job = None
        self._label = ""
        self.busy_changed.emit(False)

    def shutdown(self, ms: int = 3000) -> None:
        self.cancel()
        self._pool.waitForDone(ms)
