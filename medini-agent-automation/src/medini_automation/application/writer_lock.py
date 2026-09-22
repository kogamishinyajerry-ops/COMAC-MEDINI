"""Single-writer exclusion for cooperating processes on a local filesystem.

An old lock does NOT prove that its writer stopped. Never preempt a lock by
wall-clock age. An abandoned or damaged record deliberately fails closed.
Recovery requires an operator to stop/quiesce every writer, inspect the
workcopy and archive the lock before removing it. Do not remove a lock while
any writer can still run. This is not a distributed lease/fencing service.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

__all__ = ["WriterBusy", "writer_lock", "read_lock_holder", "LockState"]


class WriterBusy(RuntimeError):
    """Another writer, or an unresolved abandoned record, owns the scope."""

    def __init__(self, scope: str, holder: dict[str, Any]) -> None:
        self.scope = scope
        self.holder = holder
        who = holder.get("actor") or f"pid {holder.get('pid', '?')}"
        super().__init__(f"WRITER_BUSY: {scope} 正被 {who} 写入"
                         f"（自 {holder.get('at', '?')}）；锁龄不能证明作业结束")


class LockState:
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
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except (OSError, UnicodeError, ValueError):
        pass
    return {"actor": "<锁文件不可读或格式错误>"}


def read_lock_holder(root: Path, scope: str) -> LockState:
    path = _lock_path(root, scope)
    try:
        modified = path.stat().st_mtime
    except FileNotFoundError:
        return LockState(False, None, None)
    return LockState(True, _read_holder(path), time.time() - modified)


@contextmanager
def writer_lock(scope: str, *, root: Path, actor: str = "",
                wait_s: float = 0.0,
                stale_after_s: float = 900.0) -> Iterator[dict[str, Any]]:
    """Acquire an exclusive scope lock; preserve the existing public API.

    ``stale_after_s`` remains in metadata for compatibility/diagnostics only.
    It no longer grants permission to steal a lock. Waits use a monotonic
    clock. Each acquisition has a unique token, not just a PID/time pair.
    """
    for name, value in (("wait_s", wait_s), ("stale_after_s", stale_after_s)):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and non-negative")
    path = _lock_path(root, scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_s

    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise WriterBusy(scope, _read_holder(path))
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            continue
        break

    mine = {"scope": scope, "actor": actor or f"pid:{os.getpid()}",
            "pid": os.getpid(), "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "stale_after_s": stale_after_s, "lock_id": uuid.uuid4().hex}
    # On a write/fsync failure retain the record (possibly incomplete), so
    # another process cannot interpret an uncertain acquisition as unlocked.
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(mine, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        yield mine
    finally:
        holder = _read_holder(path)
        if holder.get("lock_id") == mine["lock_id"]:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
