"""application.agent_api — 受控操作接口层（长期规划 §A_核心规划.md L101-108）。

九个操作，全部返回 **JSON-ready dict**：不打印、不 sys.exit、不依赖 CLI 参数解析。
CLI 与 DSH/MCP 都只是这一层的薄封装（规划要求：先形成可脚本调用的 CLI/服务，
再按真实 DSH 版本封装工具，不重写 Harness）。

| 操作 | 规划名 | 语义 |
|---|---|---|
| :func:`get_capabilities` | medini_get_capabilities | 操作级能力矩阵 + worker 环境自检 |
| :func:`read_project` | medini_read_project | 只读快照 + 原生 ID 映射 + 映射损失 |
| :func:`prepare_change` | medini_prepare_change | 提案摘要 / diff / 校验 / patch_hash |
| :func:`apply_change` | medini_apply_change | 在**副本**实施；绑定基线 + 补丁 + 版本 |
| :func:`run_analysis` | medini_run_analysis | 批准模型哈希门禁 → job_id |
| :func:`get_job` | medini_get_job | 状态 / 耗时 / 错误 / 恢复建议 |
| :func:`readback` | medini_readback | 回读原生模型与结果并比较 |
| :func:`export_evidence` | medini_export_evidence | 文件清单 + 哈希 + 结果（**≠正式签发**） |
| :func:`reopen_check` | （P1 强校核变体） | 保存→重开→回读四重校核 |

纪律（对应公共契约与 A_核心规划.md「写入与运行的硬约束」）
------------------------------------------------------------
1. **受控 project_id**：只有 :data:`PROJECTS` 白名单内的工程可访问；只有
   ``writable=True`` 的工程可写。未登记工程一律 ``status=blocked``，并回白名单。
2. **基线绑定**：任何变更/分析都绑定当前基线哈希。``expected_baseline_hash``
   不匹配 → ``BASELINE_MISMATCH``。基线的唯一建立途径是 ``prepare_change``
   首次调用（显式传 ``expected_baseline_hash=None``），之后必须传真实哈希。
3. **审批门禁（P3 升级为可验证凭证）**：``apply_change`` 只承认**受信任审批面板
   用 Ed25519 签名**的凭证（见 :mod:`medini_automation.domain.approval`）。
   服务端逐项校验：字段齐全 → 有效期（未生效/过期都拒）→ scope 绑定 →
   **patch_hash 与基线哈希强绑定内容** → 授权人在信任根内 → 权限足够 →
   验签 → nonce 未用过。**Agent 传 approved=true 不是批准凭证**——本层根本不
   接受布尔批准字段，也不接受"字段齐全但没签名"的自述凭证。
   信任根（``<MEDINI_APPROVAL_HOME>/trust.json``，默认 ``~/.medini-approval``）
   缺失时 **fail-closed：拒绝一切审批**，而不是"没配置就放行"。
4. **幂等**：同一幂等键 + 同一载荷 → 返回既有结果并标 ``replayed=True``；
   同一键 + 不同载荷 → ``IDEMPOTENCY_CONFLICT``。重放不是错误，是调用方在重试。
5. **单写者**：写入前对 ``<project_id>/<case>`` 取文件锁（跨进程，不是
   ``threading.Lock``）。拿不到 → ``WRITER_BUSY`` 并附当前持有者。
6. **只写工作副本**：``apply_change`` 拒绝 ``writable=False`` 的工程；原始工程
   永不被覆盖。
7. **无占位**：不可用/未实现一律 ``status=blocked`` 并列出具体缺失因子；
   不允许 success 占位。

原生 ↔ 契约 的映射损失是**实测观察**（见 ``read_project`` 返回的
``mapping_loss``），不是推测：medini 的 ``.fta`` 把门输出也建模成 ``<events>``、
``<gates>`` 省略默认 ``kind``（AND）、``xmi:id`` 是导入时分配的易变标识。
"""
from __future__ import annotations

import functools
import hashlib
import json
import shutil
import socket
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..adapters.fta_diagram import FtaParseError, parse_fta
from ..adapters.medini_cli import (
    DEFAULT_PROJECT as _SOURCE_PROJECT,
    MEDINI_EXE_DEFAULT,
    WORKCOPY_PROJECT_DEFAULT,
    WORKCOPY_PROJ_NAME,
)
from ..domain.approval import (
    ApprovalContext,
    ApprovalError,
    load_trust_root,
    verify_attestation,
    verify_signature_only,
)
from ..domain.capability import initial_capabilities
from ..domain.changeset import ApprovalRef, ChangeOp, ChangeSet
from ..domain.model import ContractError, from_contract_json
from ..worker.job import JobStore
from .replay_guard import IdempotencyStore, NonceStore
from .writer_lock import WriterBusy, read_lock_holder, writer_lock
from .. import __version__

__all__ = [
    "AgentApiError", "ProjectEntry", "PROJECTS", "default_projects",
    "RUNS_ROOT", "STATE_ROOT",
    "get_capabilities", "read_project", "prepare_change", "apply_change",
    "run_analysis", "get_job", "readback", "export_evidence", "reopen_check",
    "recovery_log", "clear_recovery", "idempotency_record",
]

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: 证据包 / 作业库根（与 slice.py / persistence.py 的 out_root 约定一致）
RUNS_ROOT = _REPO_ROOT / "runs"
#: 本层自己的状态（基线、变更单、bundles）
STATE_ROOT = RUNS_ROOT / "agent"

CONTRACTS_VERSION = "0.1.0"

#: 状态取值：ok = 完成；blocked = 前置条件缺失（可恢复，附 hint）；error = 缺陷/异常
_OK, _BLOCKED, _ERROR = "ok", "blocked", "error"


class AgentApiError(RuntimeError):
    """编程性错误（非法参数、状态损坏）。业务性拒绝用 status=blocked 返回。"""

    def __init__(self, code: str, message: str,
                 hint: str = "", recovery: str = "") -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message
        self.hint, self.recovery = hint, recovery


# --------------------------------------------------------------- 受控工程
@dataclass(frozen=True)
class ProjectEntry:
    project_id: str
    path: Path
    writable: bool
    kind: str = "medini-project"
    note: str = ""


def default_projects() -> dict[str, ProjectEntry]:
    """受控工程白名单。

    可写集合**只有**本仓工作副本——对应纪律红线 §5「原始工程不直接覆盖」。
    源工程以 ``writable=False`` 登记，用于只读快照与 ID 映射比对。
    """
    return {
        WORKCOPY_PROJ_NAME: ProjectEntry(
            project_id=WORKCOPY_PROJ_NAME,
            path=WORKCOPY_PROJECT_DEFAULT,
            writable=True,
            kind="workcopy",
            note="本仓工作副本（P0/P1/P1.5 写入目标；唯一可写工程）",
        ),
        "SRC-F2244-C01": ProjectEntry(
            project_id="SRC-F2244-C01",
            path=_SOURCE_PROJECT,
            writable=False,
            kind="source-project",
            note="既有工程，只读（首期不做更新既有工程，见 EV-UPDATE-NONE）",
        ),
    }


#: 模块级白名单，测试/部署可替换（:func:`default_projects` 为出厂值）
PROJECTS: dict[str, ProjectEntry] = default_projects()


def _entry(project_id: str) -> ProjectEntry:
    e = PROJECTS.get(project_id)
    if e is None:
        raise AgentApiError(
            "PROJECT_NOT_ALLOWED",
            f"project_id={project_id!r} 不在受控白名单内",
            hint="只允许白名单内的工程；见 medini_get_capabilities 的 projects 字段",
            recovery="medini_get_capabilities")
    return e


