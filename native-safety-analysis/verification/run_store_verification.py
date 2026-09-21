"""Store verification — the persisted data, re-derived independently.

The subject here is the SQLite store: its on-disk format, its numbers, and its
state machine. Nothing in this file trusts the store to describe itself.

Part 1  NUMBERS
        Every run is read back with raw `sqlite3` (no store module involved),
        its baseline's canonical form is turned back into a schema-0.1.0 model,
        and the top probability, the minimal cut sets and ALL SEVEN importance
        values per event are recomputed by the truth-table oracle
        (`reference_fta` / `reference_importance`). Exact equality is required:
        no tolerance, because nothing here rounds.

        This is the check that matters. It does not validate that the store is
        self-consistent; it validates that what the store holds is CORRECT,
        computed by a completely different algorithm (2^n enumeration) from the
        one that wrote it (ROBDD).

Part 2  STORAGE CLASS
        Column types are audited AND every value in every table is checked with
        SQLite's own `typeof()` for 'real'. B_核心规划 §231 forbids a stored
        result passing through binary floating point; a declared type alone
        cannot prove that, so the actual on-disk storage class is inspected.

Part 3  STATE MACHINE
        Idempotent run ids, refusal on conflicting reuse, refusal to move a
        baseline silently, optimistic concurrency, stale propagation, the
        agent-cannot-approve rule, and atomicity (a refused record must leave
        no model or baseline behind).

Part 4  ROUND TRIP
        Dump -> restore -> identical dump, and the restored copy must satisfy
        Parts 1 and 2 again. B_核心规划 §88 asks for this from phase one.

Part 5  COVERAGE
        Counters proving the run is not vacuous: how many stale runs, how many
        non-terminating rationals really were stored as n/d text, how many
        undefined measures were compared, how many refusals were triggered.
"""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "verification"))

from native_safety.domain.semantic_hash import semantic_model_hash  # noqa: E402
from native_safety.domain.validation import validate_model  # noqa: E402
from native_safety.kernel.solve import solve_model  # noqa: E402
from native_safety.store import SqliteRepository, StoreError  # noqa: E402
from reference_fta import ref_solve  # noqa: E402
from reference_importance import ref_importance  # noqa: E402
from run_cross_check import random_model  # noqa: E402
from run_rate_cross_check import oracle_q, to_declared_q  # noqa: E402

MEASURE_FIELDS = {
    "event_probability": "q",
    "top_given_failed": "q_plus",
    "top_given_working": "q_minus",
    "birnbaum_importance": "birnbaum",
    "fussell_vesely": "fussell_vesely",
    "risk_achievement_worth": "risk_achievement_worth",
    "risk_reduction_worth": "risk_reduction_worth",
}

FIXED_MODELS = 14
RATE_MODELS = 8

PROBLEMS: list[str] = []
COVERAGE = {
    "runs_verified": 0,
    "events_compared": 0,
    "importance_values_compared": 0,
    "undefined_measures_compared": 0,
    "baselines_rehashed": 0,
    "runs_on_superseded_baseline": 0,
    "rational_text_values": 0,
    "decimal_text_values": 0,
    "refusals": {},
    "idempotent_noops": 0,
    "storage_value_scans": 0,
    "impact_reports": 0,
    "reverts_applied": 0,
}


def fail(message: str) -> None:
    PROBLEMS.append(message)


def run_payload(model, solution, run_id: str, *, schema="0.1.0") -> dict:
    """A payload shaped like the CLI's, without invoking the CLI."""
    stats = {
        "bdd_node_count": solution.bdd_node_count,
        "variable_order": list(solution.variable_order),
        "limits": solution.limits,
    }
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
        "engine_stats": stats,
        "calculation_mode": "exact_bdd_shannon_fraction",
    }


# --------------------------------------------------------------------------
# Part 1 — numbers re-derived by the independent oracle
# --------------------------------------------------------------------------


def reference_model_from_canonical(canonical: dict) -> dict:
    """Rebuild a schema-0.1.0 model from a stored canonical form.

    The canonical form keeps only computation-relevant data (`id`, `p`,
    gate logic, top event), which is exactly what the oracle consumes.
    """
    return {
        "schema_version": "0.1.0",
        "model_id": canonical.get("model_id", "stored"),
        "baseline_id": "stored",
        "assumptions": {
            "basic_events_independent": True,
            "probability_semantics": "fixed_conditioned_probability",
            "condition": "reconstructed from the stored canonical form",
        },
        "basic_events": [
            {"id": e["id"], "label": e["id"], "probability": e["p"], "source": "stored"}
            for e in canonical["basic_events"]
        ],
        "gates": canonical["gates"],
        "top_event": canonical["top_event"],
    }


