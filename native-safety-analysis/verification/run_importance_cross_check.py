"""Importance-measure cross-check — EXACT equality, no tolerance anywhere.

Part A — fixed-probability models:
    production kernel (ROBDD + single top-down reach pass) vs independent
    brute-force cofactor enumeration over the full assignment space.

Part B — rate-derived models:
    the same comparison, but the probabilities are first converted from
    failure rates through the independent rational-series oracle, so the
    kernel is exercised on 1e-7..1 magnitudes (denominators around 10^45).
    That is the regime where a sloppy implementation silently loses
    exactness; the harness would see it immediately, because both paths are
    exact rational arithmetic and must agree bit-for-bit.

Both parts additionally cross-check the SUPPORT decision: production derives
it from the compiled diagram (a variable is in the support iff some node
tests it) while the oracle derives it by comparing truth-table rows that
differ only in that variable. Two unrelated methods, one answer.

No tolerance is used: unlike the q = -expm1(-lambda*t) conversion, neither
path rounds a transcendental number, so any difference at all is a defect.
"""
from __future__ import annotations

import random
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "verification"))

from native_safety.domain.validation import validate_model  # noqa: E402
from native_safety.kernel.solve import solve_model  # noqa: E402
from reference_importance import ref_importance  # noqa: E402
from run_cross_check import random_model  # noqa: E402
from run_rate_cross_check import _decimal, oracle_q, random_case, to_declared_q  # noqa: E402

MEASURE_FIELDS = {
    "probability": "q",
    "top_given_failed": "q_plus",
    "top_given_working": "q_minus",
    "birnbaum": "birnbaum",
    "fussell_vesely": "fussell_vesely",
    "risk_achievement_worth": "risk_achievement_worth",
    "risk_reduction_worth": "risk_reduction_worth",
}

PART_A_MODELS = 80
PART_B_MODELS = 40


def declared_model(case: dict) -> dict:
    """Rate case -> schema 0.1.0 model whose probabilities are the oracle's q."""
    events = []
    for eid, (lam, t, kind) in case["events"].items():
        q = (
            to_declared_q(oracle_q(Fraction(lam), Fraction(t)))
            if kind == "rate"
            else Fraction(lam)
        )
        events.append({"id": eid, "label": eid, "probability": _decimal(q), "source": "oracle"})
    return {
        "schema_version": "0.1.0",
        "model_id": case["id"],
        "baseline_id": "SYN-RATE",
        "assumptions": {
            "basic_events_independent": True,
            "probability_semantics": "fixed_conditioned_probability",
            "condition": "oracle-converted rate cross-check",
        },
        "basic_events": events,
        "gates": case["gates"],
        "top_event": case["top"],
    }


def compare(label: str, data: dict) -> tuple[list[str], dict[str, int]]:
    """Return (mismatches, coverage stats).

    Empty mismatch list means exact agreement. The coverage stats exist so a
    green run cannot be vacuous: they count how many comparisons actually hit
    the interesting branches (events outside the support, undefined measures,
    zero Birnbaum, zero top probability).
    """
    model = validate_model(data)
    result = solve_model(model)
    top_ref, reference, support = ref_importance(data)
    problems: list[str] = []
    stats = {
        "events": 0,
        "not_in_support": 0,
        "zero_birnbaum": 0,
        "zero_top": 0,
        "undefined": 0,
        "repeated_event": 0,
    }

    if result.top_probability != top_ref:
        problems.append(f"top: {result.top_probability} != {top_ref}")
    if top_ref == 0:
        stats["zero_top"] = 1

    got = {record.event_id: record for record in result.importance}
    if set(got) != set(reference):
        problems.append(f"event set mismatch: {sorted(set(got) ^ set(reference))}")
        return problems, stats

    for eid, record in got.items():
        stats["events"] += 1
        below_top = sum(1 for gate in data["gates"] if eid in gate["inputs"])
        if below_top > 1:
            stats["repeated_event"] += 1
        for field, key in MEASURE_FIELDS.items():
            mine = getattr(record, field)
            theirs = reference[eid][key]
            if mine != theirs:
                problems.append(f"{eid}.{field}: {mine} != {theirs}")
        if record.undefined:
            stats["undefined"] += len(record.undefined)
        if record.birnbaum == 0:
            stats["zero_birnbaum"] += 1
        expected_support = eid in support
        if not expected_support:
            stats["not_in_support"] += 1
        if record.in_support != expected_support:
            problems.append(f"{eid}.in_support: {record.in_support} != {expected_support}")
    return problems, stats


def main() -> int:
    failures: list[tuple[str, list[str]]] = []
    checks = 0
    smallest = None
    coverage = {
        "events": 0,
        "not_in_support": 0,
        "zero_birnbaum": 0,
        "zero_top": 0,
        "undefined": 0,
        "repeated_event": 0,
    }

    # -- Part A: fixed probabilities -------------------------------------
    rng = random.Random(20260921)
    for i in range(PART_A_MODELS):
        events = rng.randint(2, 8)
        gates = rng.randint(1, 6)
        data = random_model(rng, events, gates)
        problems, stats = compare(f"A{i:03d}", data)
        checks += stats["events"] * (len(MEASURE_FIELDS) + 1)
        for key, value in stats.items():
            coverage[key] += value
        if problems:
            failures.append((f"A{i:03d}", problems[:4]))

    # -- Part B: rate-derived probabilities (tiny magnitudes) ------------
    rng = random.Random(88112233)
    for i in range(PART_B_MODELS):
        case = random_case(rng, 500 + i)
        data = declared_model(case)
        problems, stats = compare(f"B{i:03d}", data)
        checks += stats["events"] * (len(MEASURE_FIELDS) + 1)
        for key, value in stats.items():
            coverage[key] += value
        for event in data["basic_events"]:
            q = Fraction(event["probability"])
            if q and (smallest is None or q < smallest):
                smallest = q
        if problems:
            failures.append((f"B{i:03d}", problems[:4]))

    print(f"importance cross-check: {PART_A_MODELS + PART_B_MODELS - len(failures)}"
          f"/{PART_A_MODELS + PART_B_MODELS} models passed")
    print(f"  exact comparisons : {checks} values (no tolerance used)")
    print(f"  part A            : {PART_A_MODELS} fixed-probability models")
    print(f"  part B            : {PART_B_MODELS} rate-derived models, "
          f"smallest event probability {float(smallest):.3e}")
    print("  coverage (a green run is not vacuous):")
    print(f"    events compared            : {coverage['events']}")
    print(f"    events outside the support : {coverage['not_in_support']}")
    print(f"    events in >1 gate input    : {coverage['repeated_event']}")
    print(f"    zero Birnbaum results      : {coverage['zero_birnbaum']}")
    print(f"    undefined measures hit     : {coverage['undefined']}")
    print(f"    zero-top-probability models: {coverage['zero_top']}")

    for label, problems in failures:
        print(f"\nFAIL {label}")
        for problem in problems:
            print(f"    {problem}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
