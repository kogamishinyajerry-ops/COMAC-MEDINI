"""CLI entry points: validate / analyze / capabilities.

Exit codes (stable contract):
  0  success
  2  model invalid (semantic/parse error)
  3  unsupported semantics
  4  resource limit hit
  5  internal error
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from fractions import Fraction
from pathlib import Path

from .. import CONTRACT_VERSION, ENGINE_NAME, __version__
from ..adapters.json_io import load_model, probability_to_text
from ..domain import errors as err
from ..domain.errors import ModelError, ResourceLimitError
from ..domain.rate_model import INTERPRETATION as RATE_INTERPRETATION
from ..domain.semantic_hash import semantic_model_hash
from ..evidence.bundle import EvidenceBundle
from ..kernel.solve import (
    DEFAULT_IMPORTANCE_MAX_EVENTS,
    DEFAULT_MAX_MCS_PATHS,
    DEFAULT_MAX_NODES,
    IMPORTANCE_MODES,
    SolveResult,
    solve_model,
)

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_UNSUPPORTED = 3
EXIT_RESOURCE = 4
EXIT_INTERNAL = 5

_CALCULATION_MODE = "exact_bdd_shannon_fraction"

_UNSUPPORTED_CODES = frozenset({err.UNSUPPORTED_GATE, err.RATE_UNSUPPORTED})

# Published with every importance payload so a consumer never has to guess
# which convention produced the numbers.
IMPORTANCE_CONVENTIONS = {
    "birnbaum_importance": "dQ/dq_i = P(top|x_i=1) - P(top|x_i=0)",
    "fussell_vesely": "q_i*BI_i/Q = 1 - P(top|x_i=0)/Q (equals criticality importance)",
    "risk_achievement_worth": "P(top|x_i=1)/Q — RATIO form (DOE/NASA PSA), not 1+dQ/Q",
    "risk_reduction_worth": "Q/P(top|x_i=0) — RATIO form (DOE/NASA PSA), not 1-dQ/Q",
    "cofactors": "exact restriction of the compiled function; "
                 "Q = q_i*q+_i + (1-q_i)*q-_i holds exactly for every event",
    "undefined_policy": "a measure that is mathematically undefined is reported as null "
                        "with a reason entry; it is never replaced by a placeholder number",
}



def _exit_for_code(code: str) -> int:
    return EXIT_UNSUPPORTED if code in _UNSUPPORTED_CODES else EXIT_INVALID


def _importance_record_payload(record, max_digits: int | None) -> dict:
    def text(value):
        return None if value is None else probability_to_text(value, max_digits=max_digits)

    payload = {
        "event_id": record.event_id,
        "in_support": record.in_support,
        "event_probability": text(record.probability),
        "top_probability_given_failed": text(record.top_given_failed),
        "top_probability_given_working": text(record.top_given_working),
        "birnbaum_importance": text(record.birnbaum),
        "fussell_vesely": text(record.fussell_vesely),
        "risk_achievement_worth": text(record.risk_achievement_worth),
        "risk_reduction_worth": text(record.risk_reduction_worth),
    }
    if record.undefined:
        payload["undefined"] = [
            {"measure": measure, "reason": reason} for measure, reason in record.undefined
        ]
    return payload


def _error_payload(code: str, message: str, run_id: str | None = None, model_id: str | None = None,
                   sem_hash: str | None = None) -> dict:
    return {
        "schema_version": CONTRACT_VERSION,
        "run_id": run_id,
        "model_id": model_id,
        "engine_version": __version__,
        "semantic_model_hash": sem_hash,
        "status": "unsupported" if code in _UNSUPPORTED_CODES else "failed",
        "top_probability": None,
        "minimal_cut_sets": None,
        "cut_sets_complete": None,
        "importance_measures": None,
        "importance_status": None,
        "calculation_mode": _CALCULATION_MODE,
        "warnings": [],
        "evidence_ids": [],
        "approval_state": "not_granted_by_this_result",
        "error": {"code": code, "message": message},
    }


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        model = load_model(args.model)
    except ModelError as exc:
        print(json.dumps(_error_payload(exc.code, exc.message), indent=2, ensure_ascii=False))
        return _exit_for_code(exc.code)
    payload = {
        "schema_version": CONTRACT_VERSION,
        "model_id": model.model_id,
        "baseline_id": model.baseline_id,
        "valid": True,
        "semantic_model_hash": semantic_model_hash(model),
        "basic_event_count": len(model.basic_events),
        "gate_count": len(model.gates),
        "top_event": model.top_event,
        "engine_version": __version__,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return EXIT_OK


def cmd_analyze(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    run_id = args.run_id or Path(args.model).stem
    model_path = Path(args.model)
    bundle: EvidenceBundle | None = EvidenceBundle(args.evidence_dir, run_id) if args.evidence_dir else None

    if bundle is not None:
        input_hash = bundle.copy_input(model_path, "model_input.json")

    try:
        model = load_model(model_path)
    except ModelError as exc:
        payload = _error_payload(exc.code, exc.message, run_id=run_id)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        if bundle is not None:
            a = {"inputs/model_input.json": input_hash}
            a["results/analysis_result.json"] = bundle.write_json(bundle.results, "analysis_result.json", payload)
            bundle.finalize(
                model_file=model_path.name, semantic_hash="", status=payload["status"],
                artifacts=a, notes=[f"validation rejected: {exc.code}"], started=started,
            )
        return _exit_for_code(exc.code)

    sem_hash = semantic_model_hash(model)
    has_rates = bool(model.rates)
    payload = {
        "schema_version": CONTRACT_VERSION,
        "model_schema_version": model.schema_version,
        "run_id": run_id,
        "model_id": model.model_id,
        "engine_version": __version__,
        "semantic_model_hash": sem_hash,
        "status": None,
        "top_probability": None,
        "minimal_cut_sets": None,
        "cut_sets_complete": None,
        "importance_measures": None,
        "importance_status": None,
        "calculation_mode": _CALCULATION_MODE,
        "probability_interpretation": (
            "mission_failure_probability_from_constant_rate"
            if has_rates
            else "fixed_conditioned_probability"
        ),
        "warnings": [],
        "evidence_ids": [f"run_{run_id}/inputs/model_input.json"] if bundle else [],
        "approval_state": "not_granted_by_this_result",
    }
    if has_rates:
        payload["rate_provenance"] = [
            {"event_id": eid, "interpretation": RATE_INTERPRETATION, **record}
            for eid, record in sorted(model.rates.items())
        ]
        payload["rate_precision_digits"] = model.rate_precision_digits
        payload["warnings"].append(
            "q = -expm1(-lambda*t) is a MISSION-TIME failure probability. It is NOT a "
            "per-flight-hour rate, NOT average unavailability, and NOT a repairable "
            "steady-state value. Rate inputs were accepted only for non-repairable events "
            "with explicit units and mission time."
        )

    notes: list[str] = []
    artifacts: dict[str, str] = {"inputs/model_input.json": input_hash} if bundle else {}
    try:
        solution: SolveResult = solve_model(
            model,
            max_nodes=args.max_nodes,
            max_mcs_paths=args.max_mcs_paths,
            importance=args.importance,
        )
        payload["status"] = "succeeded" if solution.cut_sets_complete else "resource_limited"
        display_digits = model.rate_precision_digits if has_rates else None
        payload["top_probability"] = probability_to_text(
            solution.top_probability, max_digits=display_digits
        )
        payload["minimal_cut_sets"] = solution.minimal_cut_sets
        payload["cut_sets_complete"] = solution.cut_sets_complete
        payload["engine_stats"] = {
            "bdd_node_count": solution.bdd_node_count,
            "variable_order": list(solution.variable_order),
            "elapsed_seconds": round(solution.elapsed_seconds, 6),
            "limits": solution.limits,
        }
        payload["importance_status"] = solution.importance_status
        if solution.importance is None:
            payload["warnings"].append(
                f"importance measures not computed ({solution.importance_status}): "
                f"{solution.importance_note}"
            )
        else:
            # ranked by Fussell-Vesely descending (undefined last, ties by id):
            # deterministic, and puts the biggest contributors first
            ranked = sorted(
                solution.importance,
                key=lambda r: (
                    0 if r.fussell_vesely is not None else 1,
                    -(r.fussell_vesely or Fraction(0)),
                    r.event_id,
                ),
            )
            payload["importance_measures"] = [
                _importance_record_payload(record, display_digits) for record in ranked
            ]
            payload["importance_conventions"] = IMPORTANCE_CONVENTIONS
            payload["importance_exact"] = _importance_is_exact(
                payload["importance_measures"]
            )
        if display_digits is not None:
            exact_text = _exact_probability_text(solution.top_probability)
            if exact_text != payload["top_probability"]:
                payload["engine_stats"]["top_probability_exact"] = exact_text
                payload["warnings"].append(
                    f"top_probability is displayed rounded to {display_digits} significant digits; "
                    "the exact rational of the computed value is in engine_stats.top_probability_exact."
                )
            if payload.get("importance_measures") and not payload.get("importance_exact"):
                payload["warnings"].append(
                    f"importance values are displayed at the model's declared rate precision "
                    f"({display_digits} significant digits), matching probability_interpretation. "
                    "The engine computes them with exact rationals, so the shown digits are a "
                    "faithful rounding of the exact result; re-running the same model with the "
                    "same engine version reproduces them bit-for-bit."
                )
        if not solution.cut_sets_complete:
            payload["warnings"].append(
                "minimal cut set enumeration hit the path limit; reported family is INCOMPLETE"
            )
            notes.append("mcs truncated at path limit")
        exit_code = EXIT_OK if solution.cut_sets_complete else EXIT_RESOURCE
    except ResourceLimitError as exc:
        payload["status"] = "resource_limited"
        payload["warnings"].append(str(exc))
        notes.append(f"resource limit: {exc}")
        exit_code = EXIT_RESOURCE
    except Exception as exc:  # internal error — honest failure, never fake success
        payload["status"] = "failed"
        payload["warnings"].append(f"internal error: {type(exc).__name__}: {exc}")
        notes.append("internal error")
        exit_code = EXIT_INTERNAL

    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if bundle is not None:
        artifacts["results/analysis_result.json"] = bundle.write_json(
            bundle.results, "analysis_result.json", payload
        )
        artifacts["reports/report.md"] = bundle.write_text(
            bundle.reports, "report.md", _render_report(model, payload)
        )
        artifacts["execution/engine_meta.json"] = bundle.write_json(
            bundle.execution,
            "engine_meta.json",
            {
                "engine": ENGINE_NAME,
                "engine_version": __version__,
                "contract_version": CONTRACT_VERSION,
                "calculation_mode": _CALCULATION_MODE,
                "python": sys.version.split()[0],
                "argv": sys.argv,
            },
        )
        bundle.finalize(
            model_file=model_path.name,
            semantic_hash=sem_hash,
            status=payload["status"],
            artifacts=artifacts,
            notes=notes,
            started=started,
        )
    return exit_code


def _exact_probability_text(fraction) -> str:
    """Exact decimal of a bounded length, else a compact exact rational note."""
    exact = probability_to_text(fraction, max_digits=None)
    if len(exact) <= 400:
        return exact
    return f"{fraction.numerator}/{fraction.denominator}"


_IMPORTANCE_VALUE_FIELDS = (
    "event_probability",
    "top_probability_given_failed",
    "top_probability_given_working",
    "birnbaum_importance",
    "fussell_vesely",
    "risk_achievement_worth",
    "risk_reduction_worth",
)


def _importance_is_exact(measures: list[dict]) -> bool:
    """True when every rendered importance value round-trips unchanged through
    exact rendering, i.e. display rounding did not lose any information."""
    for record in measures:
        for field in _IMPORTANCE_VALUE_FIELDS:
            text = record.get(field)
            if text is None:
                continue
            if probability_to_text(Fraction(text), max_digits=None) != text:
                return False
    return True


def _render_report(model, payload: dict) -> str:
    warnings = "\n".join(f"- {w}" for w in payload.get("warnings", [])) or "- none"
    mcs = payload.get("minimal_cut_sets") or []
    mcs_lines = "\n".join(
        ", ".join(f"`{e}`" for e in cut) for cut in mcs
    ) or "_not available_"
    stats = payload.get("engine_stats") or {}

    rate_section = ""
    if payload.get("rate_provenance"):
        rows = ["| event | lambda | t | lambda*t | q (mission) |", "| --- | --- | --- | --- | --- |"]
        for rec in payload["rate_provenance"]:
            rows.append(
                f"| `{rec['event_id']}` | {rec['lambda']} {rec['lambda_unit']} | "
                f"{rec['mission_time']} {rec['mission_time_unit']} | {rec['lambda_t']} | {rec['q']} |"
            )
        rate_section = f"""
