"""domain.approval — 受信任身份层签发的审批凭证（P3）。

规划原文（`A_核心规划.md` L112）：

> 每个写入请求带 expected_baseline_hash、idempotency_key、patch_hash。
> **审批由受信任的人机界面生成，服务端校验授权人、权限、范围与有效期；
> Agent 不能通过传入"approved=true"自批。**

本模块就是那句"服务端校验"的实现。三个设计决定：

1. **凭证必须签名**（Ed25519，见 `domain/ed25519.py`）。上一版只检查
   「字段齐全 + scope 字符串相等」—— 那等于让 Agent 自己写四个字段就通过，
   与规划要求相反。签名把"伪造"从"随便填字段"提到"必须拿到私钥"。
2. **签名载荷绑定 patch_hash 与 expected_baseline_hash**，不只是 change_id。
   只绑 change_id 的话，批准的是一张"空头支票"：change_id 不变、内容换掉，
   scope 照样"匹配"。绑定内容摘要后，换内容即验签失败。
3. **信任根缺失 = fail-closed**。`<MEDINI_APPROVAL_HOME>/trust.json` 不存在或
   解析失败时，**拒绝一切审批**，而不是"没有配置就放行"。信任根放在仓库外
   （默认 `~/.medini-approval`），不在版本控制里 —— 私钥与授权人清单都不该进仓。

**边界（诚实声明）**：这仍不是身份基础设施。它做到的是"服务端可校验的凭证"，
而**私钥保护强度取决于密钥存放位置**。在本机单用户场景下，一个有完整文件权限
的进程理论上能读到私钥；真正的隔离（HSM、独立审批机、SSO）不在本仓职责内。
本模块的价值是：默认配置下 Agent **没有**任何可用的私钥，且每次批准都被签名、
可审计、一次性、有有效期。
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import ed25519

__all__ = [
    "ATTESTATION_VERSION", "PERMISSIONS", "DEFAULT_PERMISSION",
    "ApprovalError", "ApprovalContext", "TrustEntry", "TrustRoot",
    "approval_home", "trust_path", "load_trust_root",
    "approval_payload", "issue_attestation", "verify_attestation",
    "verify_signature_only", "new_nonce",
]

ATTESTATION_VERSION = 1
PERMISSIONS = ("model_write", "analysis_run", "evidence_publish", "admin")
DEFAULT_PERMISSION = "model_write"

#: 签名必须覆盖的字段（``signature`` 自身除外）。顺序无关 —— 载荷走 canonical JSON。
SIGNED_FIELDS = (
    "v", "approver", "approved_at", "expires_at", "scope", "project_id",
    "patch_hash", "expected_baseline_hash", "permission", "nonce",
    "credential_fingerprint",
)
#: 其中「必须有值」的字段。``credential_fingerprint`` 是审计信息，允许为空串
#: （但它仍参与签名，所以被改一样验不过）。
_REQUIRED_FIELDS = tuple(f for f in SIGNED_FIELDS if f != "credential_fingerprint")


class ApprovalError(Exception):
    """审批校验失败。``code`` 是稳定标识，供 agent_api 直接转结构化返回。"""

    def __init__(self, code: str, message: str, *,
                 hint: str = "", recovery: str = "") -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.hint = hint
        self.recovery = recovery


@dataclass(frozen=True)
class ApprovalContext:
    """服务端期望值 —— 凭证里的对应字段必须逐项相等。"""
    change_id: str
    project_id: str
    patch_hash: str
    expected_baseline_hash: str
    required_permission: str = DEFAULT_PERMISSION


@dataclass(frozen=True)
class TrustEntry:
    identity: str
    public_key: str                      # hex，32 字节
    permissions: tuple[str, ...] = (DEFAULT_PERMISSION,)
    note: str = ""


@dataclass(frozen=True)
class TrustRoot:
    """授权人清单。空 = fail-closed。"""
    entries: dict[str, TrustEntry] = field(default_factory=dict)
    max_validity_seconds: int = 3600
    source: str = ""

    def get(self, identity: str) -> TrustEntry | None:
        return self.entries.get(identity)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "max_validity_seconds": self.max_validity_seconds,
            "approvers": [
                {"identity": e.identity, "public_key": e.public_key,
                 "permissions": list(e.permissions), "note": e.note}
                for e in sorted(self.entries.values(), key=lambda x: x.identity)
            ],
        }


# --------------------------------------------------------------- 信任根读取
def approval_home() -> Path:
    """信任根目录。环境变量在**调用时**读（便于测试隔离与多身份切换）。"""
    env = os.environ.get("MEDINI_APPROVAL_HOME")
    return Path(env) if env else Path.home() / ".medini-approval"


def trust_path() -> Path:
    return approval_home() / "trust.json"


def load_trust_root(path: Path | None = None) -> TrustRoot:
    """读信任根。**任何异常都退化为空 TrustRoot**（fail-closed，绝不抛给调用方）。

    解析失败时把原因写进 ``source``，让 `cli trust` 能显示"为什么所有审批都被拒"。
    """
    p = path or trust_path()
    if not p.exists():
        return TrustRoot(source=f"缺失: {p}（fail-closed：拒绝一切审批）")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:                       # noqa: BLE001 — 有意全兜
        return TrustRoot(source=f"解析失败: {p}: {type(exc).__name__}: {exc}")
    entries: dict[str, TrustEntry] = {}
    try:
        for row in raw.get("approvers") or []:
            ident = str(row["identity"])
            entries[ident] = TrustEntry(
                identity=ident,
                public_key=str(row["public_key"]).lower(),
                permissions=tuple(row.get("permissions") or (DEFAULT_PERMISSION,)),
                note=str(row.get("note", "")),
            )
    except Exception as exc:                       # noqa: BLE001
        return TrustRoot(source=f"结构错误: {p}: {type(exc).__name__}: {exc}")
    return TrustRoot(
        entries=entries,
        max_validity_seconds=int(raw.get("max_validity_seconds", 3600)),
        source=str(p))


# ------------------------------------------------------------------- 载荷
def approval_payload(attestation: dict[str, Any]) -> bytes:
    """签名/验签用的规范化字节串：canonical JSON，覆盖全部签名字段。

    对缺字段**不做宽容** —— 缺字段的载荷与「提供该字段但值为空」不同，
    这里直接让 `json.dumps` 报错，由调用方在校验链的更早步骤拦下。
    """
    body = {k: attestation[k] for k in SIGNED_FIELDS if k in attestation}
    missing = [k for k in SIGNED_FIELDS if k not in attestation]
    if missing:
        raise ValueError(f"载荷缺字段 {missing}")
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def new_nonce() -> str:
    return secrets.token_hex(16)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ApprovalError("APPROVAL_INCOMPLETE", f"{field_name} 不是时间字符串")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApprovalError(
            "APPROVAL_TIMESTAMP", f"{field_name}={value!r} 不是合法 ISO8601: {exc}",
            hint="用带时区的 ISO8601，如 2026-09-21T20:00:00+08:00") from exc
    if dt.tzinfo is None:
        raise ApprovalError(
            "APPROVAL_TIMESTAMP", f"{field_name}={value!r} 未带时区",
            hint="必须显式带时区（否则有效期无法判定）")
    return dt.astimezone(timezone.utc)


# ------------------------------------------------------------------- 签发
def issue_attestation(*, seed: bytes, approver: str, context: ApprovalContext,
                      permission: str | None = None,
                      validity_seconds: int = 1800,
                      credential_fingerprint: str = "",
                      nonce: str | None = None,
                      now: datetime | None = None) -> dict[str, Any]:
    """签发一份审批凭证（**受信任人机界面**侧调用，不是 Agent 侧）。

    ``validity_seconds`` 必须为正；上限由信任根的 ``max_validity_seconds``
    在**校验侧**强制（签发侧不设限但超限会被拒，避免两处规则漂移）。
    """
    if validity_seconds <= 0:
        raise ValueError("validity_seconds 必须为正")
    if not approver:
        raise ValueError("approver 不能为空")
    if permission is not None and permission not in PERMISSIONS:
        raise ValueError(f"未知权限 {permission!r}，合法值 {list(PERMISSIONS)}")
    t0 = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    att: dict[str, Any] = {
        "v": ATTESTATION_VERSION,
        "approver": approver,
        "approved_at": _iso(t0),
        "expires_at": _iso(t0 + timedelta(seconds=validity_seconds)),
        "scope": f"change:{context.change_id}",
        "project_id": context.project_id,
        "patch_hash": context.patch_hash,
        "expected_baseline_hash": context.expected_baseline_hash,
        "permission": permission or context.required_permission,
        "nonce": nonce or new_nonce(),
        "credential_fingerprint": credential_fingerprint,
    }
    att["signature"] = ed25519.sign(seed, approval_payload(att)).hex()
    return att


# --------------------------------------------------------------- 校验链
def verify_attestation(attestation: dict[str, Any], *, context: ApprovalContext,
                       root: TrustRoot | None = None,
                       now: datetime | None = None) -> dict[str, Any]:
    """完整校验链。通过返回 ``ApprovalRef`` 的 dict 形式；失败抛 `ApprovalError`。

    顺序是**从便宜到贵**（字段 → 时间 → 绑定 → 身份 → 验签），且每步给精确 code，
    便于调用方知道"到底卡在哪一环"而不是笼统的"审批无效"。

    nonce 的一次性消费**不在这里**做：它必须与基线落盘在同一临界区，否则
    校验与消费之间会留下重放窗口。见 `application/agent_api.py::apply_change`。
    """
    if not isinstance(attestation, dict) or not attestation:
        raise ApprovalError(
            "NO_APPROVAL", "变更未经受信任身份批准，禁止写入工作副本",
            hint=("需要受信任审批面板签发的凭证（含 signature）；"
                  "Agent 自述的批准（如 approved=true）不是批准凭证"),
            recovery="medini_prepare_change")

    missing = [k for k in _REQUIRED_FIELDS if not attestation.get(k)]
    absent = [k for k in SIGNED_FIELDS if k not in attestation]
    if "signature" not in attestation or not attestation.get("signature"):
        raise ApprovalError(
            "APPROVAL_UNSIGNED",
            "审批凭证没有签名 —— 上一版只查字段齐全，那等于允许 Agent 自批",
            hint=("用审批面板签发：cli approve --key-file <私钥> "
                  "--change-id <id>；签名覆盖全部字段"),
            recovery="medini_prepare_change")
    if missing or absent:
        raise ApprovalError(
            "APPROVAL_INCOMPLETE",
            f"审批凭证缺字段 {sorted(set(missing) | set(absent))}",
            hint=f"必需字段：{', '.join(SIGNED_FIELDS)}",
            recovery="medini_prepare_change")

    if attestation["v"] != ATTESTATION_VERSION:
        raise ApprovalError(
            "APPROVAL_VERSION",
            f"凭证版本 {attestation['v']!r} 不受支持（当前 {ATTESTATION_VERSION}）",
            hint="用与当前适配器同版本的工具签发", recovery="medini_prepare_change")

    permission = attestation["permission"]
    if permission not in PERMISSIONS:
        raise ApprovalError(
            "APPROVAL_PERMISSION_UNKNOWN",
            f"未知权限 {permission!r}，合法值 {list(PERMISSIONS)}",
            recovery="medini_prepare_change")

    # ---- 有效期 ----
    approved_at = _parse_ts(attestation["approved_at"], "approved_at")
    expires_at = _parse_ts(attestation["expires_at"], "expires_at")
    t_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires_at <= approved_at:
        raise ApprovalError(
            "APPROVAL_TIMESTAMP", "expires_at 不晚于 approved_at",
            recovery="medini_prepare_change")
    if t_now < approved_at:
        raise ApprovalError(
            "APPROVAL_NOT_YET_VALID",
            f"凭证尚未生效（approved_at {_iso(approved_at)} 在未来）",
            hint="检查审批机与本机的时钟是否一致", recovery="medini_prepare_change")
    if t_now >= expires_at:
        raise ApprovalError(
            "APPROVAL_EXPIRED",
            f"凭证已于 {_iso(expires_at)} 过期（当前 {_iso(t_now)}）",
            hint="重新走一次审批 —— 过期是有意的：批准针对「当下这个基线」",
            recovery="medini_prepare_change")

    # ---- 绑定项（强绑定内容，不只是 change_id）----
    want_scope = f"change:{context.change_id}"
    if attestation["scope"] != want_scope:
        raise ApprovalError(
            "APPROVAL_SCOPE_MISMATCH",
            f"批准范围 {attestation['scope']!r} 未绑定本变更 {want_scope!r}",
            hint=f"重新按本变更签发：scope 必须是 {want_scope}",
            recovery="medini_prepare_change")
    if attestation["project_id"] != context.project_id:
        raise ApprovalError(
            "APPROVAL_PROJECT_MISMATCH",
            f"批准针对工程 {attestation['project_id']!r}，本变更属于 "
            f"{context.project_id!r}", recovery="medini_prepare_change")
    if attestation["patch_hash"] != context.patch_hash:
        raise ApprovalError(
            "APPROVAL_PATCH_MISMATCH",
            ("批准绑定的补丁摘要与变更单不符 —— 批准的是**具体内容**，"
             "不是一张可复用的空头支票"),
            hint="变更内容在批准后被动过：重新提案 + 重新审批",
            recovery="medini_prepare_change")
    if attestation["expected_baseline_hash"] != context.expected_baseline_hash:
        raise ApprovalError(
            "APPROVAL_BASELINE_MISMATCH",
            "批准绑定的基线哈希与变更单不符",
            hint="重新提案 + 重新审批", recovery="medini_prepare_change")

    # ---- 授权人与权限 ----
    trust = root if root is not None else load_trust_root()
    if not trust.entries:
        raise ApprovalError(
            "TRUST_ROOT_MISSING",
            f"信任根为空或不可用（{trust.source or trust_path()}）—— fail-closed："
            "拒绝一切审批",
            hint=("在受信任目录放 trust.json："
                  '{"approvers":[{"identity":"...","public_key":"<hex32>",'
                  '"permissions":["model_write"]}]}；'
                  "或设 MEDINI_APPROVAL_HOME 指向它"),
            recovery="medini_prepare_change")
    entry = trust.get(attestation["approver"])
    if entry is None:
        raise ApprovalError(
            "APPROVAL_UNKNOWN_APPROVER",
            f"授权人 {attestation['approver']!r} 不在信任根中（唯一合法来源："
            f"{trust.source}）",
            hint=f"信任根现有授权人：{sorted(trust.entries)}",
            recovery="medini_prepare_change")
    if permission not in entry.permissions and "admin" not in entry.permissions:
        raise ApprovalError(
            "APPROVAL_PERMISSION_DENIED",
            f"{entry.identity} 的权限 {list(entry.permissions)} 不含 "
            f"{permission!r}",
            recovery="medini_prepare_change")
    if context.required_permission not in entry.permissions and \
            "admin" not in entry.permissions:
        raise ApprovalError(
            "APPROVAL_PERMISSION_DENIED",
            f"本变更要求 {context.required_permission!r}，"
            f"{entry.identity} 只有 {list(entry.permissions)}",
            recovery="medini_prepare_change")

    # ---- 有效期跨度上限（在信任根侧统一强制，避免两处规则漂移）----
    span = int((expires_at - approved_at).total_seconds())
    if span > trust.max_validity_seconds:
        raise ApprovalError(
            "APPROVAL_VALIDITY_TOO_LONG",
            f"凭证有效期 {span}s 超过信任根上限 "
            f"{trust.max_validity_seconds}s",
            hint="有效期越长，被盗用的窗口越大", recovery="medini_prepare_change")

    # 签名覆盖全部字段 → 任何一处被改都会在这里失败
    try:
        payload = approval_payload(attestation)
    except ValueError as exc:
        raise ApprovalError("APPROVAL_INCOMPLETE", str(exc),
                            recovery="medini_prepare_change") from exc
    try:
        pk = bytes.fromhex(entry.public_key)
    except ValueError as exc:
        raise ApprovalError(
            "TRUST_ROOT_INVALID_KEY",
            f"信任根里 {entry.identity} 的公钥不是合法 hex: {entry.public_key!r}",
            hint=f"修 {trust.source}") from exc
    try:
        sig = bytes.fromhex(attestation["signature"])
    except ValueError as exc:
        raise ApprovalError(
            "APPROVAL_SIGNATURE_INVALID", "signature 不是合法 hex",
            recovery="medini_prepare_change") from exc
    if not ed25519.verify(pk, sig, payload):
        raise ApprovalError(
            "APPROVAL_SIGNATURE_INVALID",
            f"签名验证失败（授权人 {entry.identity}）—— 凭证被篡改或并非由其签发",
            hint="重新签发；不要手工编辑凭证 JSON", recovery="medini_prepare_change")

    return {
        "approver": entry.identity,
        "approved_at": attestation["approved_at"],
        "expires_at": attestation["expires_at"],
        "scope": attestation["scope"],
        "permission": permission,
        "nonce": attestation["nonce"],
        "credential_fingerprint": attestation["credential_fingerprint"],
        "patch_hash": attestation["patch_hash"],
        "expected_baseline_hash": attestation["expected_baseline_hash"],
        "signed": True,
        "trust_source": trust.source,
    }


def verify_signature_only(attestation: dict[str, Any], *,
                          root: TrustRoot | None = None) -> tuple[bool, str]:
    """只验「签名 + 授权人身份」，**不查**有效期 / 绑定 / nonce。

    用途：幂等重放时确认调用方手里还是那份**真凭证**。

    为什么重放时不跑完整校验链：重放针对的是"那个**已经完成**的作业"，
    首次执行时完整链已经跑过。若重放还要求有效期与 nonce 有效，就会出现
    "同一幂等键重试却因凭证过期失败"—— 与规划 L114「同一幂等键与同一载荷
    返回既有作业」直接冲突。

    但**验签仍要做**：否则一份被篡改的凭证会走上重放路径并拿到 `ok`，
    给调用方"这份凭证有效"的错误信号（实测抓到过这个漏洞）。
    """
    if not isinstance(attestation, dict) or not attestation.get("signature"):
        return False, "APPROVAL_UNSIGNED: 凭证没有签名"
    missing = [k for k in SIGNED_FIELDS if k not in attestation]
    if missing:
        return False, f"APPROVAL_INCOMPLETE: 缺字段 {missing}"
    trust = root if root is not None else load_trust_root()
    if not trust.entries:
        return False, f"TRUST_ROOT_MISSING: {trust.source}"
    entry = trust.get(str(attestation.get("approver") or ""))
    if entry is None:
        return False, (f"APPROVAL_UNKNOWN_APPROVER: "
                       f"{attestation.get('approver')!r} 不在信任根中")
    try:
        pk = bytes.fromhex(entry.public_key)
        sig = bytes.fromhex(attestation["signature"])
        payload = approval_payload(attestation)
    except ValueError as exc:
        return False, f"APPROVAL_SIGNATURE_INVALID: {exc}"
    if not ed25519.verify(pk, sig, payload):
        return False, f"APPROVAL_SIGNATURE_INVALID: 签名验证失败（{entry.identity}）"
    return True, ""
