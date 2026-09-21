"""CLI entry points: validate / analyze / capabilities / store / review.

Exit codes (stable contract):
  0  success
  2  model invalid (semantic/parse error) or refused store/review request
  3  unsupported semantics or an incompatible store schema generation
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
from ..domain.fmea import DEFAULT_ATTENTION_TOP, plan_attention_drafts
from ..domain.ratnum import exact_decimal_or_fraction
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
from ..store import errors as store_err
from ..store import FMEA_CANDIDATE_STATES, IMPORTANCE_SORT_KEYS, SqliteRepository
from ..store.errors import StoreError

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
    if code in _UNSUPPORTED_CODES or code in store_err.UNSUPPORTED_CODES:
        return EXIT_UNSUPPORTED
    return EXIT_INVALID


def _store_exit_for_code(code: str) -> int:
    return EXIT_UNSUPPORTED if code in store_err.UNSUPPORTED_CODES else EXIT_INVALID


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _store_refusal(code: str, message: str) -> int:
    _emit({
        "schema_version": CONTRACT_VERSION,
        "engine_version": __version__,
        "status": "failed",
        "approval_state": "not_granted_by_this_result",
        "error": {"code": code, "message": message},
    })
    return _store_exit_for_code(code)


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
    # Defined here so the (optional) persist step can record a run that never
    # produced a solution — an honest "failed" row beats a missing one.
    solution: SolveResult | None = None
    try:
        solution = solve_model(
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

    # Persist BEFORE printing so the payload can carry the store outcome.
    # A refused record does not discard the analysis: the payload still holds
    # the full result, `store.status` says why it was not kept, and the exit
    # code reflects the refusal so a script cannot mistake it for success.
    store_exit = EXIT_OK
    if args.db:
        try:
            with SqliteRepository(args.db) as repo:
                outcome = repo.record_run(
                    run_id=run_id,
                    model=model,
                    solution=solution,
                    payload=payload,
                    actor=args.actor,
                    calculation_mode=_CALCULATION_MODE,
                )
            payload["store"] = {
                "status": "recorded" if outcome.created else "idempotent",
                "db": args.db,
                "run_id": outcome.run_id,
                "baseline_hash": outcome.baseline_hash,
                "content_fingerprint": outcome.fingerprint,
                "note": outcome.reason,
            }
        except StoreError as exc:
            payload["store"] = {
                "status": "refused", "db": args.db,
                "error": {"code": exc.code, "message": exc.message},
            }
            payload["warnings"].append(f"store refused the record: {exc.code}: {exc.message}")
            store_exit = _store_exit_for_code(exc.code)
        except OSError as exc:
            payload["store"] = {
                "status": "refused", "db": args.db,
                "error": {"code": "STORE_IO", "message": str(exc)},
            }
            payload["warnings"].append(f"store unavailable: {exc}")
            store_exit = EXIT_INVALID
        if store_exit != EXIT_OK and exit_code == EXIT_OK:
            exit_code = store_exit

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
                "capability_id": "local_baseline_run_store",
                "status": "verified",
                "method": "single-file SQLite store (models/baselines/runs/importance/reviews) with "
                          "explicit migrations; semantic hash anchors every baseline; stored numbers "
                          "are exact decimals or exact n/d rationals (no float column exists)",
                "restrictions": [
                    "one writer, optimistic concurrency (expected baseline hash must match)",
                    "a baseline is immutable: a change registers a new hash and flags dependent runs stale",
                    "run_id is idempotent on identical content and refuses to overwrite on conflict",
                    "an agent may propose a change but never approve or apply it",
                    "the store carries no approval authority; approval_state stays "
                    "not_granted_by_this_result",
                ],
                "evidence_ids": [
                    "tests/test_store.py",
                    "verification/run_store_verification.py",
                ],
            },
            {
                "capability_id": "fmea_requirements_traceability",
                "status": "verified",
                "method": "FMEA base table (component / function / failure mode / cause / local and "
                          "upper-level effect / controls / evidence) with many-to-many links to basic "
                          "events and to requirement ids; every row reaches the official table only "
                          "through propose -> decide -> apply under a named human; the importance "
                          "ranking can drive draft attention items, which stay placeholders until a "
                          "human writes the real content",
                "restrictions": [
                    "base FMEA table only: no severity / occurrence / detection / RPN — those are "
                    "FMECA extensions and are not implemented",
                    "component / function / requirement ids are stable identifiers carried on the row; "
                    "there is no separate requirement or component object yet",
                    "a row whose source is 'inference' must state what remains unconfirmed, and the "
                    "original source survives promotion (a machine-proposed row is never laundered)",
                    "an official row is never rewritten in place: a revision is a new version and the "
                    "previous one is marked superseded and stays readable",
                    "links are checked against the fault-tree baseline the draft was proposed "
                    "against; a link that stops resolving after a baseline change is reported as a "
                    "traceability gap, not silently repaired or deleted",
                    "attention items generated from an importance ranking are work items, not content: "
                    "the engine ranks events but does not know failure modes, so every descriptive "
                    "field is a marked placeholder and the store refuses to promote one until a human "
                    "fills it in; generation from a superseded run is refused",
                ],
                "evidence_ids": [
                    "tests/test_fmea.py",
                    "verification/run_fmea_verification.py",
                ],
            },
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return EXIT_OK


# --------------------------------------------------------------------------
# store / review: persistence and the change-management minimum loop
# --------------------------------------------------------------------------


_STORE_ENVELOPE = {"schema_version": CONTRACT_VERSION, "engine_version": __version__}


def _store_payload(command: str, **fields) -> dict:
    return {**_STORE_ENVELOPE, "command": command, **fields}


def _load_or_fail(path: str) -> tuple[object | None, tuple[str, str] | None]:
    """Load and validate a model; on failure return (None, (code, message))."""
    try:
        return load_model(path), None
    except ModelError as exc:
        return None, (exc.code, exc.message)


def _model_refusal(code: str, message: str) -> int:
    _emit({
        **_STORE_ENVELOPE, "status": "failed",
        "error": {"code": code, "message": message},
    })
    return _exit_for_code(code)


def _raw_model_id(path: str) -> str | None:
    """Best-effort model_id from an unvalidated file, for the audit trail."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("model_id") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