## Failure-rate provenance (declared → converted)

Formula: `q = -expm1(-lambda*t)`, precision {payload.get('rate_precision_digits')} significant digits.
Non-repairable only; units and mission time explicit.

{chr(10).join(rows)}

**Interpretation:** `{payload.get('probability_interpretation')}` — these q values are
mission-time failure probabilities, NOT per-flight-hour rates.
"""

    exact_note = ""
    if "top_probability_exact" in stats:
        exact_note = f"\n- Exact rational of the computed value: `{stats['top_probability_exact']}`\n"

    importance_section = ""
    measures = payload.get("importance_measures")
    if measures:
        def short(text: str | None) -> str:
            if text is None:
                return "—"
            rounded = probability_to_text(Fraction(text), max_digits=6)
            return rounded

        rows = [
            "| event | q_i | q+ (failed) | q− (working) | Birnbaum | Fussell-Vesely | RAW | RRW |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        undefined: list[str] = []
        for rec in measures:
            marker = "" if rec["in_support"] else " ⚠not-in-tree"
            rows.append(
                f"| `{rec['event_id']}`{marker} | {short(rec['event_probability'])} | "
                f"{short(rec['top_probability_given_failed'])} | "
                f"{short(rec['top_probability_given_working'])} | "
                f"{short(rec['birnbaum_importance'])} | {short(rec['fussell_vesely'])} | "
                f"{short(rec['risk_achievement_worth'])} | {short(rec['risk_reduction_worth'])} |"
            )
            for entry in rec.get("undefined", ()):
                undefined.append(f"- `{rec['event_id']}` / {entry['measure']}: `{entry['reason']}`")
        undefined_block = (
            "\n\n**Mathematically undefined measures** (reported as `—`, never faked):\n\n"
            + "\n".join(undefined)
            if undefined
            else ""
        )
        importance_section = f"""
