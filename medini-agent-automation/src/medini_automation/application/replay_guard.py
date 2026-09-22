"""application.replay_guard — 控制面写入的防重复机制（P3）。

规划原文（`A_核心规划.md` L114）：

> 同一幂等键与同一载荷返回既有作业；同一键配不同载荷拒绝。

这里防的是两类**不同**的重复，混作一谈会写出既漏又误杀的实现：

| 机制 | 防的是 | 语义 |
|---|---|---|
| `NonceStore` | **审批凭证重放** —— 同一份批准被用第二次 | 消耗式：用过即废 |
| `IdempotencyStore` | **写入请求重发** —— Agent/网络重试同一个请求 | 同键同载荷 → 返回既有结果；同键异载荷 → 拒绝 |

关键区别：重放（replay）**不是错误**。第二次带同一幂等键来，说明调用方在重试，
正确反应是把第一次的结果原样还给它（并标 `replayed: true`），而不是报错或再写一遍。
但"同一键 + 不同载荷"是另一回事 —— 那是调用方把键用错了，必须拒绝。

原子性：两处都用 ``os.open(..., O_CREAT | O_EXCL)`` 做**跨进程**原子创建。
``threading.Lock`` 在这里没用 —— 同一个键可能来自 MCP server 进程、CLI 进程、
另一个 DSH 会话，进程内锁一个都拦不住。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "NonceVerdict", "NonceStore", "IdempotencyVerdict", "IdempotencyStore",
    "new_idempotency_key",
]


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _slug(value: str) -> str:
    """任意字符串 → 稳定、跨平台安全的文件名（避免路径穿越与大小写问题）。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:40]


def _write_exclusive(path: Path, body: dict[str, Any]) -> bool:
    """原子创建。已存在返回 False（不抛）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    try:
        os.write(fd, json.dumps(body, ensure_ascii=False,
                                separators=(",", ":")).encode("utf-8"))
    finally:
        os.close(fd)
    return True


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                       # noqa: BLE001 — 损坏的记录不该让主流程崩
        return None


# ============================================================ 审批 nonce
@dataclass(frozen=True)
class NonceVerdict:
    state: str                 # "fresh" | "replay"
    first_used_at: str = ""
    first_used_by: str = ""
    conflicting_scope: str = ""

    @property
    def is_replay(self) -> bool:
        return self.state == "replay"


class NonceStore:
    """审批凭证的一次性消费。目录：``<root>/nonces``。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.dir = self.root / "nonces"

    def _path(self, nonce: str) -> Path:
        return self.dir / f"{_slug(nonce)}.json"

    def peek(self, nonce: str) -> NonceVerdict:
        """只查不消费（用于在真正落盘前预检）。"""
        rec = _read_json(self._path(nonce))
        if rec is None:
            return NonceVerdict("fresh")
        return NonceVerdict("replay", str(rec.get("at", "")),
                            str(rec.get("actor", "")),
                            str(rec.get("scope", "")))

    def consume(self, nonce: str, *, actor: str = "",
                scope: str = "") -> NonceVerdict:
        """消费。首次返回 fresh；重复返回 replay（并带上首次的使用者）。

        原子：O_CREAT|O_EXCL 保证两个进程同时消费只有一个拿到 fresh。
        """
        body = {"at": _now(), "actor": actor, "scope": scope,
                "nonce_prefix": nonce[:12]}
        if _write_exclusive(self._path(nonce), body):
            return NonceVerdict("fresh", body["at"], actor, scope)
        rec = _read_json(self._path(nonce)) or {}
        return NonceVerdict("replay", str(rec.get("at", "")),
                            str(rec.get("actor", "")),
                            str(rec.get("scope", "")))


# ============================================================ 幂等键
@dataclass(frozen=True)
class IdempotencyVerdict:
    state: str                        # "new" | "replay" | "conflict"
    record: dict[str, Any] | None = None
    detail: str = ""

    @property
    def is_replay(self) -> bool:
        return self.state == "replay"


def new_idempotency_key() -> str:
    return "idem-" + secrets.token_hex(12)


class IdempotencyStore:
    """写入请求幂等。目录：``<root>/idempotency``。

    记录的是 ``payload_hash``（本次写入意图的摘要）与首次执行的**完整返回值**。
    重放时把原结果还回去 —— 调用方拿到的与第一次逐字节一致，不需要自己判断
    "这次到底写没写进去"。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.dir = self.root / "idempotency"

    def _path(self, key: str) -> Path:
        return self.dir / f"{_slug(key)}.json"

    def lookup(self, key: str, payload_hash: str) -> IdempotencyVerdict:
        rec = _read_json(self._path(key))
        if rec is None:
            return IdempotencyVerdict("new")
        if rec.get("payload_hash") != payload_hash:
            return IdempotencyVerdict(
                "conflict", rec,
                detail=(f"幂等键 {key!r} 已用于载荷 "
                        f"{str(rec.get('payload_hash'))[:16]}…，"
                        f"本次载荷是 {payload_hash[:16]}…"))
        return IdempotencyVerdict("replay", rec)

    def record(self, key: str, payload_hash: str, operation: str,
               result: dict[str, Any], *, actor: str = "") -> None:
        body = {"key": key, "payload_hash": payload_hash,
                "operation": operation, "at": _now(), "actor": actor,
                "result": result}
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2),
                        encoding="utf-8")
