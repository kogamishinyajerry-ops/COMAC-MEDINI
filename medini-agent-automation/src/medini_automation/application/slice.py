"""application.slice — 首个真实纵向切片编排。

链路：契约JSON → 校验 → 独立参考值(有理数穷举) → FaultTreePlus XML →
(headless CLI 可用时) 实机计算 → 回读对照 → 证据包。
许可不可用时：完成 dry-run（生成全部工件+参考值），实机步骤标 blocked。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..adapters import ftplus
from ..adapters.medini_cli import (
    MEDINI_EXE_DEFAULT, DEFAULT_WORKSPACE, DEFAULT_PROJECT,
    MediniUnavailable, run_headless,
)
from ..domain import reference
from ..domain.model import FaultTree, from_contract_json
from ..worker.job import Job, JobStore

TEMPLATE_JS = Path(__file__).resolve().parents[2] / "scripts" / "medini" / "slice-run-case.js"


def license_service_running() -> bool:
    """探测本机 1055 许可端口（SERVER 模式许可的健康信号）。"""
    import socket
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("localhost", 1055))
        return True
    except OSError:
        return False
    finally:
        s.close()


@dataclass
class SliceResult:
    case: str
    mode: str                      # "dry-run" | "medini-run"
    contract_valid: bool
    semantic_hash: str
    reference_q: str | None        # 有理数精确值（独立参考）
    reference_mcs: list[list[str]] | None
    xml_path: str | None
    medini: dict[str, Any] | None  # 实机结果（仅 medini-run）
    verdict: str                   # pass / fail / blocked / error
    verdict_detail: str
    evidence_dir: str
    job_id: str
    artifacts: dict[str, str] = field(default_factory=dict)


def run_slice(
    contract_json: Path,
    out_root: Path,
    *,
    case: str | None = None,
    k_max: int = 6,
    execute: bool = True,
    exe: Path = MEDINI_EXE_DEFAULT,
    workspace: Path = DEFAULT_WORKSPACE,
    project: Path = DEFAULT_PROJECT,
) -> SliceResult:
    """执行一个切片。execute=True 且许可可用时走实机；否则 dry-run。"""
    t_start = time.time()
    data = json.loads(contract_json.read_text(encoding="utf-8"))
    case = case or data.get("name", contract_json.stem)

    store = JobStore(out_root / "jobs")
    job = Job(kind="run_analysis", change_id=f"slice:{case}")
    job.transition("validated", f"契约JSON解析成功 case={case}")
    store.save(job)

    evidence = out_root / f"run_{case}_{job.job_id}"
    evidence.mkdir(parents=True, exist_ok=True)

    # ---- 1. 契约校验 + 语义哈希 ----
    try:
        ft = from_contract_json(data)
    except Exception as e:  # ContractError
        job.transition("failed", f"契约校验失败: {e}")
        store.save(job)
        return SliceResult(
            case=case, mode="dry-run", contract_valid=False,
            semantic_hash="", reference_q=None, reference_mcs=None,
            xml_path=None, medini=None, verdict="error",
            verdict_detail=f"契约校验失败: {e}",
            evidence_dir=str(evidence), job_id=job.job_id)

    sem_hash = ft.semantic_hash()
    job.notes.append(f"semantic_hash={sem_hash[:16]}")

    # ---- 2. 独立参考值（不依赖 medini，永远计算）----
    try:
        q_ref = reference.exact_q(ft)
        mcs_ref = reference.minimal_cut_sets(ft)
        ref_block = {
            "engine": "python-rational-exhaustive",
            "Q_top_exact": str(q_ref),
            "Q_top_decimal": float(q_ref),
            "MCS": [list(cs) for cs in mcs_ref],
            "method_note": "2^n 有理数穷举（n<=20），独立参考，非生产求解器",
        }
    except ValueError as e:  # REFERENCE_LIMIT
        q_ref, mcs_ref, ref_block = None, None, {"error": str(e)}

    (evidence / "inputs").mkdir(exist_ok=True)
    (evidence / "inputs" / "contract.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 3. XML 生成 ----
    xml_path = evidence / "inputs" / f"{case}_faulttreeplus.xml"
    ftplus.write_xml(ft, xml_path)
    xml_hash = hashlib.sha256(xml_path.read_bytes()).hexdigest()

    # ---- 4. 决定模式 ----
    mode = "dry-run"
    medini_result: dict[str, Any] | None = None
    verdict, detail = "blocked", "dry-run 完成，实机未执行"

    if execute:
        if license_service_running() and exe.exists():
            mode = "medini-run"
            js_out = evidence / "execution" / f"{case}-actual.json"
            run_log = evidence / "execution" / f"{case}-run.log"
            js_path = evidence / "execution" / f"run-{case}.js"
            js_path.parent.mkdir(parents=True, exist_ok=True)
            js_path.write_text(
                TEMPLATE_JS.read_text(encoding="utf-8")
                .replace("__CASE__", case)
                .replace("__K_MAX__", str(k_max))
                .replace("__XML_PATH__", str(xml_path).replace("\\", "/"))
                .replace("__OUT_JSON__", str(js_out).replace("\\", "/"))
                .replace("__RUN_LOG__", str(run_log).replace("\\", "/")),
                encoding="utf-8")

            job.transition("queued", "实机通道可用，提交 headless")
            store.save(job)
            job.transition("running", "medini headless 执行中")
            store.save(job)
            try:
                cli = run_headless(
                    js_path, exe=exe, workspace=workspace, project=project,
                    expect_json=js_out)
                if cli.fresh:
                    raw = json.loads(js_out.read_text(encoding="utf-8"))
                    medini_result = raw
                    (evidence / "results").mkdir(exist_ok=True)
                    (evidence / "results" / "medini-actual.json").write_text(
                        json.dumps(raw, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                    # ---- 回读对照 ----
                    job.transition("readback", "结果新鲜，回读对照")
                    verdict, detail = _compare(raw, q_ref, mcs_ref)
                    if verdict == "pass":
                        job.transition("verified", detail)
                    else:
                        job.transition("failed", detail)
                else:
                    medini_result = {
                        "status": "no_fresh_result",
                        "exit_code": cli.exit_code,
                        "stdout_tail": cli.stdout_tail,
                    }
                    verdict, detail = "error", (
                        f"medini 退出码 {cli.exit_code}，无新鲜结果文件")
                    job.transition("failed", detail)
            except MediniUnavailable as e:
                verdict, detail = "blocked", f"medini 不可用: {e}"
                job.transition("blocked", detail)
        else:
            reason = []  # 诚实列出每个阻塞因子
            if not exe.exists():
                reason.append(f"medini exe 不存在: {exe}")
            if not license_service_running():
                reason.append(
                    "许可服务 STOPPED（sc query 实测；需管理员启动 "
                    "'ANSYS, Inc. License Manager'）")
            verdict, detail = "blocked", "实机通道阻塞: " + "; ".join(reason)
            job.transition("blocked", detail)
    store.save(job)

    # ---- 5. 证据包 ----
    manifest = {
        "job_id": job.job_id,
        "case": case,
        "mode": mode,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "duration_s": round(time.time() - t_start, 2),
        "contract_file": str(contract_json),
        "semantic_model_hash": sem_hash,
        "native_raw_hash": hashlib.sha256(
            contract_json.read_bytes()).hexdigest(),
        "xml_artifact_hash": xml_hash,
        "adapter_version": _pkg_version(),
        "contracts_version": "0.1.0",
        "medini_target": str(exe),
        "verdict": verdict,
        "verdict_detail": detail,
        "job_state": job.state,
        "k_max": k_max,
    }
    (evidence / "verification").mkdir(exist_ok=True)
    (evidence / "verification" / "reference.json").write_text(
        json.dumps(ref_block, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return SliceResult(
        case=case, mode=mode, contract_valid=True,
        semantic_hash=sem_hash,
        reference_q=str(q_ref) if q_ref is not None else None,
        reference_mcs=[list(c) for c in mcs_ref] if mcs_ref else None,
        xml_path=str(xml_path), medini=medini_result,
        verdict=verdict, verdict_detail=detail,
        evidence_dir=str(evidence), job_id=job.job_id,
        artifacts={"xml": str(xml_path),
                   "manifest": str(evidence / "manifest.json")})


def _compare(raw: dict[str, Any], q_ref: Fraction | None,
             mcs_ref: list[tuple[str, ...]] | None) -> tuple[str, str]:
    """三角校核：medini vs 独立参考。Q 容差 1e-9（相对）；MCS 集合精确相等。"""
    if raw.get("status") == "script_error":
        return "error", f"JS 脚本错误: {raw.get('error')}"
    q_medini = raw.get("Q_top")
    if q_medini is None:
        return "error", "medini 未返回 Q_top"
    if q_ref is None:
        return "fail", "参考值不可用，无法对照（模型超穷举上限）"
    q_m = Fraction(q_medini)
    rel = abs(q_m - q_ref) / q_ref if q_ref != 0 else abs(q_m)
    # MCS 对照（事件集合，忽略顺序）
    mcs_medini = sorted(
        tuple(sorted(c["events"])) for c in raw.get("cutsets", []))
    mcs_ref_s = sorted(mcs_ref) if mcs_ref else []
    mcs_ok = mcs_medini == mcs_ref_s
    if rel <= Fraction(1, 10**9) and mcs_ok:
        return "pass", (
            f"Q 相对误差 {float(rel):.2e}（容差 1e-9），MCS {len(mcs_medini)} 组一致")
    return "fail", (
        f"Q medini={float(q_m):.12g} vs 参考={float(q_ref):.12g} "
        f"(rel={float(rel):.2e})；MCS 一致={mcs_ok}"
        f"（medini={mcs_medini} vs 参考={mcs_ref_s}）")


def _pkg_version() -> str:
    from .. import __version__
    return __version__