## Importance measures

Ranked by Fussell-Vesely descending. `q+` = P(top | event failed), `q−` = P(top | event works).
`BI = q+ − q−`; `FV = q_i·BI/Q`; `RAW = q+/Q`; `RRW = Q/q−` (RAW/RRW are ratio forms, DOE/NASA PSA).
Values are shown rounded to 6 significant digits for readability; the exact rationals are in
`results/analysis_result.json` (JSON is authoritative).
Events marked ⚠not-in-tree exist in the model but do not appear in the compiled function.

{chr(10).join(rows)}{undefined_block}
"""
    elif payload.get("importance_status"):
        importance_section = (
            f"\n## Importance measures\n\nNot computed: `{payload.get('importance_status')}`.\n"
        )

    return f"""# Static FTA Analysis Report

- **Run**: `{payload.get('run_id')}`
- **Model**: `{model.model_id}` (baseline `{model.baseline_id}`), schema {payload.get('model_schema_version')}
- **Engine**: {ENGINE_NAME} v{__version__}, mode `{payload.get('calculation_mode')}`
- **Status**: `{payload.get('status')}`
- **Semantic model hash**: `{payload.get('semantic_model_hash')}`

## Result

- **Top event**: `{model.top_event}`
- **Top event probability**: `{payload.get('top_probability')}`
- **Probability interpretation**: `{payload.get('probability_interpretation')}`
- **Minimal cut sets** ({"complete" if payload.get("cut_sets_complete") else "INCOMPLETE — truncated"}): {len(mcs)}
{exact_note}
{mcs_lines}
{rate_section}{importance_section}
## Assumptions & condition

