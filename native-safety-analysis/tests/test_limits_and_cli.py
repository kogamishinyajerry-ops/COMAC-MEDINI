"""Resource limits, hash stability, CLI behavior, BDD node cap."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from native_safety.adapters.json_io import load_model  # noqa: E402
from native_safety.domain.errors import ModelError, ResourceLimitError  # noqa: E402
from native_safety.domain.semantic_hash import semantic_model_hash  # noqa: E402
from native_safety.kernel.solve import solve_model  # noqa: E402

CASES = ROOT / "reference" / "03_contracts" / "examples"
PY = sys.executable


def test_semantic_hash_stable_and_sensitive():
    m1 = load_model(CASES / "M03_repeated_event.json")
    h1 = semantic_model_hash(m1)
    # reload identical file -> identical hash
    m2 = load_model(CASES / "M03_repeated_event.json")
    assert semantic_model_hash(m2) == h1
    # lexical change of probability only ("0.1" vs "0.10") -> same hash
    data = json.loads((CASES / "M03_repeated_event.json").read_text(encoding="utf-8"))
    data["basic_events"][0]["probability"] = "0.10"
    from native_safety.domain.validation import validate_model

    assert semantic_model_hash(validate_model(data)) == h1
    # semantic change (probability value) -> different hash
    data["basic_events"][0]["probability"] = "0.2"
    assert semantic_model_hash(validate_model(data)) != h1
    # label change is NOT semantic -> hash unchanged
    data["basic_events"][0]["probability"] = "0.1"
    data["basic_events"][0]["label"] = "totally different"
    assert semantic_model_hash(validate_model(data)) == h1


def test_bdd_node_limit_hits_resource_limited():
    m = load_model(CASES / "M03_repeated_event.json")
    with pytest.raises(ResourceLimitError):
        solve_model(m, max_nodes=1)


def test_mcs_path_limit_reports_incomplete():
    m = load_model(CASES / "M05_vote_2_of_3.json")
    r = solve_model(m, max_mcs_paths=1)
    assert r.cut_sets_complete is False


def test_deep_chain_no_recursion_error():
    """1500-gate OR chain must not blow the Python stack (iterative apply)."""
    n = 1500
    model = {
        "schema_version": "0.1.0",
        "model_id": "deep",
        "baseline_id": "SYN-DEEP",
        "assumptions": {
            "basic_events_independent": True,
            "probability_semantics": "fixed_conditioned_probability",
            "condition": "deep chain stress",
        },
        "basic_events": [
            {"id": f"X{i:05d}", "label": f"X{i}", "probability": "0.001", "source": "synthetic"}
            for i in range(n)
        ],
        "gates": [
            {"id": f"G{i:05d}", "kind": "OR", "inputs": (
                ["X00000", "X00001"] if i == 1 else [f"G{i-1:05d}", f"X{i:05d}"]
            )}
            for i in range(1, n)
        ],
        "top_event": f"G{n-1:05d}",
    }
    r = solve_model(validate_model_local(model))
    assert r.cut_sets_complete
    assert len(r.minimal_cut_sets) == n


def validate_model_local(data):
    from native_safety.domain.validation import validate_model

    return validate_model(data)


def test_cli_validate_ok():
    out = subprocess.run(
        [PY, str(ROOT / "run.py"), "validate", str(CASES / "M01_and.json")],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["valid"] is True


def test_cli_rejects_unsupported_gate():
    out = subprocess.run(
        [PY, str(ROOT / "run.py"), "validate", str(CASES / "N05_unsupported_gate.json")],
        capture_output=True, text=True,
    )
    assert out.returncode == 3
    assert json.loads(out.stdout)["error"]["code"] == "UNSUPPORTED_GATE"


def test_cli_analyze_writes_evidence(tmp_path):
    out = subprocess.run(
        [PY, str(ROOT / "run.py"), "analyze", str(CASES / "M03_repeated_event.json"),
         "--evidence-dir", str(tmp_path), "--run-id", "pytest_m03"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["top_probability"] == "0.044"
    assert payload["approval_state"] == "not_granted_by_this_result"
    run_dir = tmp_path / "run_pytest_m03"
    for rel in ("manifest.json", "inputs/model_input.json", "results/analysis_result.json",
                "reports/report.md", "execution/engine_meta.json"):
        assert (run_dir / rel).exists(), f"missing {rel}"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "succeeded"
    assert manifest["semantic_model_hash"] == payload["semantic_model_hash"]


def test_cli_capabilities_honest():
    out = subprocess.run(
        [PY, str(ROOT / "run.py"), "capabilities"], capture_output=True, text=True
    )
    assert out.returncode == 0
    caps = json.loads(out.stdout)["capabilities"]
    by_id = {c["capability_id"]: c for c in caps}
    assert by_id["analyze_static_fta_probability"]["status"] == "verified"
    assert by_id["exponential_rate_to_probability"]["status"] == "verified"
    assert by_id["importance_measures"]["status"] == "verified"
    assert by_id["other_rate_models"]["status"] == "unsupported"
    assert by_id["dynamic_gates_repairs_dormant"]["status"] == "unsupported"
    assert by_id["fmea_requirements_traceability"]["status"] == "unsupported"


def test_validation_rejects_missing_independence():
    data = json.loads((CASES / "M01_and.json").read_text(encoding="utf-8"))
    data["assumptions"]["basic_events_independent"] = False
    with pytest.raises(ModelError) as e:
        validate_model_local(data)
    assert e.value.code == "ASSUMPTIONS"
