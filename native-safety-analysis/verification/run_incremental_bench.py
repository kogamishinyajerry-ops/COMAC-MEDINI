"""Phase-cost breakdown for the incremental-recalc decision (B03 wrap-up).

Measures, on chain pressure models, the three phases of `solve_model` when
ONLY probabilities change (structure fixed):

  1. compile   variable order + BDD build (structure only, q-independent)
  2. table     _probability_table (O(#nodes), Fraction arithmetic)
  3. importance compute_importance (reach + diff + cofactors)

The mathematically relevant question: which phase dominates, and what does a
probability-only change actually invalidate? (Answer it with numbers before
building anything.)

Run:  .venv/Scripts/python verification/run_incremental_bench.py
"""
from __future__ import annotations

import sys
import time
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from native_safety.domain.validation import validate_model  # noqa: E402
from native_safety.kernel.bdd import BddManager  # noqa: E402
from native_safety.kernel.importance import compute_importance  # noqa: E402
from native_safety.kernel.solve import _compile, _probability_table, _variable_order  # noqa: E402


def chain_model(n: int) -> dict:
    """Chain-OR pressure model: TOP = E1 OR E2 OR ... OR En (BDD nodes = n)."""
    events = [
        {"id": f"E{i:04d}", "label": f"E{i}", "probability": "0.01", "source": "bench"}
        for i in range(n)
    ]
    return {
        "schema_version": "0.1.0",
        "model_id": f"BENCH_CHAIN_{n}",
        "baseline_id": "BENCH",
        "assumptions": {
            "basic_events_independent": True,
            "probability_semantics": "fixed_conditioned_probability",
            "condition": "bench chain-OR pressure model",
        },
        "basic_events": events,
        "gates": [{"id": "TOP", "kind": "OR", "inputs": [e["id"] for e in events]}],
        "top_event": "TOP",
    }


def bench(n: int) -> dict:
    model = validate_model(chain_model(n))
    # phase 1: compile (structure only — identical for ANY probabilities)
    t0 = time.perf_counter()
    var_order = _variable_order(model)
    mgr = BddManager(var_order=var_order)
    root = _compile(model, mgr)
    t_compile = time.perf_counter() - t0

    # phase 2: probability table
    t0 = time.perf_counter()
    table = _probability_table(root, mgr, model.probabilities)
    t_table = time.perf_counter() - t0
    probability = table[root]

    # phase 3: importance
    t0 = time.perf_counter()
    records = compute_importance(root, mgr, model.probabilities, table, probability)
    t_importance = time.perf_counter() - t0

    # what a probability-only change REALLY needs:
    # recompile? NO — structure and variable order do not depend on q.
    # re-table? YES — every node value is multilinear in the q's.
    # re-importance? YES — Q changes, so every ratio changes.
    # The saving a cache offers is exactly the compile phase.
    return {
        "n": n,
        "nodes": len(mgr.unique),
        "compile_s": t_compile,
        "table_s": t_table,
        "importance_s": t_importance,
        "records": len(records),
    }


def main() -> int:
    print("phase-cost breakdown (chain-OR pressure models, Windows, venv 3.13)")
    print(f"{'n':>6} {'nodes':>7} {'compile':>9} {'table':>9} {'importance':>10} {'sum':>9}  cache-win")
    for n in (200, 800, 1500, 3000):
        r = bench(n)
        total = r["compile_s"] + r["table_s"] + r["importance_s"]
        win = r["compile_s"] / total * 100 if total else 0
        print(
            f"{r['n']:>6} {r['nodes']:>7} {r['compile_s']:>9.3f} {r['table_s']:>9.3f} "
            f"{r['importance_s']:>10.3f} {total:>9.3f}  {win:5.1f}%"
        )

    print(
        "\nreading: the compiled BDD (structure + variable order) does not depend on\n"
        "probabilities. A probability-only change therefore needs: fresh table (O(#nodes),\n"
        "unavoidable — every node value is multilinear in q), fresh importance (Q changed,\n"
        "so every ratio changed; q+/q- of the CHANGED event are invariant but the ratios\n"
        "still move), and NO compile. The 'incremental' saving is exactly the compile column."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
