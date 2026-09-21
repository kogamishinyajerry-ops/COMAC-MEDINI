"""cli — medini-automation 命令行入口。

子命令：doctor / capabilities / validate / dry-run / run / readback
纪律：未实现动作返回明确错误码 2 与说明，禁止 success 占位。
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from ..domain.capability import initial_capabilities
from ..domain.model import ContractError, from_contract_json
from .. import __version__

MEDINI_EXE_DEFAULT = Path(r"E:\ANSYS Inc\Medini Analyze 2023 R2\Program\mediniAnalyze.exe")


# ---------------------------------------------------------------- doctor
def cmd_doctor(args: argparse.Namespace) -> int:
    rows: dict[str, object] = {"adapter_version": __version__}

    # python
    rows["python"] = sys.version.split()[0]

    # medini exe
    exe = Path(args.medini_exe) if args.medini_exe else MEDINI_EXE_DEFAULT
    rows["medini_exe"] = {"path": str(exe), "exists": exe.exists()}

    # license service
    try:
        r = subprocess.run(
            ["sc", "query", "ANSYS, Inc. License Manager"],
            capture_output=True, text=True, timeout=10)
        state = "unknown"
        for line in (r.stdout or "").splitlines():
            if "STATE" in line:
                state = line.split(":", 1)[1].strip()
                break
        rows["license_service"] = state
    except Exception as e:
        rows["license_service"] = f"probe-error: {e}"

    # license port 1055
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("localhost", 1055))
        rows["license_port_1055"] = "open"
    except OSError:
        rows["license_port_1055"] = "closed"
    finally:
        s.close()

    # workspace / project (headless 通道依赖)
    from ..adapters.medini_cli import (
        DEFAULT_PROJECT, DEFAULT_WORKSPACE, WORKCOPY_PROJECT_DEFAULT,
    )
    rows["headless_workspace"] = {
        "path": str(DEFAULT_WORKSPACE), "exists": DEFAULT_WORKSPACE.exists()}
    rows["headless_project"] = {
        "path": str(DEFAULT_PROJECT), "exists": DEFAULT_PROJECT.exists()}
    rows["workcopy_project"] = {
        "path": str(WORKCOPY_PROJECT_DEFAULT),
        "exists": WORKCOPY_PROJECT_DEFAULT.exists(),
        "note": "P1 保存/重开专用工作副本（不更新既有工程）"}

    # git
    git = shutil.which("git")
    rows["git"] = git or "not-found"

    overall = "READY" if (
        exe.exists() and rows["license_port_1055"] == "open") else "BLOCKED"
    rows["overall"] = overall
    if overall == "BLOCKED":
        rows["blocked_reason"] = (
            "实机通道不可用。若许可服务 STOPPED：需管理员 PowerShell 执行 "
            "Start-Service 'ANSYS, Inc. License Manager'（用户态 sc start 已验证 rc=5）。"
            "其余子命令（validate/dry-run/capabilities）不受影响。")

    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0 if overall == "READY" else 1


# ---------------------------------------------------------- capabilities
def cmd_capabilities(args: argparse.Namespace) -> int:
    caps = [c.to_dict() for c in initial_capabilities(__version__)]
    print(json.dumps(
        {"adapter_version": __version__, "capabilities": caps},
        ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- validate
def cmd_validate(args: argparse.Namespace) -> int:
    p = Path(args.contract)
    if not p.exists():
        print(json.dumps({"ok": False, "error": f"FILE_NOT_FOUND: {p}"}),
              file=sys.stderr)
        return 2
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ft = from_contract_json(data)
        print(json.dumps({
            "ok": True, "name": ft.name,
            "events": len(ft.events), "gates": len(ft.gates),
            "semantic_model_hash": ft.semantic_hash(),
        }, ensure_ascii=False, indent=2))
        return 0
    except ContractError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2


# ------------------------------------------------------------- dry-run/run
def cmd_slice(args: argparse.Namespace, execute: bool) -> int:
    from ..application.slice import run_slice
    contract = Path(args.contract)
    if not contract.exists():
        print(json.dumps({"error": f"FILE_NOT_FOUND: {contract}"}),
              file=sys.stderr)
        return 2
    out_root = Path(args.out)
    res = run_slice(
        contract, out_root, case=args.case, k_max=args.k_max,
        execute=execute)
    payload = {
        "case": res.case, "mode": res.mode, "verdict": res.verdict,
        "verdict_detail": res.verdict_detail,
        "semantic_model_hash": res.semantic_hash,
        "reference_Q": res.reference_q,
        "reference_MCS": res.reference_mcs,
        "xml": res.xml_path, "evidence_dir": res.evidence_dir,
        "job_id": res.job_id,
    }
    if res.medini:
        payload["medini_Q_top"] = res.medini.get("Q_top")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if res.verdict == "pass":
        return 0
    if res.verdict == "blocked":
        return 1
    return 3  # fail / error


# ---------------------------------------------------------------- readback
def cmd_readback(args: argparse.Namespace) -> int:
    """读取证据包 manifest + 参考值 + 实机结果并汇总三方对照。"""
    ev = Path(args.evidence)
    manifest_p = ev / "manifest.json"
    if not manifest_p.exists():
        print(json.dumps({"error": f"MANIFEST_NOT_FOUND: {manifest_p}"}),
              file=sys.stderr)
        return 2
    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    ref_p = ev / "verification" / "reference.json"
    medini_p = ev / "results" / "medini-actual.json"
    out = {"manifest": manifest, "reference": None, "medini": None}
    if ref_p.exists():
        out["reference"] = json.loads(ref_p.read_text(encoding="utf-8"))
    if medini_p.exists():
        out["medini"] = json.loads(medini_p.read_text(encoding="utf-8"))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ------------------------------------------------------------ reopen-check
def cmd_reopen_check(args: argparse.Namespace) -> int:
    """P1：保存 → 重开 → 回读链（两阶段独立进程）。"""
    from ..application.persistence import run_reopen_check
    contract = Path(args.contract)
    if not contract.exists():
        print(json.dumps({"error": f"FILE_NOT_FOUND: {contract}"}),
              file=sys.stderr)
        return 2
    res = run_reopen_check(
        contract, Path(args.out), case=args.case, k_max=args.k_max,
        execute=not args.dry, publish=args.publish)
    payload = {
        "case": res.case, "mode": res.mode, "verdict": res.verdict,
        "verdict_detail": res.verdict_detail,
        "reference_Q": res.reference_q,
        "checks": res.checks,
        "saved_fta": res.saved_fta,
        "publish": res.publish,
        "evidence_dir": res.evidence_dir, "job_id": res.job_id,
    }
    if res.save:
        payload["phaseA"] = {k: res.save.get(k) for k in
                             ("status", "Q0", "semantic_digest",
                              "saved_size", "saved_sha256", "error")}
    if res.reopen:
        payload["phaseB"] = {k: res.reopen.get(k) for k in
                             ("status", "Q1", "semantic_digest",
                              "loaded_size", "loaded_sha256", "error")}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if res.verdict == "pass":
        return 0
    if res.verdict == "blocked":
        return 1
    return 3  # fail / error


# --------------------------------------------------------- publish-diagram
def cmd_publish_diagram(args: argparse.Namespace) -> int:
    """P1.5：为落盘 .fta 生成 .fta_diagram 并登记进 .project.medini。"""
    from ..application.visibility import publish_diagram
    res = publish_diagram(args.fta, project_dir=args.project, case=args.case,
                          register=not args.no_register)
    payload = {
        "case": res.case, "slug": res.slug, "ok": res.ok,
        "fta": res.fta_path, "diagram": res.diagram_path,
        "diagram_sha256": res.diagram_sha256,
        "counts": res.counts, "bbox": res.bbox,
        "registration": (None if res.registration is None
                         else {"action": res.registration.action,
                               "project_file": res.registration.project_file}),
        "errors": res.errors,
        "notes": res.notes,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if res.ok else 2


# --------------------------------------------------------------- visibility
def cmd_visibility(args: argparse.Namespace) -> int:
    """审计工程的 GUI 可见性：哪些 .fta 已在工程树里、哪些是孤儿。"""
    from ..application.visibility import list_orphans
    report = list_orphans(args.project)
    payload = {
        "project": str(Path(args.project)),
        "registered_count": len(report["registered"]),
        "unregistered_count": len(report["unregistered"]),
        "registered": report["registered"],
        "unregistered": report["unregistered"],
        "note": "unregistered = 有 .fta 但未登记 PJDiagram，在 GUI 项目树中不可见；"
                "用 publish-diagram 发布",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not report["unregistered"] else 1


# ---------------------------------------------------------- verify-diagram
def cmd_verify_diagram(args: argparse.Namespace) -> int:
    """P1.5 实机校验：在 medini 里加载 .fta_diagram，核对结构与引用解析。"""
    from ..application.visibility import verify_diagram
    res = verify_diagram(args.case, project_dir=args.project, out_root=args.out)
    payload = {k: res.get(k) for k in
               ("case", "status", "verdict", "checks", "failed_checks",
                "expect_nodes", "expect_edges")}
    raw = res.get("raw") or {}
    payload["observed"] = {k: raw.get(k) for k in
                           ("children_total", "children_proxy", "edges_total",
                            "edges_proxy", "top_element_type", "diagram_etype")}
    if res.get("error"):
        payload["error"] = res["error"]
    if res.get("diagnosis"):
        payload["diagnosis"] = res["diagnosis"]
    if res.get("stdout_tail"):
        payload["stdout_tail"] = res["stdout_tail"]
    if res.get("log_tail"):
        payload["log_tail"] = res["log_tail"]
    if res.get("out_json"):
        payload["evidence_dir"] = str(Path(res["out_json"]).parent)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    v = res.get("verdict")
    return 0 if v == "pass" else (1 if v == "blocked" else 3)


# ------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="medini-automation",
        description="A线 medini-agent-automation CLI")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="环境自检（medini/许可/依赖）")
    d.add_argument("--medini-exe", default=None)
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("capabilities", help="按操作粒度的能力矩阵")
    c.set_defaults(func=cmd_capabilities)

    v = sub.add_parser("validate", help="校验契约 JSON（静态FTA）")
    v.add_argument("contract")
    v.set_defaults(func=cmd_validate)

    dr = sub.add_parser("dry-run", help="生成 XML+参考值+证据包（不实机）")
    dr.add_argument("contract")
    dr.add_argument("--out", default="runs")
    dr.add_argument("--case", default=None)
    dr.add_argument("--k", type=int, default=6, dest="k_max")
    dr.set_defaults(func=lambda a: cmd_slice(a, execute=False))

    r = sub.add_parser("run", help="实机执行（许可可用时）")
    r.add_argument("contract")
    r.add_argument("--out", default="runs")
    r.add_argument("--case", default=None)
    r.add_argument("--k", type=int, default=6, dest="k_max")
    r.set_defaults(func=lambda a: cmd_slice(a, execute=True))

    rb = sub.add_parser("readback", help="汇总证据包三方对照")
    rb.add_argument("evidence")
    rb.set_defaults(func=cmd_readback)

    rc = sub.add_parser("reopen-check",
                        help="P1：保存.fta→新进程重开→回读语义/重算Q 四重校核")
    rc.add_argument("contract")
    rc.add_argument("--out", default="runs")
    rc.add_argument("--case", default=None)
    rc.add_argument("--k", type=int, default=6, dest="k_max")
    rc.add_argument("--dry", action="store_true", help="只生成工件，不跑实机")
    rc.add_argument("--publish", action="store_true",
                    help="P1.5：保存成功后自动生成 .fta_diagram 并登记进工程")
    rc.set_defaults(func=cmd_reopen_check)

    pd = sub.add_parser("publish-diagram",
                        help="P1.5：生成 .fta_diagram + 登记 .project.medini（GUI 可见）")
    pd.add_argument("project", help="medini 工程目录（含 .project.medini）")
    pd.add_argument("--case", required=True, help="案例名（决定 .fta/.fta_diagram 文件名）")
    pd.add_argument("--fta", default=None, help=".fta 路径，默认 <project>/fta/<case>.fta")
    pd.add_argument("--no-register", action="store_true",
                    help="只生成图文件，不改 .project.medini")
    pd.set_defaults(func=cmd_publish_diagram)

    vis = sub.add_parser("visibility",
                         help="审计工程 GUI 可见性（列出未登记的孤儿 .fta）")
    vis.add_argument("project")
    vis.set_defaults(func=cmd_visibility)

    vd = sub.add_parser("verify-diagram",
                        help="P1.5：实机校验 .fta_diagram 可加载/结构/引用解析")
    vd.add_argument("project")
    vd.add_argument("--case", required=True)
    vd.add_argument("--out", default="runs")
    vd.set_defaults(func=cmd_verify_diagram)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
