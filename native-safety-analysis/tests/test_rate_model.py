"""Failure-rate model tests: q = -expm1(-lambda*t) conversion.

Includes an INDEPENDENT oracle: exact rational Taylor series for exp(-x),
which shares no code with the production Decimal path (no Decimal, no
Taylor-on-q, different algorithm shape).
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from native_safety.adapters.json_io import probability_to_text  # noqa: E402
from native_safety.domain.errors import ModelError  # noqa: E402
from native_safety.domain.rate_model import (  # noqa: E402
    mission_probability,
    parse_rate_spec,
)
from native_safety.domain.validation import validate_model  # noqa: E402
from native_safety.kernel.solve import solve_model  # noqa: E402

PY = sys.executable


def oracle_q(lam: Fraction, t: Fraction, tol: Fraction | None = None) -> Fraction:
    """Independent oracle: q = 1 - exp(-lambda t) via exact rational series.

    exp(-x) = sum_k (-x)^k / k! evaluated in exact rational arithmetic; no
    Decimal, no expm1, no shared code with the production implementation.
    Only used with small x (tests keep lambda*t <= 1).
    """
    x = lam * t
    if x == 0:
        return Fraction(0)
    tol = tol or Fraction(1, 10**80)
    term = Fraction(1)
    total = Fraction(1)
    k = 0
    while True:
        k += 1
        term = term * (-x) / k
        total += term
        if abs(term) < tol:
            break
        if k > 1000:
            raise AssertionError("oracle series did not converge")
    return 1 - total


def spec(lam="0.000001", t="1000", **over):
    raw = {
        "model": "constant_failure_rate",
        "lambda": lam,
        "lambda_unit": "1/h",
        "mission_time": t,
        "mission_time_unit": "h",
        "repairable": False,
        "source": "synthetic",
    }
    raw.update(over)
    return parse_rate_spec(raw, "E")


def model_with_rates(events, gates, top="TOP", semantics="fixed_mission_probability_from_constant_rate"):
    return {
        "schema_version": "0.2.0",
        "model_id": "RATE_TEST",
        "baseline_id": "SYN",
        "assumptions": {
            "basic_events_independent": True,
            "probability_semantics": semantics,
            "condition": "rate-model test",
        },
        "basic_events": events,
        "gates": gates,
        "top_event": top,
    }


def rate_event(eid, lam, t="1000"):
    return {
        "id": eid,
        "label": eid,
        "source": "synthetic_rate",
        "failure_rate": {
            "model": "constant_failure_rate",
            "lambda": lam,
            "lambda_unit": "1/h",
            "mission_time": t,
            "mission_time_unit": "h",
            "repairable": False,
            "source": "synthetic",
        },
    }


# ---------------------------------------------------------------- numerics


def test_anchor_matches_libm_expm1():
    """lambda=1e-6, t=1000 -> q ~ 9.995001666250083e-4 (matches libm)."""
    q = mission_probability(spec("1e-6", "1000"), 40)
    ref = -math.expm1(-1e-6 * 1000)
    assert abs(float(q) - ref) <= 1e-15 * abs(ref)
    assert str(float(q)).startswith("0.0009995001666")


def test_matches_independent_rational_oracle():
    """Production Decimal path vs exact rational series oracle."""
    for lam, t in [("1e-6", "1000"), ("2e-6", "1000"), ("3e-7", "500"), ("1e-3", "1000")]:
        q = mission_probability(spec(lam, t), 40)
        ref = oracle_q(Fraction(lam), Fraction(t))
        diff = abs(q - ref)
        assert diff < Fraction(1, 10**38), f"lambda*t={lam}x{t}: diff={float(diff)}"


def test_zero_rate_is_exactly_zero():
    assert mission_probability(spec("0", "1000"), 40) == 0
    assert mission_probability(spec("0", "0"), 40) == 0


def test_large_lambda_t_saturates_to_one():
    assert mission_probability(spec("1", "1000"), 40) == 1
    assert mission_probability(spec("0.1", "1000"), 40) == 1


def test_tiny_lambda_t_approximates_linear_term():
    """For tiny x, q = x - x^2/2 + ... so q/x -> 1 and q < x."""
    lam, t = Fraction("1e-6"), Fraction("0.001")
    q = mission_probability(spec("1e-6", "0.001"), 40)
    x = lam * t
    assert q < x
    assert abs(q / x - 1) < Fraction(1, 10**6)  # relative deviation ~ x/2


def test_monotonic_in_lambda_and_time():
    prev = Fraction(0)
    for lam in ["1e-8", "1e-7", "1e-6", "1e-5", "1e-4"]:
        q = mission_probability(spec(lam, "1000"), 40)
        assert q > prev
        prev = q
    prev = Fraction(0)
    for t in ["1", "10", "100", "1000", "10000"]:
        q = mission_probability(spec("1e-6", t), 40)
        assert q > prev
        prev = q


def test_q_stays_in_unit_interval():
    for lam in ["0", "1e-12", "1e-6", "0.001", "0.1", "1", "1000"]:
        for t in ["0", "1", "1000", "1e6"]:
            q = mission_probability(spec(lam, t), 40)
            assert 0 <= q <= 1


def test_precision_is_honoured():
    q15 = mission_probability(spec("1e-6", "1000"), 15)
    q40 = mission_probability(spec("1e-6", "1000"), 40)
    assert q15 == q40 or abs(q15 - q40) < Fraction(1, 10**14)


# ------------------------------------------------------------- rejections


def test_repairable_true_rejected():
    with pytest.raises(ModelError) as e:
        spec(repairable=True)
    assert e.value.code == "RATE_UNSUPPORTED"


def test_repairable_missing_rejected():
    raw = {
        "lambda": "1e-6", "lambda_unit": "1/h",
        "mission_time": "1000", "mission_time_unit": "h", "source": "s",
    }
    with pytest.raises(ModelError) as e:
        parse_rate_spec(raw, "E")
    assert e.value.code == "RATE_UNSUPPORTED"


def test_unsupported_rate_model_rejected():
    with pytest.raises(ModelError) as e:
        spec(model="weibull")
    assert e.value.code == "RATE_UNSUPPORTED"


def test_dormant_semantics_rejected():
    with pytest.raises(ModelError) as e:
        spec(dormant=True)
    assert e.value.code == "RATE_UNSUPPORTED"


def test_inspection_interval_rejected():
    with pytest.raises(ModelError) as e:
        spec(inspection_interval="500")
    assert e.value.code == "RATE_UNSUPPORTED"


def test_missing_mission_time_rejected():
    raw = {"lambda": "1e-6", "lambda_unit": "1/h", "mission_time_unit": "h", "repairable": False, "source": "s"}
    with pytest.raises(ModelError) as e:
        parse_rate_spec(raw, "E")
    assert e.value.code == "RATE_VALUE"


def test_missing_units_rejected():
    raw = {"lambda": "1e-6", "mission_time": "1000", "repairable": False, "source": "s"}
    with pytest.raises(ModelError) as e:
        parse_rate_spec(raw, "E")
    assert e.value.code == "RATE_UNITS"


def test_wrong_unit_rejected():
    with pytest.raises(ModelError) as e:
        spec(lambda_unit="1/s")
    assert e.value.code == "RATE_UNITS"
    with pytest.raises(ModelError) as e:
        spec(mission_time_unit="min")
    assert e.value.code == "RATE_UNITS"


def test_negative_rates_rejected():
    with pytest.raises(ModelError) as e:
        spec(lam="-1e-6")
    assert e.value.code == "RATE_VALUE"
    with pytest.raises(ModelError) as e:
        spec(t="-1000")
    assert e.value.code == "RATE_VALUE"


# --------------------------------------------------------- model-level rules


def test_both_probability_and_rate_rejected():
    ev = rate_event("A", "1e-6")
    ev["probability"] = "0.1"
    data = model_with_rates([ev], [])
    data["top_event"] = "A"
    with pytest.raises(ModelError) as e:
        validate_model(data)
    assert e.value.code == "RATE_VALUE"


def test_neither_probability_nor_rate_rejected():
    ev = {"id": "A", "label": "A", "source": "s"}
    data = model_with_rates([ev], [])
    data["top_event"] = "A"
    with pytest.raises(ModelError) as e:
        validate_model(data)
    assert e.value.code == "RATE_VALUE"


def test_rate_in_v01_rejected():
    data = model_with_rates([rate_event("A", "1e-6")], [])
    data["schema_version"] = "0.1.0"
    data["top_event"] = "A"
    with pytest.raises(ModelError) as e:
        validate_model(data)
    assert e.value.code == "VERSION"


def test_semantics_must_match_presence_of_rates():
    # rates present but semantics says plain fixed probability
    data = model_with_rates([rate_event("A", "1e-6")], [], semantics="fixed_conditioned_probability")
    data["top_event"] = "A"
    with pytest.raises(ModelError) as e:
        validate_model(data)
    assert e.value.code == "ASSUMPTIONS"
    # no rates but semantics claims rate-derived
    declared = {"id": "A", "label": "A", "probability": "0.1", "source": "s"}
    data = model_with_rates([declared], [], semantics="fixed_mission_probability_from_constant_rate")
    data["top_event"] = "A"
    with pytest.raises(ModelError) as e:
        validate_model(data)
    assert e.value.code == "ASSUMPTIONS"


# ------------------------------------------------------------- integration


def test_rate_model_solves_end_to_end():
    data = model_with_rates(
        [rate_event("P1", "1e-6"), rate_event("V1", "2e-6")],
        [{"id": "TOP", "kind": "AND", "inputs": ["P1", "V1"]}],
    )
    model = validate_model(data)
    result = solve_model(model)
    q1 = mission_probability(spec("1e-6", "1000"), 40)
    q2 = mission_probability(spec("2e-6", "1000"), 40)
    assert result.top_probability == q1 * q2  # exact rational product
    assert result.minimal_cut_sets == [["P1", "V1"]]


def test_mixed_model_probability():
    """OR of a declared 0.1 event and a rate-derived event."""
    data = model_with_rates(
        [
            {"id": "A", "label": "A", "probability": "0.1", "source": "synthetic"},
            rate_event("B", "1e-6", "1000"),
        ],
        [{"id": "TOP", "kind": "OR", "inputs": ["A", "B"]}],
    )
    model = validate_model(data)
    result = solve_model(model)
    qb = mission_probability(spec("1e-6", "1000"), 40)
    expected = Fraction("0.1") + qb - Fraction("0.1") * qb
    assert result.top_probability == expected


def test_cli_rate_model_emits_provenance_and_warning(tmp_path):
    out = subprocess.run(
        [PY, str(ROOT / "run.py"), "analyze", str(ROOT / "examples" / "R01_rate_and.json"),
         "--evidence-dir", str(tmp_path), "--run-id", "rate_cli"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["model_schema_version"] == "0.2.0"
    assert payload["probability_interpretation"] == "mission_failure_probability_from_constant_rate"
    assert len(payload["rate_provenance"]) == 2
    assert any("NOT a" in w and "per-flight-hour" in w for w in payload["warnings"])
    # exact rational of the top event equals q1*q2 (the AND of two rate events)
    q1 = mission_probability(spec("1e-6", "1000"), 40)
    q2 = mission_probability(spec("2e-6", "1000"), 40)
    assert Fraction(payload["engine_stats"]["top_probability_exact"]) == q1 * q2


def test_cli_rejects_repairable_rate():
    tmp = ROOT / "tests" / "_tmp_bad_rate.json"
    data = model_with_rates([rate_event("A", "1e-6")], [])
    data["top_event"] = "A"
    data["basic_events"][0]["failure_rate"]["repairable"] = True
    tmp.write_text(json.dumps(data), encoding="utf-8")
    try:
        out = subprocess.run(
            [PY, str(ROOT / "run.py"), "analyze", str(tmp)], capture_output=True, text=True
        )
        assert out.returncode == 3
        assert json.loads(out.stdout)["error"]["code"] == "RATE_UNSUPPORTED"
    finally:
        tmp.unlink(missing_ok=True)


# ------------------------------------------------------- display rounding


def test_probability_text_rounds_to_significant_digits():
    q = mission_probability(spec("1e-6", "1000"), 40)
    text40 = probability_to_text(q * q, max_digits=40)
    assert len(text40.replace("0.", "").lstrip("0")) <= 40
    # exact path when within budget
    exact = probability_to_text(Fraction("0.044"))
    assert exact == "0.044"


def test_probability_text_exact_when_no_cap():
    q = mission_probability(spec("1e-6", "1000"), 20)
    text = probability_to_text(q)
    assert Fraction(text) == q


# ------------------------------------------------- end-to-end cross-check


def test_semantics_infers_plain_fixed_when_no_rate_events():
    """A v0.2.0 model with NO failure_rate must use plain-fixed semantics.

    Regression: the cross-check harness previously hard-coded rate semantics,
    which validation correctly rejected for all-declared cases.
    """
    data = model_with_rates(
        [{"id": "A", "label": "A", "probability": "0.1", "source": "s"}],
        [],
        semantics="fixed_conditioned_probability",
    )
    data["top_event"] = "A"
    model = validate_model(data)
    assert model.rates == {}
    assert solve_model(model).top_probability == Fraction("0.1")


def test_rate_cross_check_harness_passes():
    """The independent rate oracle path must agree with production end-to-end.

    Uses the shared harness (different exp algorithm + truth-table solver)
    on a deterministic subset; compares within the declared tolerance.
    """
    sys.path.insert(0, str(ROOT / "verification"))
    import random as _random

    from run_rate_cross_check import (  # noqa: E402
        TOLERANCE,
        build_rate_model,
        random_case,
        reference_probability,
    )

    rng = _random.Random(20260921)
    for i in range(40):
        case = random_case(rng, i)
        assert any(k == "rate" for _l, _t, k in case["events"].values()), "case must exercise rates"
        model = validate_model(build_rate_model(case))
        production = solve_model(model).top_probability
        reference = reference_probability(case)
        diff = abs(production - reference)
        assert diff <= TOLERANCE, f"{case['id']}: diff={float(diff):.3e} > tolerance"

