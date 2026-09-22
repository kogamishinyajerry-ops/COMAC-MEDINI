"""Change-management layer 2: structured impact diff and first-class revert.

Two capabilities land here:
  * `review impact` / `baseline impact` — a structural diff between two
    canonical forms, both read from rows the store already holds, so the
    analysis cannot drift from what is actually on disk;
  * `review revert` — proposing to go back to a previously held baseline,
    built from the baseline table's own canonical form, never from a
    caller-supplied file, and still requiring propose -> decide -> apply
    under a named human like every other baseline move.

The round also fixes a real defect the revert exposed: stale used to be
cleared never, only set, so after a revert the runs that became current
again stayed flagged stale forever.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from native_safety.adapters.json_io import load_model  # noqa: E402
from native_safety.cli.main import main  # noqa: E402
from native_safety.domain.model_diff import change_summary, diff_canonical_forms  # noqa: E402
from native_safety.domain.semantic_hash import (  # noqa: E402
    canonical_model_form,
    semantic_model_hash,
)
from native_safety.kernel.solve import solve_model  # noqa: E402
from native_safety.store import SqliteRepository, StoreError  # noqa: E402
from native_safety.store import errors as store_err  # noqa: E402

EXAMPLES = ROOT / "reference" / "03_contracts" / "examples"
HUMAN = "J. Engineer"


def variant_of(model, *, probability: str, event_id: str = "A"):
    import dataclasses
    from fractions import Fraction

    events = tuple(
        dataclasses.replace(e, probability=Fraction(probability), probability_text=probability)
        if e.id == event_id else e
        for e in model.basic_events
    )
    return dataclasses.replace(model, basic_events=events)


def reorder_gates_of(model):
    import dataclasses

    gates = tuple(
        dataclasses.replace(g, inputs=tuple(reversed(g.inputs))) if g.id == "G1" else g
        for g in model.gates
    )
    return dataclasses.replace(model, gates=gates)


@pytest.fixture()
def three_baselines(tmp_path):
    """A store whose model has moved M03 -> variant; both baselines are held.

    Returns (repo, model, variant, old_hash, current_hash).
    """
    model = load_model(EXAMPLES / "M03_repeated_event.json")
    variant = variant_of(model, probability="0.3")
    with SqliteRepository(str(tmp_path / "store.sqlite")) as repo:
        solution = solve_model(model)
        repo.record_run(
            run_id="r1", model=model, solution=solution,
            payload={
                "schema_version": "0.1.0", "model_schema_version": model.schema_version,
                "run_id": "r1", "model_id": model.model_id, "engine_version": "0.1.0",
                "semantic_model_hash": semantic_model_hash(model), "status": "succeeded",
                "top_probability": str(solution.top_probability), "cut_sets_complete": True,
                "importance_status": "not_requested",
                "engine_stats": {"top_probability_exact": str(solution.top_probability)},
            },
            actor="local-cli",
        )
        old = repo.current_baseline_hash("M03_repeated_event")
        repo.register_model_baseline(variant, actor=HUMAN, expected_baseline_hash=old)
        solution2 = solve_model(variant)
        repo.record_run(
            run_id="r2", model=variant, solution=solution2,
            payload={
                "schema_version": "0.1.0", "model_schema_version": variant.schema_version,
                "run_id": "r2", "model_id": variant.model_id, "engine_version": "0.1.0",
                "semantic_model_hash": semantic_model_hash(variant), "status": "succeeded",
                "top_probability": str(solution2.top_probability), "cut_sets_complete": True,
                "importance_status": "not_requested",
                "engine_stats": {"top_probability_exact": str(solution2.top_probability)},
            },
            actor="local-cli",
        )
        current = repo.current_baseline_hash("M03_repeated_event")
        yield repo, model, variant, old, current


# --------------------------------------------------------------------------
# the pure diff
# --------------------------------------------------------------------------


def test_diff_of_identical_forms_is_empty_and_semantically_equal():
    model = load_model(EXAMPLES / "M03_repeated_event.json")
    form = canonical_model_form(model)
    diff = diff_canonical_forms(form, json.loads(json.dumps(form)))
    assert diff["semantically_equal"] is True
    summary = change_summary(diff)
    assert summary["events_changed"] == 0 and summary["gates_changed"] == 0
    assert summary["assumptions_changed"] == 0


def test_diff_reports_a_probability_change_exactly():
    model = load_model(EXAMPLES / "M03_repeated_event.json")  # A=0.1, B=0.2, C=0.3
    variant = variant_of(model, probability="0.3")
    diff = diff_canonical_forms(canonical_model_form(model), canonical_model_form(variant))
    assert diff["semantically_equal"] is False
    changed = diff["events"]["changed"]
    assert [(c["id"], c["old"]["p"], c["new"]["p"]) for c in changed] == [("A", "0.1", "0.3")]
    # and no other kind of change is reported
    assert diff["events"]["added"] == [] and diff["events"]["removed"] == []
    assert diff["gates"]["changed"] == []
    assert diff["assumptions_changed"] == []


def test_lexical_noise_is_already_gone_before_the_diff_sees_it():
    model = load_model(EXAMPLES / "M03_repeated_event.json")
    variant = variant_of(model, probability="0.10")  # same rational, other spelling
    diff = diff_canonical_forms(canonical_model_form(model), canonical_model_form(variant))
    assert diff["semantically_equal"] is True
    assert diff["events"]["changed"] == []


def test_gate_input_reorder_is_changed_but_flagged_order_only():
    model = load_model(EXAMPLES / "M03_repeated_event.json")
    reordered = reorder_gates_of(model)
    diff = diff_canonical_forms(canonical_model_form(model), canonical_model_form(reordered))
    # the hash genuinely changes (canonical-v1 keeps declared order)...
    assert semantic_model_hash(model) != semantic_model_hash(reordered)
    # ...so the diff must report it, but call it what it is
    changed = diff["gates"]["changed"]
    assert len(changed) == 1
    assert changed[0]["order_only"] is True
    assert change_summary(diff)["gates_changed_order_only"] == 1


def test_diff_of_disjoint_forms_counts_adds_and_removals():
    empty = {
        "canonicalization": "canonical-v1", "schema_version": "0.1.0", "model_id": "M",
        "assumptions": {
            "basic_events_independent": True,
            "probability_semantics": "fixed_conditioned_probability",
            "condition": "x",
        },
        "basic_events": [], "gates": [], "top_event": "TOP",
    }
    two = dict(empty, basic_events=[
        {"id": "A", "p": "0.1"}, {"id": "B", "p": "0.2"},
    ])
    diff = diff_canonical_forms(empty, two)
    assert [e["id"] for e in diff["events"]["added"]] == ["A", "B"]
    back = diff_canonical_forms(two, empty)
    assert [e["id"] for e in back["events"]["removed"]] == ["A", "B"]
    assert diff_canonical_forms(None, two)["semantically_equal"] is False


def test_assumption_and_top_event_changes_are_reported():
    base = {
        "canonicalization": "canonical-v1", "schema_version": "0.1.0", "model_id": "M",
        "assumptions": {
            "basic_events_independent": True,
            "probability_semantics": "fixed_conditioned_probability",
            "condition": "x",
        },
        "basic_events": [{"id": "A", "p": "0.1"}],
        "gates": [{"id": "G", "kind": "OR", "inputs": ["A"]}],
        "top_event": "G",
    }
    edited = json.loads(json.dumps(base))
    edited["assumptions"]["condition"] = "y"
    edited["top_event"] = "A"
    diff = diff_canonical_forms(base, edited)
    assert diff["assumptions_changed"] == [{"field": "condition", "old": "x", "new": "y"}]
    assert diff["top_event_changed"] == {"old": "G", "new": "A"}


# --------------------------------------------------------------------------
# impact, read from the store alone
# --------------------------------------------------------------------------


def test_review_impact_diffs_the_proposal_against_the_current_baseline(three_baselines):
    repo, model, variant, old, current = three_baselines
    repo.propose_review(
        review_id="R2", model_id="M03_repeated_event",
        expected_baseline_hash=current, proposed_model=model, proposed_by="agent",
    )
    impact = repo.review_impact("R2")
    assert impact["baseline_still_current"] is True
    assert impact["summary"]["events_changed"] == 1  # 0.3 -> 0.1 when going back
    changed = impact["diff"]["events"]["changed"]
    assert changed[0]["id"] == "A" and changed[0]["old"]["p"] == "0.3"


def test_review_impact_flags_a_stale_expectation(three_baselines, tmp_path):
    repo, model, variant, old, current = three_baselines
    # a THIRD semantics: a proposal written now (valid), then the baseline
    # moves underneath it — the exact situation optimistic concurrency exists for
    third = variant_of(model, probability="0.25", event_id="B")
    repo.propose_review(
        review_id="STALE", model_id="M03_repeated_event",
        expected_baseline_hash=current, proposed_model=third, proposed_by="agent",
    )
    repo.propose_revert(
        review_id="RV-PRE", model_id="M03_repeated_event",
        target_baseline_hash=old, expected_baseline_hash=current, proposed_by="agent",
    )
    repo.decide_review("RV-PRE", approve=True, reviewer=HUMAN)
    repo.apply_review("RV-PRE", reviewer=HUMAN)
    # now the current baseline is `old` again and STALE's expectation is gone
    impact = repo.review_impact("STALE")
    assert impact["baseline_still_current"] is False
    assert impact["summary"]["events_changed"] == 1  # 0.3 -> 0.25 against the old baseline
    # and applying that stale approval is refused, as everywhere else
    repo.decide_review("STALE", approve=True, reviewer=HUMAN)
    with pytest.raises(StoreError) as caught:
        repo.apply_review("STALE", reviewer=HUMAN)
    assert caught.value.code == store_err.BASELINE_CONFLICT


def test_baseline_impact_of_the_current_baseline_is_empty(three_baselines):
    repo, model, variant, old, current = three_baselines
    impact = repo.baseline_impact("M03_repeated_event", current)
    assert impact["is_current"] is True
    assert impact["summary"]["semantically_equal"] is True


def test_impact_of_an_unknown_review_is_refused(three_baselines):
    repo, *_ = three_baselines
    with pytest.raises(StoreError) as caught:
        repo.review_impact("NOPE")
    assert caught.value.code == store_err.REVIEW_NOT_FOUND


# --------------------------------------------------------------------------
# revert: a proposal like any other, built from the store
# --------------------------------------------------------------------------


def test_a_revert_proposal_never_touches_the_baseline(three_baselines):
    repo, model, variant, old, current = three_baselines
    record = repo.propose_revert(
        review_id="RV-1", model_id="M03_repeated_event",
        target_baseline_hash=old, expected_baseline_hash=current, proposed_by="agent",
    )
    assert record["state"] == "proposed"
    assert repo.current_baseline_hash("M03_repeated_event") == current
    assert repo.get_run("r1")["stale"] == 1  # nothing moved


def test_a_revert_requires_a_named_human_in_two_steps(three_baselines):
    repo, model, variant, old, current = three_baselines
    repo.propose_revert(
        review_id="RV-1", model_id="M03_repeated_event",
        target_baseline_hash=old, expected_baseline_hash=current, proposed_by="agent",
    )
    with pytest.raises(StoreError) as caught:
        repo.decide_review("RV-1", approve=True, reviewer="agent")
    assert caught.value.code == store_err.APPROVAL_AUTHORITY
    with pytest.raises(StoreError) as caught:
        repo.apply_review("RV-1", reviewer="claude")
    assert caught.value.code == store_err.APPROVAL_AUTHORITY
    assert repo.current_baseline_hash("M03_repeated_event") == current


def test_an_applied_revert_restores_the_old_baseline_and_flips_stale(three_baselines):
    repo, model, variant, old, current = three_baselines
    assert repo.get_run("r1")["stale"] == 1 and repo.get_run("r2")["stale"] == 0

    repo.propose_revert(
        review_id="RV-1", model_id="M03_repeated_event",
        target_baseline_hash=old, expected_baseline_hash=current, proposed_by="agent",
    )
    repo.decide_review("RV-1", approve=True, reviewer=HUMAN)
    record, _ = repo.apply_review("RV-1", reviewer=HUMAN)

    assert record["applied_baseline_hash"] == old
    assert repo.current_baseline_hash("M03_repeated_event") == old
    # the derived quantity flips BOTH ways: r1 is current again, r2 fell off
    assert repo.get_run("r1")["stale"] == 0
    assert repo.get_run("r1")["stale_reason"] is None
    assert repo.get_run("r2")["stale"] == 1
    # old runs' payloads stay byte-identical (only derived flags moved)
    assert repo.verify() == []


def test_a_revert_cannot_target_the_current_baseline(three_baselines):
    repo, model, variant, old, current = three_baselines
    with pytest.raises(StoreError) as caught:
        repo.propose_revert(
            review_id="RV-X", model_id="M03_repeated_event",
            target_baseline_hash=current, expected_baseline_hash=current, proposed_by="agent",
        )
    assert caught.value.code == store_err.REVERT_TARGET


def test_a_revert_cannot_target_a_baseline_the_store_never_held(three_baselines):
    repo, *_ = three_baselines
    with pytest.raises(StoreError) as caught:
        repo.propose_revert(
            review_id="RV-X", model_id="M03_repeated_event",
            target_baseline_hash="f" * 64, expected_baseline_hash=None, proposed_by="agent",
        )
    assert caught.value.code == store_err.REVERT_TARGET


def test_a_revert_cannot_target_another_models_baseline(three_baselines):
    repo, model, *_ = three_baselines
    other = load_model(EXAMPLES / "M02_or.json")
    repo.record_run(
        run_id="m2-1", model=other, solution=solve_model(other),
        payload={
            "schema_version": "0.1.0", "model_schema_version": other.schema_version,
            "run_id": "m2-1", "model_id": other.model_id, "engine_version": "0.1.0",
            "semantic_model_hash": semantic_model_hash(other), "status": "succeeded",
            "top_probability": str(solve_model(other).top_probability),
            "cut_sets_complete": True, "importance_status": "not_requested",
            "engine_stats": {"top_probability_exact": str(solve_model(other).top_probability)},
        },
        actor="local-cli",
    )
    m2_hash = repo.current_baseline_hash("M02_or")
    with pytest.raises(StoreError) as caught:
        repo.propose_revert(
            review_id="RV-X", model_id="M03_repeated_event",
            target_baseline_hash=m2_hash, expected_baseline_hash=None, proposed_by="agent",
        )
    assert caught.value.code == store_err.REVERT_TARGET


def test_a_revert_rejects_a_stale_expected_baseline(three_baselines):
    repo, model, variant, old, current = three_baselines
    with pytest.raises(StoreError) as caught:
        repo.propose_revert(
            review_id="RV-X", model_id="M03_repeated_event",
            target_baseline_hash=old, expected_baseline_hash="0" * 64, proposed_by="agent",
        )
    assert caught.value.code == store_err.BASELINE_CONFLICT


def test_a_reverting_twice_needs_a_fresh_proposal_each_time(three_baselines):
    repo, model, variant, old, current = three_baselines
    repo.propose_revert(
        review_id="RV-1", model_id="M03_repeated_event",
        target_baseline_hash=old, expected_baseline_hash=current, proposed_by="agent",
    )
    repo.decide_review("RV-1", approve=True, reviewer=HUMAN)
    repo.apply_review("RV-1", reviewer=HUMAN)
    # the approval is spent: applying it again is a state error
    with pytest.raises(StoreError) as caught:
        repo.apply_review("RV-1", reviewer=HUMAN)
    assert caught.value.code == store_err.REVIEW_STATE
    # going forward again is a NEW revert proposal with fresh expectations
    forward = repo.propose_revert(
        review_id="RV-2", model_id="M03_repeated_event",
        target_baseline_hash=current, expected_baseline_hash=old, proposed_by="agent",
    )
    assert forward["state"] == "proposed"
    repo.decide_review("RV-2", approve=True, reviewer=HUMAN)
    repo.apply_review("RV-2", reviewer=HUMAN)
    assert repo.current_baseline_hash("M03_repeated_event") == current
    assert repo.get_run("r2")["stale"] == 0
    assert repo.verify() == []


def test_a_rejected_revert_leaves_everything_untouched(three_baselines):
    repo, model, variant, old, current = three_baselines
    repo.propose_revert(
        review_id="RV-1", model_id="M03_repeated_event",
        target_baseline_hash=old, expected_baseline_hash=current, proposed_by="agent",
    )
    repo.decide_review("RV-1", approve=False, reviewer=HUMAN)
    with pytest.raises(StoreError) as caught:
        repo.apply_review("RV-1", reviewer=HUMAN)
    assert caught.value.code == store_err.REVIEW_STATE
    assert repo.current_baseline_hash("M03_repeated_event") == current
    assert repo.verify() == []


def test_baseline_rows_are_never_rewritten_by_a_revert(three_baselines, tmp_path):
    repo, model, variant, old, current = three_baselines
    import sqlite3

    def baseline_row(baseline_hash):
        conn = sqlite3.connect(repo.path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM baselines WHERE baseline_hash=?", (baseline_hash,)
            ).fetchone()
            return tuple(row)  # positionally comparable, no dict() ambiguity
        finally:
            conn.close()

    before = baseline_row(old)
    before_current = baseline_row(current)
    repo.propose_revert(
        review_id="RV-1", model_id="M03_repeated_event",
        target_baseline_hash=old, expected_baseline_hash=current, proposed_by="agent",
    )
    repo.decide_review("RV-1", approve=True, reviewer=HUMAN)
    repo.apply_review("RV-1", reviewer=HUMAN)
    assert baseline_row(old) == before  # a revert re-POINTS at a row; it never edits one
    assert baseline_row(current) == before_current


def test_verify_catches_a_tampered_revert_target(three_baselines):
    # not via the public API: corrupt the stored canonical form of the target
    import sqlite3

    repo, model, variant, old, current = three_baselines
    conn = sqlite3.connect(repo.path)
    try:
        conn.execute(
            "UPDATE baselines SET canonical_json='{\"tampered\": true}' WHERE baseline_hash=?",
            (old,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(StoreError) as caught:
        repo.propose_revert(
            review_id="RV-X", model_id="M03_repeated_event",
            target_baseline_hash=old, expected_baseline_hash=current, proposed_by="agent",
        )
    assert caught.value.code == store_err.INTEGRITY


def test_dump_restore_round_trips_reverts(three_baselines, tmp_path):
    repo, model, variant, old, current = three_baselines
    repo.propose_revert(
        review_id="RV-1", model_id="M03_repeated_event",
        target_baseline_hash=old, expected_baseline_hash=current, proposed_by="agent",
    )
    dump = repo.dump()
    restored_path = tmp_path / "restored.sqlite"
    restored = SqliteRepository.restore(str(restored_path), dump)
    try:
        assert restored.dump() == dump
        # the pending revert survives the round trip and is still decidable
        record = restored.get_review("RV-1")
        assert record["state"] == "proposed"
        restored.decide_review("RV-1", approve=True, reviewer=HUMAN)
        applied, _ = restored.apply_review("RV-1", reviewer=HUMAN)
        assert applied["applied_baseline_hash"] == old
        assert restored.verify() == []
    finally:
        restored.close()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def cli(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if captured.out.strip() else None


def test_cli_revert_flow_and_refusals(three_baselines, capsys):
    repo, model, variant, old, current = three_baselines
    db = repo.path

    code, payload = cli(
        capsys, "review", "impact", db, "NOPE",
    )
    assert code == 2 and payload["error"]["code"] == store_err.REVIEW_NOT_FOUND

    code, payload = cli(
        capsys, "review", "revert", db,
        "--model-id", "M03_repeated_event", "--target-baseline-hash", old,
        "--review-id", "RV-1",
    )
    assert code == 0 and payload["review"]["state"] == "proposed"
    assert payload["impact"]["events_changed"] == 1

    code, payload = cli(capsys, "review", "decide", db, "RV-1", "--approve", "--reviewer", "agent")
    assert code == 2 and payload["error"]["code"] == store_err.APPROVAL_AUTHORITY

    cli(capsys, "review", "decide", db, "RV-1", "--approve", "--reviewer", HUMAN)
    code, payload = cli(capsys, "review", "apply", db, "RV-1", "--reviewer", HUMAN)
    assert code == 0 and payload["review"]["applied_baseline_hash"] == old

    code, payload = cli(
        capsys, "review", "revert", db,
        "--model-id", "M03_repeated_event", "--target-baseline-hash", old,
        "--review-id", "RV-2",
    )
    assert code == 2 and payload["error"]["code"] == store_err.REVERT_TARGET

    code, payload = cli(
        capsys, "review", "revert", db,
        "--model-id", "M03_repeated_event", "--target-baseline-hash", "a" * 64,
        "--review-id", "RV-3",
    )
    assert code == 2 and payload["error"]["code"] == store_err.REVERT_TARGET

    code, payload = cli(capsys, "store", "verify", db)
    assert code == 0 and payload["problems"] == []


def test_cli_review_impact_on_a_live_proposal(three_baselines, capsys):
    repo, model, variant, old, current = three_baselines
    repo.propose_review(
        review_id="FWD", model_id="M03_repeated_event",
        expected_baseline_hash=current, proposed_model=model, proposed_by="agent",
    )
    code, payload = cli(capsys, "review", "impact", repo.path, "FWD")
    assert code == 0
    assert payload["summary"]["events_changed"] == 1
    assert payload["baseline_still_current"] is True
