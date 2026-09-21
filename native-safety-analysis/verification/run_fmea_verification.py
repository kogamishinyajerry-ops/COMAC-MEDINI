"""FMEA verification — the stored FMEA rows, re-derived independently.

Two claims are under test, and neither can be settled by asking the store to
describe itself:

  * what is in the official table is what the row's own columns say it is, and
    it got there through the documented loop (a draft, a named human, an apply);
  * the many-to-many association with basic events is real, and a link that no
    longer resolves is REPORTED rather than quietly repaired or deleted.

Part 1  CONTENT      Every row is read with raw `sqlite3` (no store module) and
                     its canonical form is REBUILT from its scalar columns, its
                     JSON sidecars and its link rows. The rebuild must equal the
                     stored canonical form, and the stored form must re-hash to
                     the stored content hash. One check, wide coverage: editing a
                     column, editing the stored canonical form, editing the hash,
                     and adding or deleting a link all break it. An official row
                     that still carries the machine attention marker is a failure:
                     an unfilled work item is not an approved fact.
Part 2  PROVENANCE   No official row carries an approving identity from the
                     non-approving set (re-listed locally, on purpose, so a
                     weakened list in the store cannot silently weaken this
                     check); every machine-inferred row still states what is
                     unconfirmed; and every current row is RECONSTRUCTIBLE from
                     an applied candidate with the same content hash — i.e. it
                     demonstrably came through propose -> decide -> apply.
Part 3  REVISION     For every fmea_id exactly one version is current, every
                     superseded version names a successor that exists and is
                     newer, and no superseded version's content was edited.
Part 4  BASELINE     Each row's links are resolved against the baseline it was
                     validated against and against the CURRENT baseline, with raw
                     reads; the gaps are counted.
Part 5  STATE        Live state machine on a scratch store: a draft alone never
                     enters the official table, an agent can never decide or
                     apply, an invalid draft is recorded and stays unapprovable,
                     decide/apply are single-step, a revision must name its
                     target, a baseline move invalidates a pending approval, and a
                     marked attention work item is refused promotion even after a
                     human approves it.
Part 6  ROUND TRIP   Dump -> restore -> identical dump, with Parts 1-4 clean on
                     the restored copy.
Part 7  TEETH        Mutations that must ALL be caught. A missed one means the
                     corresponding check is decorative.
"""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "verification"))

from native_safety.domain.semantic_hash import semantic_model_hash  # noqa: E402
from native_safety.domain.validation import validate_model  # noqa: E402
from native_safety.kernel.solve import solve_model  # noqa: E402
from native_safety.store import SqliteRepository, StoreError  # noqa: E402
from run_cross_check import random_model  # noqa: E402

# Deliberately duplicated from the store rather than imported: this is the
# independent statement of who may not approve, and importing it would make the
# check agree with the code even when the code is wrong.
NON_APPROVING = frozenset(
    {"", "agent", "assistant", "ai", "bot", "claude", "copilot", "local-cli", "unknown", "none"}
)
FMEA_CANONICALIZATION = "fmea-canonical-v1"
SOURCES = frozenset({"human", "inference", "import"})
INFERENCE = "inference"
CANDIDATE_STATES = frozenset({"proposed", "invalid", "approved", "rejected", "applied"})
DECIDED_STATES = frozenset({"approved", "rejected", "applied"})

# Also re-listed locally rather than imported: a machine-generated attention item
# is a WORK ITEM whose descriptive fields are unfilled. It must never stand in the
# official table as an approved fact, and if this file imported the production
# marker it could not notice the marker being weakened.
ATTENTION_MARKER = "[UNCONFIRMED]"
ATTENTION_FIELDS = ("failure_mode", "cause", "local_effect", "system_effect")

HUMAN = "J. Engineer"
MODELS = 5

PROBLEMS: list[str] = []
COVERAGE = {
    "official_rows": 0,
    "superseded_rows": 0,
    "candidate_rows": 0,
    "links": 0,
    "models": 0,
    "events_cited_by_many_rows": 0,
    "rows_citing_many_events": 0,
    "inference_rows": 0,
    "applied_candidates": 0,
    "rejected_candidates": 0,
    "invalid_candidates": 0,
    "pending_candidates": 0,
    "resolved_links": 0,
    "storage_value_scans": 0,
    "attention_drafts": 0,
    "refusals": {},
}


def fail(message: str) -> None:
    PROBLEMS.append(message)


# Coverage is only accumulated for the population run; the restored copy and the
# mutants are checked but must not inflate the counters.
COUNT_COVERAGE = True


def bump(key: str, n: int = 1) -> None:
    if COUNT_COVERAGE:
        COVERAGE[key] = COVERAGE.get(key, 0) + n


def note_refusal(code: str) -> None:
    COVERAGE["refusals"][code] = COVERAGE["refusals"].get(code, 0) + 1


def expect_refusal(label: str, code_expected: str, fn) -> None:
    try:
        fn()
    except StoreError as exc:
        if exc.code != code_expected:
            fail(f"{label}: expected refusal {code_expected}, got {exc.code}: {exc.message}")
        else:
            note_refusal(exc.code)
    except Exception as exc:  # noqa: BLE001 — deliberate
        fail(f"{label}: expected {code_expected}, raised {type(exc).__name__}: {exc}")
    else:
        fail(f"{label}: expected refusal {code_expected}, nothing was raised")


