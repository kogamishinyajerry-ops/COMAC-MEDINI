"""worker.job — 作业状态机（公共契约 §5 状态表）。

received → validated → awaiting_approval → queued → running → readback → verified
分支：blocked / failed / cancelled / timed_out。
约束：
- 状态只能沿主链前进或进入分支终态；
- 取消请求 ≠ 进程已停止：无法确认原生计算终止时保持 running 并标注。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

MAIN_CHAIN = [
    "received", "validated", "awaiting_approval", "queued",
    "running", "readback", "verified",
]
BRANCH_STATES = ["blocked", "failed", "cancelled", "timed_out"]
ALL_STATES = MAIN_CHAIN + BRANCH_STATES


class InvalidTransition(RuntimeError):
    pass


@dataclass
class Job:
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: str = "run_analysis"          # run_analysis | dry_run | probe
    state: str = "received"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    idempotency_key: str = ""
    payload_digest: str = ""            # 幂等键绑定的载荷摘要
    change_id: str = ""
    notes: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)  # name -> abs path

    def transition(self, new_state: str, note: str = "") -> None:
        if new_state not in ALL_STATES:
            raise InvalidTransition(f"UNKNOWN_STATE: {new_state}")
        # 主链只允许前进
        if new_state in MAIN_CHAIN:
            if MAIN_CHAIN.index(new_state) < MAIN_CHAIN.index(self.state):
                raise InvalidTransition(
                    f"BACKWARD: {self.state} -> {new_state}")
        elif new_state in BRANCH_STATES and self.state == "verified":
            raise InvalidTransition(f"TERMINAL: verified -> {new_state}")
        self.state = new_state
        self.updated_at = time.time()
        if note:
            self.notes.append(f"[{new_state}] {note}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        return path


class JobStore:
    """极简文件作业库：jobs/<job_id>.json。单实例单写者，无并发承诺。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, job: Job) -> Path:
        return job.write(self.root / f"{job.job_id}.json")

    def load(self, job_id: str) -> Job:
        p = self.root / f"{job_id}.json"
        if not p.exists():
            raise KeyError(f"JOB_NOT_FOUND: {job_id}")
        data = json.loads(p.read_text(encoding="utf-8"))
        j = Job(**{k: v for k, v in data.items() if k in Job.__dataclass_fields__})
        return j

    def find_by_idempotency(self, key: str, payload_digest: str) -> Job | None:
        """同键同载荷 → 返回既有作业；同键不同载荷 → 冲突（调用方拒绝）。"""
        for p in sorted(self.root.glob("*.json")):
            try:
                j = self.load(p.stem)
            except Exception:
                continue
            if j.idempotency_key == key:
                if j.payload_digest == payload_digest:
                    return j
                raise KeyError(
                    f"IDEMPOTENCY_CONFLICT: key={key} 已绑定不同载荷，拒绝执行")
        return None
