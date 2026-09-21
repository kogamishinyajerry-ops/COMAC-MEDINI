"""Cross-check harness: production ROBDD kernel vs independent truth-table reference.

Compares exact Fraction probabilities and minimal cut set families on:
  - all seed positive cases;
  - randomly generated models (fixed seed, reproducible).

Discrepancy in EITHER probability OR cut sets is a hard failure.
"""
from __future__ import annotations

import json
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


def check_model(data: dict, label: str) -> bool:
    model = validate_model(data)
    production = solve_model(model)
    ref_p, ref_mcs = ref_solve(data)
    ok_p = production.top_probability == ref_p  # exact Fraction equality
    ok_m = production.minimal_cut_sets == ref_mcs
    status = "PASS" if (ok_p and ok_m) else "FAIL"
    print(f"{status} {label}: prod={production.top_probability} ref={ref_p} mcs_eq={ok_m}")
    return ok_p and ok_m


def random_model(rng: random.Random, n_events: int, n_gates: int) -> dict:
    events = [f"E{i:02d}" for i in range(n_events)]
    gates: list[dict] = []
    available = list(events)
    for gi in range(n_gates):
        kind = rng.choice(["AND", "OR", "OR", "K_OF_N"]) if n_events > 2 else rng.choice(["AND", "OR"])
        n_inputs = rng.randint(1, min(4, len(available)))
        inputs = rng.sample(available, n_inputs)
        gate: dict = {"id": f"G{gi:02d}", "kind": kind, "inputs": inputs}
        if kind == "K_OF_N":
            # distinct references enforced by contract; sample already distinct
            gate["k"] = rng.randint(1, len(inputs))
        gates.append(gate)
        available.append(gate["id"])
        # occasionally rewire inputs to create shared subgraphs (never self-ref)
        if rng.random() < 0.3 and len(available) > 2:
            others = [x for x in available if x != gate["id"]]
            inputs = rng.sample(others, rng.randint(1, min(3, len(others))))
            if kind == "K_OF_N":
                inputs = list(dict.fromkeys(inputs))
                gate["k"] = min(gate["k"], len(inputs))
            gate["inputs"] = inputs
    top = available[-1]
    return {
        "schema_version": "0.1.0",
        "model_id": f"RANDOM_{rng.randint(1000, 9999)}",
        "baseline_id": "SYNTH-RAND",
        "assumptions": {
            "basic_events_independent": True,
            "probability_semantics": "fixed_conditioned_probability",
            "condition": "Random cross-check model (synthetic)",
        },
        "basic_events": [
            {
                "id": e,
                "label": e,
                "probability": rng.choice(["0", "0.001", "0.01", "0.1", "0.2", "0.5", "0.9", "1", "0.000001"]),
                "source": "synthetic_random",
            }
            for e in events
        ],
        "gates": gates,
        "top_event": top,
    }


def main() -> int:
    cases_dir = ROOT / "reference" / "03_contracts" / "examples"
    failures = 0

    for name in sorted(p.name for p in cases_dir.glob("M*.json")):
        data = json.loads((cases_dir / name).read_text(encoding="utf-8"))
        failures += 0 if check_model(data, name) else 1

    rng = random.Random(20260921)
    for i in range(200):
        n_events = rng.randint(1, 8)
        n_gates = rng.randint(1, 6)
        data = random_model(rng, n_events, n_gates)
        failures += 0 if check_model(data, f"random-{i:03d}") else 1

    total = 10 + 200
    print(f"\ncross-check: {total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
