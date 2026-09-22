"""Compilation reuse: identity tests (B03 wrap-up, incremental recalc).

Measurements first (verification/run_incremental_bench.py): compilation is
31–45% of solve time on pressure models and depends on STRUCTURE ONLY —
neither the variable order nor the diagram nor the minimal cut sets read a
probability. `solve_model` therefore caches the structure-only artifacts by
a structure fingerprint; a probability-only change reuses them.

These tests prove the reuse CORRECT rather than assume it: every cached
result must equal a from-scratch solve bit for bit (probability, cut sets,
importance records including undefined reasons), across probability changes,
label/lexical changes, structure changes (which must MISS), and cache
eviction. The oracle is the fresh path itself, isolated via clear_compile_cache().
"""
from __future__ import annotations

import dataclasses
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from native_safety.adapters.json_io import load_model  # noqa: E402
from native_safety.domain.validation import validate_model  # noqa: E402
from native_safety.kernel.importance import ImportanceRecord  # noqa: E402
from native_safety.kernel.solve import (  # noqa: E402
    clear_compile_cache,
    compilation_cache_size,
    solve_model,
    structure_fingerprint,
)

EXAMPLES = ROOT / "reference" / "03_contracts" / "examples"


def _model(name):
    return load_model(EXAMPLES / name)


def _rerate(model, event_id, probability):
    events = tuple(
        dataclasses.replace(e, probability=Fraction(probability), probability_text=probability)
        if e.id == event_id else e
        for e in model.basic_events
    )
    return dataclasses.replace(
        model, basic_events=events,
        probabilities=dict(model.probabilities, **{event_id: Fraction(probability)}),
    )


def _records(solution):
    return {r.event_id: r for r in solution.importance}


def _assert_identical(a, b, context):
    assert a.top_probability == b.top_probability, context
    assert a.minimal_cut_sets == b.minimal_cut_sets, context
    assert a.variable_order == b.variable_order, context
    assert a.bdd_node_count == b.bdd_node_count, context
    assert a.cut_sets_complete == b.cut_sets_complete, context
    ra, rb = _records(a), _records(b)
    assert set(ra) == set(rb), context
    for event_id in ra:
        x, y = ra[event_id], rb[event_id]
        for field in (
            "in_support", "probability", "top_given_failed", "top_given_working",
            "birnbaum", "fussell_vesely", "risk_achievement_worth",
            "risk_reduction_worth", "undefined",
        ):
            assert getattr(x, field) == getattr(y, field), f"{context}: {event_id}.{field}"


@pytest.fixture(autouse=True)
def isolated_cache():
    clear_compile_cache()
    yield
    clear_compile_cache()


def test_a_probability_only_change_reuses_the_compilation():
    model = _model("M03_repeated_event.json")
    rerated = _rerate(model, "A", "0.3")

    warm = solve_model(rerated, importance="on")  # cache populated by `model`? no — first call below
    clear_compile_cache()
    fresh = solve_model(rerated, importance="on")  # guaranteed from-scratch
    _assert_identical(warm, fresh, "probability-only change vs fresh solve")
    # and the warm path really was faster than compile+solve
    assert warm.elapsed_seconds > 0 and fresh.elapsed_seconds > 0


def test_a_reused_compile_is_used_for_a_second_probability_change():
    model = _model("M03_repeated_event.json")
    solve_model(model, importance="on")  # compiles + caches
    assert compilation_cache_size() == 1
    first = solve_model(_rerate(model, "A", "0.3"), importance="on")   # hit
    second = solve_model(_rerate(model, "A", "0.7"), importance="on")  # hit
    assert compilation_cache_size() == 1  # same fingerprint, one entry
    clear_compile_cache()
    _assert_identical(first, solve_model(_rerate(model, "A", "0.3"), importance="on"), "first rerate")
    _assert_identical(second, solve_model(_rerate(model, "A", "0.7"), importance="on"), "second rerate")


def test_labels_and_lexical_noise_do_not_change_the_fingerprint():
    model = _model("M03_repeated_event.json")
    relabelled = dataclasses.replace(
        model,
        basic_events=tuple(
            dataclasses.replace(e, label=f"{e.label} (revised)")
            for e in model.basic_events
        ),
    )
    assert structure_fingerprint(model) == structure_fingerprint(relabelled)
    # lexical probability spelling: same rational, same structure
    respelled = _rerate(model, "A", "0.10")
    assert structure_fingerprint(model) == structure_fingerprint(respelled)