def independent_canonical_hash(canonical: dict) -> str:
    """Re-implement the canonicalization rule here, not in the production module.

    The rule is three lines of JSON options; re-stating it means a change to the
    production canonicalization cannot silently make every stored baseline
    "self-consistent" while no longer matching the engine's own hashes.
    """
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_numbers(db_path: Path) -> None:
    """Read the store with raw sqlite3 and re-derive every stored number."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    baselines = {r["baseline_hash"]: dict(r) for r in conn.execute("SELECT * FROM baselines")}
    runs = [dict(r) for r in conn.execute("SELECT * FROM runs")]
    current = {
        r["model_id"]: r["current_baseline_hash"]
        for r in conn.execute("SELECT model_id, current_baseline_hash FROM models")
    }
    importance_rows: dict[str, list[dict]] = {}
    for row in conn.execute("SELECT * FROM importance"):
        importance_rows.setdefault(row["run_id"], []).append(dict(row))

    # -- the anchoring itself: a baseline hash must be reproducible from the
    #    canonical form, by an implementation that is not the engine's.
    for baseline_hash, row in baselines.items():
        canonical = json.loads(row["canonical_json"])
        COVERAGE["baselines_rehashed"] += 1
        if independent_canonical_hash(canonical) != baseline_hash:
            fail(f"baseline {baseline_hash[:12]}…: canonical form does not reproduce the hash")

    for run in runs:
        run_id = run["run_id"]
        baseline = baselines.get(run["baseline_hash"])
        if baseline is None:
            fail(f"{run_id}: baseline {run['baseline_hash'][:12]}… missing")
            continue
        # -- staleness recomputed from the model's current baseline
        expected_stale = current.get(run["model_id"]) != run["baseline_hash"]
        if bool(run["stale"]) != expected_stale:
            fail(
                f"{run_id}: stale={run['stale']} but its baseline "
                f"{'is' if not expected_stale else 'is not'} the current one"
            )
        canonical = json.loads(baseline["canonical_json"])
        reference_model = reference_model_from_canonical(canonical)

        # The oracle only accepts decimal probability text. Rate-derived and
        # declared probabilities must both render as terminating decimals; a
        # failure here would mean a stored value cannot be handed to a strict
        # consumer, and is reported rather than skipped silently.
        for event in reference_model["basic_events"]:
            text = event["probability"]
            if "/" in text:
                fail(f"{run_id}: event {event['id']} stored as a rational {text!r}, not a decimal")
        if any("/" in e["probability"] for e in reference_model["basic_events"]):
            continue

        ref_top, ref_mcs = ref_solve(reference_model)
        COVERAGE["runs_verified"] += 1

        stored_top = Fraction(run["top_event_probability"])
        if stored_top != ref_top:
            fail(f"{run_id}: stored top {stored_top} != oracle {ref_top}")
        if run["top_event_probability_kind"] not in ("exact_decimal", "exact_rational"):
            fail(f"{run_id}: unknown rendering kind {run['top_event_probability_kind']!r}")

        stored_mcs = sorted(sorted(s) for s in json.loads(run["cut_sets_json"]))
        if stored_mcs != ref_mcs:
            fail(f"{run_id}: stored cut sets differ from the oracle")
        if len(stored_mcs) != run["minimal_cut_set_count"]:
            fail(f"{run_id}: cut set count column disagrees with the stored JSON")

        # ---- importance, all seven values per event, exact equality
        ref_top_i, ref_measures, ref_support = ref_importance(reference_model)
        if ref_top_i != ref_top:
            fail(f"{run_id}: the two oracles disagree with each other on the top probability")
        rows = importance_rows.get(run_id, [])
        if run["importance_status"] == "computed":
            if not rows:
                fail(f"{run_id}: importance_status is computed but no rows are stored")
            if len(rows) != len(ref_measures):
                fail(f"{run_id}: {len(rows)} importance rows stored, oracle has {len(ref_measures)}")
            for row in rows:
                eid = row["event_id"]
                expected = ref_measures.get(eid)
                if expected is None:
                    fail(f"{run_id}/{eid}: stored but not a variable of the model")
                    continue
                COVERAGE["events_compared"] += 1
                if row["in_support"] != (1 if eid in ref_support else 0):
                    fail(f"{run_id}/{eid}: support flag disagrees with the truth-table decision")
                for column, key in MEASURE_FIELDS.items():
                    stored = row[column]
                    wanted = expected[key]
                    if wanted is None:
                        if stored is not None:
                            fail(f"{run_id}/{eid}.{column}: oracle says undefined, store holds {stored}")
                        COVERAGE["undefined_measures_compared"] += 1
                        continue
                    if stored is None:
                        fail(f"{run_id}/{eid}.{column}: oracle says {wanted}, store holds NULL")
                        continue
                    COVERAGE["importance_values_compared"] += 1
                    if Fraction(stored) != wanted:
                        fail(f"{run_id}/{eid}.{column}: stored {stored} != oracle {wanted}")
                    if "/" in stored:
                        COVERAGE["rational_text_values"] += 1
                    else:
                        COVERAGE["decimal_text_values"] += 1

    conn.close()


# --------------------------------------------------------------------------
# Part 2 — storage class audit
# --------------------------------------------------------------------------


def scan_storage_classes(conn: sqlite3.Connection) -> tuple[list[str], int]:
    """Return (problems, values_scanned).

    Checks the declared column types, then asks SQLite for the actual storage
    class of every value in every table. A REAL anywhere is a §231 violation
    regardless of what the column claims.
    """
    problems: list[str] = []
    scanned = 0
    tables = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        declared = {r[1]: (r[2] or "").upper() for r in conn.execute(f"PRAGMA table_info({table})")}
        for column, kind in declared.items():
            if any(token in kind for token in ("REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL")):
                problems.append(f"{table}.{column} declared {kind}")
        for column in columns:
            classes = {
                r[0]
                for r in conn.execute(
                    f'SELECT DISTINCT typeof("{column}") FROM "{table}"'
                )
            }
            rows = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            scanned += rows
            if "real" in classes:
                problems.append(f"{table}.{column} actually holds REAL values")
    return problems, scanned


def read_only_verify(db_path: Path, label: str) -> None:
    """Parts 1 and 2 on an arbitrary store file."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    problems, scanned = scan_storage_classes(conn)
    COVERAGE["storage_value_scans"] += scanned
    for problem in problems:
        fail(f"[{label}] {problem}")
    for row in conn.execute("SELECT stale FROM runs WHERE stale=1"):
        COVERAGE["runs_on_superseded_baseline"] += 1
    conn.close()
    # A corrupted store must be REPORTED, not crash the verifier.
    try:
        verify_numbers(db_path)
    except Exception as exc:  # noqa: BLE001 — deliberate
        fail(f"[{label}] re-derivation raised {type(exc).__name__}: {exc}")
    # Applied reviews must still re-hash to their own proposals: an applied
    # change is a fact about history, and neither its anchored baseline nor
    # its recorded proposal may be edited after the fact.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        known_hashes = {
            row["baseline_hash"] for row in conn.execute("SELECT baseline_hash FROM baselines")
        }
        for row in conn.execute(
            "SELECT review_id, proposed_hash, applied_baseline_hash, proposed_canonical_json"
            " FROM reviews WHERE state='applied'"
        ):
            tag = f"[{label}] applied review {row['review_id']!r}"
            if row["applied_baseline_hash"] is None or row["proposed_canonical_json"] is None:
                fail(f"{tag}: applied but carries no anchored baseline/proposal")
                continue
            try:
                canonical = json.loads(row["proposed_canonical_json"])
            except json.JSONDecodeError:
                fail(f"{tag}: proposed canonical form is not JSON")
                continue
            recomputed = independent_canonical_hash(canonical)
            if recomputed != row["proposed_hash"]:
                fail(f"{tag}: proposal no longer re-hashes to its recorded hash")
            if recomputed != row["applied_baseline_hash"]:
                fail(f"{tag}: anchored baseline is not the hash of what was proposed")
            if row["applied_baseline_hash"] not in known_hashes:
                fail(f"{tag}: anchored baseline is missing from the baselines table")
    finally:
        conn.close()


