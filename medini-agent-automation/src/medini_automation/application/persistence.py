"""application.persistence — P1 保存→重开→回读链编排。

两阶段独立 JVM 进程，构成真正的「关闭 → 重开」：
  A) slice-save.js   导入 XML → 计算 Q0 → 语义摘要 → 保存 .fta 到工作副本工程
  B) slice-reopen.js 新进程从磁盘加载 .fta → 回读语义摘要 → 重新计算 Q1

验收（四重）：
  1. digest(A) == digest(B)          语义还原
  2. Q0 == Q1                        计算可复现
  3. loaded_sha256 == saved_sha256   读回的正是写入的字节
  4. 结构计数一致                     规模未变

纪律：只写本仓工作副本工程 workcopy/AUTO-WC，不更新既有 medini 工程。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..adapters import ftplus
from ..adapters.medini_cli import (
    DEFAULT_WORKSPACE, MEDINI_EXE_DEFAULT, WORKCOPY_PROJ_NAME,
    WORKCOPY_PROJECT_DEFAULT, run_headless,
)
from ..domain import reference
from ..domain.model import from_contract_json
from ..worker.job import Job, JobStore
from .slice import _pkg_version, license_service_running

_REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_SAVE = _REPO_ROOT / "scripts" / "medini" / "slice-save.js"
TEMPLATE_REOPEN = _REPO_ROOT / "scripts" / "medini" / "slice-reopen.js"


@dataclass
class ReopenResult:
    case: str
    mode: str                       # "dry-run" | "reopen-run"
    contract_valid: bool
    reference_q: str | None
    save: dict[str, Any] | None
    reopen: dict[str, Any] | None
    checks: dict[str, Any] = field(default_factory=dict)
    verdict: str = "blocked"        # pass / fail / blocked / error
    verdict_detail: str = ""
    evidence_dir: str = ""
    job_id: str = ""
    saved_fta: str | None = None
    artifacts: dict[str, str] = field(default_factory=dict)


def _slug(s: str) -> str:
    """case 名 → 安全文件名（medini 的 platform URI 不接受空格/括号/等号）。"""
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", s).strip("_") or "case"


def _q_equal(a: str | None, b: str | None) -> tuple[bool, str]:
    """Q 对照：先精确字符串，再有理数容差 1e-12。"""
    if a is None or b is None:
        return False, "缺值"
    if a == b:
        return True, "逐位一致"
    try:
        fa, fb = Fraction(a), Fraction(b)
        rel = abs(fa - fb) / fa if fa != 0 else abs(fb)
        return rel <= Fraction(1, 10**12), f"rel={float(rel):.2e}"
    except Exception as e:  # noqa: BLE001
        return False, f"无法解析: {e}"


def _evaluate(save_json: dict[str, Any] | None,
              reopen_json: dict[str, Any] | None) -> tuple[str, str, dict[str, Any]]:
    """P1 四重校核（纯函数，便于单测）。

    返回 (verdict, detail, checks)；verdict ∈ {pass, fail, error}。
    四重：语义摘要一致 / Q 一致 / 磁盘字节哈希一致 / 结构计数一致。
    """
    checks: dict[str, Any] = {}
    s_ok = (save_json or {}).get("status") == "ok"
    r_ok = (reopen_json or {}).get("status") == "ok"
    checks["phaseA_ok"] = s_ok
    checks["phaseB_ok"] = r_ok

    if not s_ok:
        return "error", (f"阶段A未成功: {(save_json or {}).get('status')} "
                         f"{(save_json or {}).get('error') or ''}").strip(), checks
    if not r_ok:
        return "error", (f"阶段B未成功: {(reopen_json or {}).get('status')} "
                         f"{(reopen_json or {}).get('error') or ''}").strip(), checks

    dg_a, dg_b = save_json.get("semantic_digest"), reopen_json.get("semantic_digest")
    checks["semantic_digest_equal"] = bool(dg_a) and dg_a == dg_b
    checks["semantic_digest_A"] = dg_a
    checks["semantic_digest_B"] = dg_b

    q_ok, q_note = _q_equal(save_json.get("Q0"), reopen_json.get("Q1"))
    checks["Q_equal"] = q_ok
    checks["Q_note"] = q_note
    checks["Q0"] = save_json.get("Q0")
    checks["Q1"] = reopen_json.get("Q1")

    checks["bytes_sha256_equal"] = (
        bool(save_json.get("saved_sha256"))
        and save_json.get("saved_sha256") == reopen_json.get("loaded_sha256"))
    checks["saved_sha256"] = save_json.get("saved_sha256")
    checks["loaded_sha256"] = reopen_json.get("loaded_sha256")

    checks["counts_equal"] = (
        save_json.get("counts") is not None
        and save_json.get("counts") == reopen_json.get("counts"))

    all_ok = (checks["semantic_digest_equal"] and q_ok
              and checks["bytes_sha256_equal"] and checks["counts_equal"])
    if all_ok:
        return "pass", (
            f"语义摘要一致({str(dg_a)[:16]})；Q0==Q1=={save_json.get('Q0')}（{q_note}）；"
            f"磁盘字节哈希一致；结构计数一致（events/gates/nodes/conns 全等）"), checks
    bad = [k for k in ("semantic_digest_equal", "Q_equal",
                       "bytes_sha256_equal", "counts_equal") if not checks.get(k)]
    return "fail", f"重开回读不一致: {', '.join(bad)}", checks


def run_reopen_check(
    contract_json: Path,
    out_root: Path,
    *,
    case: str | None = None,
    k_max: int = 6,
    execute: bool = True,
    exe: Path = MEDINI_EXE_DEFAULT,
    workspace: Path = DEFAULT_WORKSPACE,
    workcopy_project: Path = WORKCOPY_PROJECT_DEFAULT,
    proj_name: str = WORKCOPY_PROJ_NAME,
) -> ReopenResult:
    """执行保存→重开→回读链。execute=False 或通道不可用时诚实落 blocked。"""
    t_start = time.time()
    data = json.loads(contract_json.read_text(encoding="utf-8"))
    case = case or data.get("name", contract_json.stem)
    slug = _slug(case)

    store = JobStore(out_root / "jobs")
    job = Job(kind="reopen_check", change_id=f"persist:{slug}")
    job.transition("validated", f"契约JSON解析成功 case={case}")
    store.save(job)

    evidence = (out_root / f"persist_{slug}_{job.job_id}").resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    inputs = evidence / "inputs"
    inputs.mkdir(exist_ok=True)
    phases = evidence / "phases"
    phases.mkdir(exist_ok=True)
    (evidence / "results").mkdir(exist_ok=True)

    # ---- 1. 契约校验 ----
    try:
        ft = from_contract_json(data)
    except Exception as e:  # ContractError
        job.transition("failed", f"契约校验失败: {e}")
        store.save(job)
        return ReopenResult(
            case=case, mode="dry-run", contract_valid=False, reference_q=None,
            save=None, reopen=None, verdict="error",
            verdict_detail=f"契约校验失败: {e}",
            evidence_dir=str(evidence), job_id=job.job_id)

    sem_hash = ft.semantic_hash()
    try:
        q_ref: Fraction | None = reference.exact_q(ft)
    except ValueError:
        q_ref = None

    # ---- 2. XML 工件 ----
    xml_path = inputs / f"{slug}_faulttreeplus.xml"
    ftplus.write_xml(ft, xml_path)
    xml_hash = hashlib.sha256(xml_path.read_bytes()).hexdigest()

    # ---- 3. 通道可用性 ----
    blocked_reason: list[str] = []
    if not execute:
        blocked_reason.append("execute=False（dry-run 请求）")
    if not exe.exists():
        blocked_reason.append(f"medini exe 不存在: {exe}")
    if not license_service_running():
        blocked_reason.append("许可 1055 未监听（用户态 lmgrd 未启动）")
    if not workcopy_project.exists():
        blocked_reason.append(f"工作副本工程不存在: {workcopy_project}")

    mode = "dry-run"
    save_json: dict[str, Any] | None = None
    reopen_json: dict[str, Any] | None = None

    if not blocked_reason:
        mode = "reopen-run"
        saved_fta = workcopy_project / "fta" / f"{slug}.fta"
        saved_fta.parent.mkdir(parents=True, exist_ok=True)
        if saved_fta.exists():          # 幂等：清掉上轮残留，确保本轮真的新写
            saved_fta.unlink()
        save_uri = f"platform:/resource/{proj_name}/fta/{slug}.fta"

        job.transition("queued", "实机通道可用，阶段A(保存)提交")
        store.save(job)

        # ---- 阶段 A：保存 ----
        tA = time.time()
        save_js = (phases / f"phaseA-save-{slug}.js").resolve()
        save_out = phases / f"phaseA-save-{slug}.json"
        save_log = phases / f"phaseA-{slug}.log"
        save_js.write_text(
            TEMPLATE_SAVE.read_text(encoding="utf-8")
            .replace("__CASE__", slug).replace("__K_MAX__", str(k_max))
            .replace("__XML_PATH__", xml_path.as_posix())
            .replace("__OUT_JSON__", save_out.as_posix())
            .replace("__RUN_LOG__", save_log.as_posix())
            .replace("__SAVE_URI__", save_uri)
            .replace("__SAVE_PATH__", saved_fta.as_posix()),
            encoding="utf-8")

        job.transition("running", "阶段A：导入+计算Q0+保存.fta")
        store.save(job)
        cliA = run_headless(save_js, exe=exe, workspace=workspace,
                            project=workcopy_project, expect_json=save_out)
        if save_out.exists() and save_out.stat().st_mtime >= tA:
            try:
                save_json = json.loads(save_out.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                save_json = {"status": "parse_error", "error": str(e)}
        else:
            save_json = {"status": "no_output", "exit_code": cliA.exit_code,
                         "stdout_tail": cliA.stdout_tail}

        # ---- 阶段 B：重开（新进程）----
        reopen_json = None
        if (save_json or {}).get("status") == "ok":
            tB = time.time()
            reopen_js = (phases / f"phaseB-reopen-{slug}.js").resolve()
            reopen_out = phases / f"phaseB-reopen-{slug}.json"
            reopen_log = phases / f"phaseB-{slug}.log"
            reopen_js.write_text(
                TEMPLATE_REOPEN.read_text(encoding="utf-8")
                .replace("__CASE__", slug).replace("__K_MAX__", str(k_max))
                .replace("__OUT_JSON__", reopen_out.as_posix())
                .replace("__RUN_LOG__", reopen_log.as_posix())
                .replace("__LOAD_URI__", save_uri)
                .replace("__LOAD_PATH__", saved_fta.as_posix()),
                encoding="utf-8")
            job.transition("running", "阶段B：新JVM重开+回读+重算Q1")
            store.save(job)
            cliB = run_headless(reopen_js, exe=exe, workspace=workspace,
                                project=workcopy_project, expect_json=reopen_out)
            if reopen_out.exists() and reopen_out.stat().st_mtime >= tB:
                try:
                    reopen_json = json.loads(reopen_out.read_text(encoding="utf-8"))
                except Exception as e:  # noqa: BLE001
                    reopen_json = {"status": "parse_error", "error": str(e)}
            else:
                reopen_json = {"status": "no_output", "exit_code": cliB.exit_code,
                               "stdout_tail": cliB.stdout_tail}

        # ---- 判定 ----
        verdict, detail, checks = _evaluate(save_json, reopen_json)
        if verdict == "pass":
            (evidence / "results" / "checks.json").write_text(
                json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
            job.transition("readback", "重开回读四重校核通过")
            job.transition("verified", detail)
        else:
            job.transition("failed", detail)
        checks_out = checks
    else:
        checks_out = {}
        verdict = "blocked"
        detail = "实机通道阻塞: " + "; ".join(blocked_reason)
        job.transition("blocked", detail)
    store.save(job)

    # ---- 4. 证据包 ----
    manifest = {
        "job_id": job.job_id,
        "case": case,
        "slug": slug,
        "mode": mode,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "duration_s": round(time.time() - t_start, 2),
        "contract_file": str(contract_json),
        "semantic_model_hash": sem_hash,
        "native_raw_hash": hashlib.sha256(contract_json.read_bytes()).hexdigest(),
        "xml_artifact_hash": xml_hash,
        "adapter_version": _pkg_version(),
        "contracts_version": "0.1.0",
        "medini_target": str(exe),
        "workcopy_project": str(workcopy_project),
        "workcopy_proj_name": proj_name,
        "saved_fta": str(workcopy_project / "fta" / f"{slug}.fta"),
        "verdict": verdict,
        "verdict_detail": detail,
        "job_state": job.state,
        "k_max": k_max,
        "checks": checks_out,
    }
    (evidence / "verification").mkdir(exist_ok=True)
    if q_ref is not None:
        (evidence / "verification" / "reference.json").write_text(
            json.dumps({"engine": "python-rational-exhaustive",
                        "Q_top_exact": str(q_ref),
                        "Q_top_decimal": float(q_ref)},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return ReopenResult(
        case=case, mode=mode, contract_valid=True,
        reference_q=str(q_ref) if q_ref is not None else None,
        save=save_json, reopen=reopen_json, checks=checks_out,
        verdict=verdict, verdict_detail=detail,
        evidence_dir=str(evidence), job_id=job.job_id,
        saved_fta=str(workcopy_project / "fta" / f"{slug}.fta"),
        artifacts={"manifest": str(evidence / "manifest.json"),
                   "xml": str(xml_path)})