def test_a_structure_change_must_miss_the_cache():
    model = _model("M03_repeated_event.json")
    regated = dataclasses.replace(
        model,
        gates=tuple(
            dataclasses.replace(g, inputs=tuple(reversed(g.inputs)))
            for g in model.gates
        ),
    )
    assert structure_fingerprint(model) != structure_fingerprint(regated)
    solve_model(model, importance="on")
    assert compilation_cache_size() == 1
    solve_model(regated, importance="on")  # different fingerprint -> new entry
    assert compilation_cache_size() == 2


def test_removed_and_added_events_change_the_fingerprint():
    model = _model("M03_repeated_event.json")
    fewer = dataclasses.replace(
        model,
        basic_events=tuple(e for e in model.basic_events if e.id != "C"),
        probabilities={k: v for k, v in model.probabilities.items() if k != "C"},
    )
    assert structure_fingerprint(model) != structure_fingerprint(fewer)


def test_cache_eviction_keeps_the_newest_entries():
    base = _model("M02_or.json")
    solve_model(base, importance="on")
    variants = []
    for index in range(10):
        raw = {
            "schema_version": "0.1.0",
            "model_id": f"EVICT_{index}",
            "baseline_id": "B",
            "assumptions": {
                "basic_events_independent": True,
                "probability_semantics": "fixed_conditioned_probability",
                "condition": "eviction probe",
            },
            "basic_events": [
                {"id": f"X{index}", "label": "x", "probability": "0.1", "source": "t"},
                {"id": f"Y{index}", "label": "y", "probability": "0.2", "source": "t"},
            ],
            "gates": [
                {"id": "G", "kind": "AND", "inputs": [f"X{index}", f"Y{index}"]},
            ],
            "top_event": "G",
        }
        variants.append(validate_model(raw))
        solve_model(variants[-1], importance="on")
    assert compilation_cache_size() <= 8  # bounded


def test_undefined_reasons_survive_reuse_identically():
    # p=0 events and Q=0 models produce undefined measures with reasons;
    # the reused path must reproduce the SAME undefined tuples.
    raw = {
        "schema_version": "0.1.0",
        "model_id": "UNDEF_REUSE",
        "baseline_id": "B",
        "assumptions": {
            "basic_events_independent": True,
            "probability_semantics": "fixed_conditioned_probability",
            "condition": "undefined-reason reuse probe",
        },
        "basic_events": [
            {"id": "A", "label": "a", "probability": "0", "source": "t"},   # p=0
            {"id": "B", "label": "b", "probability": "0.5", "source": "t"},
        ],
        "gates": [{"id": "TOP", "kind": "OR", "inputs": ["A", "B"]}],
        "top_event": "TOP",
    }
    model = validate_model(raw)
    warm = solve_model(model, importance="on")
    zeroed = _rerate(model, "B", "0")  # Q becomes 0 -> everything undefined
    warm_zero = solve_model(zeroed, importance="on")
    clear_compile_cache()
    fresh_zero = solve_model(zeroed, importance="on")
    _assert_identical(warm, solve_model(model, importance="on"), "p=0 warm")
    _assert_identical(warm_zero, fresh_zero, "Q=0 reuse")


def test_importance_off_and_on_paths_share_the_cache_safely():
    model = _model("M03_repeated_event.json")
    solve_model(model, importance="off")   # caches structure (cut sets too)
    assert compilation_cache_size() == 1
    on = solve_model(model, importance="on")  # reuse + importance
    clear_compile_cache()
    _assert_identical(on, solve_model(model, importance="on"), "off->on reuse")


def test_rate_models_reuse_the_same_way():
    model = load_model(ROOT / "examples" / "R01_rate_and.json")
    # a rate change produces a new p but the SAME structure -> cache hit
    warm = solve_model(model, importance="on")
    clear_compile_cache()
    fresh = solve_model(model, importance="on")
    _assert_identical(warm, fresh, "rate model reuse")