def cmd_store(args: argparse.Namespace) -> int:
    command = args.store_command
    try:
        if command == "init":
            with SqliteRepository(args.db) as repo:
                _emit(_store_payload("store init", status="ok", store=repo.status()))
            return EXIT_OK

        if command == "status":
            with SqliteRepository(args.db) as repo:
                _emit(_store_payload("store status", status="ok", store=repo.status()))
            return EXIT_OK

        if command == "runs":
            with SqliteRepository(args.db) as repo:
                runs = repo.list_runs(args.model, include_stale=not args.current_only)
            _emit(_store_payload(
                "store runs", status="ok", count=len(runs),
                runs=[
                    {
                        "run_id": r["run_id"], "model_id": r["model_id"],
                        "baseline_hash": r["baseline_hash"],
                        "status": r["status"], "stale": bool(r["stale"]),
                        "stale_reason": r["stale_reason"],
                        "top_event_probability": r["top_event_probability"],
                        "importance_status": r["importance_status"],
                        "created_utc": r["created_utc"], "created_by": r["created_by"],
                    }
                    for r in runs
                ],
            ))
            return EXIT_OK

        if command == "show":
            with SqliteRepository(args.db) as repo:
                run = repo.get_run(args.run_id)
                if run is None:
                    return _store_refusal(store_err.RUN_NOT_FOUND, f"no run {args.run_id!r}")
                importance = repo.run_importance(args.run_id)
            run["payload"] = json.loads(run.pop("payload_json"))
            run["cut_sets"] = json.loads(run.pop("cut_sets_json"))
            _emit(_store_payload("store show", status="ok", run=run, importance=importance))
            return EXIT_OK

        if command == "baseline":
            with SqliteRepository(args.db) as repo:
                if repo.model_row(args.model_id) is None:
                    return _store_refusal(
                        store_err.MODEL_NOT_FOUND,
                        f"model {args.model_id!r} has no baseline in this store; "
                        "run `analyze --db` on a model of that id first",
                    )
                if args.all:
                    baselines = repo.list_baselines(args.model_id)
                    current = repo.current_baseline_hash(args.model_id)
                else:
                    one = repo.current_baseline(args.model_id)
                    baselines = [one] if one else []
                    current = one["baseline_hash"] if one else None
            _emit(_store_payload(
                "store baseline", status="ok", model_id=args.model_id,
                current_baseline_hash=current,
                baselines=[
                    {
                        "baseline_hash": b["baseline_hash"], "model_id": b["model_id"],
                        "top_event": b["top_event"], "basic_event_count": b["basic_event_count"],
                        "gate_count": b["gate_count"], "schema_version": b["schema_version"],
                        "probability_semantics": b["probability_semantics"],
                        "created_utc": b["created_utc"], "created_by": b["created_by"],
                        "is_current": b["baseline_hash"] == current,
                    }
                    for b in baselines
                ],
            ))
            return EXIT_OK

        if command == "adopt-baseline":
            model, failure = _load_or_fail(args.model)
            if failure is not None:
                return _model_refusal(*failure)
            with SqliteRepository(args.db) as repo:
                anchored, stale_ids = repo.register_model_baseline(
                    model, actor=args.reviewer,
                    expected_baseline_hash=args.expected_baseline_hash,
                )
            _emit(_store_payload(
                "store adopt-baseline", status="ok", model_id=model.model_id,
                baseline_hash=anchored, adopted_by=args.reviewer,
                stale_run_ids=list(stale_ids),
                note="re-anchored explicitly under a named reviewer; runs on the previous "
                     "baseline are flagged stale and remain readable",
            ))
            return EXIT_OK

        if command == "important":
            with SqliteRepository(args.db) as repo:
                if repo.get_run(args.run_id) is None:
                    return _store_refusal(store_err.RUN_NOT_FOUND, f"no run {args.run_id!r}")
                rows = repo.run_importance(args.run_id, by=args.by, top=args.top)
            _emit(_store_payload(
                "store important", status="ok", run_id=args.run_id, sorted_by=args.by,
                exact=True,
                convention="values are exact decimals or exact n/d rationals stored at write time; "
                           "sorting used exact Fractions, not the text",
                importance=rows,
            ))
            return EXIT_OK

        if command == "verify":
            with SqliteRepository(args.db) as repo:
                problems = repo.verify()
            _emit(_store_payload(
                "store verify", status="ok" if not problems else "failed",
                checks=[
                    "declared column types admit no float (B_核心规划 §231)",
                    "SQLite foreign_key_check",
                    "every baseline canonical_json re-hashes to its baseline_hash",
                    "every run payload agrees with its stored row",
                    "every stored number re-parses exactly",
                    "stored importance re-derives Q = q*q+ + (1-q)*q- and FV/RAW/RRW",
                    "stale flags agree with the current baseline of each model",
                ],
                problems=problems,
            ))
            return EXIT_OK if not problems else EXIT_INTERNAL

        if command == "export":
            with SqliteRepository(args.db) as repo:
                dump = repo.dump()
            text = json.dumps(dump, indent=2, ensure_ascii=False)
            if args.out:
                Path(args.out).write_text(text, encoding="utf-8")
                _emit(_store_payload(
                    "store export", status="ok", out=args.out,
                    tables={name: len(rows) for name, rows in dump["tables"].items()},
                ))
            else:
                print(text)
            return EXIT_OK

        raise StoreError(store_err.BAD_ARGUMENT, f"unknown store command {command!r}")
    except StoreError as exc:
        return _store_refusal(exc.code, exc.message)
    except OSError as exc:
        return _store_refusal("STORE_IO", str(exc))


