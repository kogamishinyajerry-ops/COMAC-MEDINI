"""Cross-check wrapper for pytest: production vs independent reference on
seed cases plus a deterministic random sample (subset of the standalone
verification/run_cross_check.py run)."""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "verification"))

from native_safety.domain.validation import validate_model  # noqa: E402
from native_safety.kernel.solve import solve_model  # noqa: E402
from run_cross_check import random_model  # noqa: E402
from reference_fta import ref_solve  # noqa: E402


def test_production_matches_reference_on_random_models():
    rng = random.Random(42)
    for i in range(60):
        data = random_model(rng, rng.randint(1, 8), rng.randint(1, 6))
        model = validate_model(data)
        production = solve_model(model)
        ref_p, ref_mcs = ref_solve(data)
        assert production.top_probability == ref_p, f"case {i}: {production.top_probability} != {ref_p}"
        assert production.minimal_cut_sets == ref_mcs, f"case {i} MCS mismatch"