def verification_catches(db_path: Path) -> str:
    """Run the read-only checks in an isolated buffer; return the first problem.

    The mutation probes must not pollute the real result, so both the problem
    list and the coverage counters are snapshotted and restored.
    """
    saved_problems = PROBLEMS[:]
    saved_coverage = dict(COVERAGE)
    del PROBLEMS[:]
    try:
        read_only_verify(db_path, "mutation")
        return PROBLEMS[0] if PROBLEMS else ""
    finally:
        del PROBLEMS[:]
        PROBLEMS.extend(saved_problems)
        COVERAGE.clear()
        COVERAGE.update(saved_coverage)


def verify_schema_mismatch(tmp: Path) -> None:
    """A store written by a future schema generation must be refused, not used."""
    path = tmp / "future.sqlite"
    with SqliteRepository(str(path)):
        pass
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_meta SET value='99' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    expect_refusal(
        "schema: open a store from a future generation", "STORE_SCHEMA_MISMATCH",
        lambda: SqliteRepository(str(path)),
    )


# --------------------------------------------------------------------------
# Part 3 — state machine
# --------------------------------------------------------------------------


def note_refusal(code: str) -> None:
    COVERAGE["refusals"][code] = COVERAGE["refusals"].get(code, 0) + 1


def expect_refusal(label: str, code_expected: str, fn) -> None:
    try:
        fn()
        fail(f"{label}: expected {code_expected}, but the call succeeded")
    except StoreError as exc:
        note_refusal(exc.code)
        if exc.code != code_expected:
            fail(f"{label}: expected {code_expected}, got {exc.code}")