def cmd_review(args: argparse.Namespace) -> int:
    command = args.review_command
    try:
        if command == "propose":
            model, failure = _load_or_fail(args.model)
            model_id = model.model_id if model is not None else (args.model_id or _raw_model_id(args.model))
            if model_id is None:
                return _store_refusal(
                    err.ID,
                    f"{args.model!r} did not validate and carries no readable model_id; "
                    "fix the model or pass --model-id so the rejected proposal is still recorded",
                )
            with SqliteRepository(args.db) as repo:
                record = repo.propose_review(
                    review_id=args.review_id,
                    model_id=model_id,
                    expected_baseline_hash=args.expected_baseline_hash,
                    proposed_model=model,
                    proposed_by=args.reviewer,
                    note=args.note,
                    validation_error=(
                        None if failure is None else {"code": failure[0], "message": failure[1]}
                    ),
                )
            _emit(_store_payload(
                "review propose", status="ok", review=record,
                note="a proposal never changes the baseline; approval and application are separate, "
                     "human-authorised steps",
            ))
            return EXIT_OK

        if command == "list":
            with SqliteRepository(args.db) as repo:
                records = repo.list_reviews(state=args.state, model_id=args.model_id)
            _emit(_store_payload("review list", status="ok", count=len(records), reviews=records))
            return EXIT_OK

        if command == "show":
            with SqliteRepository(args.db) as repo:
                record = repo.get_review(args.review_id)
            if record is None:
                return _store_refusal(store_err.REVIEW_NOT_FOUND, f"no review {args.review_id!r}")
            _emit(_store_payload("review show", status="ok", review=record))
            return EXIT_OK

        if command == "decide":
            with SqliteRepository(args.db) as repo:
                record = repo.decide_review(
                    args.review_id, approve=args.approve, reviewer=args.reviewer, note=args.note
                )
            _emit(_store_payload(
                "review decide", status="ok", review=record,
                note="a decision does not change the baseline; apply it explicitly",
            ))
            return EXIT_OK

        if command == "apply":
            with SqliteRepository(args.db) as repo:
                record, stale_ids = repo.apply_review(args.review_id, reviewer=args.reviewer)
            _emit(_store_payload(
                "review apply", status="ok", review=record, stale_run_ids=list(stale_ids),
                note="the approved baseline is now current; runs on the previous baseline are "
                     "flagged stale and their stored payloads remain readable",
            ))
            return EXIT_OK

        raise StoreError(store_err.BAD_ARGUMENT, f"unknown review command {command!r}")
    except StoreError as exc:
        return _store_refusal(exc.code, exc.message)
    except OSError as exc:
        return _store_refusal("STORE_IO", str(exc))


