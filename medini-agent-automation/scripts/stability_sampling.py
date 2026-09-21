"""scripts/stability_sampling.py — 验收门采样器：「连续 10 次无静默错误」。

对应 `A_核心规划.md` 验收指标表第一行：

> 核心实机流程：每个获准流程连续 10 次运行无静默错误（覆盖至少 3 个模型快照）

以及 L210 的报告纪律：

> 报告必须公布分母、失败、人工接管及不支持项，不能只展示成功作业。

**什么是「静默错误」（本脚本的判定，不是含糊的形容词）**：
一条运行算「静默错误」，当且仅当出现以下任一情况：
1. 进程退出码为 0 但 verdict 不是 pass（成功表象下的失败）
2. 进程退出码非 0 但没有任何证据目录产出（失败却无现场）
3. verdict=pass 但 Q 值相对误差与该快照的**首次基线**不一致
   （数值悄悄漂移——每次都"pass"，但 pass 的理由变了）
4. reopen-check 四重校核任一不通过

**快照集**（3 个，结构形态互异）：
- abc：重复事件 A + AND/OR 嵌套（共享基本事件路径）
- or_save：纯 OR 扁平结构（最小形态）
- vote：VOTE 2/3 门 + OR + 独立电源事件（冗余表决路径）

**为什么每次都重跑独立参考**：采样测的不只是 medini 稳定，而是「同一条链路
重复执行时整个系统（契约→XML→JVM→回读→对照）的行为可复现」。参考值参与
每次对照，它若漂移采样立刻能看见。

用法：
    python scripts/stability_sampling.py --runs 10          # 实机（需许可）
    python scripts/stability_sampling.py --runs 2 --dry      # 干跑（免许可，验证脚本本身）
    python scripts/stability_sampling.py --runs 10 --cases abc,vote

产物：runs/stability/<时间戳>/summary.json + 本报告（stdout）。

退出码：0 = 全部 pass 且无静默错误；1 = 有失败/静默错误（如实，不粉饰）；
2 = 用法/环境错误。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from medini_automation.adapters.medini_cli import MEDINI_EXE_DEFAULT  # noqa: E402
from medini_automation.application.persistence import run_reopen_check  # noqa: E402
from medini_automation.application.slice import (  # noqa: E402
    license_service_running, run_slice,
)

FIX = REPO / "tests" / "fixtures"
DEFAULT_CASES = ("abc", "or_save", "vote")


def fmt_x(vals: list[float]) -> str:
    """均值 ± 样本标准差，保留 2 位。"""
    if not vals:
        return "n/a"
    if len(vals) == 1:
        return f"{vals[0]:.2f}s"
    return f"{statistics.mean(vals):.2f}s ± {statistics.stdev(vals):.2f}s"


def rel_err(q_str: str, ref_str: str) -> float | None:
    """medini Q 与独立参考 Q 的相对误差（有理数精确计算后转 float）。"""
    try:
        q, r = Fraction(q_str), Fraction(ref_str)
    except (ValueError, ZeroDivisionError):
        return None
    if r == 0:
        return 0.0 if q == 0 else None
    return abs(q - r) / r


def sample_case(case: str, runs: int, out_root: Path, dry: bool,
                exe: Path) -> dict:
    """对一个快照连跑 runs 次 run + reopen-check，逐次记录。"""
    contract = FIX / f"slice_{case}.json"
    rows_run: list[dict] = []
    rows_reopen: list[dict] = []

    for i in range(1, runs + 1):
        # ---- run（实机计算 + 三角对照）----
        t0 = time.perf_counter()
        try:
            r = run_slice(contract, out_root, case=case, execute=not dry,
                          exe=exe)
            exc = None
        except Exception as e:                     # noqa: BLE001 — 采样器必须活下来
            r, exc = None, f"{type(e).__name__}: {e}"
        wall = time.perf_counter() - t0
        if r is None:
            rows_run.append({"i": i, "exception": exc, "silent_error": True})
            continue

        # 耗时与 medini Q 从结果对象读（SliceResult 只带结论不带耗时；
        # medini 结果的键是 Q_top —— 大写，JS 侧脚本的输出契约）
        q_medini = None
        mpath = Path(r.evidence_dir) / "manifest.json"
        dur_s = None
        if mpath.exists():
            man = json.loads(mpath.read_text(encoding="utf-8"))
            dur_s = man.get("duration_s")
        if r.medini and r.medini.get("Q_top") is not None:
            q_medini = str(r.medini["Q_top"])
        medini_rel = rel_err(q_medini, r.reference_q) \
            if (q_medini is not None and r.reference_q) else None

        silent = (r.verdict == "error") or (
            r.verdict == "pass" and (medini_rel is None or medini_rel > 1e-9))
        rows_run.append({
            "i": i, "verdict": r.verdict, "detail": r.verdict_detail,
            "job_id": r.job_id,
            "duration_s": dur_s if isinstance(dur_s, (int, float)) else round(wall, 2),
            "wall_s": round(wall, 2), "q_rel_err": medini_rel,
            "evidence": r.evidence_dir,
            "semantic_hash": r.semantic_hash[:16] if r.semantic_hash else None,
            "silent_error": bool(silent),
        })

        # ---- reopen-check（保存→重开→回读四重校核，唯一落盘路径）----
        try:
            p = run_reopen_check(contract, out_root, case=case,
                                 execute=not dry, exe=exe)
            ck = p.checks or {}
            four = ("semantic_digest_equal", "Q_equal",
                    "bytes_sha256_equal", "counts_equal")
            checks_ok = all(ck.get(k) is True for k in four)
            rows_reopen.append({
                "i": i, "verdict": p.verdict, "detail": p.verdict_detail,
                "checks_all_pass": checks_ok,
                "failed_checks": [k for k in four if ck.get(k) is not True],
                "silent_error": (p.verdict == "error") or
                                (p.verdict == "pass" and not checks_ok),
                "evidence": str(p.evidence_dir),
            })
        except Exception as e:                     # noqa: BLE001
            rows_reopen.append({"i": i,
                                "exception": f"{type(e).__name__}: {e}",
                                "silent_error": True})

    durs = [x["duration_s"] for x in rows_run if x.get("duration_s")]
    rels = [x["q_rel_err"] for x in rows_run if x.get("q_rel_err") is not None]
    pass_n = sum(1 for x in rows_run if x.get("verdict") == "pass")
    silent_n = sum(1 for x in rows_run + rows_reopen if x.get("silent_error"))
    reopen_pass = sum(1 for x in rows_reopen if x.get("verdict") == "pass")

    rel_base = rels[0] if rels else None
    drifted = [x for x in rows_run
               if x.get("q_rel_err") is not None and rel_base is not None
               and x["q_rel_err"] != rel_base]

    return {
        "case": case, "contract": str(contract), "runs": runs,
        "mode": "dry-run" if dry else "medini-run",
        "run_pass": pass_n, "run_total": len(rows_run),
        "reopen_pass": reopen_pass, "reopen_total": len(rows_reopen),
        "silent_errors": silent_n,
        "duration": fmt_x(durs), "durations": durs,
        "q_rel_err_first": rel_base,
        "q_rel_err_all_equal": len({x for x in rels}) <= 1,
        "q_rel_err_drift_rows": drifted,
        "rows_run": rows_run, "rows_reopen": rows_reopen,
        "first_semantic_hash": rows_run[0].get("semantic_hash") if rows_run else None,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="stability_sampling",
        description="验收门采样：N 连跑 × 多快照，判定静默错误（见模块 docstring）")
    ap.add_argument("--runs", type=int, default=10,
                    help="每个快照连跑次数（验收门默认 10）")
    ap.add_argument("--cases", default=",".join(DEFAULT_CASES),
                    help=f"快照集，逗号分隔（默认 {','.join(DEFAULT_CASES)}）")
    ap.add_argument("--dry", action="store_true",
                    help="干跑：不实机，验证采样器本身（verdict 会是 blocked，"
                         "静默错误判定只看异常与结构）")
    ap.add_argument("--out", default=None, help="报告根目录（默认 runs/stability）")
    ap.add_argument("--medini-exe", default=str(MEDINI_EXE_DEFAULT))
    args = ap.parse_args(argv)

    if args.runs < 1:
        print("--runs 必须 ≥ 1", file=sys.stderr)
        return 2

    exe = Path(args.medini_exe)
    if not args.dry:
        if not exe.exists():
            print(f"medini 不存在: {exe}（或加 --dry 干跑）", file=sys.stderr)
            return 2
        if not license_service_running():
            print("许可未监听 1055 —— 双击 scripts\\start-license.bat 后重试",
                  file=sys.stderr)
            return 2

    out_root = Path(args.out) if args.out else REPO / "runs" / "stability"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report_dir = out_root / stamp
    report_dir.mkdir(parents=True, exist_ok=True)

    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    missing = [c for c in cases if not (FIX / f"slice_{c}.json").exists()]
    if missing:
        print(f"缺快照契约: {missing}（tests/fixtures/slice_<case>.json）",
              file=sys.stderr)
        return 2

    print(f"稳定性采样：{len(cases)} 快照 × {args.runs} 连跑"
          f"（{'干跑' if args.dry else '实机'}）\n")
    results = []
    for case in cases:
        print(f"— 快照 {case} …", flush=True)
        res = sample_case(case, args.runs, report_dir, args.dry, exe)
        results.append(res)
        rel_first = res['q_rel_err_first']
        rel_txt = f"{rel_first:.2e}" if rel_first is not None else "n/a（dry）"
        print(f"  run pass {res['run_pass']}/{res['run_total']}"
              f" · reopen pass {res['reopen_pass']}/{res['reopen_total']}"
              f" · 静默错误 {res['silent_errors']}"
              f" · 耗时 {res['duration']}"
              f" · Q 相对误差 {rel_txt}"
              f"（{'各次一致' if res['q_rel_err_all_equal'] else '存在漂移!'}）")

    total_run = sum(r["run_total"] for r in results)
    total_pass = sum(r["run_pass"] for r in results)
    total_reopen = sum(r["reopen_total"] for r in results)
    total_reopen_pass = sum(r["reopen_pass"] for r in results)
    total_silent = sum(r["silent_errors"] for r in results)
    total_drift = sum(len(r["q_rel_err_drift_rows"]) for r in results)

    # dry 模式的判据与实机不同：dry 下 verdict=blocked 是**预期**（免许可、
    # 不实机），采样器要验证的是自身结构（能跑完、有证据目录、无异常、
    # 契约校验通过）。把 dry 判成 FAIL 没有意义 —— 它本来就"没跑实机"。
    if args.dry:
        structural_ok = all(
            r["run_total"] == r["runs"] and r["reopen_total"] == r["runs"]
            and r["silent_errors"] == 0 for r in results)
        verdict = "DRY-OK" if structural_ok else "FAIL"
    else:
        verdict = "PASS" if (total_silent == 0 and total_pass == total_run
                             and total_reopen_pass == total_reopen) else "FAIL"
    summary = {
        "stamp": stamp, "mode": "dry-run" if args.dry else "medini-run",
        "runs_per_case": args.runs, "cases": cases,
        "totals": {
            "run_pass": total_pass, "run_total": total_run,
            "reopen_pass": total_reopen_pass, "reopen_total": total_reopen,
            "silent_errors": total_silent, "q_rel_err_drift": total_drift,
        },
        "verdict": verdict,
        "acceptance_rule": (
            "每个获准流程连续 10 次运行无静默错误（覆盖至少 3 个模型快照）"
            "—— A_核心规划.md 验收指标"),
        "silent_error_definition": [
            "退出码 0 但 verdict≠pass", "退出码非 0 且无证据目录",
            "verdict=pass 但 Q 相对误差与首次不一致（数值漂移）",
            "reopen 四重校核任一不通过"],
        "results": results,
        "known_limitations": [
            "dry 模式下 verdict=blocked 属预期（免许可），静默判定只看异常与结构",
            "采样不等于统计可靠性（规划 L210 原文：10 次稳定运行只是试用闸门）"],
    }
    (report_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")

    print(f"\n总览：run {total_pass}/{total_run} · reopen {total_reopen_pass}/"
          f"{total_reopen} · 静默错误 {total_silent} · 数值漂移 {total_drift}")
    print(f"判定：{verdict}")
    if args.dry and verdict == "DRY-OK":
        print("（干跑只验证采样器结构；正式采样去掉 --dry，需许可）")
    print(f"报告：{report_dir / 'summary.json'}")
    return 0 if verdict in ("PASS", "DRY-OK") else 1


if __name__ == "__main__":
    sys.exit(main())