def verify_state_machine(tmp: Path) -> Path:
    db = tmp / "state.sqlite"
    models = ROOT / "reference" / "03_contracts" / "examples"

    with SqliteRepository(str(db)) as repo:
        model_a = validate_model(json.loads((models / "M03_repeated_event.json").read_text(encoding="utf-8")))
        solution_a = solve_model(model_a)
        first = repo.record_run(
            run_id="A", model=model_a, solution=solution_a,
            payload=run_payload(model_a, solution_a, "A"), actor="JerryKogami",
        )
        if not first.created:
            fail("state: the first record was reported as already existing")

        # -- idempotent re-record: same content, no new row, values untouched
        before = _table_snapshot(db, "runs")
        again = repo.record_run(
            run_id="A", model=model_a, solution=solution_a,
            payload=run_payload(model_a, solution_a, "A"), actor="someone-else",
        )
        if again.created:
            fail("state: re-recording identical content created a new run")
        if again.fingerprint != first.fingerprint:
            fail("state: identical content produced a different fingerprint")
        if _table_snapshot(db, "runs") != before:
            fail("state: idempotent re-record modified the stored row (author must not be rewritten)")
        COVERAGE["idempotent_noops"] += 1

        # -- conflicting reuse of a run id
        model_b = validate_model(json.loads((models / "M02_or.json").read_text(encoding="utf-8")))
        solution_b = solve_model(model_b)
        expect_refusal(
            "state: run id reuse with different content", "RUN_ID_CONFLICT",
            lambda: repo.record_run(
                run_id="A", model=model_b, solution=solution_b,
                payload=run_payload(model_b, solution_b, "A"),
            ),
        )
        # ... and it must have been atomic: no orphan model/baseline for M02_or
        counts = repo.status()["counts"]
        if counts["models"] != 1 or counts["baselines"] != 1:
            fail(f"state: a refused record left side effects behind ({counts})")

        # -- silent baseline drift is refused
        drifted = json.loads((models / "M03_repeated_event.json").read_text(encoding="utf-8"))
        drifted["basic_events"][1]["probability"] = "0.25"
        model_c = validate_model(drifted)
        solution_c = solve_model(model_c)
        expect_refusal(
            "state: re-anchoring through a re-run", "BASELINE_NOT_CURRENT",
            lambda: repo.record_run(
                run_id="B", model=model_c, solution=solution_c,
                payload=run_payload(model_c, solution_c, "B"),
            ),
        )

        # -- optimistic concurrency on an explicit re-anchor
        expect_refusal(
            "state: re-anchor with a wrong expected hash", "BASELINE_CONFLICT",
            lambda: repo.register_model_baseline(
                model_c, actor="JerryKogami", expected_baseline_hash="0" * 64
            ),
        )
        # -- a first-ever model cannot be re-anchored either
        expect_refusal(
            "state: re-anchor a model that has no baseline", "BASELINE_CONFLICT",
            lambda: repo.register_model_baseline(
                model_b, actor="JerryKogami", expected_baseline_hash="0" * 64
            ),
        )

        # -- review loop: propose, refuse agent approval, require approval to apply
        repo.register_model_baseline(model_b, actor="JerryKogami")  # first sighting
        expect_refusal(
            "state: apply a review that does not exist", "REVIEW_NOT_FOUND",
            lambda: repo.apply_review("REV-X", reviewer="JerryKogami"),
        )
        expect_refusal(
            "state: propose a review for an unknown model", "BASELINE_CONFLICT",
            lambda: repo.propose_review(
                review_id="REV-0", model_id="NO_SUCH_MODEL",
                expected_baseline_hash="0" * 64, proposed_model=model_c, proposed_by="agent",
            ),
        )
        proposal = repo.propose_review(
            review_id="REV-1", model_id=model_a.model_id,
            expected_baseline_hash=first.baseline_hash, proposed_model=model_c,
            proposed_by="agent", note="raise E01",
        )
        if proposal["state"] != "proposed" or proposal["proposed_hash"] is None:
            fail("state: a valid proposal was not recorded as proposed")

        expect_refusal(
            "state: propose against a stale baseline", "BASELINE_CONFLICT",
            lambda: repo.propose_review(
                review_id="REV-2", model_id=model_a.model_id,
                expected_baseline_hash="0" * 64, proposed_model=model_c, proposed_by="agent",
            ),
        )
        expect_refusal(
            "state: unknown review", "REVIEW_NOT_FOUND",
            lambda: repo.apply_review("REV-NOPE", reviewer="JerryKogami"),
        )
        expect_refusal(
            "state: agent decides", "APPROVAL_AUTHORITY",
            lambda: repo.decide_review("REV-1", approve=True, reviewer="agent"),
        )
        expect_refusal(
            "state: apply before approval", "REVIEW_STATE",
            lambda: repo.apply_review("REV-1", reviewer="JerryKogami"),
        )

        repo.decide_review("REV-1", approve=True, reviewer="JerryKogami")
        expect_refusal(
            "state: decide a review twice", "REVIEW_STATE",
            lambda: repo.decide_review("REV-1", approve=True, reviewer="JerryKogami"),
        )
        applied, stale_ids = repo.apply_review("REV-1", reviewer="JerryKogami")
        expect_refusal(
            "state: apply a review twice", "REVIEW_STATE",
            lambda: repo.apply_review("REV-1", reviewer="JerryKogami"),
        )
        if applied["state"] != "applied":
            fail("state: apply did not move the review to applied")
        if sorted(stale_ids) != ["A"]:
            fail(f"state: expected run A to be flagged stale, got {stale_ids}")

        # -- after apply, the new semantics record cleanly under a new run id
        recorded = repo.record_run(
            run_id="C", model=model_c, solution=solution_c,
            payload=run_payload(model_c, solution_c, "C"),
        )
        if not recorded.created:
            fail("state: the approved semantics could not be recorded")
        rows = {r["run_id"]: r for r in repo.list_runs()}
        if not rows["A"]["stale"] or rows["C"]["stale"]:
            fail("state: stale flags do not follow the current baseline")
        if rows["A"]["stale_reason"] is None:
            fail("state: a stale run carries no reason")
        # old evidence stays readable
        if Fraction(rows["A"]["top_event_probability"]) != Fraction(11, 250):
            fail("state: the superseded run's stored payload was not preserved")

        # -- impact analysis: the diff of a proposal, read from the store alone.
        # REV-1 was applied above, so a NEW proposal of the old semantics gives
        # a live "what would change" diff against the current baseline.
        forward = repo.propose_review(
            review_id="REV-IMP", model_id=model_a.model_id,
            expected_baseline_hash=repo.current_baseline_hash(model_a.model_id),
            proposed_model=model_a, proposed_by="agent", note="impact probe",
        )
        impact = repo.review_impact("REV-IMP")
        if impact["baseline_still_current"] is not True:
            fail("state: impact does not see the current baseline")
        if impact["summary"]["events_changed"] < 1:
            fail("state: impact of a probability change reports no event change")
        COVERAGE["impact_reports"] += 1

        # -- revert: a first-class proposal built from the baseline table.
        # The current baseline is now model_c's; going back to model_a's first
        # baseline is a real revert with a real diff.
        current_hash_now = repo.current_baseline_hash(model_a.model_id)
        if current_hash_now == first.baseline_hash:
            fail("state: precondition failed - REV-1 did not move the baseline")
        expect_refusal(
            "state: revert to the current baseline", "REVERT_TARGET",
            lambda: repo.propose_revert(
                review_id="REV-R0", model_id=model_a.model_id,
                target_baseline_hash=current_hash_now,
                expected_baseline_hash=current_hash_now,
                proposed_by="agent",
            ),
        )
        expect_refusal(
            "state: revert to a baseline the store never held", "REVERT_TARGET",
            lambda: repo.propose_revert(
                review_id="REV-R1", model_id=model_a.model_id,
                target_baseline_hash="f" * 64,
                expected_baseline_hash=current_hash_now,
                proposed_by="agent",
            ),
        )
        revert = repo.propose_revert(
            review_id="REV-R2", model_id=model_a.model_id,
            target_baseline_hash=first.baseline_hash,
            expected_baseline_hash=current_hash_now,
            proposed_by="agent",
        )
        if revert["state"] != "proposed":
            fail("state: a revert was not recorded as a plain proposal")
        if repo.current_baseline_hash(model_a.model_id) == first.baseline_hash:
            fail("state: proposing a revert moved the baseline")
        expect_refusal(
            "state: agent decides a revert", "APPROVAL_AUTHORITY",
            lambda: repo.decide_review("REV-R2", approve=True, reviewer="bot"),
        )
        repo.decide_review("REV-R2", approve=True, reviewer="JerryKogami")
        reverted, _ = repo.apply_review("REV-R2", reviewer="JerryKogami")
        if reverted["applied_baseline_hash"] != first.baseline_hash:
            fail("state: an applied revert did not restore the old baseline hash")
        rows = {r["run_id"]: r for r in repo.list_runs()}
        if rows["A"]["stale"] or not rows["C"]["stale"]:
            # stale is DERIVED and must flip BOTH ways after a revert
            fail("state: revert did not re-derive stale in both directions")
        if rows["A"]["stale_reason"] is not None:
            fail("state: a run back on the current baseline kept its stale reason")
        COVERAGE["reverts_applied"] += 1

        # the store's own integrity pass
        problems = repo.verify()
        for problem in problems:
            fail(f"[store.verify] {problem}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.close()
    read_only_verify(db, "state machine store")
    return db


def _table_snapshot(db: Path, table: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    rows = [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
    conn.close()
    return rows


# --------------------------------------------------------------------------
# Part 4 — round trip
# --------------------------------------------------------------------------


def verify_round_trip(source: Path, tmp: Path) -> None:
    with SqliteRepository(str(source)) as repo:
        original = repo.dump()
    if not original["tables"]["runs"]:
        fail("round-trip: the source store has no runs, so the test would be vacuous")

    clone_path = tmp / "clone.sqlite"
    clone = SqliteRepository.restore(str(clone_path), original)
    try:
        clone_dump = clone.dump()
        if clone_dump != original:
            fail("round-trip: the restored store does not dump identically")
        problems = clone.verify()
        for problem in problems:
            fail(f"[round-trip store.verify] {problem}")
    finally:
        clone.close()

    read_only_verify(clone_path, "round-trip clone")


# --------------------------------------------------------------------------
# Part 6 — teeth: prove the checks above can actually fail
# --------------------------------------------------------------------------


def _mutate_canonical(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT baseline_hash, canonical_json FROM baselines LIMIT 1").fetchone()
    canonical = json.loads(row["canonical_json"])
    canonical["basic_events"][0]["p"] = "0.987654321"
    conn.execute(
        "UPDATE baselines SET canonical_json=? WHERE baseline_hash=?",
        (json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
         row["baseline_hash"]),
    )


MUTATIONS: list[tuple[str, object]] = [
    ("a stored importance value is edited",
     "UPDATE importance SET fussell_vesely='0.5' WHERE rowid=(SELECT MIN(rowid) FROM importance)"),
    ("a stored birnbaum value is edited",
     "UPDATE importance SET birnbaum_importance='0.0001' WHERE rowid=(SELECT MIN(rowid) FROM importance)"),
    ("a stored top probability is edited",
     "UPDATE runs SET top_event_probability='0.5' WHERE run_id='fixed-00'"),
    ("a stored cut set family is edited",
     "UPDATE runs SET cut_sets_json='[[]]' WHERE run_id='fixed-00'"),
    ("a stale flag is flipped",
     "UPDATE runs SET stale=1-stale WHERE run_id=(SELECT run_id FROM runs ORDER BY run_id LIMIT 1)"),
    ("an importance row is deleted",
     "DELETE FROM importance WHERE rowid=(SELECT MIN(rowid) FROM importance)"),
    ("the baseline canonical form is edited in place",
     _mutate_canonical),
    ("a float-capable column appears in the schema",
     "CREATE TABLE extra_metric(level REAL)"),
    ("a REAL value is stored (BLOB affinity, no coercion)",
     "CREATE TABLE raw_box(v); INSERT INTO raw_box(v) VALUES (0.5)"),
    ("an applied review's anchored baseline is repointed",
     "UPDATE reviews SET applied_baseline_hash='eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'"
     " WHERE state='applied' AND applied_baseline_hash IS NOT NULL"),
]


def verify_teeth(population: Path, tmp: Path) -> None:
    """Every mutation must be detected. An undetected mutation is a real failure:
    it means the corresponding check is decorative."""
    undetected: list[str] = []
    for index, (label, mutation) in enumerate(MUTATIONS):
        copy_path = tmp / f"mutant_{index:02d}.sqlite"
        copy_path.write_bytes(population.read_bytes())
        conn = sqlite3.connect(copy_path)
        conn.row_factory = sqlite3.Row
        try:
            if callable(mutation):
                mutation(conn)
            else:
                conn.executescript(mutation)
            conn.commit()
        finally:
            conn.close()
        reason = verification_catches(copy_path)
        if not reason:
            undetected.append(label)
        print(f"  {'caught  ' if reason else 'MISSED  '} {label}"
              + (f" — {reason[:96]}" if reason else ""))

    if undetected:
        fail(f"undetected mutations ({len(undetected)}): {undetected}")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def build_population(tmp: Path) -> Path:
    """A store with fixed-probability runs, rate-derived runs, and superseded runs.

    Superseded runs are included deliberately: Part 1 must re-derive the numbers
    of a stale run too, and the stale flag must be visible in coverage.
    """
    db = tmp / "population.sqlite"
    rng = random.Random(20260921)
    raw_by_model: dict[str, tuple[dict, object]] = {}

    with SqliteRepository(str(db)) as repo:
        for index in range(FIXED_MODELS):
            raw = random_model(rng, rng.randint(2, 7), rng.randint(1, 5))
            raw["model_id"] = f"FIXED_{index:02d}"
            model = validate_model(raw)
            solution = solve_model(model)
            repo.record_run(
                run_id=f"fixed-{index:02d}", model=model, solution=solution,
                payload=run_payload(model, solution, f"fixed-{index:02d}"), actor="JerryKogami",
            )
            raw_by_model[raw["model_id"]] = (raw, model)

        for index in range(RATE_MODELS):
            lam = rng.choice(["1e-7", "1e-6", "5e-6", "1e-5", "1e-4"])
            t = rng.choice(["1", "10", "1000", "10000"])
            n_events = rng.randint(2, 6)
            raw = random_model(rng, n_events, rng.randint(1, 4))
            raw["schema_version"] = "0.2.0"
            raw["model_id"] = f"RATE_{index:02d}"
            for event in raw["basic_events"]:
                event.pop("probability", None)
                event["failure_rate"] = {
                    "model": "constant_failure_rate",
                    "lambda": lam, "lambda_unit": "1/h",
                    "mission_time": t, "mission_time_unit": "h",
                    "repairable": False, "source": "synthetic",
                }
            raw["assumptions"]["probability_semantics"] = "fixed_mission_probability_from_constant_rate"
            model = validate_model(raw)
            solution = solve_model(model)
            repo.record_run(
                run_id=f"rate-{index:02d}", model=model, solution=solution,
                payload=run_payload(model, solution, f"rate-{index:02d}"), actor="JerryKogami",
            )
            raw_by_model[raw["model_id"]] = (raw, model)

        # Re-anchor three models under a named reviewer, then record again, so
        # the population carries runs whose baseline has been superseded.
        for index in (2, 5, 9):
            model_id = f"FIXED_{index:02d}"
            raw, previous = raw_by_model[model_id]
            revised = json.loads(json.dumps(raw))
            event = revised["basic_events"][0]
            event["probability"] = "0.03" if event["probability"] != "0.03" else "0.07"
            model = validate_model(revised)
            repo.register_model_baseline(
                model, actor="JerryKogami", expected_baseline_hash=semantic_model_hash(previous)
            )
            solution = solve_model(model)
            repo.record_run(
                run_id=f"fixed-{index:02d}-v2", model=model, solution=solution,
                payload=run_payload(model, solution, f"fixed-{index:02d}-v2"), actor="JerryKogami",
            )

        # One FULL review loop (propose -> decide -> apply) and one applied
        # REVERT, so the population carries applied reviews whose anchored
        # baseline must keep re-hashing to its proposal (Part 6 depends on it).
        raw, previous = raw_by_model["FIXED_11"]
        revised = json.loads(json.dumps(raw))
        revised["basic_events"][0]["probability"] = "0.11"
        model = validate_model(revised)
        repo.propose_review(
            review_id="POP-R1", model_id="FIXED_11",
            expected_baseline_hash=semantic_model_hash(previous),
            proposed_model=model, proposed_by="agent", note="population review",
        )
        repo.decide_review("POP-R1", approve=True, reviewer="JerryKogami")
        repo.apply_review("POP-R1", reviewer="JerryKogami")
        repo.propose_revert(
            review_id="POP-RV1", model_id="FIXED_11",
            target_baseline_hash=semantic_model_hash(previous),
            expected_baseline_hash=repo.current_baseline_hash("FIXED_11"), proposed_by="agent",
        )
        repo.decide_review("POP-RV1", approve=True, reviewer="JerryKogami")
        repo.apply_review("POP-RV1", reviewer="JerryKogami")
    return db


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nsa_store_verify_"))
    build_population(tmp)
    population = tmp / "population.sqlite"

    before = len(PROBLEMS)
    read_only_verify(population, "population")
    population_problems = len(PROBLEMS) - before

    verify_state_machine(tmp)
    verify_schema_mismatch(tmp)
    verify_round_trip(population, tmp)
    print("mutation check (the checks above must be able to fail)")
    verify_teeth(population, tmp)
    print()

    print("store verification")
    print(f"  population            : {FIXED_MODELS} fixed + {RATE_MODELS} rate-derived runs")
    print(f"  runs re-derived       : {COVERAGE['runs_verified']}")
    print(f"  events compared       : {COVERAGE['events_compared']}")
    print(f"  importance values     : {COVERAGE['importance_values_compared']} "
          f"(exact, no tolerance)")
    print(f"  undefined measures    : {COVERAGE['undefined_measures_compared']}")
    print(f"  exact rational texts  : {COVERAGE['rational_text_values']} stored as n/d, "
          f"{COVERAGE['decimal_text_values']} as decimals")
    print(f"  baselines re-hashed   : {COVERAGE['baselines_rehashed']} "
          f"(canonical form -> SHA-256, recomputed independently)")
    print(f"  storage values scanned: {COVERAGE['storage_value_scans']} "
          f"(SQLite typeof — zero REAL expected)")
    print(f"  superseded runs       : {COVERAGE['runs_on_superseded_baseline']} "
          f"(re-derived like any other run)")
    print(f"  idempotent no-ops     : {COVERAGE['idempotent_noops']}")
    print(f"  impact reports        : {COVERAGE['impact_reports']} "
          f"(proposal diffed against the current baseline)")
    print(f"  reverts applied       : {COVERAGE['reverts_applied']} "
          f"(stale re-derived in BOTH directions)")
    print(f"  refusals triggered    : "
          + ", ".join(f"{code}x{n}" for code, n in sorted(COVERAGE["refusals"].items())))
    print(f"  population problems   : {population_problems}")

    if PROBLEMS:
        print(f"\nFAILURES ({len(PROBLEMS)}):")
        for problem in PROBLEMS[:40]:
            print(f"    {problem}")
        if len(PROBLEMS) > 40:
            print(f"    … and {len(PROBLEMS) - 40} more")
        return 1

    nonzero = [
        key for key in ("runs_verified", "events_compared", "importance_values_compared", "baselines_rehashed",
                        "undefined_measures_compared", "rational_text_values",
                        "runs_on_superseded_baseline", "idempotent_noops",
                        "impact_reports", "reverts_applied")
        if COVERAGE.get(key, 0) == 0
    ]
    if nonzero:
        print(f"\nVACUOUS: these counters are zero, so the check proved nothing: {nonzero}")
        return 1
    if len(COVERAGE["refusals"]) < 7:
        print(f"\nVACUOUS: only {len(COVERAGE['refusals'])} distinct refusal codes exercised")
        return 1
    if "REVERT_TARGET" not in COVERAGE["refusals"]:
        print("\nVACUOUS: the revert guards were never exercised")
        return 1
    print("\nstore verification: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
