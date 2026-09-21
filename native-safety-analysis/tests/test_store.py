"""Store: schema, exactness, idempotency, concurrency, staleness, review loop, CLI.

The store is the first component whose contract is about TIME rather than
arithmetic — a baseline moves, a run goes stale, an approval is required — so
these tests are as much state-machine tests as data tests.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from native_safety.adapters.json_io import load_model  # noqa: E402
from native_safety.cli.main import main  # noqa: E402
from native_safety.domain.ratnum import exact_decimal_or_fraction  # noqa: E402
from native_safety.domain.semantic_hash import semantic_model_hash  # noqa: E402
from native_safety.domain.validation import validate_model  # noqa: E402
from native_safety.kernel.solve import solve_model  # noqa: E402
from native_safety.store import (  # noqa: E402
    STORE_SCHEMA_VERSION,
    SqliteRepository,
    StoreError,
)
from native_safety.store import errors as store_err  # noqa: E402
from native_safety.store.schema import audit_no_float_columns  # noqa: E402

EXAMPLES = ROOT / "reference" / "03_contracts" / "examples"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def payload_for(model, solution, run_id: str) -> dict:
    return {
        "schema_version": "0.1.0",
        "model_schema_version": model.schema_version,
        "run_id": run_id,
        "model_id": model.model_id,
        "engine_version": "0.1.0",
        "semantic_model_hash": semantic_model_hash(model),
        "status": "succeeded" if solution.cut_sets_complete else "resource_limited",
        "top_probability": str(solution.top_probability),
        "minimal_cut_sets": solution.minimal_cut_sets,
        "cut_sets_complete": solution.cut_sets_complete,
        "importance_status": solution.importance_status,
        "probability_interpretation": (
            "mission_failure_probability_from_constant_rate"
            if model.rates
            else "fixed_conditioned_probability"
        ),
        "engine_stats": {
            "bdd_node_count": solution.bdd_node_count,
            "variable_order": list(solution.variable_order),
        },
    }


def m03() -> tuple:
    model = load_model(EXAMPLES / "M03_repeated_event.json")
    return model, solve_model(model)


def record(repo, model, solution, run_id, **kwargs):
    return repo.record_run(
        run_id=run_id, model=model, solution=solution,
        payload=payload_for(model, solution, run_id), **kwargs,
    )


def mutated_copy(tmp_path: Path, name: str, index: int, probability: str) -> Path:
    data = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    data["basic_events"][index]["probability"] = probability
    path = tmp_path / f"{name}.mutated.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.fixture()
def repo(tmp_path):
    with SqliteRepository(str(tmp_path / "store.sqlite")) as handle:
        yield handle


def raw(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------


def test_schema_version_and_no_float_columns(repo):
    assert repo.status()["store_schema_version"] == STORE_SCHEMA_VERSION
    assert audit_no_float_columns(repo.conn) == []


def test_declared_schema_contains_no_float_type():
    from native_safety.store import schema

    ddl = " ".join(" ".join(statements) for statements in schema.MIGRATIONS.values()).upper()
    for token in ("REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL"):
        assert token not in ddl, f"schema declares a {token} type"


def test_storage_class_is_never_real(repo, tmp_path):
    """No value anywhere may be stored as REAL (B_核心规划 §231)."""
    model, solution = m03()
    record(repo, model, solution, "r1")
    conn = raw(repo.path)
    for (table,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        for column in (r[1] for r in conn.execute(f"PRAGMA table_info({table})")):
            classes = {r[0] for r in conn.execute(f'SELECT DISTINCT typeof("{column}") FROM "{table}"')}
            assert "real" not in classes, f"{table}.{column} holds REAL"
    conn.close()


def test_open_a_future_store_is_refused(repo, tmp_path):
    path = tmp_path / "future.sqlite"
    with SqliteRepository(str(path)):
        pass
    conn = raw(path)
    conn.execute("UPDATE schema_meta SET value='99' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(StoreError) as excinfo:
        SqliteRepository(str(path))
    assert excinfo.value.code == store_err.SCHEMA_MISMATCH


def test_migration_is_idempotent(repo):
    before = repo.dump()
    with SqliteRepository(str(repo.path)) as again:
        assert again.dump() == before
        assert again.status()["store_schema_version"] == STORE_SCHEMA_VERSION


# --------------------------------------------------------------------------
# exactness
# --------------------------------------------------------------------------


def test_stored_numbers_reproduce_their_exact_value(repo):
    model, solution = m03()
    record(repo, model, solution, "r1")
    run = repo.get_run("r1")
    assert Fraction(run["top_event_probability"]) == solution.top_probability
    assert run["top_event_probability_kind"] == "exact_decimal"

    # run_importance is ranked by Fussell-Vesely; solution.importance is in
    # variable order, so pair them by event id rather than by position.
    expected = {record_obj.event_id: record_obj for record_obj in solution.importance}
    for stored in repo.run_importance("r1"):
        for field, attribute in (
            ("event_probability", "probability"),
            ("top_given_failed", "top_given_failed"),
            ("top_given_working", "top_given_working"),
            ("birnbaum_importance", "birnbaum"),
            ("fussell_vesely", "fussell_vesely"),
            ("risk_achievement_worth", "risk_achievement_worth"),
            ("risk_reduction_worth", "risk_reduction_worth"),
        ):
            source = getattr(expected[stored["event_id"]], attribute)
            if source is None:
                assert stored[field] is None
            else:
                assert Fraction(stored[field]) == source


def test_non_terminating_importance_is_stored_as_an_exact_rational(repo):
    """FV/RAW/RRW are ratios of probabilities and are usually non-terminating."""
    model, solution = m03()
    record(repo, model, solution, "r1")
    rows = {r["event_id"]: r for r in repo.run_importance("r1")}
    assert rows["B"]["fussell_vesely"] == "7/22"
    assert Fraction(rows["B"]["fussell_vesely"]) == Fraction(7, 22)
    assert "/" in rows["C"]["risk_achievement_worth"]


def test_rate_derived_probability_is_stored_exactly(repo):
    model = load_model(ROOT / "examples" / "R01_rate_and.json")
    solution = solve_model(model)
    record(repo, model, solution, "rate")
    run = repo.get_run("rate")
    # the exact rational of the computed value, not the display rounding
    assert Fraction(run["top_event_probability"]) == solution.top_probability
    assert run["probability_interpretation"] == "mission_failure_probability_from_constant_rate"


def test_importance_is_sorted_by_exact_value_not_by_text(repo):
    """Text order and numeric order disagree here: '1' < '6/11' < '7/22'."""
    model, solution = m03()
    record(repo, model, solution, "r1")
    by_fv = [row["event_id"] for row in repo.run_importance("r1", by="fussell_vesely")]
    assert by_fv == ["A", "C", "B"]
    assert [Fraction(r["fussell_vesely"]) for r in repo.run_importance("r1")] == [
        Fraction(1), Fraction(6, 11), Fraction(7, 22)
    ]


def test_undefined_measures_survive_the_round_trip(repo):
    model, solution = m03()
    record(repo, model, solution, "r1")
    rows = {r["event_id"]: r for r in repo.run_importance("r1")}
    assert rows["A"]["risk_reduction_worth"] is None
    assert json.loads(rows["A"]["undefined_json"]) == [
        {"measure": "risk_reduction_worth", "reason": "risk_reduction_worth_unbounded"}
    ]


# --------------------------------------------------------------------------
# idempotency, conflicts, atomicity
# --------------------------------------------------------------------------


def test_identical_re_record_is_a_no_op(repo):
    model, solution = m03()
    first = record(repo, model, solution, "r1", actor="JerryKogami")
    before = repo.dump()
    again = record(repo, model, solution, "r1", actor="someone-else")
    assert first.created is True
    assert again.created is False
    assert again.fingerprint == first.fingerprint
    # a re-record must not rewrite the stored row (not even its author)
    assert repo.dump() == before


def test_reused_run_id_with_different_content_is_refused(repo):
    model_a, solution_a = m03()
    record(repo, model_a, solution_a, "shared")
    model_b = load_model(EXAMPLES / "M02_or.json")
    solution_b = solve_model(model_b)
    with pytest.raises(StoreError) as excinfo:
        record(repo, model_b, solution_b, "shared")
    assert excinfo.value.code == store_err.RUN_ID_CONFLICT
    assert repo.get_run("shared")["model_id"] == model_a.model_id


def test_a_refused_record_leaves_no_model_or_baseline(repo):
    """The first-sighting registration and the run insert are one transaction."""
    model_a, solution_a = m03()
    record(repo, model_a, solution_a, "shared")
    before = repo.status()["counts"]
    model_b = load_model(EXAMPLES / "M02_or.json")
    with pytest.raises(StoreError):
        record(repo, model_b, solve_model(model_b), "shared")
    after = repo.status()["counts"]
    assert after == before
    assert repo.model_row(model_b.model_id) is None


def test_changed_semantics_cannot_be_recorded_by_a_re_run(repo, tmp_path):
    model, solution = m03()
    record(repo, model, solution, "r1")
    changed = validate_model(json.loads(
        mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25").read_text(encoding="utf-8")
    ))
    with pytest.raises(StoreError) as excinfo:
        record(repo, changed, solve_model(changed), "r2")
    assert excinfo.value.code == store_err.BASELINE_NOT_CURRENT
    # the baseline did not move
    assert repo.current_baseline_hash(model.model_id) == semantic_model_hash(model)


# --------------------------------------------------------------------------
# staleness
# --------------------------------------------------------------------------


def test_new_baseline_marks_old_runs_stale_and_keeps_their_evidence(repo, tmp_path):
    model, solution = m03()
    first = record(repo, model, solution, "r1")
    changed = validate_model(json.loads(
        mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25").read_text(encoding="utf-8")
    ))
    anchored, stale_ids = repo.register_model_baseline(
        changed, actor="JerryKogami", expected_baseline_hash=first.baseline_hash
    )
    assert anchored != first.baseline_hash
    assert list(stale_ids) == ["r1"]

    old = repo.get_run("r1")
    assert old["stale"] == 1
    assert "superseded" in old["stale_reason"]
    # the superseded run's numbers are untouched and still readable
    assert Fraction(old["top_event_probability"]) == Fraction(11, 250)
    assert repo.current_baseline_hash(model.model_id) == anchored

    record(repo, changed, solve_model(changed), "r2")
    assert repo.get_run("r2")["stale"] == 0


def test_baseline_row_is_never_rewritten(repo, tmp_path):
    model, solution = m03()
    first = record(repo, model, solution, "r1")
    before = {b["baseline_hash"]: b["canonical_json"] for b in repo.list_baselines()}
    changed = validate_model(json.loads(
        mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25").read_text(encoding="utf-8")
    ))
    repo.register_model_baseline(changed, actor="JerryKogami",
                                 expected_baseline_hash=first.baseline_hash)
    after = {b["baseline_hash"]: b["canonical_json"] for b in repo.list_baselines()}
    for baseline_hash, canonical in before.items():
        assert after[baseline_hash] == canonical
    assert len(after) == len(before) + 1


def test_reanchor_requires_the_expected_hash(repo, tmp_path):
    model, solution = m03()
    first = record(repo, model, solution, "r1")
    changed = validate_model(json.loads(
        mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25").read_text(encoding="utf-8")
    ))
    with pytest.raises(StoreError) as excinfo:
        repo.register_model_baseline(changed, actor="JerryKogami")
    assert excinfo.value.code == store_err.BASELINE_CONFLICT
    with pytest.raises(StoreError) as excinfo:
        repo.register_model_baseline(changed, actor="JerryKogami",
                                     expected_baseline_hash="0" * 64)
    assert excinfo.value.code == store_err.BASELINE_CONFLICT
    assert repo.current_baseline_hash(model.model_id) == first.baseline_hash


def test_cannot_expect_a_baseline_for_a_first_sighting(repo):
    model = load_model(EXAMPLES / "M01_and.json")
    with pytest.raises(StoreError) as excinfo:
        repo.register_model_baseline(model, actor="JerryKogami",
                                     expected_baseline_hash="0" * 64)
    assert excinfo.value.code == store_err.BASELINE_CONFLICT


# --------------------------------------------------------------------------
# review loop
# --------------------------------------------------------------------------


def test_full_review_loop(repo, tmp_path):
    model, solution = m03()
    first = record(repo, model, solution, "r1")
    changed = validate_model(json.loads(
        mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25").read_text(encoding="utf-8")
    ))
    proposal = repo.propose_review(
        review_id="REV-1", model_id=model.model_id,
        expected_baseline_hash=first.baseline_hash, proposed_model=changed,
        proposed_by="agent", note="tune B",
    )
    assert proposal["state"] == "proposed"
    assert proposal["proposed_hash"] == semantic_model_hash(changed)
    # proposing changed nothing
    assert repo.current_baseline_hash(model.model_id) == first.baseline_hash

    with pytest.raises(StoreError) as excinfo:
        repo.decide_review("REV-1", approve=True, reviewer="agent")
    assert excinfo.value.code == store_err.APPROVAL_AUTHORITY
    with pytest.raises(StoreError) as excinfo:
        repo.apply_review("REV-1", reviewer="JerryKogami")
    assert excinfo.value.code == store_err.REVIEW_STATE

    decided = repo.decide_review("REV-1", approve=True, reviewer="JerryKogami", note="ok")
    assert decided["state"] == "approved"
    assert decided["decided_by"] == "JerryKogami"

    applied, stale_ids = repo.apply_review("REV-1", reviewer="JerryKogami")
    assert applied["state"] == "applied"
    assert applied["applied_baseline_hash"] == proposal["proposed_hash"]
    assert list(stale_ids) == ["r1"]
    assert repo.current_baseline_hash(model.model_id) == proposal["proposed_hash"]
    assert repo.get_run("r1")["stale"] == 1
    record(repo, changed, solve_model(changed), "r2")


@pytest.mark.parametrize(
    "actor", ["agent", "assistant", "ai", "bot", "local-cli", "unknown", "", None]
)
def test_nothing_except_a_named_human_can_approve(repo, tmp_path, actor):
    model, solution = m03()
    first = record(repo, model, solution, "r1")
    changed = validate_model(json.loads(
        mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25").read_text(encoding="utf-8")
    ))
    repo.propose_review(
        review_id="REV-1", model_id=model.model_id,
        expected_baseline_hash=first.baseline_hash, proposed_model=changed, proposed_by="agent",
    )
    with pytest.raises(StoreError) as excinfo:
        repo.decide_review("REV-1", approve=True, reviewer=actor)
    assert excinfo.value.code == store_err.APPROVAL_AUTHORITY
    with pytest.raises(StoreError) as excinfo:
        repo.apply_review("REV-1", reviewer=actor)
    assert excinfo.value.code == store_err.APPROVAL_AUTHORITY


def test_a_proposal_written_against_a_stale_baseline_is_refused(repo, tmp_path):
    model, solution = m03()
    record(repo, model, solution, "r1")
    changed = validate_model(json.loads(
        mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25").read_text(encoding="utf-8")
    ))
    with pytest.raises(StoreError) as excinfo:
        repo.propose_review(
            review_id="REV-1", model_id=model.model_id,
            expected_baseline_hash="0" * 64, proposed_model=changed, proposed_by="agent",
        )
    assert excinfo.value.code == store_err.BASELINE_CONFLICT


def test_an_approval_goes_stale_if_the_baseline_moves_under_it(repo, tmp_path):
    """Approval binds to a baseline; a later move invalidates it rather than applying blindly."""
    model, solution = m03()
    first = record(repo, model, solution, "r1")
    a = validate_model(json.loads(
        mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25").read_text(encoding="utf-8")
    ))
    b = validate_model(json.loads(
        mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.31").read_text(encoding="utf-8")
    ))
    repo.propose_review(review_id="REV-A", model_id=model.model_id,
                        expected_baseline_hash=first.baseline_hash, proposed_model=a,
                        proposed_by="agent")
    repo.decide_review("REV-A", approve=True, reviewer="JerryKogami")
    # an out-of-band re-anchor happens before applying
    repo.register_model_baseline(b, actor="JerryKogami",
                                 expected_baseline_hash=first.baseline_hash)
    with pytest.raises(StoreError) as excinfo:
        repo.apply_review("REV-A", reviewer="JerryKogami")
    assert excinfo.value.code == store_err.BASELINE_CONFLICT


def test_a_rejected_proposal_cannot_be_applied(repo, tmp_path):
    model, solution = m03()
    first = record(repo, model, solution, "r1")
    changed = validate_model(json.loads(
        mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25").read_text(encoding="utf-8")
    ))
    repo.propose_review(review_id="REV-1", model_id=model.model_id,
                        expected_baseline_hash=first.baseline_hash, proposed_model=changed,
                        proposed_by="agent")
    repo.decide_review("REV-1", approve=False, reviewer="JerryKogami", note="not now")
    with pytest.raises(StoreError) as excinfo:
        repo.apply_review("REV-1", reviewer="JerryKogami")
    assert excinfo.value.code == store_err.REVIEW_STATE
    assert repo.current_baseline_hash(model.model_id) == first.baseline_hash


def test_an_invalid_proposal_is_recorded_but_has_no_hash(repo, tmp_path):
    model, solution = m03()
    first = record(repo, model, solution, "r1")
    record_row = repo.propose_review(
        review_id="REV-BAD", model_id=model.model_id,
        expected_baseline_hash=first.baseline_hash, proposed_model=None, proposed_by="agent",
        validation_error={"code": "PROBABILITY", "message": "out of range"},
    )
    assert record_row["state"] == "invalid"
    assert record_row["proposed_hash"] is None
    assert record_row["validation_code"] == "PROBABILITY"
    with pytest.raises(StoreError) as excinfo:
        repo.decide_review("REV-BAD", approve=True, reviewer="JerryKogami")
    assert excinfo.value.code == store_err.REVIEW_STATE


def test_tampering_with_a_stored_proposal_blocks_the_apply(repo, tmp_path):
    model, solution = m03()
    first = record(repo, model, solution, "r1")
    changed = validate_model(json.loads(
        mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25").read_text(encoding="utf-8")
    ))
    repo.propose_review(review_id="REV-1", model_id=model.model_id,
                        expected_baseline_hash=first.baseline_hash, proposed_model=changed,
                        proposed_by="agent")
    repo.decide_review("REV-1", approve=True, reviewer="JerryKogami")
    # an out-of-band edit of the approved canonical form must never reach the baseline
    conn = raw(repo.path)
    canonical = json.loads(conn.execute(
        "SELECT proposed_canonical_json FROM reviews WHERE review_id='REV-1'"
    ).fetchone()[0])
    canonical["basic_events"][0]["p"] = "0.99"
    conn.execute("UPDATE reviews SET proposed_canonical_json=? WHERE review_id='REV-1'",
                 (json.dumps(canonical),))
    conn.commit()
    conn.close()
    with pytest.raises(StoreError) as excinfo:
        repo.apply_review("REV-1", reviewer="JerryKogami")
    assert excinfo.value.code == store_err.INTEGRITY


def test_unknown_review_ids(repo):
    with pytest.raises(StoreError) as excinfo:
        repo.apply_review("NOPE", reviewer="JerryKogami")
    assert excinfo.value.code == store_err.REVIEW_NOT_FOUND
    with pytest.raises(StoreError) as excinfo:
        repo.decide_review("NOPE", approve=True, reviewer="JerryKogami")
    assert excinfo.value.code == store_err.REVIEW_NOT_FOUND


# --------------------------------------------------------------------------
# integrity and portability
# --------------------------------------------------------------------------


def test_verify_is_clean_on_a_populated_store(repo, tmp_path):
    model, solution = m03()
    record(repo, model, solution, "r1")
    changed = validate_model(json.loads(
        mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25").read_text(encoding="utf-8")
    ))
    repo.register_model_baseline(changed, actor="JerryKogami",
                                 expected_baseline_hash=semantic_model_hash(model))
    assert repo.verify() == []


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE importance SET fussell_vesely='0.5' WHERE rowid=(SELECT MIN(rowid) FROM importance)",
        "UPDATE runs SET top_event_probability='0.5' WHERE run_id='r1'",
        "UPDATE runs SET cut_sets_json='[[]]' WHERE run_id='r1'",
        "UPDATE runs SET stale=1-stale WHERE run_id='r1'",
        "DELETE FROM importance WHERE rowid=(SELECT MIN(rowid) FROM importance)",
    ],
)
def test_verify_detects_corruption(repo, statement):
    model, solution = m03()
    record(repo, model, solution, "r1")
    conn = raw(repo.path)
    conn.executescript(statement)
    conn.commit()
    conn.close()
    assert repo.verify() != []


def test_verify_detects_a_rewritten_baseline_canonical_form(repo):
    model, solution = m03()
    record(repo, model, solution, "r1")
    conn = raw(repo.path)
    canonical = json.loads(conn.execute("SELECT canonical_json FROM baselines").fetchone()[0])
    canonical["basic_events"][0]["p"] = "0.42"
    conn.execute("UPDATE baselines SET canonical_json=?", (json.dumps(canonical),))
    conn.commit()
    conn.close()
    problems = repo.verify()
    assert any("re-hashes" in problem for problem in problems)


def test_verify_reports_float_capable_columns(repo):
    conn = raw(repo.path)
    conn.executescript("CREATE TABLE extra_metric(level REAL)")
    conn.commit()
    conn.close()
    assert any("declared REAL" in problem for problem in repo.verify())


def test_verify_checks_the_multilinearity_identity_on_stored_numbers(repo):
    model, solution = m03()
    record(repo, model, solution, "r1")
    # q+ is part of Q = q*q+ + (1-q)*q-; break it and the identity must fail
    conn = raw(repo.path)
    conn.execute("UPDATE importance SET top_given_failed='0.123456' WHERE event_id='B'")
    conn.commit()
    conn.close()
    problems = repo.verify()
    assert any("multilinearity" in problem for problem in problems)


def test_dump_restore_round_trip(repo, tmp_path):
    model, solution = m03()
    record(repo, model, solution, "r1")
    changed = validate_model(json.loads(
        mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25").read_text(encoding="utf-8")
    ))
    repo.register_model_baseline(changed, actor="JerryKogami",
                                 expected_baseline_hash=semantic_model_hash(model))
    record(repo, changed, solve_model(changed), "r2")
    repo.propose_review(review_id="REV-1", model_id=changed.model_id,
                        expected_baseline_hash=semantic_model_hash(changed),
                        proposed_model=changed, proposed_by="agent")

    original = repo.dump()
    clone = SqliteRepository.restore(str(tmp_path / "clone.sqlite"), original)
    try:
        assert clone.dump() == original
        assert clone.verify() == []
        assert clone.get_run("r1")["stale"] == 1
        assert clone.get_run("r2")["stale"] == 0
    finally:
        clone.close()


def test_restored_store_still_refuses_a_conflicting_run_id(repo, tmp_path):
    model, solution = m03()
    record(repo, model, solution, "r1")
    clone = SqliteRepository.restore(str(tmp_path / "clone.sqlite"), repo.dump())
    try:
        other = load_model(EXAMPLES / "M02_or.json")
        with pytest.raises(StoreError) as excinfo:
            record(clone, other, solve_model(other), "r1")
        assert excinfo.value.code == store_err.RUN_ID_CONFLICT
    finally:
        clone.close()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_store_init_status_and_verify(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    assert main(["store", "init", db]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["store"]["store_schema_version"] == STORE_SCHEMA_VERSION
    assert payload["store"]["float_columns"] == []

    assert main(["store", "verify", db]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"] == []


def test_cli_analyze_with_db_records_and_is_idempotent(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    model_path = str(EXAMPLES / "M03_repeated_event.json")

    assert main(["analyze", model_path, "--db", db, "--run-id", "r1",
                 "--actor", "JerryKogami"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["store"]["status"] == "recorded"
    assert payload["status"] == "succeeded"

    assert main(["analyze", model_path, "--db", db, "--run-id", "r1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["store"]["status"] == "idempotent"


def test_cli_analyze_db_conflict_preserves_the_result_but_exits_nonzero(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    assert main(["analyze", str(EXAMPLES / "M03_repeated_event.json"),
                 "--db", db, "--run-id", "shared"]) == 0
    capsys.readouterr()

    code = main(["analyze", str(EXAMPLES / "M02_or.json"), "--db", db, "--run-id", "shared"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["store"]["status"] == "refused"
    assert payload["store"]["error"]["code"] == store_err.RUN_ID_CONFLICT
    # the analysis itself still succeeded and is still reported
    assert payload["status"] == "succeeded"
    assert payload["top_probability"] is not None


def test_cli_important_sorts_exactly(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    assert main(["analyze", str(EXAMPLES / "M03_repeated_event.json"),
                 "--db", db, "--run-id", "r1"]) == 0
    capsys.readouterr()
    assert main(["store", "important", db, "r1", "--by", "fussell_vesely"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [row["event_id"] for row in payload["importance"]] == ["A", "C", "B"]
    assert payload["importance"][1]["fussell_vesely"] == "6/11"


def test_cli_store_show_and_runs(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    assert main(["analyze", str(EXAMPLES / "M03_repeated_event.json"),
                 "--db", db, "--run-id", "r1"]) == 0
    capsys.readouterr()

    assert main(["store", "runs", db]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["runs"][0]["run_id"] == "r1"
    assert payload["runs"][0]["stale"] is False

    assert main(["store", "show", db, "r1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["payload"]["run_id"] == "r1"
    assert payload["run"]["cut_sets"] == [["A", "B"], ["A", "C"]]


def test_cli_unknown_run_and_model_are_refused(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    assert main(["store", "init", db]) == 0
    capsys.readouterr()
    assert main(["store", "show", db, "nope"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == store_err.RUN_NOT_FOUND
    assert main(["store", "baseline", db, "nope"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == store_err.MODEL_NOT_FOUND


def test_cli_review_loop_and_agent_guard(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    model_path = str(EXAMPLES / "M03_repeated_event.json")
    assert main(["analyze", model_path, "--db", db, "--run-id", "r1"]) == 0
    capsys.readouterr()

    assert main(["store", "baseline", db, "M03_repeated_event"]) == 0
    current = json.loads(capsys.readouterr().out)["current_baseline_hash"]

    changed = mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25")
    assert main(["review", "propose", db, str(changed), "--review-id", "REV-1",
                 "--expected-baseline-hash", current, "--reviewer", "agent"]) == 0
    assert json.loads(capsys.readouterr().out)["review"]["state"] == "proposed"

    assert main(["review", "decide", db, "REV-1", "--approve", "--reviewer", "agent"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == store_err.APPROVAL_AUTHORITY

    assert main(["review", "decide", db, "REV-1", "--approve",
                 "--reviewer", "JerryKogami"]) == 0
    capsys.readouterr()
    assert main(["review", "apply", db, "REV-1", "--reviewer", "JerryKogami"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["review"]["state"] == "applied"
    assert payload["stale_run_ids"] == ["r1"]

    assert main(["analyze", str(changed), "--db", db, "--run-id", "r2"]) == 0
    assert json.loads(capsys.readouterr().out)["store"]["status"] == "recorded"


def test_cli_export_round_trips_through_the_repository(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    out = tmp_path / "dump.json"
    assert main(["analyze", str(EXAMPLES / "M03_repeated_event.json"),
                 "--db", db, "--run-id", "r1"]) == 0
    capsys.readouterr()
    assert main(["store", "export", db, "--out", str(out)]) == 0
    capsys.readouterr()

    dump = json.loads(out.read_text(encoding="utf-8"))
    clone = SqliteRepository.restore(str(tmp_path / "clone.sqlite"), dump)
    try:
        assert clone.dump() == dump
        assert clone.verify() == []
    finally:
        clone.close()


def test_cli_adopt_baseline_requires_a_reviewer_and_the_right_hash(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    assert main(["analyze", str(EXAMPLES / "M03_repeated_event.json"),
                 "--db", db, "--run-id", "r1"]) == 0
    capsys.readouterr()
    assert main(["store", "baseline", db, "M03_repeated_event"]) == 0
    current = json.loads(capsys.readouterr().out)["current_baseline_hash"]

    changed = mutated_copy(tmp_path, "M03_repeated_event.json", 1, "0.25")
    assert main(["store", "adopt-baseline", db, str(changed),
                 "--expected-baseline-hash", "0" * 64, "--reviewer", "JerryKogami"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == store_err.BASELINE_CONFLICT

    assert main(["store", "adopt-baseline", db, str(changed),
                 "--expected-baseline-hash", current, "--reviewer", "JerryKogami"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stale_run_ids"] == ["r1"]


def test_cli_capabilities_lists_the_store(tmp_path, capsys):
    assert main(["capabilities"]) == 0
    payload = json.loads(capsys.readouterr().out)
    by_id = {c["capability_id"]: c for c in payload["capabilities"]}
    assert by_id["local_baseline_run_store"]["status"] == "verified"