def _writable(project_id: str) -> ProjectEntry:
    e = _entry(project_id)
    if not e.writable:
        raise AgentApiError(
            "PROJECT_READ_ONLY",
            f"project_id={project_id!r} 登记为只读（{e.kind}）",
            hint="写入只允许本仓工作副本（纪律红线 §5：原始工程不直接覆盖）",
            recovery="medini_get_capabilities")
    return e


def _refused(code: str, message: str, *, hint: str = "",
             recovery: str = "", **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": _BLOCKED, "code": code, "error": message,
        "hint": hint, "recovery": recovery,
    }
    out.update(extra)
    return out


def _guard(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """把策略性拒绝（:class:`AgentApiError`）转成结构化 blocked 返回。

    公开操作**绝不**因策略原因抛异常——上层（CLI / MCP）需要统一的
    ``{status, code, error, hint, recovery}`` 才能给出可执行的恢复建议。
    真正的编程缺陷仍会照常抛出（便于测试与定位）。
    """
    @functools.wraps(fn)
    def wrap(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except AgentApiError as exc:
            return _refused(exc.code, exc.message,
                            hint=exc.hint, recovery=exc.recovery)
    return wrap


# ------------------------------------------------------------ 基线（状态层）
def _project_state(pid: str) -> Path:
    d = STATE_ROOT / "projects" / pid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _baseline_paths(pid: str, case: str) -> tuple[Path, Path]:
    d = _project_state(pid)
    return d / f"{case}.contract.json", d / f"{case}.baseline.json"


def _load_baseline(pid: str, case: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    cp, bp = _baseline_paths(pid, case)
    if not (cp.exists() and bp.exists()):
        return None
    return (json.loads(cp.read_text(encoding="utf-8")),
            json.loads(bp.read_text(encoding="utf-8")))


def _store_baseline(pid: str, case: str, contract: dict[str, Any],
                    meta: dict[str, Any]) -> dict[str, Any]:
    cp, bp = _baseline_paths(pid, case)
    cp.write_text(json.dumps(contract, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    bp.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    return meta


def _contract_hash(data: dict[str, Any]) -> str:
    """契约语义哈希（走 domain 规范化，与 slice/persistence 同源）。"""
    ft = from_contract_json(data)
    sem = ft.semantic_hash()
    return sem


# ---------------------------------------------------------------- 原生快照
def _native_snapshot(fta: Path) -> dict[str, Any]:
    """解析落盘 ``.fta`` 里**带名字**的那部分（``xmi:id`` / ``id`` / ``name``）。

    与 :func:`medini_automation.adapters.fta_diagram.parse_fta` 互补：那个做严格的
    结构校验（fail-closed，供绘图用），这个取标识与命名（供 ID 映射用）。
    两者都只读同一份文件，不重复实现任何写入逻辑。
    """
    root = ET.parse(fta).getroot()
    xid = "{http://www.omg.org/XMI}id"
    xtype = "{http://www.w3.org/2001/XMLSchema-instance}type"

    events = [{
        "xmi_id": el.get(xid),
        "contract_id": el.get("id"),
        "name": el.get("name"),
        "raw_probability": el.get("rawProbability"),
        "node_ids": (el.get("nodes") or "").split(),
    } for el in root.findall("events")]

    gates = [{
        "xmi_id": el.get(xid),
        "name": el.get("name"),
        "kind": el.get("kind"),
        "native_type": el.get(xtype),
        "inputs": (el.get("inputs") or "").split(),
        "outputs": (el.get("outputs") or "").split(),
    } for el in root.findall("gates")]

    nodes = [{
        "xmi_id": el.get(xid),
        "occurrence": el.get("occurrence"),
        "event_xmi_id": el.get("event"),
    } for el in root.findall("eventNodes")]

    conns = [{
        "xmi_id": el.get(xid),
        "output_node": el.get("outputNode"),
        "input_node": el.get("inputNode"),
    } for el in root.findall("connections")]

    return {
        "model_xmi_id": root.get(xid),
        "medini_identifier": root.get("mediniIdentifier"),
        "events": events, "gates": gates, "event_nodes": nodes,
        "connections": conns,
    }


def _mapping_loss(native: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    """实测的表示差异（不是推测——每条都能在真实 ``.fta`` 里对上）。"""
    loss: list[str] = []

    n_ev, c_ev = len(native["events"]), len(contract.get("events") or [])
    if n_ev != c_ev:
        loss.append(
            f"事件计数语义不同：.fta 的 <events> 有 {n_ev} 条（含门输出被建模成的 "
            f"Event），契约的 events 只有 {c_ev} 条（仅基本事件）；"
            "门输出在契约里由 gates 表达")

    no_kind = [g["name"] for g in native["gates"] if not g["kind"]]
    if no_kind:
        loss.append(
            "原生 <gates> 省略默认 kind（medini 语义 = AND）："
            f"{', '.join(no_kind)}；规范化契约必须显式写出 type，"
            "回写时需补默认值")

    dec = [e["contract_id"] for e in native["events"] if e["raw_probability"]]
    if dec:
        loss.append(
            "rawProbability 是有限位十进制字符串，契约用精确有理数："
            "来源精度 < 12 位有效数字时，重新导出可能出现舍入差异")

    if any(len(e["node_ids"]) > 1 for e in native["events"]):
        shared = [e["contract_id"] for e in native["events"] if len(e["node_ids"]) > 1]
        loss.append(
            "共享基本事件：原生用一条 <events> 挂多个 <eventNodes>（"
            f"{', '.join(shared)}）；契约用重复引用表达共享，"
            "两侧的「事件实例数」不同但不影响语义")

    if native["event_nodes"]:
        loss.append(
            "eventNodes 的 occurrence 计数在契约中无对应字段（可视化/实例语义）")

    tf = [g["name"] for g in native["gates"]
          if (g["native_type"] or "").endswith("TransferGate")]
    if tf:
        loss.append(f"TransferGate（{', '.join(tf)}）在契约中不可表示")

    loss.append(
        "xmi:id / mediniIdentifier 由 medini 在导入时分配，重新导入会变——"
        "不能作为稳定标识；稳定标识是契约的逻辑 id")
    return loss


# =============================================================== 1. 能力
@_guard
def get_capabilities(*, worker_id: str | None = None,
                     required_version: str | None = None) -> dict[str, Any]:
    """能力矩阵（操作级 verified/partial/unsupported/unverified）+ worker 自检。

    ``required_version`` 给定时逐项标注 ``version_match``——调用方据此判断
    某个能力在目标版本上是否可用（不匹配不代表不可用，而是 **未在本机实测**）。
    """
    caps = initial_capabilities(__version__)
    rows: list[dict[str, Any]] = []
    for c in caps:
        d = c.to_dict()
        if required_version is not None:
            d["version_match"] = (c.software_version == required_version)
        rows.append(d)

    # worker 自检：诚实列出每个前置因子，不合并成一句 "ready"
    env: dict[str, Any] = {
        "adapter_version": __version__,
        "contracts_version": CONTRACTS_VERSION,
        "medini_exe": {"path": str(MEDINI_EXE_DEFAULT),
                       "exists": MEDINI_EXE_DEFAULT.exists()},
        "license_port_1055": _port_open(1055),
        "runs_root": {"path": str(RUNS_ROOT), "exists": RUNS_ROOT.exists()},
    }
    projects = [{"project_id": e.project_id, "path": str(e.path),
                 "exists": e.path.exists(), "writable": e.writable,
                 "kind": e.kind, "note": e.note}
                for e in PROJECTS.values()]
    env["projects"] = projects

    blocked: list[str] = []
    if not MEDINI_EXE_DEFAULT.exists():
        blocked.append(f"medini exe 不存在: {MEDINI_EXE_DEFAULT}")
    if env["license_port_1055"] != "open":
        blocked.append(
            "许可服务未监听 1055（用户态拉起：双击 scripts/start-license.bat）")

    out: dict[str, Any] = {
        "status": _OK if not blocked else _BLOCKED,
        "worker_id": worker_id or "local-workstation",
        "worker_count": 1,
        "capabilities": rows,
        "worker_env": env,
    }
    if required_version is not None:
        out["required_version"] = required_version
        out["version_satisfied"] = all(
            r.get("version_match", False) for r in rows) if rows else False
    if blocked:
        out["blocked_reason"] = blocked
        out["hint"] = "static 能力（契约校验 / XML 生成 / 独立参考）不受影响"
        out["recovery"] = "medini_read_project"
    return out


def _port_open(port: int, host: str = "localhost") -> str:
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((host, port))
        return "open"
    except OSError:
        return "closed"
    finally:
        s.close()


# =============================================================== 2. 只读
@_guard
def read_project(project_id: str, *, case: str | None = None) -> dict[str, Any]:
    """只读快照：原生结构 + 规范化基线 + 原生 ID 映射 + 映射损失。

    不改任何文件（``case`` 未给时只列清单）。
    """
    e = _entry(project_id)
    fta_dir = e.path / "fta"
    if not e.path.exists():
        return _refused("PROJECT_MISSING", f"工程目录不存在: {e.path}",
                        hint="工作副本可用 python scripts/setup-workcopy.py 重建",
                        recovery="medini_get_capabilities")
    listing = sorted(p.stem for p in fta_dir.glob("*.fta")) if fta_dir.exists() else []

    out: dict[str, Any] = {
        "status": _OK, "project_id": project_id, "path": str(e.path),
        "writable": e.writable, "kind": e.kind,
        "registered_diagrams": [], "cases": listing,
    }
    try:
        from .visibility import list_orphans
        vis = list_orphans(e.path)
        out["registered_diagrams"] = vis["registered"]
        out["orphan_cases"] = vis["unregistered"]
    except Exception as exc:  # noqa: BLE001 — 可见性审计失败不应让只读快照失败
        out["visibility_error"] = f"{type(exc).__name__}: {exc}"

    if not case:
        if not listing:
            out["status"] = _BLOCKED
            out["hint"] = ("工程 fta/ 下没有 .fta；先跑 reopen-check 落盘一个模型")
            out["recovery"] = "medini_reopen_check"
        return out

    fta = fta_dir / f"{case}.fta"
    if not fta.exists():
        return _refused("CASE_NOT_FOUND", f"{fta} 不存在",
                        hint=f"可用 case：{', '.join(listing) or '（无）'}",
                        recovery="medini_read_project", project_id=project_id)

    try:
        graph = parse_fta(fta)          # 严格结构校验（fail-closed）
    except FtaParseError as exc:
        return _refused("FTA_PARSE_FAILED", str(exc),
                        hint=".fta 结构异常，拒绝产出半成品快照",
                        recovery="medini_verify_diagram")
    native = _native_snapshot(fta)

    out["case"] = case
    out["native"] = {
        "file": str(fta),
        "file_sha256": _sha256(fta),
        "model_xmi_id": native["model_xmi_id"],
        "counts": dict(graph.counts),
        "events": native["events"],
        "gates": native["gates"],
        "event_nodes": len(native["event_nodes"]),
        "connections": len(native["connections"]),
    }

    # 上一次写入中断留下的现场（P3 恢复记录）。有值就说明"上次写到一半"，
    # 调用方应先核对现状再决定要不要重新提案 —— 不要直接重试 apply_change。
    out["pending_recovery"] = [r for r in recovery_log(project_id)
                              if r.get("case") == case]
    if out["pending_recovery"]:
        out["next_action"] = (
            f"检测到 {len(out['pending_recovery'])} 条未清理的恢复记录（上次写入"
            "中断）。先看 pending_recovery 里的 stage 判断写到哪一步，"
            "核对基线后再重新提案；人工确认现场已处理后可清除记录")

    base = _load_baseline(project_id, case)
    if base is None:
        out["baseline"] = None
        out["normalized"] = None
        out["mapping"] = None
        out["mapping_loss"] = None
        out["native_drift"] = None
        out["next_action"] = (
            "尚无规范化基线。用 medini_prepare_change 首次建立："
            "expected_baseline_hash=null + contract_path 指向该 case 的契约 JSON")
        return out

    contract, meta = base
    ft = from_contract_json(contract)
    norm: dict[str, Any] = {
        "name": contract.get("name"), "top_gate_id": contract.get("top_gate_id"),
        "events": [{"id": x.id, "probability": str(x.probability),
                    "description": x.description} for x in ft.events.values()],
        "gates": [{"id": g.id, "type": g.type.value, "inputs": list(g.inputs)}
                  for g in ft.gates.values()],
    }
    out["baseline"] = meta
    out["normalized"] = norm
    out["mapping"] = _id_mapping(native, contract)
    out["mapping_loss"] = _mapping_loss(native, contract)

    # 原生文件 vs 已接受基线：apply_change 只推进基线，工作副本里的 .fta 要等
    # run_analysis 才重新落盘。这中间的差距必须显式暴露，不能让调用方以为
    # GUI 里看到的就是当前基线。
    drift = _native_drift(native, contract)
    out["native_drift"] = drift
    if drift:
        out["next_action"] = (
            "工作副本里的原生 .fta 仍是旧模型（下列事件概率与当前基线不一致）："
            "run_analysis 只在契约上算 Q、不落盘；要让原生文件与 GUI 图跟上基线，"
            "需 medini_reopen_check（保存 .fta，可加 publish=true 同步生成图）")
    return out


def _native_drift(native: dict[str, Any],
                  contract: dict[str, Any]) -> list[dict[str, Any]]:
    """原生 rawProbability 与契约概率的逐事件精确比对（Fraction 精确，非浮点）。"""
    want = {}
    for e in contract.get("events") or []:
        try:
            want[e.get("id")] = Fraction(str(e.get("probability")))
        except (ValueError, ZeroDivisionError):
            continue
    drift = []
    for row in native["events"]:
        cid, raw = row["contract_id"], row["raw_probability"]
        if cid is None or raw is None or cid not in want:
            continue
        try:
            got = Fraction(raw)
        except (ValueError, ZeroDivisionError):
            continue
        if got != want[cid]:
            drift.append({"contract_id": cid, "native": raw,
                          "baseline": str(want[cid])})
    return drift


def _id_mapping(native: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    """原生 ↔ 契约 的稳定映射表（按契约逻辑 id 对齐）。

    对齐依据：``.fta`` 的 ``<events>`` 带 ``id`` 属性（= 契约事件 id）；
    ``<gates>`` 带 ``name``（= 契约门 id）。两者都已用真实落盘文件确证。
    """
    by_event = {e["contract_id"]: e for e in native["events"]
                if e["contract_id"]}
    by_gate = {g["name"]: g for g in native["gates"] if g["name"]}

    events, unmatched_ev = [], []
    for ev in contract.get("events") or []:
        cid = ev.get("id")
        n = by_event.get(cid)
        if n is None:
            unmatched_ev.append(cid)
        else:
            events.append({"contract_id": cid, "native_xmi_id": n["xmi_id"],
                           "native_name": n["name"],
                           "native_raw_probability": n["raw_probability"],
                           "native_node_count": len(n["node_ids"])})

    gates, unmatched_g = [], []
    for g in contract.get("gates") or []:
        cid = g.get("id")
        n = by_gate.get(cid)
        if n is None:
            unmatched_g.append(cid)
        else:
            gates.append({"contract_id": cid, "native_xmi_id": n["xmi_id"],
                          "native_kind": n["kind"] or "AND(默认省略)",
                          "native_inputs": len(n["inputs"])})

    return {
        "events": events, "gates": gates,
        "unmatched_contract_events": unmatched_ev,
        "unmatched_contract_gates": unmatched_g,
        "note": "原生 xmi:id 每次导入都会重新分配；跨会话稳定标识是契约逻辑 id",
    }


# =========================================================== 3. 变更提案
@_guard
def prepare_change(project_id: str, case: str, ops: list[dict[str, Any]], *,
                   expected_baseline_hash: str | None,
                   contract_path: str | None = None,
                   reason: str = "", source: str = "",
                   evidence_refs: list[str] | None = None,
                   change_id: str | None = None) -> dict[str, Any]:
    """把规范化变更做成提案：校验 + diff + patch_hash（**不写工程**）。

    ``expected_baseline_hash=None`` 仅在**尚无基线**时允许，用于建立 v1 基线
    （此时 ``contract_path`` 必填）。之后必须传真实哈希。
    """
    entry = _writable(project_id)  # 变更最终要落到可写工程，前置拒绝更诚实

    base = _load_baseline(project_id, case)
    if base is None:
        if expected_baseline_hash is not None:
            return _refused(
                "BASELINE_UNKNOWN",
                f"{project_id}/{case} 尚无基线，但传了 expected_baseline_hash",
                hint="首次建立基线请传 expected_baseline_hash=null 并提供 contract_path",
                recovery="medini_read_project")
        if not contract_path:
            return _refused(
                "CONTRACT_REQUIRED",
                "建立初始基线必须提供 contract_path（该 case 的规范化契约 JSON）",
                hint="契约 JSON 需符合 static_fta schema（03_contracts/schemas）",
                recovery="medini_read_project")
        cp = Path(contract_path)
        if not cp.exists():
            return _refused("CONTRACT_NOT_FOUND", f"{cp} 不存在",
                            recovery="medini_read_project")
        try:
            contract = json.loads(cp.read_text(encoding="utf-8"))
            base_hash = _contract_hash(contract)
        except (ContractError, json.JSONDecodeError, KeyError) as exc:
            return _refused("CONTRACT_INVALID", f"{type(exc).__name__}: {exc}",
                            hint="契约未通过 domain 校验，拒绝建立基线",
                            recovery="medini_validate")
        version, bootstrapped = 1, True
    else:
        contract, meta = base
        base_hash = meta["semantic_hash"]
        version, bootstrapped = int(meta.get("version", 1)), False
        if expected_baseline_hash is None:
            return _refused(
                "BASELINE_HASH_REQUIRED",
                f"{project_id}/{case} 已有基线 v{version}，必须传 expected_baseline_hash",
                hint=f"当前基线哈希 = {base_hash}",
                recovery="medini_read_project")
        if expected_baseline_hash != base_hash:
            return _refused(
                "BASELINE_MISMATCH",
                f"expected={expected_baseline_hash} 与当前基线 {base_hash} 不符",
                hint="基线已被其它变更推进；重新 read_project 取最新哈希后再提案",
                recovery="medini_read_project")

    # ---- 规范化 ops ----
    parsed: list[ChangeOp] = []
    for raw in ops:
        try:
            parsed.append(ChangeOp(op=raw.get("op", ""), args=raw.get("args") or {}))
        except (ValueError, AttributeError) as exc:
            return _refused("OP_UNSUPPORTED", f"{exc}",
                            hint="支持 add_event / add_gate / set_probability",
                            recovery="medini_get_capabilities")

    change = ChangeSet(
        change_id=change_id or f"chg-{int(time.time())}-{_rand6()}",
        project_id=project_id,
        expected_baseline_hash=base_hash,
        ops=parsed, reason=reason, source=source,
        risk_flags=list(evidence_refs or []),
    )

    # ---- 在内存副本上实施 → 校验 → diff ----
    candidate, diff, problems = _apply_ops(contract, parsed)
    if problems:
        return _refused("OP_REJECTED", "; ".join(problems),
                        hint="候选模型未通过 domain 校验（拒绝产出半成品提案）",
                        recovery="medini_validate",
                        change_id=change.change_id)

    try:
        cand_hash = _contract_hash(candidate)
    except (ContractError, KeyError) as exc:
        return _refused("CANDIDATE_INVALID", f"{type(exc).__name__}: {exc}",
                        hint="候选模型 domain 校验失败", recovery="medini_validate",
                        change_id=change.change_id)

    change.state = "awaiting_approval"
    cpath = _change_path(change.change_id)
    cpath.parent.mkdir(parents=True, exist_ok=True)

    if bootstrapped:
        # 建立 v1 基线：seed 契约即「当前已接受模型」。此后所有变更/分析
        # 都必须绑定它的语义哈希（这一步放在候选校验通过之后，
        # 保证被拒绝的提案不会留下半成品基线）。
        _store_baseline(project_id, case, contract, {
            "project_id": project_id, "case": case, "version": 1,
            "semantic_hash": base_hash, "prev_semantic_hash": None,
            "patch_hash": None, "change_id": None,
            "bootstrapped_from": str(cp),
            "adapter_version": __version__,
            "contracts_version": CONTRACTS_VERSION,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })

    blob = {
        "change_id": change.change_id, "project_id": project_id, "case": case,
        "expected_baseline_hash": base_hash, "patch_hash": change.patch_hash(),
        "idempotency_key": change.idempotency_key,
        "candidate_hash": cand_hash, "baseline_version": version,
        "bootstrapped": bootstrapped, "ops": [{"op": o.op, "args": o.args}
                                              for o in parsed],
        "reason": reason, "source": source,
        "evidence_refs": list(evidence_refs or []),
        "required_permission": change.required_permission,
        "state": change.state,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "candidate_contract": candidate,
    }
    cpath.write_text(json.dumps(blob, ensure_ascii=False, indent=2),
                     encoding="utf-8")

    return {
        "status": _OK,
        "change_id": change.change_id,
        "project_id": project_id, "case": case,
        "bootstrapped_baseline": bootstrapped,
        "baseline_hash": base_hash, "candidate_hash": cand_hash,
        "baseline_version": version, "next_version": version + 1,
        "patch_hash": change.patch_hash(),
        "idempotency_key": change.idempotency_key,
        "required_permission": change.required_permission,
        "ops": [{"op": o.op, "args": o.args} for o in parsed],
        "diff": diff,
        "change_file": str(cpath),
        "requires_approval": True,
        "next_action": (
            "变更处于 awaiting_approval。apply_change 只承认受信任审批面板"
            "**签名**的凭证（Ed25519，覆盖 approver/scope/patch_hash/"
            "基线哈希/有效期/nonce）——字段齐全但没签名的自述凭证会被 "
            "APPROVAL_UNSIGNED 拒绝。签发：cli approve --change-id "
            f"{change.change_id} --key-file <私钥>"),
    }


def _apply_ops(contract: dict[str, Any],
               ops: list[ChangeOp]) -> tuple[dict[str, Any], list[dict[str, Any]],
                                             list[str]]:
    """在深拷贝上实施 ops。返回 (候选契约, diff, 问题列表)。"""
    cand = json.loads(json.dumps(contract))
    ev = cand.setdefault("events", [])
    ga = cand.setdefault("gates", [])
    ev_ids = {e.get("id") for e in ev}
    ga_ids = {g.get("id") for g in ga}
    diff: list[dict[str, Any]] = []
    problems: list[str] = []

    def _need(args: dict[str, Any], *keys: str) -> str | None:
        missing = [k for k in keys if k not in args or args[k] in (None, "")]
        return f"缺参数 {missing}" if missing else None

    for o in ops:
        a = dict(o.args)
        if o.op == "add_event":
            bad = _need(a, "id", "probability")
            if bad:
                problems.append(f"add_event {bad}")
                continue
            if a["id"] in ev_ids or a["id"] in ga_ids:
                problems.append(f"add_event: id={a['id']!r} 已存在")
                continue
            try:
                p = Fraction(str(a["probability"]))
            except (ValueError, ZeroDivisionError) as exc:
                problems.append(f"add_event: probability 非法 ({exc})")
                continue
            ev.append({"id": a["id"], "probability": str(p),
                       "description": a.get("description", ""),
                       "source": a.get("source", "agent 变更（需审批）"),
                       "condition": a.get("condition", "固定概率")})
            ev_ids.add(a["id"])
            diff.append({"op": o.op, "target": a["id"], "before": None,
                         "after": {"probability": str(p)}})
        elif o.op == "add_gate":
            bad = _need(a, "id", "type", "inputs")
            if bad:
                problems.append(f"add_gate {bad}")
                continue
            if a["id"] in ev_ids or a["id"] in ga_ids:
                problems.append(f"add_gate: id={a['id']!r} 已存在")
                continue
            if not isinstance(a["inputs"], list) or not a["inputs"]:
                problems.append(f"add_gate {a['id']}: inputs 必须是非空列表")
                continue
            dangling = [i for i in a["inputs"]
                        if i not in ev_ids and i not in ga_ids]
            if dangling:
                problems.append(f"add_gate {a['id']}: 输入引用不存在 {dangling}")
                continue
            ga.append({"id": a["id"], "type": a["type"],
                       "inputs": list(a["inputs"]),
                       "description": a.get("description", "")})
            ga_ids.add(a["id"])
            diff.append({"op": o.op, "target": a["id"], "before": None,
                         "after": {"type": a["type"], "inputs": list(a["inputs"])}})
        elif o.op == "set_probability":
            bad = _need(a, "id", "probability")
            if bad:
                problems.append(f"set_probability {bad}")
                continue
            target = next((e for e in ev if e.get("id") == a["id"]), None)
            if target is None:
                problems.append(f"set_probability: 事件 {a['id']!r} 不存在")
                continue
            try:
                p = Fraction(str(a["probability"]))
            except (ValueError, ZeroDivisionError) as exc:
                problems.append(f"set_probability: probability 非法 ({exc})")
                continue
            before = target.get("probability")
            target["probability"] = str(p)
            diff.append({"op": o.op, "target": a["id"],
                         "before": {"probability": before},
                         "after": {"probability": str(p)}})

    if not problems:
        try:
            from_contract_json(cand)   # 复用 domain 校验，不另写一套规则
        except (ContractError, KeyError, ValueError) as exc:
            problems.append(f"候选模型校验失败: {exc}")
    return cand, diff, problems


def _change_path(change_id: str) -> Path:
    return STATE_ROOT / "changes" / f"{change_id}.json"


def _rand6() -> str:
    import secrets
    return secrets.token_hex(3)


# =========================================================== 4. 变更实施
def _recovery_path(pid: str) -> Path:
    return _project_state(pid) / "recovery.jsonl"


def _write_recovery(pid: str, row: dict[str, Any]) -> None:
    """写一条恢复记录。**绝不因为写日志本身失败而掩盖原始异常**。"""
    try:
        p = _recovery_path(pid)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:                       # noqa: BLE001 — 有意全兜
        pass


def recovery_log(project_id: str) -> list[dict[str, Any]]:
    """未清理的恢复记录 —— apply 中断留下的现场。

    规划（A_核心规划.md L80）要求变更协调器产出「执行日志、恢复记录」；
    中断后的现场必须能查得到，否则"上次到底写到哪一步"只能靠猜。
    """
    p = _recovery_path(project_id)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in
            p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def clear_recovery(project_id: str, change_id: str) -> int:
    """人工核对现场后清除指定变更的恢复记录。返回清除条数。"""
    p = _recovery_path(project_id)
    if not p.exists():
        return 0
    rows = [json.loads(ln) for ln in
            p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    kept = [r for r in rows if r.get("change_id") != change_id]
    removed = len(rows) - len(kept)
    if removed:
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                             for r in kept), encoding="utf-8")
    return removed


def idempotency_record(project_id: str, case: str) -> list[dict[str, Any]]:
    """该工程/切片已登记过的幂等记录（审计用，不含结果载荷）。"""
    d = IdempotencyStore(STATE_ROOT).dir
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                   # noqa: BLE001
            continue
        result = rec.get("result") or {}
        if result.get("project_id") == project_id and result.get("case") == case:
            out.append({"key": rec.get("key"), "payload_hash": rec.get("payload_hash"),
                        "operation": rec.get("operation"), "at": rec.get("at"),
                        "actor": rec.get("actor")})
    return out


@_guard
def apply_change(change_id: str, *, approval: dict[str, Any] | None = None,
                 idempotency_key: str | None = None) -> dict[str, Any]:
    """在**副本**实施已批准变更；绑定基线 + 补丁 + 适配器版本。

    ``approval`` 必须是受信任审批面板**签名**的凭证（Ed25519）。本函数
    **不接受**布尔式 ``approved`` 参数，也不接受"字段齐全但没签名"的自述凭证
    —— 后者正是 P2 的漏洞：只查 scope 字符串，等于让 Agent 自己写四个字段就通过。

    门禁顺序（从便宜到贵，每步给精确 code）：

    1. 幂等短路（重放返回既有结果，**不是错误**）
    2. 变更单状态 = ``awaiting_approval``
    3. 审批校验链（字段 → 有效期 → scope → 内容绑定 → 授权人 → 权限 → 验签）
    4. 变更单防篡改（重算 patch_hash）
    5. 单写者文件锁
    6. 基线二次核对（批准之后基线可能被别人推进）
    7. nonce 一次性消费（与基线落盘同一临界区）
    8. 落盘 + 幂等登记（任一步失败写恢复记录）
    """
    cpath = _change_path(change_id)
    if not cpath.exists():
        return _refused("CHANGE_NOT_FOUND", f"变更单不存在: {cpath}",
                        hint="change_id 由 medini_prepare_change 返回",
                        recovery="medini_read_project")
    blob = json.loads(cpath.read_text(encoding="utf-8"))
    pid, case = blob["project_id"], blob["case"]

    entry = _writable(pid)

    key = idempotency_key or blob.get("idempotency_key") or ""
    patch_hash = blob["patch_hash"]

    # ---- 幂等短路：必须在状态检查之前 ----
    # 已成功实施的变更单状态是 validated，若先查状态就会把"重试"误判成
    # "状态不对"。重放要返回**第一次的结果**，让调用方不必自己判断写没写进去。
    if key:
        verdict = IdempotencyStore(STATE_ROOT).lookup(key, patch_hash)
        if verdict.state == "conflict":
            return _refused(
                "IDEMPOTENCY_CONFLICT",
                "同一幂等键被用于不同载荷 —— 重发重试可以，换内容必须换键",
                hint=(verdict.detail + "；新变更请用 medini_prepare_change "
                      "生成的新键，不要手改幂等键"),
                recovery="medini_prepare_change",
                change_id=change_id, idempotency_key=key)
        if verdict.state == "replay" and verdict.record:
            # 重放也要验签：否则一份**被篡改的凭证**会从这条路拿到 ok，
            # 给调用方"这份凭证有效"的错误信号（端到端实测抓到过这个漏洞）。
            # 但只验签名，不查有效期/绑定/nonce —— 首次执行时已查过，
            # 重放针对的是那个**已完成的作业**，不该要求凭证此刻仍然有效。
            if approval:
                ok, why = verify_signature_only(approval)
                if not ok:
                    return _refused(
                        "APPROVAL_SIGNATURE_INVALID",
                        f"幂等重放时验签失败：{why}",
                        hint=("幂等键命中了既有作业，但本次提供的凭证没有有效签名"
                              "（被篡改或授权人不在信任根）。首次结果不受本次调用"
                              "影响 —— 但别把它当成「这份凭证可用」的证明"),
                        recovery="medini_prepare_change", change_id=change_id,
                        idempotency_key=key)
            first = verdict.record
            return {**(first.get("result") or {}), "replayed": True,
                    "replayed_from": first.get("at"),
                    "change_id": change_id, "idempotency_key": key,
                    "approval_reverified": bool(approval),
                    "note": ("幂等命中：未重复写入，以下为首次执行的原始结果"
                             "（逐字段一致）")}

    if blob.get("state") != "awaiting_approval":
        return _refused(
            "CHANGE_STATE", f"变更单状态 {blob.get('state')!r} 不可实施",
            hint="只有 awaiting_approval 的变更单可实施",
            recovery="medini_read_project", change_id=change_id)

    # ---- 审批校验链（唯一入口：受信任审批面板签名）----
    ctx = ApprovalContext(
        change_id=change_id, project_id=pid, patch_hash=patch_hash,
        expected_baseline_hash=blob["expected_baseline_hash"],
        required_permission=blob.get("required_permission", "model_write"))
    try:
        vref = verify_attestation(approval or {}, context=ctx)
    except ApprovalError as exc:
        return _refused(exc.code, exc.message, hint=exc.hint,
                        recovery=exc.recovery or "medini_prepare_change",
                        change_id=change_id)
    ref = ApprovalRef.from_verified(vref)

    # ---- 凭证一次性（早拒）：这里只 peek，真正的消费在锁内落盘前 ----
    # 早拒的理由：「这份批准已经被用过了」比「基线不匹配」更根本 —— 拿一份
    # 用过的凭证去撞任何一条后续校验，都会给出误导性的失败原因。
    # 只 peek 不消费，是为了不让"后续校验失败"白白烧掉一份批准。
    if ref.nonce:
        seen = NonceStore(STATE_ROOT).peek(ref.nonce)
        if seen.is_replay:
            return _refused(
                "APPROVAL_REPLAYED",
                (f"该审批凭证已被使用过（{seen.first_used_at} 由 "
                 f"{seen.first_used_by or '未知'}，范围 "
                 f"{seen.conflicting_scope or ref.scope}）"),
                hint=("审批凭证是一次性的 —— 这是防重放，不是错误。"
                      "需要再次写入就重新走审批（内容没变也要重新签）"),
                recovery="medini_prepare_change", change_id=change_id,
                nonce=ref.nonce)

    cs = ChangeSet(
        change_id=change_id, project_id=pid,
        expected_baseline_hash=blob["expected_baseline_hash"],
        ops=[ChangeOp(op=o["op"], args=o["args"]) for o in blob["ops"]],
        approval=ref, state="awaiting_approval")
    problems = cs.validate_for_apply()
    if problems:
        return _refused("APPROVAL_REJECTED", "; ".join(problems),
                        hint=f"批准必须绑定本变更：scope=change:{change_id}",
                        recovery="medini_prepare_change", change_id=change_id)

    # ---- 变更单防篡改（落盘内容与 patch_hash 必须自洽）----
    if cs.patch_hash() != patch_hash:
        return _refused("PATCH_HASH_MISMATCH",
                        "变更单内容与 patch_hash 不符（落盘后被改动）",
                        hint="重新 prepare_change，不要手工编辑变更单",
                        recovery="medini_prepare_change", change_id=change_id)

    # ---- 单写者：跨进程文件锁 ----
    lock_scope = f"{pid}/{case}"
    try:
        with writer_lock(lock_scope, root=STATE_ROOT, actor=ref.approver):
            return _apply_under_lock(change_id, cpath, blob, ref, cs, key)
    except WriterBusy as exc:
        st = read_lock_holder(STATE_ROOT, lock_scope)
        h = st.holder
        return _refused(
            "WRITER_BUSY", str(exc),
            hint=(f"当前持有者 {h.get('actor')}（pid {h.get('pid')}，"
                  f"自 {h.get('at')}，已 {st.age_s}s）。写作业是秒级的："
                  "稍后重试即可；若确认该进程已崩溃，锁会在 stale 超时"
                  "后被自动抢占（抢占动作有审计记录）"),
            recovery="medini_read_project", change_id=change_id,
            lock_holder=h)


def _apply_under_lock(change_id: str, cpath: Path, blob: dict[str, Any],
                      ref: ApprovalRef, cs: ChangeSet,
                      key: str) -> dict[str, Any]:
    """持锁执行。调用方已保证锁在手 —— 本函数只管"把事做完或留下现场"。"""
    pid, case = blob["project_id"], blob["case"]
    entry = _writable(pid)
    stage = "precheck"

    base = _load_baseline(pid, case)
    if base is None:
        return _refused("BASELINE_LOST", f"{pid}/{case} 基线丢失",
                        recovery="medini_read_project", change_id=change_id)
    _, meta = base
    if meta["semantic_hash"] != blob["expected_baseline_hash"]:
        return _refused(
            "BASELINE_MISMATCH",
            f"批准时基线 {blob['expected_baseline_hash'][:16]}… 已被推进为 "
            f"{meta['semantic_hash'][:16]}…",
            hint="基线在批准后变化——必须重新提案并重新取得批准",
            recovery="medini_read_project", change_id=change_id)

    # ---- nonce 一次性消费：与基线落盘同一临界区 ----
    # 若在校验阶段就消费，校验通过但随后失败会白白烧掉一份批准；
    # 若在落盘之后消费，两个进程可能同时通过校验并双双写入。
    # 放在锁内的落盘之前，是唯一既不烧凭证也不留重放窗口的位置。
    nonces = NonceStore(STATE_ROOT)
    if ref.nonce:
        verdict = nonces.consume(ref.nonce, actor=ref.approver, scope=ref.scope)
        if verdict.is_replay:
            return _refused(
                "APPROVAL_REPLAYED",
                (f"该审批凭证已被使用过（{verdict.first_used_at} 由 "
                 f"{verdict.first_used_by or '未知'}，范围 "
                 f"{verdict.conflicting_scope or ref.scope}）"),
                hint=("审批凭证是一次性的 —— 这是防重放，不是错误。"
                      "需要再次写入就重新走审批（内容没变也要重新签）"),
                recovery="medini_prepare_change", change_id=change_id,
                nonce=ref.nonce)

    try:
        candidate = blob["candidate_contract"]
        new_meta = {
            "project_id": pid, "case": case,
            "version": int(blob["baseline_version"]) + 1,
            "semantic_hash": blob["candidate_hash"],
            "prev_semantic_hash": blob["expected_baseline_hash"],
            "patch_hash": blob["patch_hash"], "change_id": change_id,
            "approved_by": ref.approver, "approved_at": ref.approved_at,
            "approval_expires_at": ref.expires_at,
            "approval_nonce": ref.nonce,
            "approval_signed": ref.signed,
            "approval_permission": ref.permission,
            "trust_source": ref.trust_source,
            "credential_fingerprint": ref.credential_fingerprint,
            "idempotency_key": key,
            "adapter_version": __version__, "contracts_version": CONTRACTS_VERSION,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        cp, _ = _baseline_paths(pid, case)

        stage = "write_baseline"
        _store_baseline(pid, case, candidate, new_meta)

        stage = "update_change_state"
        blob["state"] = "validated"
        blob["applied_at"] = new_meta["updated_at"]
        blob["approval_nonce"] = ref.nonce
        blob["approved_by"] = ref.approver
        cpath.write_text(json.dumps(blob, ensure_ascii=False, indent=2),
                         encoding="utf-8")

        stage = "append_changelog"
        _append_change_log(pid, case, {
            "change_id": change_id, "from": meta["semantic_hash"],
            "to": blob["candidate_hash"], "approver": ref.approver,
            "signed": ref.signed, "permission": ref.permission,
            "nonce": ref.nonce, "idempotency_key": key,
            "at": new_meta["updated_at"]})

        result = {
            "status": _OK, "change_id": change_id,
            "project_id": pid, "case": case,
            "applied_to": str(cp),
            "baseline": new_meta,
            "ops_applied": len(blob["ops"]),
            "target_project_writable": entry.writable,
            "approved_by": ref.approver, "approval_signed": ref.signed,
            "approval_permission": ref.permission,
            "approval_expires_at": ref.expires_at,
            "approval_nonce": ref.nonce, "trust_source": ref.trust_source,
            "idempotency_key": key, "replayed": False,
            "note": ("只写本仓工作副本与控制面状态；既有工程未被触碰"
                     "（纪律红线 §5：原始工程不直接覆盖）"),
            "next_action": "medini_run_analysis（model_hash 用新的 semantic_hash）",
        }

        stage = "record_idempotency"
        if key:
            IdempotencyStore(STATE_ROOT).record(
                key, blob["patch_hash"], "apply_change", result,
                actor=ref.approver)
    except Exception as exc:                # noqa: BLE001 — 留现场再抛
        _write_recovery(pid, {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "change_id": change_id, "case": case, "stage": stage,
            "error": f"{type(exc).__name__}: {exc}",
            "state": "recovery_required",
            "nonce_consumed": bool(ref.nonce),
            "baseline_may_have_advanced": stage in
            ("write_baseline", "update_change_state", "append_changelog",
             "record_idempotency"),
            "note": ("写入中断。基线可能已推进而变更单未同步 —— 先 "
                     "medini_read_project 核对现状，再决定是否重新提案；"
                     "不要直接重试 apply_change"),
        })
        raise

    return result


def _append_change_log(pid: str, case: str, row: dict[str, Any]) -> None:
    d = _project_state(pid)
    log = d / f"{case}.changelog.jsonl"
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def change_log(project_id: str, case: str) -> list[dict[str, Any]]:
    d = _project_state(project_id)
    log = d / f"{case}.changelog.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in
            log.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ============================================================= 5. 分析
@_guard
def run_analysis(project_id: str, case: str, *, model_hash: str,
                 out_root: Path | None = None, k_max: int = 6) -> dict[str, Any]:
    """提交实机分析。``model_hash`` 必须等于当前基线语义哈希（配置门禁）。"""
    _entry(project_id)
    base = _load_baseline(project_id, case)
    if base is None:
        return _refused("NO_BASELINE", f"{project_id}/{case} 尚无基线",
                        hint="先 prepare_change 建立基线并 apply_change",
                        recovery="medini_read_project")
    contract, meta = base
    if model_hash != meta["semantic_hash"]:
        return _refused(
            "MODEL_HASH_MISMATCH",
            f"提交的 model_hash={model_hash[:16]}… 与当前基线 "
            f"{meta['semantic_hash'][:16]}… 不符",
            hint="分析必须绑定已批准的确切模型版本",
            recovery="medini_read_project")

    from .slice import license_service_running, run_slice
    if not MEDINI_EXE_DEFAULT.exists():
        return _refused("MEDINI_MISSING", f"medini exe 不存在: {MEDINI_EXE_DEFAULT}",
                        recovery="medini_get_capabilities")
    if not license_service_running():
        return _refused(
            "LICENSE_DOWN", "许可服务未监听 1055（实机通道不可用）",
            hint="双击 scripts/start-license.bat（用户态拉起，无需管理员）",
            recovery="medini_get_capabilities")

    cp, _ = _baseline_paths(project_id, case)
    root = Path(out_root) if out_root else RUNS_ROOT
    res = run_slice(cp, root, case=case, k_max=k_max, execute=True)
    return {
        "status": _OK if res.verdict == "pass" else _BLOCKED,
        "job_id": res.job_id,
        "project_id": project_id, "case": case,
        "model_hash": model_hash,
        "verdict": res.verdict, "verdict_detail": res.verdict_detail,
        "mode": res.mode,
        "reference_Q": res.reference_q, "reference_MCS": res.reference_mcs,
        "medini_Q_top": (res.medini or {}).get("Q_top"),
        "evidence_dir": res.evidence_dir,
        "recovery": "medini_get_job" if res.verdict == "pass" else "medini_readback",
    }


# ============================================================== 6. 作业
@_guard
def get_job(job_id: str, *, out_root: Path | None = None) -> dict[str, Any]:
    """作业状态、耗时、错误与恢复建议。"""
    root = Path(out_root) if out_root else RUNS_ROOT
    try:
        job = JobStore(root / "jobs").load(job_id)
    except KeyError as exc:
        return _refused("JOB_NOT_FOUND", str(exc),
                        hint="job_id 来自 run_analysis / reopen_check",
                        recovery="medini_get_capabilities")
    d = job.to_dict()
    terminal = job.state in ("verified", "failed", "cancelled", "timed_out")
    stages = [n for n in job.notes]
    out = {
        "status": _OK, "job_id": job_id, "kind": job.kind, "state": job.state,
        "terminal": terminal,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                    time.localtime(job.created_at)),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                    time.localtime(job.updated_at)),
        "duration_s": round(job.updated_at - job.created_at, 2),
        "idempotency_key": job.idempotency_key,
        "change_id": job.change_id,
        "stage_notes": stages,
        "artifacts": job.artifacts,
    }
    if job.state == "verified":
        out["recovery"] = "medini_readback"
    elif job.state in ("failed", "blocked"):
        out["hint"] = "作业未通过；用 medini_readback 看回读差异，再决定是否重提案"
        out["recovery"] = "medini_readback"
        out["errors"] = [n for n in stages if "failed" in n or "blocked" in n]
    elif not terminal:
        out["hint"] = "作业仍在进行；本版不支持取消运行中的 medini 进程"
        out["recovery"] = "medini_get_job"
    return out


# ============================================================= 7. 回读
@_guard
def readback(job_id: str, *, expected_model_hash: str | None = None,
             out_root: Path | None = None) -> dict[str, Any]:
    """重新读取原生模型与结果并与预期模型哈希比较。"""
    root = Path(out_root) if out_root else RUNS_ROOT
    try:
        job = JobStore(root / "jobs").load(job_id)
    except KeyError as exc:
        return _refused("JOB_NOT_FOUND", str(exc),
                        hint="job_id 来自 run_analysis / reopen_check",
                        recovery="medini_get_capabilities")

    hits = sorted(p for p in root.glob(f"*_{job_id}") if p.is_dir())
    if not hits:
        return _refused("EVIDENCE_NOT_FOUND", f"未找到 job {job_id} 的证据目录",
                        hint=f"在 {root} 下按 *_<job_id> 查找",
                        recovery="medini_get_job", job_id=job_id)
    ev = hits[0]
    manifest_p = ev / "manifest.json"
    manifest = (json.loads(manifest_p.read_text(encoding="utf-8"))
                if manifest_p.exists() else None)
    ref_p = ev / "verification" / "reference.json"
    med_p = ev / "results" / "medini-actual.json"
    reference_blk = (json.loads(ref_p.read_text(encoding="utf-8"))
                     if ref_p.exists() else None)
    medini_blk = (json.loads(med_p.read_text(encoding="utf-8"))
                  if med_p.exists() else None)

    checks: dict[str, Any] = {
        "manifest_present": manifest is not None,
        "reference_present": reference_blk is not None,
        "native_result_present": medini_blk is not None,
    }
    if manifest:
        checks["job_state"] = manifest.get("job_state", job.state)
        checks["model_hash_matches_manifest"] = (
            None if expected_model_hash is None
            else manifest.get("semantic_model_hash") == expected_model_hash)
        if expected_model_hash is not None:
            checks["manifest_semantic_model_hash"] = manifest.get(
                "semantic_model_hash")
    failed = [k for k, v in checks.items() if v is False]

    # reopen 型证据（persist_*）自带四重校核
    checks_p = ev / "checks.json"
    reopen_checks = (json.loads(checks_p.read_text(encoding="utf-8"))
                     if checks_p.exists() else None)

    return {
        "status": _OK if not failed else _ERROR,
        "job_id": job_id, "job_state": job.state,
        "evidence_dir": str(ev),
        "checks": checks, "failed_checks": failed,
        "manifest": manifest, "reference": reference_blk,
        "native_result": medini_blk,
        "reopen_checks": reopen_checks,
        "verdict": (manifest or {}).get("verdict"),
        "hint": (None if not failed else
                 "证据包不完整；用 medini_get_job 看阶段记录"),
        "recovery": "medini_export_evidence" if not failed else "medini_get_job",
    }


# ========================================================= 8. 证据导出
@_guard
def export_evidence(job_id: str, *, out_root: Path | None = None) -> dict[str, Any]:
    """把**已校核**的作业打成交付包：文件清单 + 逐文件 SHA-256 + 结果摘要。

    明确不是正式签发：``approval`` 字段保持 ``null``，``disclaimer`` 说明
    该包仅用于工程复核，不构成适航/合规批准。
    """
    root = Path(out_root) if out_root else RUNS_ROOT
    try:
        job = JobStore(root / "jobs").load(job_id)
    except KeyError as exc:
        return _refused("JOB_NOT_FOUND", str(exc),
                        hint="job_id 来自 run_analysis / reopen_check",
                        recovery="medini_get_capabilities")

    if job.state != "verified":
        return _refused(
            "JOB_NOT_VERIFIED",
            f"作业状态 {job.state!r}，只有 verified 的作业可导出证据",
            hint="先跑 medini_readback 确认回读通过",
            recovery="medini_readback", job_id=job_id)

    hits = sorted(p for p in root.glob(f"*_{job_id}") if p.is_dir())
    if not hits:
        return _refused("EVIDENCE_NOT_FOUND", f"未找到 job {job_id} 的证据目录",
                        recovery="medini_get_job", job_id=job_id)
    src = hits[0]

    dest = STATE_ROOT / "bundles" / job_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)

    files = []
    for p in sorted(dest.rglob("*")):
        if p.is_file():
            files.append({
                "path": str(p.relative_to(dest)).replace("\\", "/"),
                "size": p.stat().st_size,
                "sha256": _sha256(p),
            })

    manifest_p = dest / "manifest.json"
    manifest = (json.loads(manifest_p.read_text(encoding="utf-8"))
                if manifest_p.exists() else {})
    results_p = dest / "results" / "medini-actual.json"
    results_digest = _sha256(results_p) if results_p.exists() else None

    bundle = {
        "schema": "medini-evidence-bundle",
        "version": "0.1.0",
        "job_id": job_id,
        "job_state": job.state,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "adapter_version": __version__,
        "contracts_version": CONTRACTS_VERSION,
        "medini_target": manifest.get("medini_target"),
        "model_hash": manifest.get("semantic_model_hash"),
        "verdict": manifest.get("verdict"),
        "results_sha256": results_digest,
        "file_count": len(files),
        "files": files,
        "bundle_root": str(dest),
        "approval": None,
        "disclaimer": ("本包为工程复核用证据集合，含文件清单与逐文件 SHA-256；"
                       "approval 为 null。不构成适航、合规或正式签发。"),
    }
    (dest / "bundle.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "status": _OK, "job_id": job_id, "bundle_root": str(dest),
        "bundle_manifest": str(dest / "bundle.json"),
        "file_count": len(files),
        "files": files,
        "model_hash": bundle["model_hash"],
        "verdict": bundle["verdict"],
        "approval": None,
        "disclaimer": bundle["disclaimer"],
    }


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ======================================================= 9. 保存重开回读
@_guard
def reopen_check(project_id: str, case: str, *, contract_path: str | None = None,
                 publish: bool = False, k_max: int = 6, execute: bool = True,
                 out_root: Path | None = None) -> dict[str, Any]:
    """P1 强校核：保存 → 新进程重开 → 回读四重校核（可顺带发布 GUI 图）。"""
    _entry(project_id)
    base = _load_baseline(project_id, case)
    if base is not None:
        contract = _baseline_paths(project_id, case)[0]
    else:
        if not contract_path or not Path(contract_path).exists():
            return _refused(
                "NO_MODEL",
                f"{project_id}/{case} 既无基线也未提供 contract_path",
                hint="先 prepare_change 建立基线，或直接给 contract_path",
                recovery="medini_read_project")
        contract = Path(contract_path)

    if not MEDINI_EXE_DEFAULT.exists():
        return _refused("MEDINI_MISSING", f"medini exe 不存在: {MEDINI_EXE_DEFAULT}",
                        recovery="medini_get_capabilities")

    from .persistence import run_reopen_check
    root = Path(out_root) if out_root else RUNS_ROOT
    res = run_reopen_check(contract, root, case=case, k_max=k_max,
                           execute=execute, publish=publish)
    return {
        "status": _OK if res.verdict == "pass" else _BLOCKED,
        "job_id": res.job_id, "project_id": project_id, "case": res.case,
        "mode": res.mode, "verdict": res.verdict,
        "verdict_detail": res.verdict_detail,
        "checks": res.checks, "publish": res.publish,
        "saved_fta": res.saved_fta, "evidence_dir": res.evidence_dir,
        "phaseA": ({k: res.save.get(k) for k in
                    ("status", "Q0", "semantic_digest", "saved_sha256")}
                   if res.save else None),
        "phaseB": ({k: res.reopen.get(k) for k in
                    ("status", "Q1", "semantic_digest", "loaded_sha256")}
                   if res.reopen else None),
        "recovery": "medini_export_evidence" if res.verdict == "pass"
                    else "medini_get_job",
    }
