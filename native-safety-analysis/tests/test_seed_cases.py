"""Seed contract cases: 10 positive + 5 negative, exact expectations from cases.json."""
from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from native_safety.adapters.json_io import load_model  # noqa: E402
from native_safety.domain.errors import ModelError  # noqa: E402
from native_safety.kernel.solve import solve_model  # noqa: E402

CASES = ROOT / "reference" / "03_contracts"
CATALOG = json.loads((CASES / "cases.json").read_text(encoding="utf-8"))
POSITIVE = [c for c in CATALOG["cases"] if c["expected_valid"]]
NEGATIVE = [c for c in CATALOG["cases"] if not c["expected_valid"]]


@pytest.mark.parametrize("case", POSITIVE, ids=[c["case_id"] for c in POSITIVE])
def test_positive_seed(case):
    model = load_model(CASES / case["file"])
    result = solve_model(model)
    assert result.top_probability == Fraction(case["expected_probability"])
    expected_mcs = sorted(sorted(x) for x in case["expected_minimal_cut_sets"])
    assert result.minimal_cut_sets == expected_mcs
    assert result.cut_sets_complete is True


@pytest.mark.parametrize("case", NEGATIVE, ids=[c["case_id"] for c in NEGATIVE])
def test_negative_seed(case):
    with pytest.raises(ModelError) as excinfo:
        load_model(CASES / case["file"])
    assert excinfo.value.code == case["expected_error"]


def test_m03_repeated_event_anchor():
    """The canonical anti-pseudo-independence anchor: 0.044, NOT 0.0494."""
    model = load_model(CASES / "examples/M03_repeated_event.json")
    result = solve_model(model)
    assert result.top_probability == Fraction(11, 250)
    assert float(result.top_probability) == pytest.approx(0.044, abs=1e-15)
    assert float(result.top_probability) != pytest.approx(0.0494, abs=1e-6)


def test_zero_and_one_probabilities():
    model = load_model(CASES / "examples/M06_probability_zero_one.json")
    result = solve_model(model)
    assert result.top_probability == 1


def test_tiny_probability_exact():
    model = load_model(CASES / "examples/M08_tiny_probability.json")
    result = solve_model(model)
    # 1e-24 represented EXACTLY as a Fraction — no float underflow
    assert result.top_probability == Fraction(1, 10**24)
    assert float(result.top_probability) > 0