# --------------------------------------------------------------------------
# Part 1 — content: rebuild the canonical form from raw columns
# --------------------------------------------------------------------------


def independent_fmea_hash(canonical: dict) -> str:
    """The canonicalization rule, re-implemented here on purpose."""
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_from_columns(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """Rebuild the canonical form from the row's own columns and link rows."""
    links = sorted(
        r["event_id"]
        for r in conn.execute(
            "SELECT event_id FROM fmea_links WHERE model_id=? AND fmea_id=? AND version=?",
            (row["model_id"], row["fmea_id"], row["version"]),
        )
    )
    return {
        "canonicalization": FMEA_CANONICALIZATION,
        "model_id": row["model_id"],
        "fmea_id": row["fmea_id"],
        "component_id": row["component_id"],
        "function_id": row["function_id"],
        "failure_mode": row["failure_mode"],
        "cause": row["cause"],
        "local_effect": row["local_effect"],
        "system_effect": row["system_effect"],
        "controls": sorted(json.loads(row["controls_json"])),
        "evidence": sorted(json.loads(row["evidence_json"]), key=lambda r: r["evidence_id"]),
        "requirement_ids": sorted(json.loads(row["requirement_ids_json"])),
        "linked_event_ids": links,
        "source": row["source"],
    }


def baseline_events(conn: sqlite3.Connection, model_id: str) -> tuple[set[str], str]:
    """(event ids, current baseline hash) — resolved with a raw join."""
    row = conn.execute(
        "SELECT b.canonical_json AS c, b.baseline_hash AS h FROM baselines b"
        " JOIN models m ON m.current_baseline_hash = b.baseline_hash WHERE m.model_id=?",
        (model_id,),
    ).fetchone()
    if row is None:
        return set(), ""
    return {e["id"] for e in json.loads(row["c"]).get("basic_events", [])}, row["h"]


def scan_storage_classes(conn: sqlite3.Connection) -> tuple[list[str], int]:
    problems: list[str] = []
    scanned = 0
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        for column in conn.execute(f"PRAGMA table_info({table})"):
            declared = (column["type"] or "").upper()
            if any(token in declared for token in ("REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL")):
                problems.append(f"{table}.{column['name']} is declared {declared}")
        for column in [c["name"] for c in conn.execute(f"PRAGMA table_info({table})")]:
            classes = {
                r[0]
                for r in conn.execute(f'SELECT typeof("{column}") FROM "{table}"')
                if r[0] is not None
            }
            scanned += 1
            if "real" in classes:
                problems.append(f"{table}.{column} actually holds REAL values")
    return problems, scanned


def audit_population(db_path: Path, label: str, *, count_coverage: bool = True) -> list[dict]:
    """Parts 1-4 on a store file, using nothing but sqlite3 and this file.

    Returns the dangling links found. A dangling link is NOT a failure here: a
    fault tree may legitimately change under an FMEA row, and the row must
    survive it. It is reported and counted, never repaired — which is exactly
    why it is returned rather than raised.
    """
    global COUNT_COVERAGE
    saved_flag = COUNT_COVERAGE
    COUNT_COVERAGE = count_coverage
    dangling: list[dict] = []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        problems, scanned = scan_storage_classes(conn)
        bump("storage_value_scans", scanned)
        for problem in problems:
            fail(f"[{label}] {problem}")

        rows = [dict(r) for r in conn.execute("SELECT * FROM fmea_rows ORDER BY model_id, fmea_id, version")]
        candidates = [dict(r) for r in conn.execute("SELECT * FROM fmea_candidates ORDER BY candidate_id")]
        bump("candidate_rows", len(candidates))

        applied_by_hash: dict[str, list[dict]] = {}
        for candidate in candidates:
            if candidate["state"] == "applied" and candidate["proposed_content_hash"]:
                applied_by_hash.setdefault(candidate["proposed_content_hash"], []).append(candidate)

        version_index: dict[tuple[str, str, int], dict] = {}
        for row in rows:
            version_index[(row["model_id"], row["fmea_id"], row["version"])] = row

        current_per_id: dict[tuple[str, str], int] = {}
        events_by_model: dict[str, set[str]] = {}

        for row in rows:
            tag = f"[{label}] fmea {row['model_id']}/{row['fmea_id']}v{row['version']}"
            # --- Part 1: rebuild the canonical form from the raw columns
            try:
                rebuilt = canonical_from_columns(conn, row)
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                fail(f"{tag}: a JSON sidecar could not be decoded ({exc})")
                continue
            try:
                stored = json.loads(row["canonical_json"])
            except json.JSONDecodeError as exc:
                fail(f"{tag}: canonical_json is not JSON ({exc})")
                continue
            if rebuilt != stored:
                differing = sorted(k for k in set(rebuilt) | set(stored) if rebuilt.get(k) != stored.get(k))
                fail(f"{tag}: the row's columns rebuild a different canonical form ({differing})")
            recomputed = independent_fmea_hash(stored)
            if recomputed != row["content_hash"]:
                fail(f"{tag}: canonical form re-hashes to {recomputed[:12]}…, not {row['content_hash'][:12]}…")

            # --- Part 1b: an unfinished work item must never stand as a fact
            unfinished = [
                field for field in ATTENTION_FIELDS
                if ATTENTION_MARKER in (stored.get(field) or "")
            ]
            if unfinished:
                # There is no legitimate path to this: the store refuses to apply
                # a marked draft, so a marked official row means a bypassed guard
                # or an out-of-band edit. An unfilled work item must never stand
                # as an approved fact (§110).
                fail(
                    f"{tag}: the official row still carries the machine attention placeholder "
                    f"({unfinished}); an unfilled work item is not an approved fact"
                )

            # --- Part 2: provenance and the human gate
            if row["source"] not in SOURCES:
                fail(f"{tag}: unknown source {row['source']!r}")
            if row["source"] == INFERENCE and not (row["inference_note"] or "").strip():
                fail(f"{tag}: machine-inferred but states nothing about what is unconfirmed")
            approver = (row["approved_by"] or "").strip()
            if not approver:
                fail(f"{tag}: no approving human")
            elif approver.lower() in NON_APPROVING:
                fail(f"{tag}: approved by the non-approving identity {approver!r}")
            if not applied_by_hash.get(row["content_hash"]):
                fail(f"{tag}: no applied candidate carries this content hash — it did not come through the loop")
            if not row["validated_baseline_hash"]:
                fail(f"{tag}: no baseline recorded for its traceability check")

            # --- Part 3: revision ledger
            key = (row["model_id"], row["fmea_id"])
            if row["superseded"]:
                bump("superseded_rows")
                successor = row["superseded_by_version"]
                if successor is None:
                    fail(f"{tag}: superseded with no successor")
                elif (row["model_id"], row["fmea_id"], successor) not in version_index:
                    fail(f"{tag}: successor v{successor} does not exist")
                elif successor <= row["version"]:
                    fail(f"{tag}: successor v{successor} is not newer")
                if key in current_per_id:
                    fail(f"{tag}: a superseded version appears after the current one")
            else:
                if key in current_per_id:
                    fail(f"{tag}: more than one current version for one fmea_id")
                current_per_id[key] = row["version"]
                bump("official_rows")

            # --- Part 4: baseline resolution, raw
            if row["model_id"] not in events_by_model:
                events, _ = baseline_events(conn, row["model_id"])
                events_by_model[row["model_id"]] = events
            current_events = events_by_model[row["model_id"]]
            links = sorted(stored.get("linked_event_ids", []))
            bump("links", len(links))
            if len(links) >= 2:
                bump("rows_citing_many_events")
            if row["source"] == INFERENCE:
                bump("inference_rows")
            for event_id in links:
                if event_id in current_events:
                    bump("resolved_links")
                else:
                    dangling.append(
                        {
                            "model_id": row["model_id"],
                            "fmea_id": row["fmea_id"],
                            "version": row["version"],
                            "event_id": event_id,
                        }
                    )

        # The relation must be genuinely many-to-many, not accidentally 1:1.
        cited: dict[tuple[str, str], set[str]] = {}
        for row in conn.execute(
            "SELECT l.model_id, l.event_id, l.fmea_id, r.superseded FROM fmea_links l"
            " JOIN fmea_rows r ON r.model_id=l.model_id AND r.fmea_id=l.fmea_id"
            " AND r.version=l.version WHERE r.superseded=0"
        ):
            cited.setdefault((row["model_id"], row["event_id"]), set()).add(row["fmea_id"])
        bump("events_cited_by_many_rows", sum(1 for v in cited.values() if len(v) >= 2))

        # --- candidates
        for candidate in candidates:
            tag = f"[{label}] candidate {candidate['candidate_id']!r}"
            if candidate["state"] not in CANDIDATE_STATES:
                fail(f"{tag}: unknown state {candidate['state']!r}")
                continue
            if candidate["state"] == "applied":
                bump("applied_candidates")
                if candidate["applied_version"] is None:
                    fail(f"{tag}: applied with no version")
                elif (
                    candidate["model_id"], candidate["fmea_id"], candidate["applied_version"]
                ) not in version_index:
                    fail(f"{tag}: applied version does not exist")
            elif candidate["state"] == "rejected":
                bump("rejected_candidates")
            elif candidate["state"] == "invalid":
                bump("invalid_candidates")
                if not candidate["validation_code"]:
                    fail(f"{tag}: invalid without a reason code")
            else:
                bump("pending_candidates")

            if candidate["state"] in DECIDED_STATES:
                decider = (candidate["decided_by"] or "").strip()
                if not decider:
                    fail(f"{tag}: decided without a named decider")
                elif decider.lower() in NON_APPROVING:
                    fail(f"{tag}: decided by the non-approving identity {decider!r}")
            elif candidate["decided_by"] is not None:
                fail(f"{tag}: undecided state {candidate['state']!r} carries a decider")

            if candidate["proposed_content_hash"]:
                try:
                    proposed = json.loads(candidate["proposed_canonical_json"])
                except (TypeError, json.JSONDecodeError):
                    fail(f"{tag}: proposed canonical form is not JSON")
                    continue
                if independent_fmea_hash(proposed) != candidate["proposed_content_hash"]:
                    fail(f"{tag}: proposed canonical form no longer matches its recorded hash")
            elif candidate["state"] in ("approved", "applied"):
                fail(f"{tag}: {candidate['state']} without a valid proposed row")
    finally:
        conn.close()
        COUNT_COVERAGE = saved_flag
    return dangling


# --------------------------------------------------------------------------
# Part 7 — teeth
# --------------------------------------------------------------------------


def repository_verify(path: Path) -> list[str]:
    try:
        with SqliteRepository(str(path)) as repo:
            return repo.verify()
    except Exception as exc:  # noqa: BLE001 — deliberate
        return [f"{type(exc).__name__}: {exc}"]


def verification_catches(db_path: Path) -> str:
    """Run the checks in an isolated buffer; return the first problem seen.

    Both the independent audit and the store's own verify() are run: the former
    is what this file exists for, the latter is a check that must also have
    teeth.
    """
    saved_problems = PROBLEMS[:]
    saved_coverage = dict(COVERAGE)
    del PROBLEMS[:]
    try:
        audit_population(db_path, "mutation")
        if not PROBLEMS:
            for problem in repository_verify(db_path):
                fail(f"[mutation/verify] {problem}")
        return PROBLEMS[0] if PROBLEMS else ""
    finally:
        del PROBLEMS[:]
        PROBLEMS.extend(saved_problems)
        COVERAGE.clear()
        COVERAGE.update(saved_coverage)


def mutate_add_link(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT model_id, fmea_id, version FROM fmea_rows WHERE superseded=0 LIMIT 1").fetchone()
    conn.execute(
        "INSERT INTO fmea_links(model_id, fmea_id, version, event_id) VALUES(?,?,?,'GHOST')",
        (row["model_id"], row["fmea_id"], row["version"]),
    )


def mutate_delete_link(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM fmea_links WHERE rowid=(SELECT MIN(rowid) FROM fmea_links)")


def mutate_blank_inference_note(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE fmea_rows SET inference_note='' WHERE source='inference' AND superseded=0")


def mutate_plant_second_current(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE fmea_rows SET superseded=0, superseded_by_version=NULL"
        " WHERE version=(SELECT MIN(version) FROM fmea_rows)"
    )


def mutate_canonical_in_place(conn: sqlite3.Connection) -> None:
    """Edit one current row's stored canonical form, leaving its columns alone."""
    row = conn.execute(
        "SELECT model_id, fmea_id, version, canonical_json FROM fmea_rows"
        " WHERE superseded=0 ORDER BY model_id, fmea_id LIMIT 1"
    ).fetchone()
    canonical = json.loads(row["canonical_json"])
    canonical["system_effect"] = "edited in place"
    conn.execute(
        "UPDATE fmea_rows SET canonical_json=? WHERE model_id=? AND fmea_id=? AND version=?",
        (
            json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            row["model_id"], row["fmea_id"], row["version"],
        ),
    )


def mutate_candidate_canonical(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE fmea_candidates SET proposed_canonical_json='{\"canonicalization\":\"fmea-canonical-v1\"}'"
        " WHERE proposed_content_hash IS NOT NULL"
    )


def mutate_plant_attention_row(conn: sqlite3.Connection) -> None:
    """Turn a current official row into a *consistent* attention placeholder.

    Every derived artifact is rewritten (columns, canonical form, hash), so the
    hash and column checks all agree and the ONLY thing left wrong is that an
    unfilled work item is standing as an approved fact.
    """
    row = conn.execute(
        "SELECT model_id, fmea_id, version, canonical_json FROM fmea_rows"
        " WHERE superseded=0 ORDER BY model_id, fmea_id LIMIT 1"
    ).fetchone()
    canonical = json.loads(row["canonical_json"])
    canonical["cause"] = f"{ATTENTION_MARKER} 失效原因待填写"
    conn.execute(
        "UPDATE fmea_rows SET canonical_json=?, content_hash=?, cause=?"
        " WHERE model_id=? AND fmea_id=? AND version=?",
        (
            json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                .encode("utf-8")
            ).hexdigest(),
            canonical["cause"],
            row["model_id"], row["fmea_id"], row["version"],
        ),
    )


MUTATIONS: list[tuple[str, object]] = [
    ("a row's content hash is edited",
     "UPDATE fmea_rows SET content_hash='ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'"),
    ("a row's stored canonical form is edited in place", mutate_canonical_in_place),
    ("a scalar column is edited (disagreeing with the canonical form)",
     "UPDATE fmea_rows SET cause='tampered cause'"),
    ("the controls sidecar is edited",
     "UPDATE fmea_rows SET controls_json='[\"brand new control\"]'"),
    ("the requirement list is edited",
     "UPDATE fmea_rows SET requirement_ids_json='[\"REQ-TAMPERED\"]'"),
    ("a link row is deleted", mutate_delete_link),
    ("a link row is added", mutate_add_link),
    ("an inference note is blanked", mutate_blank_inference_note),
    ("a row is approved by a non-approving identity",
     "UPDATE fmea_rows SET approved_by='claude'"),
    ("a row has no approving human", "UPDATE fmea_rows SET approved_by=''"),
    ("a superseded row loses its successor",
     "UPDATE fmea_rows SET superseded_by_version=NULL WHERE superseded=1"),
    ("a second current version is planted", mutate_plant_second_current),
    ("the recorded validated baseline is blanked",
     "UPDATE fmea_rows SET validated_baseline_hash=''"),
    ("a candidate's proposed form is edited", mutate_candidate_canonical),
    ("a candidate's state is set to an unknown value",
     "UPDATE fmea_candidates SET state='bogus'"),
    ("a candidate is decided by a non-approving identity",
     "UPDATE fmea_candidates SET state='approved', decided_by='copilot'"),
    ("an applied candidate points at a version that does not exist",
     "UPDATE fmea_candidates SET applied_version=999 WHERE state='applied'"),
    ("an unfilled attention work item is promoted to the official table",
     mutate_plant_attention_row),
    ("a float-capable column appears in the schema", "CREATE TABLE extra_metric(level REAL)"),
    ("a REAL value is stored",
     "CREATE TABLE raw_box(v); INSERT INTO raw_box(v) VALUES (0.5)"),
]


def verify_teeth(population: Path, tmp: Path) -> None:
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
              + (f" — {reason[:110]}" if reason else ""))
    if undetected:
        fail(f"undetected mutations ({len(undetected)}): {undetected}")


# --------------------------------------------------------------------------
# Part 5 — state machine
# --------------------------------------------------------------------------


def row_payload(model_id, fmea_id, events, *, source="human", note=None, **overrides):
    payload = {
        "fmea_id": fmea_id,
        "model_id": model_id,
        "component_id": f"COMP-{fmea_id}",
        "function_id": f"FUNC-{fmea_id}",
        "failure_mode": f"mode of {fmea_id}",
        "cause": f"cause of {fmea_id}",
        "local_effect": "local effect",
        "system_effect": "system effect",
        "controls": ["inspection"],
        "evidence": [{"evidence_id": "EV-1", "kind": "analysis", "locator": "doc p.1"}],
        "requirement_ids": [f"REQ-{fmea_id}"],
        "linked_event_ids": list(events),
        "source": source,
    }
    if note is not None:
        payload["inference_note"] = note
    payload.update(overrides)
    return payload


def attention_row(model_id, event_id):
    """An attention draft built HERE, not by calling the production generator.

    The point of the check is the contract — a marked work item must be refused
    promotion — so the verifier states that contract itself instead of echoing
    whatever the production builder happens to produce today.
    """
    return {
        "fmea_id": f"FMEA-ATTN-{event_id}",
        "model_id": model_id,
        "component_id": "COMPONENT-UNASSIGNED",
        "function_id": "FUNCTION-UNASSIGNED",
        "failure_mode": f"{ATTENTION_MARKER} failure mode to be written (event {event_id})",
        "cause": f"{ATTENTION_MARKER} cause to be written",
        "local_effect": f"{ATTENTION_MARKER} local effect to be written",
        "system_effect": f"{ATTENTION_MARKER} system effect to be written",
        "controls": [],
        "evidence": [],
        "requirement_ids": [],
        "linked_event_ids": [event_id],
        "source": INFERENCE,
        "certainty": "generated from an importance ranking, unconfirmed",
        "inference_note": f"flagged by the importance ranking of a run against {event_id}; every "
                          "descriptive field is unfilled and must be written by a human",
    }


def verify_state_machine(tmp: Path) -> None:
    db = tmp / "state.sqlite"
    rng = random.Random(4711)
    with SqliteRepository(str(db)) as repo:
        raw = random_model(rng, 4, 2)
        raw["model_id"] = "SM"
        model = validate_model(raw)
        solution = solve_model(model)
        repo.record_run(
            run_id="sm-1", model=model, solution=solution,
            payload={
                "schema_version": "0.1.0", "model_schema_version": model.schema_version,
                "run_id": "sm-1", "model_id": "SM", "engine_version": "0.1.0",
                "semantic_model_hash": semantic_model_hash(model), "status": "succeeded",
                "top_probability": str(solution.top_probability), "cut_sets_complete": True,
                "importance_status": "not_requested",
                "engine_stats": {"top_probability_exact": str(solution.top_probability)},
            },
            actor=HUMAN,
        )
        baseline = repo.current_baseline_hash("SM")
        events = sorted(repo.baseline_event_ids("SM"))
        first, second = events[0], events[-1]

        # a draft alone never reaches the official table
        repo.propose_fmea_candidate(
            candidate_id="D1", raw_row=row_payload("SM", "F-001", (first, second)),
            expected_baseline_hash=baseline, proposed_by="agent",
        )
        if repo.list_fmea_rows():
            fail("state: a draft alone reached the official table")

        # an agent can never decide or apply, and a refusal changes nothing
        expect_refusal(
            "state: agent decides", "APPROVAL_AUTHORITY",
            lambda: repo.decide_fmea_candidate("D1", approve=True, reviewer="agent"),
        )
        expect_refusal(
            "state: agent applies", "APPROVAL_AUTHORITY",
            lambda: repo.apply_fmea_candidate("D1", reviewer="claude"),
        )
        expect_refusal(
            "state: apply before approval", "FMEA_STATE",
            lambda: repo.apply_fmea_candidate("D1", reviewer=HUMAN),
        )
        if repo.get_fmea_candidate("D1")["state"] != "proposed":
            fail("state: a refused decision mutated the draft")

        # an invalid draft is recorded and stays unapprovable
        invalid = repo.propose_fmea_candidate(
            candidate_id="D2", raw_row=row_payload("SM", "F-002", (first,), source="inference"),
            expected_baseline_hash=baseline, proposed_by="agent",
        )
        if invalid["state"] != "invalid" or invalid["validation_code"] != "FMEA_SOURCE":
            fail(f"state: inference without a note was not recorded invalid ({invalid['state']!r})")
        expect_refusal(
            "state: approve an invalid draft", "FMEA_STATE",
            lambda: repo.decide_fmea_candidate("D2", approve=True, reviewer=HUMAN),
        )

        # the happy path, then decide/apply are single-step
        repo.decide_fmea_candidate("D1", approve=True, reviewer=HUMAN)
        expect_refusal(
            "state: decide twice", "FMEA_STATE",
            lambda: repo.decide_fmea_candidate("D1", approve=True, reviewer=HUMAN),
        )
        _, promoted = repo.apply_fmea_candidate("D1", reviewer=HUMAN)
        if promoted["version"] != 1:
            fail(f"state: first promotion produced version {promoted['version']}")
        expect_refusal(
            "state: apply twice", "FMEA_STATE",
            lambda: repo.apply_fmea_candidate("D1", reviewer=HUMAN),
        )

        # a revision must name the version it revises
        expect_refusal(
            "state: revision without a target", "FMEA_REVISION_CONFLICT",
            lambda: repo.propose_fmea_candidate(
                candidate_id="D3", raw_row=row_payload("SM", "F-001", (first,)),
                expected_baseline_hash=baseline, proposed_by="agent",
            ),
        )
        expect_refusal(
            "state: revision with a wrong target", "FMEA_REVISION_CONFLICT",
            lambda: repo.propose_fmea_candidate(
                candidate_id="D3", raw_row=row_payload("SM", "F-001", (first,)),
                expected_baseline_hash=baseline, proposed_by="agent", expected_version=7,
            ),
        )
        expect_refusal(
            "state: a first row claims a revision target", "FMEA_REVISION_CONFLICT",
            lambda: repo.propose_fmea_candidate(
                candidate_id="D4", raw_row=row_payload("SM", "F-099", (first,)),
                expected_baseline_hash=baseline, proposed_by="agent", expected_version=1,
            ),
        )

        # a stale approval cannot be applied once a newer revision has landed
        for candidate_id in ("D5", "D6"):
            repo.propose_fmea_candidate(
                candidate_id=candidate_id,
                raw_row=row_payload("SM", "F-001", (first, second), cause=f"cause {candidate_id}"),
                expected_baseline_hash=baseline, proposed_by="agent", expected_version=1,
            )
            repo.decide_fmea_candidate(candidate_id, approve=True, reviewer=HUMAN)
        repo.apply_fmea_candidate("D6", reviewer=HUMAN)
        expect_refusal(
            "state: apply a superseded approval", "FMEA_REVISION_CONFLICT",
            lambda: repo.apply_fmea_candidate("D5", reviewer=HUMAN),
        )

        # a pending approval dies when the baseline moves
        repo.propose_fmea_candidate(
            candidate_id="D7", raw_row=row_payload("SM", "F-003", (first,)),
            expected_baseline_hash=repo.current_baseline_hash("SM"), proposed_by="agent",
        )
        repo.decide_fmea_candidate("D7", approve=True, reviewer=HUMAN)
        revised = json.loads(json.dumps(raw))
        for index, event in enumerate(revised["basic_events"]):
            event["probability"] = "0.02" if event["probability"] != "0.02" else "0.03"
            if index == 0:
                event["label"] = "edited"
        moved = validate_model(revised)
        repo.register_model_baseline(
            moved, actor=HUMAN, expected_baseline_hash=repo.current_baseline_hash("SM")
        )
        expect_refusal(
            "state: apply an approval from before a baseline move", "BASELINE_CONFLICT",
            lambda: repo.apply_fmea_candidate("D7", reviewer=HUMAN),
        )

        # attention items: a ranking may drive DRAFTS, never facts
        atn_id = f"FMEA-ATTN-{first}"
        atn = repo.propose_fmea_candidate(
            candidate_id="ATT-1", raw_row=attention_row("SM", first),
            expected_baseline_hash=repo.current_baseline_hash("SM"), proposed_by="agent",
        )
        bump("attention_drafts")
        if atn["state"] != "proposed":
            fail(f"state: a marked attention draft was not accepted as a draft ({atn['state']!r})")
        if any(r["fmea_id"] == atn_id for r in repo.list_fmea_rows("SM")):
            fail("state: a draft alone put an attention row in the official table")
        repo.decide_fmea_candidate("ATT-1", approve=True, reviewer=HUMAN)
        expect_refusal(
            "state: promote an unfilled attention work item", "FMEA_PLACEHOLDER",
            lambda: repo.apply_fmea_candidate("ATT-1", reviewer=HUMAN),
        )
        if repo.get_fmea_row("SM", atn_id) is not None:
            fail("state: a refused attention promotion still wrote a row")
        # the generator reads this to stay quiet instead of nagging: the state of
        # an existing draft must be visible for the same fmea_id
        if repo.drafted_fmea_states("SM").get(atn_id) != "approved":
            fail("state: an existing attention draft is invisible to the generator")
        if repo.verify() != []:
            fail(f"state: attention drafts made verify() unclean: {repo.verify()}")

        # unknown subjects and malformed input are refused, never coerced
        expect_refusal(
            "state: decide an unknown candidate", "FMEA_NOT_FOUND",
            lambda: repo.decide_fmea_candidate("NOPE", approve=True, reviewer=HUMAN),
        )
        expect_refusal(
            "state: apply an unknown candidate", "FMEA_NOT_FOUND",
            lambda: repo.apply_fmea_candidate("NOPE", reviewer=HUMAN),
        )
        expect_refusal(
            "state: draft for a model with no baseline", "MODEL_NOT_FOUND",
            lambda: repo.propose_fmea_candidate(
                candidate_id="D8", raw_row=row_payload("NOT_IN_STORE", "F-001", (first,)),
                expected_baseline_hash="0" * 64, proposed_by="agent",
            ),
        )
        expect_refusal(
            "state: a draft that is not an object", "STORE_BAD_ARGUMENT",
            lambda: repo.propose_fmea_candidate(
                candidate_id="D9", raw_row=["not", "a", "row"],
                expected_baseline_hash=repo.current_baseline_hash("SM"), proposed_by="agent",
            ),
        )


# --------------------------------------------------------------------------
# Part 6 — round trip
# --------------------------------------------------------------------------


def verify_round_trip(source: Path, tmp: Path, expected_dangling: list[dict]) -> None:
    with SqliteRepository(str(source)) as repo:
        dump = repo.dump()
        problems = repo.verify()
    for problem in problems:
        fail(f"round trip: the population does not verify before dumping: {problem}")

    restored_path = tmp / "restored.sqlite"
    with SqliteRepository.restore(str(restored_path), dump) as other:
        if other.dump()["tables"] != dump["tables"]:
            fail("round trip: the restored dump differs from the original")
        restored_problems = other.verify()
    for problem in restored_problems:
        fail(f"round trip: {problem}")

    # The traceability gap must survive the round trip identically: a gap that
    # disappears when the data moves is a gap that was never really recorded.
    restored_dangling = audit_population(restored_path, "restored", count_coverage=False)
    if restored_dangling != expected_dangling:
        fail(
            "round trip: the dangling-link set changed across dump/restore "
            f"({len(expected_dangling)} -> {len(restored_dangling)})"
        )


# --------------------------------------------------------------------------
# population
# --------------------------------------------------------------------------


def build_population(tmp: Path) -> Path:
    """A store with human / inference / import rows, revisions, and a gap.

    Built so the coverage counters cannot be vacuous: at least one event is
    cited by two rows, at least one row cites two events, at least one
    machine-inferred row exists, at least one revision exists, and a baseline
    move leaves at least one dangling link.
    """
    db = tmp / "population.sqlite"
    rng = random.Random(20260921)
    with SqliteRepository(str(db)) as repo:
        for index in range(MODELS):
            raw = random_model(rng, 5, 3)
            raw["model_id"] = f"FMEA_M{index:02d}"
            model = validate_model(raw)
            solution = solve_model(model)
            run_id = f"fmea-{index:02d}"
            repo.record_run(
                run_id=run_id, model=model, solution=solution,
                payload={
                    "schema_version": "0.1.0", "model_schema_version": model.schema_version,
                    "run_id": run_id, "model_id": model.model_id, "engine_version": "0.1.0",
                    "semantic_model_hash": semantic_model_hash(model), "status": "succeeded",
                    "top_probability": str(solution.top_probability),
                    "minimal_cut_sets": solution.minimal_cut_sets, "cut_sets_complete": True,
                    "importance_status": "not_requested",
                    "engine_stats": {"top_probability_exact": str(solution.top_probability)},
                },
                actor=HUMAN,
            )
            model_id = model.model_id
            COVERAGE["models"] += 1
            baseline = repo.current_baseline_hash(model_id)
            events = sorted(repo.baseline_event_ids(model_id))

            rows = [
                ("F-001", events[:2], "human", None),
                ("F-002", events[:1], "inference", rng.choice(
                    ["extracted from the maintenance log; needs bench confirmation",
                     "proposed from a similar component; failure mode not confirmed"])),
                ("F-003", events[:3], "import", None),
            ]
            for fmea_id, links, source, note in rows:
                candidate_id = f"{model_id}-{fmea_id}-1"
                repo.propose_fmea_candidate(
                    candidate_id=candidate_id,
                    raw_row=row_payload(model_id, fmea_id, links, source=source, note=note),
                    expected_baseline_hash=baseline, proposed_by="agent",
                    note="drafted offline",
                )
                repo.decide_fmea_candidate(candidate_id, approve=True, reviewer=HUMAN)
                repo.apply_fmea_candidate(candidate_id, reviewer=HUMAN)

            # a revision on the first row of the first two models -> superseded rows
            if index < 2:
                candidate_id = f"{model_id}-F-001-2"
                repo.propose_fmea_candidate(
                    candidate_id=candidate_id,
                    raw_row=row_payload(
                        model_id, "F-001", events[-2:], cause="revised after review"
                    ),
                    expected_baseline_hash=baseline, proposed_by="agent", expected_version=1,
                )
                repo.decide_fmea_candidate(candidate_id, approve=True, reviewer=HUMAN)
                repo.apply_fmea_candidate(candidate_id, reviewer=HUMAN)

            # one rejected draft and one invalid draft per store
            if index == 3:
                repo.propose_fmea_candidate(
                    candidate_id=f"{model_id}-REJ", raw_row=row_payload(model_id, "F-009", events[:1]),
                    expected_baseline_hash=baseline, proposed_by="agent",
                )
                repo.decide_fmea_candidate(f"{model_id}-REJ", approve=False, reviewer=HUMAN,
                                           note="not a credible mode")
                repo.propose_fmea_candidate(
                    candidate_id=f"{model_id}-BAD",
                    raw_row=row_payload(model_id, "F-010", events[:1], source="inference"),
                    expected_baseline_hash=baseline, proposed_by="agent",
                )

            # a draft left pending
            if index == 4:
                repo.propose_fmea_candidate(
                    candidate_id=f"{model_id}-PEND", raw_row=row_payload(model_id, "F-011", events[:1]),
                    expected_baseline_hash=baseline, proposed_by="agent",
                )

        # Move one model's baseline so its links stop resolving. The replacement
        # model is built by hand rather than pruned from the random one: pruning
        # can silently invalidate a K_OF_N gate, and the point here is the link,
        # not the fault tree.
        target = "FMEA_M00"
        current = repo.current_baseline_hash(target)
        moved = validate_model(
            {
                "schema_version": "0.1.0",
                "model_id": target,
                "baseline_id": "moved",
                "assumptions": {
                    "basic_events_independent": True,
                    "probability_semantics": "fixed_conditioned_probability",
                    "condition": "verification population: the baseline moved to orphan a link",
                },
                "basic_events": [
                    {"id": f"KEPT{i}", "label": f"kept {i}", "probability": "0.01",
                     "source": "synthetic"}
                    for i in range(3)
                ],
                "gates": [{"id": "TOP", "kind": "OR", "inputs": ["KEPT0", "KEPT1", "KEPT2"]}],
                "top_event": "TOP",
            }
        )
        repo.register_model_baseline(moved, actor=HUMAN, expected_baseline_hash=current)

    return db


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nsa_fmea_verify_"))
    population = build_population(tmp)

    before = len(PROBLEMS)
    dangling = audit_population(population, "population")
    population_problems = len(PROBLEMS) - before

    verify_state_machine(tmp)
    verify_round_trip(population, tmp, dangling)
    print("mutation check (the checks above must be able to fail)")
    verify_teeth(population, tmp)
    print()

    print("fmea verification")
    print(f"  models                : {COVERAGE['models']} with a recorded baseline")
    print(f"  official rows         : {COVERAGE['official_rows']} current, "
          f"{COVERAGE['superseded_rows']} superseded (all re-hashed from raw columns)")
    print(f"  links checked         : {COVERAGE['links']} "
          f"({COVERAGE['resolved_links']} resolved, {len(dangling)} dangling — reported, not repaired)")
    print(f"  many-to-many proof    : {COVERAGE['events_cited_by_many_rows']} events cited by >1 row, "
          f"{COVERAGE['rows_citing_many_events']} rows citing >1 event")
    print(f"  machine-inferred rows : {COVERAGE['inference_rows']} (each still states what is unconfirmed)")
    print(f"  attention drafts      : {COVERAGE['attention_drafts']} marked work items "
          "(refused promotion, custom text)")
    print(f"  candidates            : {COVERAGE['candidate_rows']} "
          f"({COVERAGE['applied_candidates']} applied, {COVERAGE['rejected_candidates']} rejected, "
          f"{COVERAGE['invalid_candidates']} invalid, {COVERAGE['pending_candidates']} pending)")
    print(f"  storage values scanned: {COVERAGE['storage_value_scans']} (SQLite typeof — zero REAL expected)")
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
        key for key in (
            "official_rows", "superseded_rows", "candidate_rows", "links",
            "events_cited_by_many_rows", "rows_citing_many_events", "inference_rows",
            "applied_candidates", "rejected_candidates", "invalid_candidates",
            "pending_candidates", "resolved_links", "storage_value_scans", "attention_drafts",
        )
        if COVERAGE.get(key, 0) == 0
    ]
    if nonzero:
        print(f"\nVACUOUS: these counters are zero, so the check proved nothing: {nonzero}")
        return 1
    if not dangling:
        print("\nVACUOUS: a baseline move produced no dangling link, so Part 4 proved nothing")
        return 1
    if len(COVERAGE["refusals"]) < 7:
        print(f"\nVACUOUS: only {len(COVERAGE['refusals'])} distinct refusal codes exercised")
        return 1
    if "FMEA_PLACEHOLDER" not in COVERAGE["refusals"]:
        print("\nVACUOUS: the attention-placeholder guard was never exercised, so Part 5 proved nothing")
        return 1
    print("\nfmea verification: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
