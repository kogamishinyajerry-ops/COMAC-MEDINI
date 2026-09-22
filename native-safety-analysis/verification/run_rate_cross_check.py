"""Rate-model end-to-end cross-check.

Path A (production): Decimal-based q = -expm1(-lambda*t) -> exact rational
                     -> ROBDD kernel -> exact probability.
Path B (independent): exact rational series for exp(-x) -> different q
                      rationals -> brute-force truth table.

The two paths share NO conversion code and NO solver code. Because the q
values themselves differ (both are roundings of the same transcendental
number), comparison uses a declared absolute tolerance instead of exact
equality — the tolerance is derived from the conversion precision, not
chosen to hide errors.
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
from reference_fta import ref_solve  # noqa: E402

PRECISION = 40
TOLERANCE = Fraction(1, 10**30)  # well above conversion error (~1e-40), far below any real defect


def oracle_q(lam: Fraction, t: Fraction) -> Fraction:
    """Exact rational series for 1 - exp(-lambda t) (independent of Decimal)."""
    x = lam * t
    if x == 0:
        return Fraction(0)
    if x > 5:
        # exp(-5) ~ 6.7e-3; series still converges but needs many terms.
        # Use exp(-x) = exp(-x/2)^2 recursively to keep the series short.
        half = oracle_q(lam, t / 2)
        return 2 * half - half * half
    tol = Fraction(1, 10**90)
    term = Fraction(1)
    total = Fraction(1)
    k = 0
    while True:
        k += 1
        term = term * (-x) / k
        total += term
        if abs(term) < tol:
            break
        if k > 5000:
            raise AssertionError("series did not converge")
    return 1 - total


def to_declared_q(q_ref: Fraction, digits: int = PRECISION) -> Fraction:
    """Round a reference q to `digits` significant digits (like production does)."""
    if q_ref == 0:
        return Fraction(0)
    places = digits + 5
    scaled = q_ref * (10**places)
    q, r = divmod(scaled.numerator, scaled.denominator)
    if 2 * r >= scaled.denominator:
        q += 1
    return Fraction(q, 10**places)


def build_rate_model(case: dict) -> dict:
    events = []
    has_rate = False
    for eid, (lam, t, kind) in case["events"].items():
        if kind == "rate":
            has_rate = True
            events.append({
                "id": eid, "label": eid, "source": "synthetic_rate",
                "failure_rate": {
                    "model": "constant_failure_rate",
                    "lambda": str(lam), "lambda_unit": "1/h",
                    "mission_time": str(t), "mission_time_unit": "h",
                    "repairable": False, "source": "synthetic",
                },
            })
        else:
            events.append({"id": eid, "label": eid, "probability": str(lam), "source": "synthetic"})
    # Semantics must match the actual content (contract, validation.py L140-150):
    # rate semantics iff at least one event declares failure_rate.
    semantics = (
        "fixed_mission_probability_from_constant_rate"
        if has_rate
        else "fixed_conditioned_probability"
    )
    return {
        "schema_version": "0.2.0",
        "model_id": case["id"],
        "baseline_id": "SYN-RATE",
        "assumptions": {
            "basic_events_independent": True,
            "probability_semantics": semantics,
            "condition": "synthetic rate cross-check",
        },
        "basic_events": events,
        "gates": case["gates"],
        "top_event": case["top"],
    }


def reference_probability(case: dict) -> Fraction:
    """Independent path: oracle q values + truth-table reference solver."""
    ref_model = {
        "schema_version": "0.1.0",
        "model_id": case["id"],
        "baseline_id": "SYN-RATE",
        "assumptions": {
            "basic_events_independent": True,
            "probability_semantics": "fixed_conditioned_probability",
            "condition": "oracle-converted",
        },
        "basic_events": [],
        "gates": case["gates"],
        "top_event": case["top"],
    }
    for eid, (lam, t, kind) in case["events"].items():
        if kind == "rate":
            q = to_declared_q(oracle_q(Fraction(lam), Fraction(t)))
        else:
            q = Fraction(lam)
        ref_model["basic_events"].append(
            {"id": eid, "label": eid, "probability": _decimal(q), "source": "oracle"}
        )
    prob, _ = ref_solve(ref_model)
    return prob


def _decimal(value: Fraction) -> str:
    for digits in range(1, 200):
        scaled = value * (10**digits)
        if scaled.denominator == 1:
            text = str(scaled.numerator).rjust(digits + 1, "0")
            return f"{text[:-digits]}.{text[-digits:]}"
    return f"{value.numerator}/{value.denominator}"


def random_case(rng: random.Random, idx: int) -> dict:
    n = rng.randint(1, 5)
    events = {}
    for i in range(n):
        eid = f"E{i}"
        # Guarantee >=1 rate event per case so the conversion path is always
        # exercised (an all-fixed case would not test lambda*t -> q at all).
        is_rate = i == 0 or rng.random() < 0.75
        if is_rate:
            lam = rng.choice(["1e-7", "1e-6", "5e-6", "1e-5", "0.0001", "0.001"])
            t = rng.choice(["1", "10", "100", "1000", "10000"])
            events[eid] = (lam, t, "rate")
        else:
            events[eid] = (rng.choice(["0.01", "0.1", "0.5", "0.9"]), "0", "fixed")
    available = list(events)
    gates = []
    for gi in range(rng.randint(1, 4)):
        kind = rng.choice(["AND", "OR", "K_OF_N"])
        inputs = rng.sample(available, rng.randint(1, min(3, len(available))))
        g = {"id": f"G{gi}", "kind": kind, "inputs": inputs}
        if kind == "K_OF_N":
            g["k"] = rng.randint(1, len(inputs))
        gates.append(g)
        available.append(g["id"])
    return {"id": f"RATE-{idx:03d}", "events": events, "gates": gates, "top": available[-1]}


def main() -> int:
    failures = 0
    total = 0
    max_diff = Fraction(0)
    max_seen_q = Fraction(0)
    rng = random.Random(20260921)
    for i in range(120):
        case = random_case(rng, i)
        total += 1
        data = build_rate_model(case)
        model = validate_model(data)
        production = solve_model(model).top_probability
        reference = reference_probability(case)
        diff = abs(production - reference)
        if diff > max_diff:
            max_diff = diff
        for lam_text, time_text, kind in case["events"].values():
            if kind == "rate":
                q = to_declared_q(oracle_q(Fraction(lam_text), Fraction(time_text)))
                if q > max_seen_q:
                    max_seen_q = q
        ok = diff <= TOLERANCE or diff <= TOLERANCE * max(reference, Fraction(1))
        if not ok:
            failures += 1
            print(f"FAIL {case['id']}: prod={float(production):.6e} ref={float(reference):.6e} diff={float(diff):.3e}")
    ratio = float(max_diff / TOLERANCE) if TOLERANCE else 0.0
    print(f"\nrate cross-check: {total - failures}/{total} passed")
    print(f"  tolerance        : {float(TOLERANCE):.0e}")
    print(f"  max observed diff: {float(max_diff):.3e}  ({ratio:.3e} of tolerance)")
    print(f"  max event q      : {float(max_seen_q):.6e}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
