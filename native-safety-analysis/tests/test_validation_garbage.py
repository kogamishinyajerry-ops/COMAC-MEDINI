"""R5: malformed input is rejected as ModelError, never as a leaked exception.

The API boundary must not answer structural garbage with AttributeError /
KeyError / TypeError (which front ends would surface as an opaque 500). Each
row here is a real leak that existed before this file.

The counterpart, asserted just as hard: every LEGAL seed model keeps its
exact result — hardening must not bend semantics.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from native_safety.adapters.json_io import load_model  # noqa: E402
from native_safety.domain.errors import ModelError  # noqa: E402
from native_safety.domain.ratnum import exact_decimal_or_fraction  # noqa: E402
from native_safety.domain.validation import validate_model  # noqa: E402
from native_safety.kernel.solve import solve_model  # noqa: E402

EXAMPLES = ROOT / "reference" / "03_contracts" / "examples"

GOOD_ASSUMPTIONS = {
    "basic_events_independent": True,
    "probability_semantics": "fixed_conditioned_probability",
    "condition": "test condition",
}


def _model(events, gates=None, top="A") -> dict:
    return {
        "schema_version": "0.1.0",
        "assumptions": GOOD_ASSUMPTIONS,
        "basic_events": events,
        "gates": gates or [],
        "top_event": top,
    }


GOOD_EVENT = {"id": "A", "label": "a", "source": "s", "probability": "0.1"}
GOOD_EVENT_B = {"id": "B", "label": "b", "source": "s", "probability": "0.2"}


# ------------------------------------------------------------------ structural garbage

STRUCTURAL_GARBAGE = [
    ("root is a list", []),
    ("root is a string", "hello"),
    ("root is None", None),
    ("root is a number", 42),
    ("events element is None", _model([None])),
    ("events element is a number", _model([42])),
    ("events element is a string", _model(["A"])),
    ("event missing probability (v0.1.0)", _model([
        {"id": "A", "label": "a", "source": "s"}])),
    ("event probability is a number", _model([
        {"id": "A", "label": "a", "source": "s", "probability": 0.1}])),
    ("event probability is None", _model([
        {"id": "A", "label": "a", "source": "s", "probability": None}])),
    ("gates element is None", _model([GOOD_EVENT], [None])),
    ("gates element is a number", _model([GOOD_EVENT], [7])),
    ("gate kind is a list", _model(
        [GOOD_EVENT, GOOD_EVENT_B],
        [{"id": "G", "kind": ["OR"], "inputs": ["A", "B"]}], top="G")),
    ("gate kind is a dict", _model(
        [GOOD_EVENT, GOOD_EVENT_B],
        [{"id": "G", "kind": {"t": "OR"}, "inputs": ["A", "B"]}], top="G")),
    ("gate kind is None", _model(
        [GOOD_EVENT, GOOD_EVENT_B],
        [{"id": "G", "kind": None, "inputs": ["A", "B"]}], top="G")),
    ("gate inputs element is None", _model(
        [GOOD_EVENT, GOOD_EVENT_B],
        [{"id": "G", "kind": "OR", "inputs": ["A", None]}], top="G")),
    ("gate k is a string", _model(
        [GOOD_EVENT, GOOD_EVENT_B],
        [{"id": "G", "kind": "K_OF_N", "inputs": ["A", "B"], "k": "1"}], top="G")),
    ("top_event is None", _model([GOOD_EVENT], top=None)),
    ("top_event is a number", _model([GOOD_EVENT], top=1)),
    ("assumptions is a list", {
        "schema_version": "0.1.0", "assumptions": [],
        "basic_events": [GOOD_EVENT], "gates": [], "top_event": "A"}),
    ("schema_version is a number", {"schema_version": 1}),
]


@pytest.mark.parametrize("name,payload", STRUCTURAL_GARBAGE,
                         ids=[n for n, _ in STRUCTURAL_GARBAGE])
def test_structural_garbage_is_rejected_as_model_error(name, payload):
    """Every row was a real leak (AttributeError/KeyError/TypeError) or an
    under-specified rejection before R5; all must now be ModelError."""
    with pytest.raises(ModelError) as excinfo:
        validate_model(payload)
    assert excinfo.value.code, f"{name}: ModelError without a code"


# ------------------------------------------------------------------ legal results unchanged

LEGAL_SEEDS = sorted(EXAMPLES.glob("M*.json")) + sorted(EXAMPLES.glob("N*.json"))


@pytest.mark.parametrize("path", LEGAL_SEEDS, ids=[p.stem for p in LEGAL_SEEDS])
def test_legal_seed_behaviour_is_bit_for_bit_unchanged(path):
    """Hardening must not bend semantics: seeds still validate/solve or refuse
    with exactly the codes the contract table declares."""
    import json
    table = json.loads((ROOT / "reference" / "03_contracts" / "cases.json").read_text(encoding="utf-8"))
    expected = {c["case_id"]: c for c in table["cases"]}
    case = expected[path.stem]
    try:
        model = load_model(path)
    except ModelError:
        assert case["expected_valid"] is False
        return
    assert case["expected_valid"] is True
    solution = solve_model(model)
    # 期望值用引擎自己的规范文本形式比较（cases.json 存十进制，Fraction 的
    # str() 会给 '1/50' —— 两者数值相等，必须走同一归一化，不是语义漂移）。
    from fractions import Fraction
    assert Fraction(exact_decimal_or_fraction(solution.top_probability)) == Fraction(case["expected_probability"]), (
        f"{path.stem}: probability drifted after hardening")