# --------------------------------------------------------------------------
# fmea: the FMEA base table and its traceability links
# --------------------------------------------------------------------------


_FMEA_ROW_FIELDS = (
    "model_id", "fmea_id", "version", "component_id", "function_id", "failure_mode", "cause",
    "local_effect", "system_effect", "source", "certainty", "inference_note", "content_hash",
    "validated_baseline_hash", "superseded", "superseded_by_version", "approved_by", "approved_utc",
    "created_utc", "created_by",
)


def _fmea_row_view(row: dict, linked_event_ids: list[str] | None = None) -> dict:
    """Decode a stored FMEA row for display (JSON sidecars parsed, types clean)."""
    view = {field: row[field] for field in _FMEA_ROW_FIELDS if field in row}
    view["superseded"] = bool(view.get("superseded"))
    for column, key in (
        ("controls_json", "controls"),
        ("evidence_json", "evidence"),
        ("requirement_ids_json", "requirement_ids"),
    ):
        try:
            view[key] = json.loads(row[column])
        except (KeyError, TypeError, json.JSONDecodeError):
            view[key] = None
    if linked_event_ids is not None:
        view["linked_event_ids"] = list(linked_event_ids)
    return view


def cmd_fmea(args: argparse.Namespace) -> int:
    command = args.fmea_command
    try:
        if command == "rows":
            with SqliteRepository(args.db) as repo:
                rows = repo.list_fmea_rows(args.model, include_superseded=args.include_superseded)
                view = [
                    _fmea_row_view(r, repo.fmea_links_of_row(r["model_id"], r["fmea_id"], r["version"]))
                    for r in rows
                ]
            _emit(_store_payload(
                "fmea rows", status="ok", count=len(view),
                official_table_note=(
                    "these are approved rows only; a machine-proposed draft lives in the candidate "
                    "queue until a named human confirms it"
                ),
                rows=view,
            ))
            return EXIT_OK

        if command == "candidates":
            with SqliteRepository(args.db) as repo:
                records = repo.list_fmea_candidates(state=args.state, model_id=args.model)
            _emit(_store_payload("fmea candidates", status="ok", count=len(records), candidates=records))
            return EXIT_OK

        if command == "show":
            with SqliteRepository(args.db) as repo:
                record = repo.get_fmea_candidate(args.candidate_id)
            if record is None:
                return _store_refusal(store_err.FMEA_NOT_FOUND, f"no FMEA candidate {args.candidate_id!r}")
            _emit(_store_payload("fmea show", status="ok", candidate=record))
            return EXIT_OK

        if command == "propose":
            try:
                raw_row = json.loads(Path(args.row).read_text(encoding="utf-8"))
            except OSError as exc:
                return _store_refusal("STORE_IO", str(exc))
            except json.JSONDecodeError as exc:
                return _store_refusal(err.FMEA_INPUTS, f"{args.row!r} is not valid JSON ({exc})")
            with SqliteRepository(args.db) as repo:
                record = repo.propose_fmea_candidate(
                    candidate_id=args.candidate_id,
                    raw_row=raw_row,
                    expected_baseline_hash=args.expected_baseline_hash,
                    proposed_by=args.reviewer,
                    expected_version=args.expected_version,
                    note=args.note,
                )
            _emit(_store_payload(
                "fmea propose", status="ok", candidate=record,
                note="a draft never enters the official table by itself; approval and application are "
                     "separate, human-authorised steps",
            ))
            return EXIT_OK

        if command == "decide":
            with SqliteRepository(args.db) as repo:
                record = repo.decide_fmea_candidate(
                    args.candidate_id, approve=args.approve, reviewer=args.reviewer, note=args.note
                )
            _emit(_store_payload(
                "fmea decide", status="ok", candidate=record,
                note="a decision does not touch the official table; apply it explicitly",
            ))
            return EXIT_OK

        if command == "apply":
            with SqliteRepository(args.db) as repo:
                record, promoted = repo.apply_fmea_candidate(args.candidate_id, reviewer=args.reviewer)
                linked = repo.fmea_links_of_row(
                    promoted["model_id"], promoted["fmea_id"], promoted["version"]
                )
            _emit(_store_payload(
                "fmea apply", status="ok", candidate=record,
                row=_fmea_row_view(promoted, linked),
                note="the draft is now an official row; any earlier version of the same fmea_id is "
                     "marked superseded and stays readable",
            ))
            return EXIT_OK

        if command == "trace":
            with SqliteRepository(args.db) as repo:
                if args.dangling:
                    rows = repo.dangling_fmea_links(args.model)
                    _emit(_store_payload(
                        "fmea trace", status="ok", mode="dangling", count=len(rows),
                        dangling=rows,
                        note="a linked event that no longer exists in the current baseline is a "
                             "traceability gap, not a corrupted store: the FMEA text is human "
                             "knowledge and is never deleted automatically",
                    ))
                    return EXIT_OK
                if args.event:
                    rows = repo.fmea_rows_for_event(args.model, args.event)
                    mode, key = "event", args.event
                else:
                    rows = repo.fmea_rows_by_component(args.model, args.component)
                    mode, key = "component", args.component
                view = [
                    _fmea_row_view(r, repo.fmea_links_of_row(r["model_id"], r["fmea_id"], r["version"]))
                    for r in rows
                ]
            _emit(_store_payload(
                "fmea trace", status="ok", mode=mode, model_id=args.model, key=key,
                count=len(view), rows=view,
                note="many-to-many: one row may cite several events and one event may be cited by "
                     "several rows; the association is never forced to be one-to-one",
            ))
            return EXIT_OK

        if command == "propose-from-importance":
            try:
                min_value = Fraction(args.min_value) if args.min_value is not None else None
            except (ValueError, ZeroDivisionError):
                return _store_refusal(
                    store_err.BAD_ARGUMENT,
                    f"--min-value {args.min_value!r} is not a number or fraction (e.g. 0.1 or 1/10)",
                )
            with SqliteRepository(args.db) as repo:
                run = repo.get_run(args.run_id)
                if run is None:
                    return _store_refusal(store_err.RUN_NOT_FOUND, f"no run {args.run_id!r}")
                if run["stale"]:
                    # The ranking came from a superseded baseline; promoting work
                    # items from it would point at a model that no longer exists.
                    return _store_refusal(
                        store_err.BASELINE_NOT_CURRENT,
                        f"run {args.run_id!r} sits on a superseded baseline "
                        f"({run['baseline_hash'][:12]}…); re-run `analyze --db` against the current "
                        "baseline and generate the attention items from that run",
                    )
                model_id = run["model_id"]
                rows = repo.run_importance(args.run_id, by=args.by)
                covered = repo.covered_event_ids(model_id)
                drafted = repo.drafted_fmea_states(model_id)
                drafts, skipped, not_considered = plan_attention_drafts(
                    rows,
                    model_id=model_id,
                    measure=args.by,
                    run_id=args.run_id,
                    baseline_hash=run["baseline_hash"],
                    covered_event_ids=covered,
                    drafted_fmea_states=drafted,
                    top=args.top,
                    min_value=min_value,
                )
                proposed = [
                    repo.propose_fmea_candidate(
                        candidate_id=f"ATTN-{args.run_id}-{draft['linked_event_ids'][0]}",
                        raw_row=draft,
                        expected_baseline_hash=run["baseline_hash"],
                        proposed_by=args.reviewer,
                        expected_version=None,
                        note=args.note
                        or f"attention item generated from the importance ranking of run {args.run_id}",
                    )
                    for draft in drafts
                ]
            _emit(_store_payload(
                "fmea propose-from-importance", status="ok", run_id=args.run_id,
                model_id=model_id, sorted_by=args.by, top=args.top,
                min_value=None if min_value is None else exact_decimal_or_fraction(min_value),
                proposed=len(proposed),
                candidates=[c["candidate_id"] for c in proposed],
                skipped=skipped,
                not_considered=not_considered,
                scanned=sorted({row["event_id"] for row in rows}),
                covered_events=sorted(covered),
                note="an attention item is a WORK ITEM, not a fact: the engine ranks events but "
                     "does not know a failure mode. Every descriptive field is a marked placeholder, "
                     "the store refuses to promote one, and only a named human can write the real row",
            ))
            return EXIT_OK

        raise StoreError(store_err.BAD_ARGUMENT, f"unknown fmea command {command!r}")
    except StoreError as exc:
        return _store_refusal(exc.code, exc.message)
    except OSError as exc:
        return _store_refusal("STORE_IO", str(exc))


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
    p_analyze.add_argument(
        "--db", default=None,
        help="persist this run into a local SQLite store (idempotent on identical content); "
             "a model whose semantics changed must be approved through the review loop first",
    )
    p_analyze.add_argument("--actor", default="local-cli", help="recorded as the run's author")
    p_analyze.set_defaults(func=cmd_analyze)

    p_caps = sub.add_parser("capabilities", help="print capability matrix with honest statuses")
    p_caps.set_defaults(func=cmd_capabilities)

    # ---- store -----------------------------------------------------------
    p_store = sub.add_parser("store", help="local SQLite store: baselines, runs, importance")
    store_sub = p_store.add_subparsers(dest="store_command", required=True)

    p_s = store_sub.add_parser("init", help="create (or migrate) the store file")
    p_s.add_argument("db")
    p_s.set_defaults(func=cmd_store)

    p_s = store_sub.add_parser("status", help="counts, stale runs, pending reviews")
    p_s.add_argument("db")
    p_s.set_defaults(func=cmd_store)

    p_s = store_sub.add_parser("runs", help="list runs and their staleness")
    p_s.add_argument("db")
    p_s.add_argument("--model", default=None, help="restrict to one model_id")
    p_s.add_argument("--current-only", action="store_true", help="hide runs on superseded baselines")
    p_s.set_defaults(func=cmd_store)

    p_s = store_sub.add_parser("show", help="full stored run: row, payload, importance")
    p_s.add_argument("db")
    p_s.add_argument("run_id")
    p_s.set_defaults(func=cmd_store)

    p_s = store_sub.add_parser("baseline", help="show the current (or all) baselines of a model")
    p_s.add_argument("db")
    p_s.add_argument("model_id")
    p_s.add_argument("--all", action="store_true", help="include superseded baselines")
    p_s.set_defaults(func=cmd_store)

    p_s = store_sub.add_parser(
        "adopt-baseline",
        help="re-anchor a model to new semantics under a named reviewer (marks old runs stale)",
    )
    p_s.add_argument("db")
    p_s.add_argument("model")
    p_s.add_argument("--expected-baseline-hash", required=True,
                     help="optimistic concurrency: the baseline you believe is current")
    p_s.add_argument("--reviewer", required=True, help="human identity taking responsibility")
    p_s.set_defaults(func=cmd_store)

    p_s = store_sub.add_parser("important", help="top-N events by an importance measure")
    p_s.add_argument("db")
    p_s.add_argument("run_id")
    p_s.add_argument("--by", choices=IMPORTANCE_SORT_KEYS, default="fussell_vesely")
    p_s.add_argument("--top", type=int, default=None)
    p_s.set_defaults(func=cmd_store)

    p_s = store_sub.add_parser("verify", help="integrity checks over the stored data")
    p_s.add_argument("db")
    p_s.set_defaults(func=cmd_store)

    p_s = store_sub.add_parser("export", help="dump every table as JSON (migration / round-trip)")
    p_s.add_argument("db")
    p_s.add_argument("--out", default=None, help="write to this file instead of stdout")
    p_s.set_defaults(func=cmd_store)

    # ---- review ----------------------------------------------------------
    p_review = sub.add_parser("review", help="change-management minimum loop for a model baseline")
    review_sub = p_review.add_subparsers(dest="review_command", required=True)

    p_r = review_sub.add_parser("propose", help="record a proposed model change (never applies it)")
    p_r.add_argument("db")
    p_r.add_argument("model")
    p_r.add_argument("--review-id", required=True)
    p_r.add_argument("--expected-baseline-hash", required=True)
    p_r.add_argument("--reviewer", default="agent",
                     help="who proposes (default: agent — an agent may propose but never approve)")
    p_r.add_argument("--note", default=None)
    p_r.add_argument("--model-id", default=None,
                     help="used only when the proposed model fails validation")
    p_r.set_defaults(func=cmd_review)

    p_r = review_sub.add_parser("list", help="list reviews")
    p_r.add_argument("db")
    p_r.add_argument("--state", default=None)
    p_r.add_argument("--model-id", default=None)
    p_r.set_defaults(func=cmd_review)

    p_r = review_sub.add_parser("show", help="show one review")
    p_r.add_argument("db")
    p_r.add_argument("review_id")
    p_r.set_defaults(func=cmd_review)

    p_r = review_sub.add_parser("decide", help="approve or reject a proposal (requires a human)")
    p_r.add_argument("db")
    p_r.add_argument("review_id")
    decision = p_r.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", dest="approve", action="store_false")
    p_r.add_argument("--reviewer", default=None)
    p_r.add_argument("--note", default=None)
    p_r.set_defaults(func=cmd_review)

    p_r = review_sub.add_parser("apply", help="apply an approved proposal (requires a human)")
    p_r.add_argument("db")
    p_r.add_argument("review_id")
    p_r.add_argument("--reviewer", default=None)
    p_r.set_defaults(func=cmd_review)

    # ---- fmea ------------------------------------------------------------
    p_fmea = sub.add_parser(
        "fmea", help="FMEA base table and traceability (many-to-many with basic events)"
    )
    fmea_sub = p_fmea.add_subparsers(dest="fmea_command", required=True)

    p_f = fmea_sub.add_parser("rows", help="the official FMEA table (approved rows only)")
    p_f.add_argument("db")
    p_f.add_argument("--model", default=None)
    p_f.add_argument("--include-superseded", action="store_true",
                     help="include versions replaced by a revision")
    p_f.set_defaults(func=cmd_fmea)

    p_f = fmea_sub.add_parser("candidates", help="the draft queue waiting on a human")
    p_f.add_argument("db")
    p_f.add_argument("--state", default=None, choices=list(FMEA_CANDIDATE_STATES))
    p_f.add_argument("--model", default=None)
    p_f.set_defaults(func=cmd_fmea)

    p_f = fmea_sub.add_parser("show", help="show one draft")
    p_f.add_argument("db")
    p_f.add_argument("candidate_id")
    p_f.set_defaults(func=cmd_fmea)

    p_f = fmea_sub.add_parser("propose", help="record a draft FMEA row (never applies it)")
    p_f.add_argument("db")
    p_f.add_argument("row", help="path to a JSON FMEA row")
    p_f.add_argument("--candidate-id", required=True)
    p_f.add_argument("--expected-baseline-hash", required=True,
                     help="optimistic concurrency: the fault-tree baseline this row is traced against")
    p_f.add_argument("--reviewer", default="agent",
                     help="who proposes (default: agent — an agent may propose but never approve)")
    p_f.add_argument("--expected-version", type=int, default=None,
                     help="the exact version this draft revises; omit for a brand-new row")
    p_f.add_argument("--note", default=None)
    p_f.set_defaults(func=cmd_fmea)

    p_f = fmea_sub.add_parser("decide", help="approve or reject a draft (requires a human)")
    p_f.add_argument("db")
    p_f.add_argument("candidate_id")
    fmea_decision = p_f.add_mutually_exclusive_group(required=True)
    fmea_decision.add_argument("--approve", action="store_true")
    fmea_decision.add_argument("--reject", dest="approve", action="store_false")
    p_f.add_argument("--reviewer", default=None)
    p_f.add_argument("--note", default=None)
    p_f.set_defaults(func=cmd_fmea)

    p_f = fmea_sub.add_parser("apply", help="promote an approved draft (requires a human)")
    p_f.add_argument("db")
    p_f.add_argument("candidate_id")
    p_f.add_argument("--reviewer", default=None)
    p_f.set_defaults(func=cmd_fmea)

    p_f = fmea_sub.add_parser(
        "propose-from-importance",
        help="turn an importance ranking into draft attention items (never approved by this command)",
    )
    p_f.add_argument("db")
    p_f.add_argument("--run", dest="run_id", required=True,
                     help="the run whose stored importance ranking to use")
    p_f.add_argument("--by", choices=IMPORTANCE_SORT_KEYS, default="fussell_vesely")
    p_f.add_argument("--top", type=int, default=DEFAULT_ATTENTION_TOP,
                     help=f"propose at most this many NEW drafts (default {DEFAULT_ATTENTION_TOP}); "
                          "events already covered or already drafted are skipped, not counted")
    p_f.add_argument("--min-value", default=None,
                     help="only events whose exact measure is >= this value (e.g. 0.1 or 1/10)")
    p_f.add_argument("--reviewer", default="agent",
                     help="who proposes (default: agent — an agent may propose but never approve)")
    p_f.add_argument("--note", default=None)
    p_f.set_defaults(func=cmd_fmea)

    p_f = fmea_sub.add_parser("trace", help="traceability: rows by event, by component, or gaps")
    p_f.add_argument("db")
    p_f.add_argument("--model", required=True)
    trace_mode = p_f.add_mutually_exclusive_group(required=True)
    trace_mode.add_argument("--event", default=None, help="rows that cite this basic event")
    trace_mode.add_argument("--component", default=None, help="rows for this component")
    trace_mode.add_argument("--dangling", action="store_true",
                            help="links whose event no longer exists in the current baseline")
    p_f.set_defaults(func=cmd_fmea)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
