"""application.writer_lock — 单写者串行化 + 恢复记录（P3）。

规划原文（`A_核心规划.md` L114）：

> 同一幂等键与同一载荷返回既有作业；同一键配不同载荷拒绝。
> **共享工作区初期采用单写者排队。**

**为什么必须是文件锁**：写作业横跨进程 —— Agent 走 MCP 是一个进程，
工程师手动跑 `cli apply-change` 是另一个，两个 DSH 会话又是两个。
`threading.Lock` 只能管住自己那一个进程，对真实并发毫无作用。

**stale 抢占**：写作业异常中断（进程被杀、断电）会留下锁文件。若不做 stale
检测，工程就永久写不了了 —— 那比不锁更糟。超过 `stale_after_s` 的锁可被抢占，
但**抢占动作写审计记录**（`locks/<scope>.stale.jsonl`），不静默。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

__all__ = ["WriterBusy", "writer_lock", "read_lock_holder", "LockState"]


class WriterBusy(RuntimeError):
    """锁被别的写者持有，且未在允许的等待窗口内释放。"""

    def __init__(self, scope: str, holder: dict[str, Any]) -> None:
        self.scope = scope
        self.holder = holder
        who = holder.get("actor") or f"pid {holder.get('pid', '?')}"
        super().__init__(f"WRITER_BUSY: {scope} 正被 {who} 写入"
                         f"（自 {holder.get('at', '?')}）")


class LockState:
    """锁的当前状态（供只读查询，不改变任何东西）。"""

    def __init__(self, held: bool, holder: dict[str, Any] | None,
                 age_s: float | None) -> None:
        self.held = held
        self.holder = holder or {}
        self.age_s = age_s

    def to_dict(self) -> dict[str, Any]:
        return {"held": self.held, "holder": self.holder,
                "age_seconds": (round(self.age_s, 1)
                                if self.age_s is not None else None)}


def _slug(scope: str) -> str:
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:40]


def _lock_path(root: Path, scope: str) -> Path:
    return Path(root) / "locks" / f"{_slug(scope)}.lock"


def _read_holder(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                       # noqa: BLE001 — 锁文件损坏按"未知持有者"
        return {"actor": "<锁文件不可读>"}


def read_lock_holder(root: Path, scope: str) -> LockState:
    path = _lock_path(root, scope)
    if not path.exists():
        return LockState(False, None, None)
    return LockState(True, _read_holder(path), time.time() - path.stat().st_mtime)


def _audit_stale(root: Path, scope: str, holder: dict[str, Any],
                 age_s: float) -> None:
    d = Path(root) / "locks"
    d.mkdir(parents=True, exist_ok=True)
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "scope": scope,
           "stale_holder": holder, "age_seconds": round(age_s, 1),
           "action": "preempted"}
    with (d / f"{_slug(scope)}.stale.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


@contextmanager
def writer_lock(scope: str, *, root: Path, actor: str = "",
                wait_s: float = 0.0,
                stale_after_s: float = 900.0) -> Iterator[dict[str, Any]]:
    """对 ``scope``（一般是 ``<project_id>/<case>``）取独占写锁。

    ``wait_s`` 默认 0 = **不排队，立即失败**。写作业是秒级操作，"排队等待"
    只会把 Agent 挂住且不告诉它为什么；直接回 `WRITER_BUSY` 并附上当前持有者，
    让调用方自己决定等还是换目标，信息量更大。需要等待时把 ``wait_s`` 调大即可。
    """
    path = _lock_path(root, scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, wait_s)
    mine: dict[str, Any] = {}

    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            age = time.time() - path.stat().st_mtime
            if age > stale_after_s:
                holder = _read_holder(path)
                _audit_stale(root, scope, holder, age)
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise WriterBusy(scope, _read_holder(path))
            time.sleep(0.05)
            continue

        mine = {"scope": scope, "actor": actor or f"pid:{os.getpid()}",
                "pid": os.getpid(), "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "stale_after_s": stale_after_s}
        try:
            os.write(fd, json.dumps(mine, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(fd)
        break

    try:
        yield mine
    finally:
        # 只删自己拿到的锁：若被 stale 抢占后又有人拿到，就不能误删别人的
        holder = _read_holder(path)
        if holder.get("pid") == mine.get("pid") and \
                holder.get("at") == mine.get("at"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
