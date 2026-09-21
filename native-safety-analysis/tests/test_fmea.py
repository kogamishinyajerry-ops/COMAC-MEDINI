"""FMEA base table: validation, many-to-many traceability, the candidate
confirmation loop, revision immutability, dangling links, integrity, CLI.

Two contracts are under test here that the FTA side does not have:
  * provenance is never laundered — a row proposed by a machine reaches the
    official table only through a named human, and its original `source`
    survives promotion;
  * an approved fact is never overwritten in place — a revision is a new
    version and the previous one stays readable, exactly like a baseline.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from native_safety.adapters.json_io import load_model  # noqa: E402
from native_safety.cli.main import main  # noqa: E402
from native_safety.domain import errors as err  # noqa: E402
from native_safety.domain.errors import ModelError  # noqa: E402
from native_safety.domain.fmea import (  # noqa: E402
    canonical_fmea_form,
    fmea_content_hash,
    fmea_provenance,
    validate_fmea_row,
)
from native_safety.domain.semantic_hash import semantic_model_hash  # noqa: E402
from native_safety.kernel.solve import solve_model  # noqa: E402
from native_safety.store import SqliteRepository, StoreError  # noqa: E402
from native_safety.store import errors as store_err  # noqa: E402

EXAMPLES = ROOT / "reference" / "03_contracts" / "examples"
HUMAN = "J. Engineer"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def model_payload(events, gates, top="TOP", model_id="M03_repeated_event"):
    return {
        "schema_version": "0.1.0",
        "model_id": model_id,
        "baseline_id": "SYNTHETIC_01",
        "assumptions": {
            "basic_events_independent": True,
            "probability_semantics": "fixed_conditioned_probability",
            "condition": "Synthetic test model; not an aircraft system assessment.",
        },
        "basic_events": [
            {"id": eid, "label": eid, "probability": p, "source": "synthetic"} for eid, p in events
        ],
        "gates": gates,
        "top_event": top,
    }


def write_model(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def row_payload(fmea_id="FMEA-001", links=("A", "B"), source="human", note=None, **overrides):
    payload = {
        "fmea_id": fmea_id,
        "model_id": "M03_repeated_event",
        "component_id": "COMP-PUMP",
        "function_id": "FUNC-SUPPLY",
        "failure_mode": "seal leak",
        "cause": "worn O-ring",
        "local_effect": "fluid loss",
        "system_effect": "loss of supply pressure",
        "controls": ["leak test at assembly"],
        "evidence": [{"evidence_id": "EV-1", "kind": "test", "locator": "TR-114 p.22"}],
        "requirement_ids": ["REQ-FUEL-9"],
        "linked_event_ids": list(links),
        "source": source,
    }
    if note is not None:
        payload["inference_note"] = note
    payload.update(overrides)
    return payload


def run_payload(model, solution, run_id):
    return {
        "schema_version": "0.1.0",
        "model_schema_version": model.schema_version,
        "run_id": run_id,
        "model_id": model.model_id,
        "engine_version": "0.1.0",
        "semantic_model_hash": semantic_model_hash(model),
        "status": "succeeded",
        "top_probability": str(solution.top_probability),
        "minimal_cut_sets": solution.minimal_cut_sets,
        "cut_sets_complete": True,
        "importance_status": "not_requested",
        "probability_interpretation": "fixed_conditioned_probability",
        "engine_stats": {"top_probability_exact": str(solution.top_probability)},
    }


def raw(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_one(db_path, sql: str) -> dict:
    """Read one row over a throwaway connection, so the store's own handle is
    never used to observe what was actually written."""
    conn = raw(db_path)
    try:
        row = conn.execute(sql).fetchone()
        return dict(row) if row is not None else {}
    finally:
        conn.close()


def corrupt(db_path, sql: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


def promote(repo, candidate_id, payload, baseline_hash, reviewer=HUMAN, **kwargs):
    repo.propose_fmea_candidate(
        candidate_id=candidate_id, raw_row=payload, expected_baseline_hash=baseline_hash,
        proposed_by="agent", **kwargs,
    )
    repo.decide_fmea_candidate(candidate_id, approve=True, reviewer=reviewer)
    return repo.apply_fmea_candidate(candidate_id, reviewer=reviewer)


@pytest.fixture()
def seeded(tmp_path):
    """A store holding M03_repeated_event (A∧B ∨ A∧C) with one recorded run."""
    model = load_model(EXAMPLES / "M03_repeated_event.json")
    solution = solve_model(model)
    with SqliteRepository(str(tmp_path / "store.sqlite")) as repo:
        repo.record_run(
            run_id="r1", model=model, solution=solution,
            payload=run_payload(model, solution, "r1"), actor="local-cli",
        )
        yield repo, model, tmp_path


# --------------------------------------------------------------------------
# domain validation
# --------------------------------------------------------------------------


def test_valid_row_parses_and_hashes_stably():
    row = validate_fmea_row(row_payload())
    assert row.fmea_id == "FMEA-001"
    assert row.linked_event_ids == ("A", "B")
    assert row.is_inference is False
    assert fmea_content_hash(row) == fmea_content_hash(validate_fmea_row(row_payload()))
    assert canonical_fmea_form(row)["canonicalization"] == "fmea-canonical-v1"


def test_collections_are_order_insensitive_in_the_hash():
    a = validate_fmea_row(row_payload(links=("A", "B"), controls=["x", "y"]))
    b = validate_fmea_row(row_payload(links=("B", "A"), controls=["y", "x"]))
    assert fmea_content_hash(a) == fmea_content_hash(b)


def test_certainty_and_note_are_outside_the_hash():
    """A confidence statement is descriptive, never an acceptance criterion."""
    base = validate_fmea_row(row_payload(source="inference", note="needs a bench test"))
    other = dataclasses.replace(
        base, certainty="medium", inference_note="still needs evidence"
    )
    assert fmea_content_hash(base) == fmea_content_hash(other)
    assert fmea_provenance(other)["certainty"] == "medium"
    assert fmea_provenance(other)["is_machine_inferred"] is True


def test_row_with_no_links_is_allowed():
    row = validate_fmea_row(row_payload(links=()))
    assert row.linked_event_ids == ()


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"source": "guess"}, err.FMEA_SOURCE),
        ({"source": ""}, err.FMEA_SOURCE),
        ({"source": "inference"}, err.FMEA_SOURCE),          # inference without its note
        ({"failure_mode": ""}, err.FMEA_INPUTS),
        ({"cause": "   "}, err.FMEA_INPUTS),
        ({"local_effect": None}, err.FMEA_INPUTS),
        ({"controls": ["dup", "dup"]}, err.FMEA_INPUTS),
        ({"linked_event_ids": ["A", "A"]}, err.FMEA_INPUTS),
        ({"requirement_ids": ["REQ-1", "REQ-1"]}, err.FMEA_INPUTS),
        ({"controls": [1]}, err.FMEA_INPUTS),
        ({"evidence": [{"evidence_id": "EV-1", "kind": "vibes", "locator": "x"}]}, err.FMEA_INPUTS),
        ({"evidence": [{"evidence_id": "EV-1", "kind": "test"}]}, err.FMEA_INPUTS),
        ({"evidence": [{"evidence_id": "1bad", "kind": "test", "locator": "x"}]}, err.FMEA_ID),
        ({"fmea_id": "9bad"}, err.FMEA_ID),
        ({"component_id": ""}, err.FMEA_ID),
        ({"function_id": "has space"}, err.FMEA_ID),
        ({"model_id": ""}, err.FMEA_ID),
        ({"linked_event_ids": ["bad id"]}, err.FMEA_ID),
        ({"requirement_ids": ["has space"]}, err.FMEA_ID),
    ],
)
def test_rejects_malformed_rows(overrides, code):
    with pytest.raises(ModelError) as excinfo:
        validate_fmea_row(row_payload(**overrides))
    assert excinfo.value.code == code


def test_rejects_non_object_row():
    with pytest.raises(ModelError) as excinfo:
        validate_fmea_row(["not", "a", "row"])
    assert excinfo.value.code == err.FMEA_INPUTS


def test_rejects_link_to_an_event_outside_the_baseline():
    with pytest.raises(ModelError) as excinfo:
        validate_fmea_row(row_payload(links=("A", "NOPE")), known_event_ids={"A", "B", "C"})
    assert excinfo.value.code == err.FMEA_LINK
    assert "NOPE" in excinfo.value.message


# --------------------------------------------------------------------------
# candidate loop: a machine proposal never becomes an official fact on its own
# --------------------------------------------------------------------------


def test_a_draft_alone_does_not_touch_the_official_table(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    record = repo.propose_fmea_candidate(
        candidate_id="C1", raw_row=row_payload(), expected_baseline_hash=baseline,
        proposed_by="agent",
    )
    assert record["state"] == "proposed"
    assert repo.list_fmea_rows() == []
    assert repo.get_fmea_row("M03_repeated_event", "FMEA-001") is None
    assert repo.status()["pending_fmea_candidates"] == 1


def test_agent_may_propose_but_never_decide_or_apply(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    repo.propose_fmea_candidate(
        candidate_id="C1", raw_row=row_payload(), expected_baseline_hash=baseline,
        proposed_by="agent",
    )
    for identity in ("agent", "claude", "local-cli", "", "unknown"):
        with pytest.raises(StoreError) as excinfo:
            repo.decide_fmea_candidate("C1", approve=True, reviewer=identity)
        assert excinfo.value.code == store_err.APPROVAL_AUTHORITY
    with pytest.raises(StoreError) as excinfo:
        repo.apply_fmea_candidate("C1", reviewer="agent")
    assert excinfo.value.code == store_err.APPROVAL_AUTHORITY
    # the draft is untouched by every refused attempt
    assert repo.get_fmea_candidate("C1")["state"] == "proposed"


def test_a_machine_proposed_row_must_still_go_through_a_human(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    repo.propose_fmea_candidate(
        candidate_id="C1",
        raw_row=row_payload(source="inference", note="candidate mode from a text extraction"),
        expected_baseline_hash=baseline, proposed_by="agent",
    )
    repo.decide_fmea_candidate("C1", approve=True, reviewer=HUMAN)
    _, promoted = repo.apply_fmea_candidate("C1", reviewer=HUMAN)
    # provenance survives: the fact that a machine proposed it is not erased
    assert promoted["source"] == "inference"
    assert promoted["inference_note"] == "candidate mode from a text extraction"
    assert promoted["approved_by"] == HUMAN
    assert promoted["created_by"] == "agent"


def test_an_invalid_draft_is_recorded_not_dropped(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    record = repo.propose_fmea_candidate(
        candidate_id="C1", raw_row=row_payload(source="inference"),
        expected_baseline_hash=baseline, proposed_by="agent",
    )
    assert record["state"] == "invalid"
    assert record["validation_code"] == err.FMEA_SOURCE
    assert record["proposed_content_hash"] is None
    # ... and it can never be approved afterwards
    with pytest.raises(StoreError) as excinfo:
        repo.decide_fmea_candidate("C1", approve=True, reviewer=HUMAN)
    assert excinfo.value.code == store_err.FMEA_STATE


def test_a_draft_linking_an_unknown_event_is_recorded_invalid(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    record = repo.propose_fmea_candidate(
        candidate_id="C1", raw_row=row_payload(links=("A", "GHOST")),
        expected_baseline_hash=baseline, proposed_by="agent",
    )
    assert record["state"] == "invalid"
    assert record["validation_code"] == err.FMEA_LINK


def test_decide_and_apply_are_single_step(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    repo.propose_fmea_candidate(
        candidate_id="C1", raw_row=row_payload(), expected_baseline_hash=baseline,
        proposed_by="agent",
    )
    with pytest.raises(StoreError) as excinfo:
        repo.apply_fmea_candidate("C1", reviewer=HUMAN)   # before approval
    assert excinfo.value.code == store_err.FMEA_STATE
    repo.decide_fmea_candidate("C1", approve=True, reviewer=HUMAN)
    with pytest.raises(StoreError) as excinfo:
        repo.decide_fmea_candidate("C1", approve=True, reviewer=HUMAN)  # twice
    assert excinfo.value.code == store_err.FMEA_STATE
    repo.apply_fmea_candidate("C1", reviewer=HUMAN)
    with pytest.raises(StoreError) as excinfo:
        repo.apply_fmea_candidate("C1", reviewer=HUMAN)   # twice
    assert excinfo.value.code == store_err.FMEA_STATE


def test_reject_keeps_the_draft_out_of_the_official_table(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    repo.propose_fmea_candidate(
        candidate_id="C1", raw_row=row_payload(), expected_baseline_hash=baseline,
        proposed_by="agent",
    )
    repo.decide_fmea_candidate("C1", approve=False, reviewer=HUMAN, note="not a credible mode")
    assert repo.get_fmea_candidate("C1")["state"] == "rejected"
    assert repo.list_fmea_rows() == []


def test_unknown_candidate_raises(seeded):
    repo, _, _ = seeded
    with pytest.raises(StoreError) as excinfo:
        repo.decide_fmea_candidate("nope", approve=True, reviewer=HUMAN)
    assert excinfo.value.code == store_err.FMEA_NOT_FOUND
    with pytest.raises(StoreError) as excinfo:
        repo.apply_fmea_candidate("nope", reviewer=HUMAN)
    assert excinfo.value.code == store_err.FMEA_NOT_FOUND


# --------------------------------------------------------------------------
# optimistic concurrency: the FMEA row is bound to a fault-tree baseline
# --------------------------------------------------------------------------


def test_a_draft_against_a_model_without_a_baseline_is_refused(seeded):
    repo, _, _ = seeded
    with pytest.raises(StoreError) as excinfo:
        repo.propose_fmea_candidate(
            candidate_id="C1", raw_row=row_payload(model_id="NOT_IN_STORE"),
            expected_baseline_hash="0" * 64, proposed_by="agent",
        )
    assert excinfo.value.code == store_err.MODEL_NOT_FOUND


def test_a_draft_with_a_stale_expected_baseline_is_refused(seeded):
    repo, _, _ = seeded
    with pytest.raises(StoreError) as excinfo:
        repo.propose_fmea_candidate(
            candidate_id="C1", raw_row=row_payload(),
            expected_baseline_hash="f" * 64, proposed_by="agent",
        )
    assert excinfo.value.code == store_err.BASELINE_CONFLICT


def test_an_approval_is_bound_to_the_baseline_it_was_proposed_against(seeded):
    repo, _, tmp_path = seeded
    old = repo.current_baseline_hash("M03_repeated_event")
    repo.propose_fmea_candidate(
        candidate_id="C1", raw_row=row_payload(), expected_baseline_hash=old, proposed_by="agent",
    )
    repo.decide_fmea_candidate("C1", approve=True, reviewer=HUMAN)

    new_model = load_model(
        write_model(
            tmp_path, "moved.json",
            model_payload([("A", "0.1"), ("B", "0.2"), ("C", "0.3")],
                          [{"id": "TOP", "kind": "OR", "inputs": ["A", "B", "C"]}]),
        )
    )
    repo.register_model_baseline(
        new_model, actor=HUMAN, expected_baseline_hash=old
    )
    with pytest.raises(StoreError) as excinfo:
        repo.apply_fmea_candidate("C1", reviewer=HUMAN)
    assert excinfo.value.code == store_err.BASELINE_CONFLICT
    assert repo.get_fmea_candidate("C1")["state"] == "approved"  # still awaiting a re-decision


# --------------------------------------------------------------------------
# many-to-many traceability
# --------------------------------------------------------------------------


def test_one_row_may_cite_several_events_and_one_event_several_rows(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    promote(repo, "C1", row_payload("FMEA-001", links=("A", "B")), baseline)
    promote(repo, "C2", row_payload("FMEA-002", links=("A",)), baseline)
    promote(repo, "C3", row_payload("FMEA-003", links=("C",)), baseline)

    assert [r["fmea_id"] for r in repo.fmea_rows_for_event("M03_repeated_event", "A")] == [
        "FMEA-001", "FMEA-002",
    ]
    assert [r["fmea_id"] for r in repo.fmea_rows_for_event("M03_repeated_event", "B")] == ["FMEA-001"]
    assert repo.fmea_links_of_row("M03_repeated_event", "FMEA-001", 1) == ["A", "B"]
    assert repo.fmea_links_of_row("M03_repeated_event", "FMEA-002", 1) == ["A"]
    assert {r["fmea_id"] for r in repo.fmea_rows_by_component("M03_repeated_event", "COMP-PUMP")} == {
        "FMEA-001", "FMEA-002", "FMEA-003",
    }


def test_a_row_linking_no_event_is_allowed_and_traces_to_nothing(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    promote(repo, "C1", row_payload("FMEA-001", links=()), baseline)
    assert repo.fmea_links_of_row("M03_repeated_event", "FMEA-001", 1) == []
    assert repo.fmea_rows_for_event("M03_repeated_event", "A") == []
    assert repo.verify() == []


# --------------------------------------------------------------------------
# revision: an approved fact is never overwritten in place
# --------------------------------------------------------------------------


def test_a_revision_creates_a_new_version_and_only_supersedes_the_old_one(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    _, v1 = promote(repo, "C1", row_payload("FMEA-001", links=("A", "B")), baseline)
    assert v1["version"] == 1 and v1["superseded"] == 0
    frozen_v1 = dict(v1)

    _, v2 = promote(
        repo, "C2", row_payload("FMEA-001", links=("A", "C"), failure_mode="seal rupture"),
        baseline, expected_version=1,
    )
    assert v2["version"] == 2 and v2["superseded"] == 0
    assert v2["failure_mode"] == "seal rupture"

    stored_v1 = repo.get_fmea_row("M03_repeated_event", "FMEA-001", 1)
    assert stored_v1["superseded"] == 1
    assert stored_v1["superseded_by_version"] == 2
    # the superseded row's CONTENT is untouched — only the flag moved
    for field in ("failure_mode", "cause", "content_hash", "canonical_json", "approved_by"):
        assert stored_v1[field] == frozen_v1[field]
    assert repo.fmea_links_of_row("M03_repeated_event", "FMEA-001", 1) == ["A", "B"]
    assert repo.fmea_links_of_row("M03_repeated_event", "FMEA-001", 2) == ["A", "C"]
    assert [r["version"] for r in repo.list_fmea_rows("M03_repeated_event")] == [2]
    assert [r["version"] for r in repo.list_fmea_rows("M03_repeated_event", include_superseded=True)] == [1, 2]
    assert repo.verify() == []


def test_a_revision_must_name_the_version_it_revises(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    promote(repo, "C1", row_payload("FMEA-001"), baseline)
    # silent revision of an existing row is refused
    with pytest.raises(StoreError) as excinfo:
        repo.propose_fmea_candidate(
            candidate_id="C2", raw_row=row_payload("FMEA-001"), expected_baseline_hash=baseline,
            proposed_by="agent",
        )
    assert excinfo.value.code == store_err.FMEA_REVISION_CONFLICT
    with pytest.raises(StoreError) as excinfo:
        repo.propose_fmea_candidate(
            candidate_id="C2", raw_row=row_payload("FMEA-001"), expected_baseline_hash=baseline,
            proposed_by="agent", expected_version=7,
        )
    assert excinfo.value.code == store_err.FMEA_REVISION_CONFLICT
    # and a first row must not pretend to revise something
    with pytest.raises(StoreError) as excinfo:
        repo.propose_fmea_candidate(
            candidate_id="C3", raw_row=row_payload("FMEA-NEW"), expected_baseline_hash=baseline,
            proposed_by="agent", expected_version=1,
        )
    assert excinfo.value.code == store_err.FMEA_REVISION_CONFLICT


def test_a_superseded_approval_cannot_be_applied_after_a_newer_revision_lands(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    promote(repo, "C1", row_payload("FMEA-001"), baseline)          # v1
    for candidate_id in ("C2", "C3"):
        repo.propose_fmea_candidate(
            candidate_id=candidate_id, raw_row=row_payload("FMEA-001", cause=f"cause {candidate_id}"),
            expected_baseline_hash=baseline, proposed_by="agent", expected_version=1,
        )
        repo.decide_fmea_candidate(candidate_id, approve=True, reviewer=HUMAN)
    repo.apply_fmea_candidate("C3", reviewer=HUMAN)                  # v2 -> current is now 2
    with pytest.raises(StoreError) as excinfo:
        repo.apply_fmea_candidate("C2", reviewer=HUMAN)              # still targets v1
    assert excinfo.value.code == store_err.FMEA_REVISION_CONFLICT
    assert repo.get_fmea_row("M03_repeated_event", "FMEA-001")["version"] == 2


def test_the_official_row_is_written_once(seeded):
    """No code path may UPDATE the content of an official row."""
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    _, v1 = promote(repo, "C1", row_payload("FMEA-001"), baseline)
    before = fetch_one(repo.path, "SELECT * FROM fmea_rows WHERE version=1")
    promote(repo, "C2", row_payload("FMEA-001", cause="other cause"), baseline, expected_version=1)
    after = fetch_one(repo.path, "SELECT * FROM fmea_rows WHERE version=1")

    changed = {column for column in before if before[column] != after[column]}
    assert changed <= {"superseded", "superseded_by_version"}, changed
    assert before["content_hash"] == v1["content_hash"]
    assert after["cause"] == v1["cause"]


# --------------------------------------------------------------------------
# dangling links: a traceability gap, reported and never auto-repaired
# --------------------------------------------------------------------------


def test_a_baseline_move_that_drops_an_event_leaves_a_dangling_link(seeded):
    repo, _, tmp_path = seeded
    old = repo.current_baseline_hash("M03_repeated_event")
    promote(repo, "C1", row_payload("FMEA-001", links=("A", "C")), old)
    assert repo.dangling_fmea_links() == []

    moved = load_model(
        write_model(
            tmp_path, "dropped.json",
            model_payload([("A", "0.1"), ("B", "0.2")],
                          [{"id": "TOP", "kind": "AND", "inputs": ["A", "B"]}]),
        )
    )
    repo.register_model_baseline(moved, actor=HUMAN, expected_baseline_hash=old)

    dangling = repo.dangling_fmea_links()
    assert [(d["fmea_id"], d["event_id"]) for d in dangling] == [("FMEA-001", "C")]
    assert repo.status()["dangling_fmea_links"] == 1
    # the row itself is untouched and the store is still self-consistent:
    # a traceability gap must not read as corruption
    assert repo.get_fmea_row("M03_repeated_event", "FMEA-001") is not None
    assert repo.fmea_links_of_row("M03_repeated_event", "FMEA-001", 1) == ["A", "C"]
    assert repo.verify() == []


def test_a_new_draft_cannot_cite_an_event_the_current_baseline_lacks(seeded):
    repo, _, tmp_path = seeded
    old = repo.current_baseline_hash("M03_repeated_event")
    moved = load_model(
        write_model(
            tmp_path, "dropped.json",
            model_payload([("A", "0.1"), ("B", "0.2")],
                          [{"id": "TOP", "kind": "AND", "inputs": ["A", "B"]}]),
        )
    )
    repo.register_model_baseline(moved, actor=HUMAN, expected_baseline_hash=old)
    record = repo.propose_fmea_candidate(
        candidate_id="C1", raw_row=row_payload(links=("A", "C")),
        expected_baseline_hash=repo.current_baseline_hash("M03_repeated_event"),
        proposed_by="agent",
    )
    assert record["state"] == "invalid"
    assert record["validation_code"] == err.FMEA_LINK


# --------------------------------------------------------------------------
# integrity
# --------------------------------------------------------------------------


def test_verify_is_clean_over_a_full_lifecycle(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    promote(repo, "C1", row_payload("FMEA-001", links=("A", "B")), baseline)
    promote(repo, "C2", row_payload("FMEA-002", links=("A",), source="inference", note="text mining"),
            baseline)
    promote(repo, "C3", row_payload("FMEA-001", links=("A", "C")), baseline, expected_version=1)
    repo.propose_fmea_candidate(
        candidate_id="C4", raw_row=row_payload("FMEA-009", source="inference"),
        expected_baseline_hash=baseline, proposed_by="agent",
    )
    assert repo.verify() == []


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE fmea_rows SET content_hash='0000000000000000000000000000000000000000000000000000000000000000'",
        "UPDATE fmea_rows SET canonical_json='{}'",
        "UPDATE fmea_rows SET failure_mode='tampered'",
        "UPDATE fmea_rows SET source='guess'",
        "UPDATE fmea_rows SET approved_by='claude'",
        "UPDATE fmea_rows SET approved_by=''",
        "UPDATE fmea_rows SET superseded=1, superseded_by_version=99",
        "UPDATE fmea_rows SET superseded=1 WHERE superseded=0",
        "UPDATE fmea_rows SET validated_baseline_hash=''",
        "UPDATE fmea_rows SET controls_json='[\"tampered\"]'",
        "DELETE FROM fmea_links",
        "INSERT INTO fmea_links(model_id, fmea_id, version, event_id) VALUES('M03_repeated_event','FMEA-001',1,'GHOST')",
        "UPDATE fmea_candidates SET state='bogus'",
        "UPDATE fmea_candidates SET decided_by='claude'",
        "UPDATE fmea_candidates SET state='approved', decided_by='claude'",
        "UPDATE fmea_candidates SET state='applied', applied_version=99",
        "UPDATE fmea_candidates SET proposed_canonical_json='{}'",
    ],
)
def test_verify_detects_corruption(seeded, sql):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    promote(repo, "C1", row_payload("FMEA-001", links=("A", "B")), baseline)
    repo.propose_fmea_candidate(
        candidate_id="C2", raw_row=row_payload("FMEA-002", links=("C",)),
        expected_baseline_hash=baseline, proposed_by="agent",
    )
    assert repo.verify() == []
    corrupt(repo.path, sql)
    assert repo.verify() != []


def test_dump_and_restore_round_trip(seeded, tmp_path):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    promote(repo, "C1", row_payload("FMEA-001", links=("A", "B")), baseline)
    promote(repo, "C2", row_payload("FMEA-001", links=("A", "C")), baseline, expected_version=1)
    repo.propose_fmea_candidate(
        candidate_id="C3", raw_row=row_payload("FMEA-003", source="inference"),
        expected_baseline_hash=baseline, proposed_by="agent",
    )
    dump = repo.dump()

    with SqliteRepository.restore(str(tmp_path / "restored.sqlite"), dump) as other:
        assert other.verify() == []
        assert other.dump()["tables"] == dump["tables"]
        assert other.fmea_links_of_row("M03_repeated_event", "FMEA-001", 2) == ["A", "C"]
        assert other.get_fmea_row("M03_repeated_event", "FMEA-001")["version"] == 2


def test_status_counts_the_fmea_tables(seeded):
    repo, _, _ = seeded
    baseline = repo.current_baseline_hash("M03_repeated_event")
    promote(repo, "C1", row_payload("FMEA-001", links=("A", "B")), baseline)
    status = repo.status()
    assert status["counts"]["fmea_rows"] == 1
    assert status["counts"]["fmea_links"] == 2
    assert status["counts"]["fmea_candidates"] == 1
    assert status["current_fmea_rows"] == 1
    assert status["pending_fmea_candidates"] == 0
    assert status["dangling_fmea_links"] == 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def cli(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if captured.out.strip() else None


def test_cli_full_fmea_flow(seeded, capsys, tmp_path):
    repo, _, _ = seeded
    db = repo.path
    baseline = repo.current_baseline_hash("M03_repeated_event")
    row_path = write_model(tmp_path, "row.json", row_payload())

    code, payload = cli(
        capsys, "fmea", "propose", db, str(row_path),
        "--candidate-id", "C1", "--expected-baseline-hash", baseline,
    )
    assert code == 0 and payload["candidate"]["state"] == "proposed"

    # an agent has no approval authority → exit 2
    code, payload = cli(capsys, "fmea", "decide", db, "C1", "--approve", "--reviewer", "claude")
    assert code == 2 and payload["error"]["code"] == store_err.APPROVAL_AUTHORITY

    code, payload = cli(capsys, "fmea", "decide", db, "C1", "--approve", "--reviewer", HUMAN)
    assert code == 0 and payload["candidate"]["state"] == "approved"

    code, payload = cli(capsys, "fmea", "apply", db, "C1", "--reviewer", HUMAN)
    assert code == 0
    assert payload["row"]["linked_event_ids"] == ["A", "B"]
    assert payload["row"]["approved_by"] == HUMAN

    code, payload = cli(capsys, "fmea", "rows", db)
    assert code == 0 and payload["count"] == 1

    code, payload = cli(capsys, "fmea", "trace", db, "--model", "M03_repeated_event", "--event", "A")
    assert code == 0 and [r["fmea_id"] for r in payload["rows"]] == ["FMEA-001"]

    code, payload = cli(
        capsys, "fmea", "trace", db, "--model", "M03_repeated_event", "--dangling"
    )
    assert code == 0 and payload["count"] == 0

    code, payload = cli(capsys, "fmea", "candidates", db, "--state", "applied")
    assert code == 0 and payload["count"] == 1

    code, payload = cli(capsys, "store", "verify", db)
    assert code == 0 and payload["problems"] == []


def test_cli_reports_a_missing_draft(seeded, capsys):
    repo, _, _ = seeded
    code, payload = cli(capsys, "fmea", "show", repo.path, "nope")
    assert code == 2 and payload["error"]["code"] == store_err.FMEA_NOT_FOUND


def test_capabilities_declare_fmea_verified(capsys):
    code, payload = cli(capsys, "capabilities")
    assert code == 0
    entry = next(c for c in payload["capabilities"] if c["capability_id"] == "fmea_requirements_traceability")
    assert entry["status"] == "verified"
    assert entry["evidence_ids"] == [
        "tests/test_fmea.py",
        "verification/run_fmea_verification.py",
    ]
