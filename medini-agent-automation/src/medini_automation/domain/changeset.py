"""domain.changeset — 受控变更对象（公共契约 §5）。

ChangeSet 至少含 change_id、project_id、expected_baseline_hash、patch_hash、
操作列表、理由、来源、风险标记和所需权限。执行前核对基线与补丁哈希；
重试使用 idempotency_key；未知执行结果先回读，不盲目再次写入。
Agent 输入 approved=true 不是批准凭证（审批来自受信任身份层）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

VALID_STATES = (
    "received", "validated", "awaiting_approval", "queued", "running",
    "readback", "verified", "failed", "cancelled", "timed_out", "blocked",
)
TERMINAL_STATES = ("verified", "failed", "cancelled", "timed_out")


@dataclass
class ChangeOp:
    """最小操作集：add_event / add_gate / set_probability。"""
    op: str                 # "add_event" | "add_gate" | "set_probability"
    args: dict[str, Any]

    def __post_init__(self) -> None:
        if self.op not in ("add_event", "add_gate", "set_probability"):
            raise ValueError(f"UNSUPPORTED_OP: {self.op}")


@dataclass
class ApprovalRef:
    """审批引用——只承认受信任会话/身份层签发，含审批人、时间、凭据指纹。

    P3 起这是**已验签凭证**的投影：``signed=True`` 表示它来自
    :func:`medini_automation.domain.approval.verify_attestation` 的通过结果，
    而不是调用方手写的四个字段。

    本对象**不做密码学校验**（那需要信任根与公钥，属于应用层职责），
    只承载校验结论。`ChangeSet.validate_for_apply` 因此只做结构检查 ——
    真正拒绝未签名凭证的地方是 ``agent_api.apply_change``。
    """
    approver: str            # 受信身份（工号/姓名）
    approved_at: str         # ISO8601（签发时刻）
    credential_fingerprint: str  # 受信会话凭据指纹（如 SSO token hash）
    scope: str               # 批准范围（change_id 绑定）
    # ---- P3 新增：签名凭证的完整投影（默认值保证旧构造点仍可用）----
    expires_at: str = ""     # ISO8601，超过即失效
    permission: str = "model_write"
    nonce: str = ""          # 一次性，防重放
    patch_hash: str = ""     # 签名绑定的补丁摘要（批准的是**内容**不是 change_id）
    expected_baseline_hash: str = ""
    signed: bool = False     # True = 经 Ed25519 验签通过
    trust_source: str = ""   # 该授权人来自哪个信任根（审计用）

    @classmethod
    def from_verified(cls, verdict: dict[str, Any]) -> "ApprovalRef":
        """从 `verify_attestation` 的返回构造。"""
        return cls(
            approver=verdict["approver"], approved_at=verdict["approved_at"],
            credential_fingerprint=verdict.get("credential_fingerprint", ""),
            scope=verdict["scope"], expires_at=verdict.get("expires_at", ""),
            permission=verdict.get("permission", "model_write"),
            nonce=verdict.get("nonce", ""),
            patch_hash=verdict.get("patch_hash", ""),
            expected_baseline_hash=verdict.get("expected_baseline_hash", ""),
            signed=bool(verdict.get("signed")),
            trust_source=verdict.get("trust_source", ""))


@dataclass
class ChangeSet:
    change_id: str
    project_id: str
    expected_baseline_hash: str
    ops: list[ChangeOp] = field(default_factory=list)
    reason: str = ""
    source: str = ""          # 变更来源（需求文档/人工指令）
    risk_flags: list[str] = field(default_factory=list)
    required_permission: str = "model_write"  # 只读/模型修改/执行分析/发布按角色分开
    idempotency_key: str = ""
    approval: ApprovalRef | None = None       # None = awaiting_approval
    state: str = "received"

    def __post_init__(self) -> None:
        if self.state not in VALID_STATES:
            raise ValueError(f"INVALID_STATE: {self.state}")
        if not self.idempotency_key:
            # 默认幂等键 = 载荷哈希（同载荷同键=同一作业；不同载荷拒绝）
            self.idempotency_key = "auto-" + self.patch_hash()[:16]

    def ops_canon(self) -> str:
        return json.dumps(
            [{"op": o.op, "args": _canon_args(o.args)} for o in self.ops],
            sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def patch_hash(self) -> str:
        blob = json.dumps({
            "project": self.project_id, "ops": json.loads(self.ops_canon()),
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def validate_for_apply(self) -> list[str]:
        """执行前置条件检查。返回阻塞原因列表（空=可执行）。"""
        problems: list[str] = []
        if self.approval is None:
            problems.append("NO_APPROVAL: 变更未经受信任身份批准，禁止写入工作副本")
        else:
            expected = f"change:{self.change_id}"
            if self.approval.scope != expected:
                problems.append(
                    f"APPROVAL_SCOPE_MISMATCH: 批准范围 {self.approval.scope!r} "
                    f"未绑定本变更 {expected!r}")
        if not self.ops:
            problems.append("EMPTY_OPS: 空操作列表")
        return problems


def _canon_args(args: dict[str, Any]) -> dict[str, Any]:
    """概率统一为字符串（Fraction 可序列化且规范）。"""
    out = {}
    for k, v in args.items():
        if isinstance(v, Fraction):
            out[k] = str(v)
        else:
            out[k] = v
    return out


from fractions import Fraction  # noqa: E402  (dataclass 字段类型引用)