- Basic events independent: **{model.assumptions.basic_events_independent}**
- Probability semantics: `{model.assumptions.probability_semantics}`

- Condition: {model.assumptions.condition}

## Warnings

{warnings}

## Engine stats

- BDD nodes: {stats.get('bdd_node_count')}
- Variables: {len(stats.get('variable_order', []))}
- Elapsed: {stats.get('elapsed_seconds')} s

---

This result is a deterministic computation over the declared model.
It grants no approval and is not an aviation safety conclusion.
Approval state: `{payload.get('approval_state')}`.
"""


def cmd_capabilities(args: argparse.Namespace) -> int:
    payload = {
        "schema_version": CONTRACT_VERSION,
        "engine": ENGINE_NAME,
        "software_version": __version__,
        "adapter_version": "builtin-json-0.1.0",
        "capabilities": [
            {
                "capability_id": "validate_static_fta",
                "status": "verified",
                "method": "domain validation against contract 0.1.0 (fixed probabilities) and 0.2.0 (adds failure_rate)",
                "restrictions": [
                    "AND/OR/K_OF_N only; coherent models",
                    "0.1.0: fixed conditioned probabilities; 0.2.0: exactly one of probability/failure_rate per event",
                ],
                "evidence_ids": [
                    "tests/test_seed_cases.py",
                    "tests/test_rate_model.py",
                    "reference/03_contracts/cases.json",
                ],
            },
            {
                "capability_id": "analyze_static_fta_probability",
                "status": "verified",
                "method": "ROBDD compile + exact Fraction Shannon expansion",
                "restrictions": ["independent basic events; p in [0,1] fixed conditioned"],
                "evidence_ids": ["tests/test_seed_cases.py", "tests/test_cross_check.py"],
            },
            {
                "capability_id": "minimal_cut_sets",
                "status": "verified",
                "method": "BDD path enumeration + inclusion minimality; completeness flag",
                "restrictions": ["path cap; truncation reported as incomplete, never hidden"],
                "evidence_ids": ["tests/test_seed_cases.py"],
            },
            {
                "capability_id": "exponential_rate_to_probability",
                "status": "verified",
                "method": "q = -expm1(-lambda*t) via high-precision Decimal (Taylor for small x); "
                          "model schema 0.2.0 failure_rate block",
                "restrictions": [
                    "non-repairable only (repairable must be explicitly false)",
                    "lambda_unit 1/h and mission_time_unit h required",
                    "mission_time required",
                    "result is a MISSION failure probability, not a per-flight-hour metric",
                ],
                "evidence_ids": [
                    "tests/test_rate_model.py",
                    "verification/run_rate_cross_check.py",
                ],
            },
            {
                "capability_id": "importance_measures",
                "status": "verified",
                "method": "exact cofactors from the compiled ROBDD (one top-down reach pass "
                          "plus a difference array for level-skipping paths); "
                          "Birnbaum / Fussell-Vesely / RAW / RRW in O(#nodes)",
                "restrictions": [
                    "coherent models only (same boundary as the solver)",
                    "RAW/RRW are ratio forms (DOE/NASA PSA), not incremental forms",
                    "Q=0 or q-=0 cases are reported as null WITH a reason, never faked",
                    "auto mode skips models wider than "
                    f"{DEFAULT_IMPORTANCE_MAX_EVENTS} basic events (stated in the payload)",
                ],
                "evidence_ids": [
                    "tests/test_importance.py",
                    "verification/run_importance_cross_check.py",
                ],
            },
            {
                "capability_id": "other_rate_models",
                "status": "unsupported",
                "method": "explicit rejection with RATE_UNSUPPORTED",
                "restrictions": ["Weibull, repair, dormancy, inspection intervals are all rejected"],
                "evidence_ids": ["tests/test_rate_model.py"],
            },
            {
                "capability_id": "dynamic_gates_repairs_dormant",
                "status": "unsupported",
                "method": "explicit rejection with UNSUPPORTED_GATE",
                "restrictions": ["PAND, PDP, spare, repair, dormancy are all rejected"],
                "evidence_ids": ["tests/test_seed_cases.py"],
            },
            {
                "capability_id": "fmea_requirements_traceability",
                "status": "unsupported",
                "method": "not implemented in v0.1.0",
                "restrictions": ["30-day scope; see docs/NEXT_STEPS.md"],
                "evidence_ids": [],
            },
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="native-safety",
        description="Autonomous static FTA analysis (B-line). Deterministic, offline, no Medini.",
    )
    parser.add_argument("--version", action="version", version=f"{ENGINE_NAME} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate a static FTA JSON model")
    p_validate.add_argument("model", help="path to model JSON")
    p_validate.set_defaults(func=cmd_validate)

    p_analyze = sub.add_parser("analyze", help="run deterministic FTA analysis")
    p_analyze.add_argument("model", help="path to model JSON")
    p_analyze.add_argument("--evidence-dir", default=None, help="write evidence bundle under this dir")
    p_analyze.add_argument("--run-id", default=None, help="run identifier (default: model stem)")
    p_analyze.add_argument("--max-nodes", type=int, default=DEFAULT_MAX_NODES)
    p_analyze.add_argument("--max-mcs-paths", type=int, default=DEFAULT_MAX_MCS_PATHS)
    p_analyze.add_argument(
        "--importance",
        choices=IMPORTANCE_MODES,
        default="auto",
        help="importance measures: auto (default, computed unless the model is very wide), "
             "on (always), off (skip; the payload states it was skipped)",
    )
    p_analyze.set_defaults(func=cmd_analyze)

    p_caps = sub.add_parser("capabilities", help="print capability matrix with honest statuses")
    p_caps.set_defaults(func=cmd_capabilities)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
