"""Metamorphic tests: transformations that must NOT change semantics.

Per B_核心规划 §06: input reordering, child swapping (commutativity),
absorption/distribution rewrites, duplicate reference elimination,
monotonicity (raising an event probability never lowers P(TOP) in a
coherent model), bounds [0,1], and cut-set minimality invariants.
"""
from __future__ import annotations

import copy
import random
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from native_safety.domain.validation import validate_model  # noqa: E402
from native_safety.kernel.solve import solve_model  # noqa: E402

BASE = {
    "schema_version": "0.1.0",
    "model_id": "Meta",
    "baseline_id": "SYN-META",
    "assumptions": {
        "basic_events_independent": True,
        "probability_semantics": "fixed_conditioned_probability",
        "condition": "Metamorphic synthetic model",
    },
    "basic_events": [
        {"id": "A", "label": "A", "probability": "0.1", "source": "synthetic"},
        {"id": "B", "label": "B", "probability": "0.2", "source": "synthetic"},
        {"id": "C", "label": "C", "probability": "0.3", "source": "synthetic"},
    ],
    "gates": [
        {"id": "G1", "kind": "AND", "inputs": ["A", "B"]},
        {"id": "G2", "kind": "AND", "inputs": ["A", "C"]},
        {"id": "TOP", "kind": "OR", "inputs": ["G1", "G2"]},
    ],
    "top_event": "TOP",
}


def solve(data: dict):
    return solve_model(validate_model(copy.deepcopy(data)))


def test_input_reordering_invariant():
    shuffled = copy.deepcopy(BASE)
    shuffled["basic_events"] = list(reversed(shuffled["basic_events"]))
    shuffled["gates"] = list(reversed(shuffled["gates"]))
    assert solve(BASE).top_probability == solve(shuffled).top_probability
    assert solve(BASE).minimal_cut_sets == solve(shuffled).minimal_cut_sets


def test_child_swap_invariant():
    swapped = copy.deepcopy(BASE)
    # swap children of G1 (A,B) -> (B,A): commutativity of AND
    swapped["gates"][0]["inputs"] = ["B", "A"]
    assert solve(BASE).top_probability == solve(swapped).top_probability


def test_absorption_rewrite_invariant():
    """A OR (A AND B) == A — absorption must hold on the DAG, not just trees."""
    model = {
        **copy.deepcopy(BASE),
        "gates": [
            {"id": "GAB", "kind": "AND", "inputs": ["A", "B"]},
            {"id": "TOP", "kind": "OR", "inputs": ["A", "GAB"]},
        ],
        "top_event": "TOP",
    }
    r = solve(model)
    assert r.top_probability == Fraction("0.1")
    assert r.minimal_cut_sets == [["A"]]


def test_distribution_rewrite_invariant():
    """(A AND B) OR (A AND C) == A AND (B OR C): 0.1*(0.2+0.3-0.06)=0.044 both ways."""
    distributed = copy.deepcopy(BASE)
    factored = {
        **copy.deepcopy(BASE),
        "gates": [
            {"id": "ORBC", "kind": "OR", "inputs": ["B", "C"]},
            {"id": "TOP", "kind": "AND", "inputs": ["A", "ORBC"]},
        ],
        "top_event": "TOP",
    }
    assert solve(distributed).top_probability == solve(factored).top_probability == Fraction("0.044")


def test_duplicate_reference_no_new_event():
    """AND(A, A, B) is semantically AND(A, B): repetition introduces no new variable."""
    model = copy.deepcopy(BASE)
    model["gates"] = [
        {"id": "TOP", "kind": "AND", "inputs": ["A", "A", "B"]},
    ]
    r = solve(model)
    assert r.top_probability == Fraction("0.02")
    assert r.minimal_cut_sets == [["A", "B"]]


def test_monotonicity():
    """Coherent model: raising any p never lowers P(TOP)."""
    rng = random.Random(7)
    for _ in range(25):
        bumped = copy.deepcopy(BASE)
        idx = rng.randrange(3)
        old = Fraction(bumped["basic_events"][idx]["probability"])
        new = old + (1 - old) * Fraction(1, 2) if old < 1 else old
        bumped["basic_events"][idx]["probability"] = str(float(new)) if new.denominator != 1 else str(new.numerator)
        # rebuild with exact decimal text
        from native_safety.adapters.json_io import probability_to_text

        bumped["basic_events"][idx]["probability"] = probability_to_text(new)
        assert solve(bumped).top_probability >= solve(BASE).top_probability


def test_probability_bounds():
    rng = random.Random(11)
    for _ in range(15):
        model = copy.deepcopy(BASE)
        for e in model["basic_events"]:
            e["probability"] = rng.choice(["0", "0.5", "1", "0.007", "0.999999"])
        p = solve(model).top_probability
        assert 0 <= p <= 1


def test_cut_sets_minimal_no_supersets():
    """No cut set may properly contain another (minimality)."""
    rng = random.Random(3)
    for trial in range(30):
        n = rng.randint(2, 7)
        model = copy.deepcopy(BASE)
        model["basic_events"] = [
            {"id": f"X{i}", "label": f"X{i}", "probability": rng.choice(["0.05", "0.1", "0.5"]), "source": "s"}
            for i in range(n)
        ]
        available = [e["id"] for e in model["basic_events"]]
        gates = []
        for gi in range(rng.randint(1, 6)):
            kind = rng.choice(["AND", "OR", "K_OF_N"])
            inputs = rng.sample(available, rng.randint(1, min(3, len(available))))
            g = {"id": f"G{gi}", "kind": kind, "inputs": inputs}
            if kind == "K_OF_N":
                g["k"] = rng.randint(1, len(inputs))
            gates.append(g)
            available.append(g["id"])
        model["gates"] = gates
        model["top_event"] = available[-1]
        r = solve(model)
        cuts = [frozenset(c) for c in r.minimal_cut_sets]
        for i, a in enumerate(cuts):
            for j, b in enumerate(cuts):
                if i != j:
                    assert not (a < b), f"cut {sorted(b)} contains cut {sorted(a)}"
