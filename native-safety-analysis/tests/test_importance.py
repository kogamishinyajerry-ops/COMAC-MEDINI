"""Importance measures: hand-computed anchors, invariants, undefined policy,
support decisions, metamorphic properties, and CLI integration.

The anchors are computed by hand from the textbook definitions and are NOT
derived from the engine, so they can fail independently of the implementation.
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "verification"))

from native_safety.domain.validation import validate_model  # noqa: E402
from native_safety.kernel.solve import solve_model  # noqa: E402
from run_cross_check import random_model  # noqa: E402

PY = sys.executable
CASES = ROOT / "reference" / "03_contracts" / "examples"


def model_of(events: dict[str, str], gates: list[dict], top: str, schema: str = "0.1.0") -> dict:
    return {
        "schema_version": schema,
        "model_id": "TEST",
        "baseline_id": "SYN-TEST",
        "assumptions": {
            "basic_events_independent": True,
            "probability_semantics": "fixed_conditioned_probability",
            "condition": "unit-test condition",
        },
        "basic_events": [
            {"id": eid, "label": eid, "probability": p, "source": "unit-test"}
            for eid, p in events.items()
        ],
        "gates": gates,
        "top_event": top,
    }


def importance_of(events, gates, top):
    result = solve_model(validate_model(model_of(events, gates, top)))
    return result, {record.event_id: record for record in result.importance}


def test_and_of_two_hand_computed():
    """f = A AND B, q=0.1/0.2 -> Q=0.02, both events are single points of failure."""
    _, rec = importance_of(
        {"A": "0.1", "B": "0.2"}, [{"id": "G", "kind": "AND", "inputs": ["A", "B"]}], "G"
    )
    assert rec["A"].top_given_failed == Fraction(1, 5)      # P(B) = 0.2
    assert rec["A"].top_given_working == 0
    assert rec["A"].birnbaum == Fraction(1, 5)
    assert rec["A"].fussell_vesely == 1                     # A is essential
    assert rec["A"].risk_achievement_worth == 10            # 0.2 / 0.02
    assert rec["A"].risk_reduction_worth is None            # 0.02 / 0 -> unbounded
    assert rec["B"].birnbaum == Fraction(1, 10)
    assert rec["B"].risk_achievement_worth == 5             # 0.1 / 0.02


def test_or_of_two_hand_computed():
    """f = A OR B, q=0.1/0.2 -> Q=0.28; FV = 2/7, RAW = 25/7, RRW = 7/5."""
    _, rec = importance_of(
        {"A": "0.1", "B": "0.2"}, [{"id": "G", "kind": "OR", "inputs": ["A", "B"]}], "G"
    )
    assert rec["A"].top_given_failed == 1
    assert rec["A"].top_given_working == Fraction(1, 5)
    assert rec["A"].birnbaum == Fraction(4, 5)
    assert rec["A"].fussell_vesely == Fraction(2, 7)
    assert rec["A"].risk_achievement_worth == Fraction(25, 7)
    assert rec["A"].risk_reduction_worth == Fraction(7, 5)
    assert rec["A"].undefined == ()


def test_repeated_event_anchor_two_of_three_hand_computed():
    """2-of-3 with equal q=0.1: Q=7/250, q+=19/100, q-=1/100, BI=9/50."""
    _, rec = importance_of(
        {"A": "0.1", "B": "0.1", "C": "0.1"},
        [{"id": "G", "kind": "K_OF_N", "k": 2, "inputs": ["A", "B", "C"]}],
        "G",
    )
    for eid in "ABC":
        assert rec[eid].top_given_failed == Fraction(19, 100)
        assert rec[eid].top_given_working == Fraction(1, 100)
        assert rec[eid].birnbaum == Fraction(9, 50)
        assert rec[eid].fussell_vesely == Fraction(9, 14)


def test_m03_shared_subgraph_hand_computed():
    """f = (A AND B) OR (A AND C), q=0.1/0.2/0.3 -> Q=0.044 (not 0.0494).

    A is essential:  q+ = P(B OR C) = 0.44, q- = 0.
    B: q+ = P(A) = 0.1, q- = P(A AND C) = 0.03.
    C: q+ = P(A) = 0.1, q- = P(A AND B) = 0.02.
    """
    _, rec = importance_of(
        {"A": "0.1", "B": "0.2", "C": "0.3"},
        [
            {"id": "G1", "kind": "AND", "inputs": ["A", "B"]},
            {"id": "G2", "kind": "AND", "inputs": ["A", "C"]},
            {"id": "G0", "kind": "OR", "inputs": ["G1", "G2"]},
        ],
        "G0",
    )
    assert rec["A"].top_given_failed == Fraction(11, 25)
    assert rec["A"].top_given_working == 0
    assert rec["A"].fussell_vesely == 1
    assert rec["A"].risk_reduction_worth is None
    assert rec["B"].birnbaum == Fraction(7, 100)
    assert rec["B"].fussell_vesely == Fraction(7, 22)
    assert rec["B"].risk_achievement_worth == Fraction(25, 11)
    assert rec["B"].risk_reduction_worth == Fraction(22, 15)
    assert rec["C"].birnbaum == Fraction(2, 25)
    assert rec["C"].fussell_vesely == Fraction(6, 11)
    assert rec["C"].risk_reduction_worth == Fraction(11, 5)


def test_cofactor_survives_level_skipping():
    """f = x2 AND (x0 OR x1): the diagram skips level 1 on one branch.

    A naive per-level aggregation loses the mass that jumps a level and
    reports the wrong cofactor for x1. Hand values: Q=21/250.
    """
    _, rec = importance_of(
        {"x0": "0.1", "x1": "0.2", "x2": "0.3"},
        [
            {"id": "G1", "kind": "OR", "inputs": ["x0", "x1"]},
            {"id": "G2", "kind": "AND", "inputs": ["G1", "x2"]},
        ],
        "G2",
    )
    assert rec["x1"].top_given_failed == Fraction(3, 10)
    assert rec["x1"].top_given_working == Fraction(3, 100)
    assert rec["x1"].birnbaum == Fraction(27, 100)
    assert rec["x2"].top_given_failed == Fraction(7, 25)
    assert rec["x2"].top_given_working == 0
    assert rec["x2"].fussell_vesely == 1


def test_variable_above_root_level_reports_cofactor_equal_to_top():
    """Regression: an event whose level sits ABOVE the root is never tested.

    Found by the importance cross-check on random model RANDOM_2476: the
    reach/bypass split has no jump edge to record above the root, so the
    level-free mass must be taken as the top probability itself. Without that
    case the cofactor came out 0 and the cofactor identity failed.
    """
    events = {"E00": "0.2", "E01": "0.5", "E02": "0.9", "E03": "0.01",
              "E04": "0.1", "E05": "0.01"}
    gates = [
        {"id": "G00", "kind": "OR", "inputs": ["E03", "E00"]},
        {"id": "G01", "kind": "OR", "inputs": ["E04", "E02", "G00", "E05"]},
        {"id": "G02", "kind": "OR", "inputs": ["E00", "E04", "E05"]},
        {"id": "G03", "kind": "K_OF_N", "k": 2, "inputs": ["E02", "E01"]},
        {"id": "G04", "kind": "AND", "inputs": ["G01", "E00", "E05"]},
    ]
    result, rec = importance_of(events, gates, "G04")
    assert result.top_probability == Fraction(1, 500)
    # the compiled function is exactly E00 AND E05
    assert result.variable_order[0] == "E04"
    assert rec["E04"].in_support is False
    assert rec["E04"].top_given_failed == result.top_probability
    assert rec["E04"].top_given_working == result.top_probability
    assert rec["E04"].birnbaum == 0
    assert rec["E04"].fussell_vesely == 0
    assert rec["E04"].risk_achievement_worth == 1
    assert rec["E04"].risk_reduction_worth == 1
    assert rec["E00"].top_given_failed == Fraction(1, 100)
    assert rec["E05"].top_given_failed == Fraction(1, 5)


def test_unreachable_event_is_reported_not_omitted():
    _, rec = importance_of(
        {"A": "0.1", "B": "0.2", "Z": "0.5"},
        [{"id": "G", "kind": "AND", "inputs": ["A", "B"]}],
        "G",
    )
    assert set(rec) == {"A", "B", "Z"}
    assert rec["Z"].in_support is False
    assert rec["Z"].birnbaum == 0
    assert rec["Z"].fussell_vesely == 0
    assert rec["Z"].risk_achievement_worth == 1
    assert rec["Z"].risk_reduction_worth == 1


def test_top_event_is_a_basic_event():
    result, rec = importance_of({"A": "0.25"}, [], "A")
    assert result.top_probability == Fraction(1, 4)
    assert rec["A"].top_given_failed == 1
    assert rec["A"].top_given_working == 0
    assert rec["A"].birnbaum == 1
    assert rec["A"].risk_achievement_worth == 4
    assert rec["A"].risk_reduction_worth is None


def test_zero_top_probability_marks_fv_raw_rrw_undefined():
    """q_A=0 makes Q=0; the Birnbaum index stays well-defined and is not 0."""
    result, rec = importance_of(
        {"A": "0", "B": "0.2"}, [{"id": "G", "kind": "AND", "inputs": ["A", "B"]}], "G"
    )
    assert result.top_probability == 0
    assert rec["A"].birnbaum == Fraction(1, 5)
    for measure in ("fussell_vesely", "risk_achievement_worth", "risk_reduction_worth"):
        assert getattr(rec["A"], measure) is None
        assert rec["A"].reason_for(measure) == "top_probability_is_zero"
    assert rec["B"].birnbaum == 0


def test_in_support_is_not_the_same_as_nonzero_birnbaum():
    """2-of-3 with q_B=q_C=0: A is in the support yet has Birnbaum 0."""
    _, rec = importance_of(
        {"A": "0.1", "B": "0", "C": "0"},
        [{"id": "G", "kind": "K_OF_N", "k": 2, "inputs": ["A", "B", "C"]}],
        "G",
    )
    assert rec["A"].in_support is True
    assert rec["A"].birnbaum == 0


def test_absorption_or_of_identical_inputs_matches_single_event():
    """OR(A, A) is Boolean-equal to A, so every measure must agree exactly."""
    _, folded = importance_of({"A": "0.1"}, [], "A")
    _, repeated = importance_of(
        {"A": "0.1"}, [{"id": "G", "kind": "OR", "inputs": ["A", "A"]}], "G"
    )
    for measure in ("top_given_failed", "top_given_working", "birnbaum",
                    "fussell_vesely", "risk_achievement_worth"):
        assert getattr(folded["A"], measure) == getattr(repeated["A"], measure)


def test_gate_input_order_does_not_change_measures():
    forward = {
        "A": "0.1", "B": "0.2", "C": "0.3", "D": "0.4",
    }
    gates_forward = [
        {"id": "G1", "kind": "OR", "inputs": ["A", "B"]},
        {"id": "G2", "kind": "AND", "inputs": ["C", "D"]},
        {"id": "G0", "kind": "AND", "inputs": ["G1", "G2"]},
    ]
    gates_shuffled = [
        {"id": "G1", "kind": "OR", "inputs": ["B", "A"]},
        {"id": "G2", "kind": "AND", "inputs": ["D", "C"]},
        {"id": "G0", "kind": "AND", "inputs": ["G2", "G1"]},
    ]
    _, a = importance_of(forward, gates_forward, "G0")
    _, b = importance_of(forward, gates_shuffled, "G0")
    for eid in forward:
        for measure in ("top_given_failed", "top_given_working", "birnbaum",
                        "fussell_vesely", "risk_achievement_worth", "risk_reduction_worth"):
            assert getattr(a[eid], measure) == getattr(b[eid], measure), f"{eid}.{measure}"


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_invariants_on_random_models(seed):
    """Exact identities and ordering invariants that must hold for every model.

    Q = q_i*q+ + (1-q_i)*q- ; BI >= 0 ; RAW >= 1 ; RRW >= 1 ; 0 <= FV <= 1 ;
    FV = 1 - 1/RRW ; RAW = 1 + (1-q_i)*BI/Q.
    """
    rng = random.Random(seed)
    for _ in range(12):
        data = random_model(rng, rng.randint(2, 8), rng.randint(1, 6))
        result = solve_model(validate_model(data))
        top = result.top_probability
        for record in result.importance:
            q = record.probability
            assert q * record.top_given_failed + (1 - q) * record.top_given_working == top
            assert record.birnbaum >= 0
            assert record.birnbaum == record.top_given_failed - record.top_given_working
            if top == 0:
                assert record.fussell_vesely is None
                continue
            assert record.risk_achievement_worth >= 1
            assert 0 <= record.fussell_vesely <= 1
            assert record.fussell_vesely == 1 - record.top_given_working / top
            assert record.risk_achievement_worth == 1 + (1 - q) * record.birnbaum / top
            if record.risk_reduction_worth is None:
                assert record.top_given_working == 0
                assert record.reason_for("risk_reduction_worth") == (
                    "risk_reduction_worth_unbounded"
                )
            else:
                assert record.risk_reduction_worth >= 1
                assert record.fussell_vesely == 1 - 1 / record.risk_reduction_worth


def test_not_in_support_implies_all_measures_are_identity():
    rng = random.Random(99)
    for _ in range(10):
        data = random_model(rng, rng.randint(3, 8), rng.randint(1, 5))
        result = solve_model(validate_model(data))
        for record in result.importance:
            if record.in_support:
                continue
            assert record.birnbaum == 0
            assert record.fussell_vesely == 0
            assert record.risk_achievement_worth == 1
            assert record.risk_reduction_worth == 1


def test_importance_records_are_ranked_by_fussell_vesely_in_payload(tmp_path):
    out = subprocess.run(
        [PY, str(ROOT / "run.py"), "analyze", str(CASES / "M03_repeated_event.json")],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["importance_status"] == "computed"
    assert payload["importance_exact"] is True
    measures = payload["importance_measures"]
    assert [m["event_id"] for m in measures] == ["A", "C", "B"]
    values = [Fraction(m["fussell_vesely"]) for m in measures]
    assert values == sorted(values, reverse=True)
    assert Fraction(measures[0]["fussell_vesely"]) == 1
    assert "undefined" in measures[0]
    assert measures[0]["undefined"] == [
        {"measure": "risk_reduction_worth", "reason": "risk_reduction_worth_unbounded"}
    ]
    assert payload["importance_conventions"]["risk_achievement_worth"].startswith("P(top|x_i=1)/Q")
    # exact by construction for a fixed-probability model: no rounding happened,
    # so no redundant *_exact field is emitted
    assert payload["top_probability"] == "0.044"
    assert "top_probability_exact" not in payload["engine_stats"]


def test_cli_importance_off_is_reported_not_silent():
    out = subprocess.run(
        [PY, str(ROOT / "run.py"), "analyze", str(CASES / "M03_repeated_event.json"),
         "--importance", "off"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["importance_measures"] is None
    assert payload["importance_status"] == "not_requested"
    assert any("importance measures not computed" in w for w in payload["warnings"])


def test_auto_mode_skips_wide_models_and_says_so():
    from native_safety.kernel.solve import solve_model as solve

    data = json.loads((CASES / "M03_repeated_event.json").read_text(encoding="utf-8"))
    model = validate_model(data)
    skipped = solve(model, importance="auto", importance_max_events=2)
    assert skipped.importance is None
    assert skipped.importance_status == "skipped_model_too_wide"
    assert "re-run with --importance on" in skipped.importance_note
    forced = solve(model, importance="on", importance_max_events=2)
    assert forced.importance_status == "computed"
    assert len(forced.importance) == 3


def test_unknown_importance_mode_rejected():
    data = json.loads((CASES / "M03_repeated_event.json").read_text(encoding="utf-8"))
    model = validate_model(data)
    with pytest.raises(ValueError):
        solve_model(model, importance="sometimes")


def test_evidence_report_contains_importance_table(tmp_path):
    out = subprocess.run(
        [PY, str(ROOT / "run.py"), "analyze", str(CASES / "M03_repeated_event.json"),
         "--evidence-dir", str(tmp_path), "--run-id", "imp"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    report = (tmp_path / "run_imp" / "reports" / "report.md").read_text(encoding="utf-8")
    assert "## Importance measures" in report
    assert "Fussell-Vesely" in report
    assert "risk_reduction_worth_unbounded" in report
