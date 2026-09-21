"""SQLite schema for the local store, with an explicit migration registry.

Design rules taken from B_核心规划:
  - §88  single-file SQLite on a workstation; migrate to PostgreSQL with
         migration scripts and round-trip tests from phase one. Relational
         tables are enough to express the initial ontology — deliberately no
         graph database.
  - §201 §203 one writer + optimistic concurrency checks; a baseline is
         NEVER rewritten in place, a change produces a new version, and
         dependent results are flagged stale while old evidence stays
         readable.
  - §231 keep full precision in storage; display rounding must not change a
         stored result. Every probability-ish column is therefore TEXT
         holding an exact decimal or an exact `n/d` rational. There is no
         REAL/FLOAT column in this schema and `audit_no_float_columns()`
         enforces that.
"""
from __future__ import annotations

import sqlite3

STORE_SCHEMA_VERSION = 1

# journal_mode stays at the default (DELETE) so the deliverable remains a
# single .sqlite file with no -wal/-shm sidecars.
_PRAGMAS = ("PRAGMA foreign_keys = ON",)

_MIGRATION_1 = (
    """
    CREATE TABLE schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE models (
        model_id              TEXT PRIMARY KEY,
        baseline_id           TEXT NOT NULL,
        schema_version        TEXT NOT NULL,
        current_baseline_hash TEXT,
        first_seen_utc        TEXT NOT NULL,
        last_seen_utc         TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE baselines (
        baseline_hash          TEXT PRIMARY KEY,
        model_id               TEXT NOT NULL REFERENCES models(model_id),
        schema_version         TEXT NOT NULL,
        top_event              TEXT NOT NULL,
        basic_event_count      INTEGER NOT NULL,
        gate_count             INTEGER NOT NULL,
        probability_semantics  TEXT NOT NULL,
        rate_precision_digits  INTEGER NOT NULL,
        canonical_json         TEXT NOT NULL,
        provenance_json        TEXT NOT NULL,
        created_utc            TEXT NOT NULL,
        created_by             TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE runs (
        run_id                       TEXT PRIMARY KEY,
        model_id                     TEXT NOT NULL REFERENCES models(model_id),
        baseline_hash                TEXT NOT NULL REFERENCES baselines(baseline_hash),
        engine_version               TEXT NOT NULL,
        contract_version             TEXT NOT NULL,
        model_schema_version         TEXT NOT NULL,
        status                       TEXT NOT NULL,
        top_event_probability        TEXT,
        top_event_probability_kind   TEXT NOT NULL,
        probability_interpretation   TEXT,
        cut_sets_json                TEXT NOT NULL,
        cut_sets_complete            INTEGER NOT NULL,
        minimal_cut_set_count        INTEGER NOT NULL,
        bdd_node_count               INTEGER,
        variable_count               INTEGER,
        importance_status            TEXT NOT NULL,
        content_fingerprint          TEXT NOT NULL,
        stale                        INTEGER NOT NULL DEFAULT 0,
        stale_reason                 TEXT,
        created_utc                  TEXT NOT NULL,
        created_by                   TEXT NOT NULL,
        payload_json                 TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE importance (
        run_id                 TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        event_id               TEXT NOT NULL,
        rank_in_run            INTEGER NOT NULL,
        in_support             INTEGER NOT NULL,
        event_probability      TEXT NOT NULL,
        top_given_failed       TEXT NOT NULL,
        top_given_working      TEXT NOT NULL,
        birnbaum_importance    TEXT NOT NULL,
        fussell_vesely         TEXT,
        risk_achievement_worth TEXT,
        risk_reduction_worth   TEXT,
        undefined_json         TEXT,
        PRIMARY KEY (run_id, event_id)
    )
    """,
    """
    CREATE TABLE reviews (
        review_id              TEXT PRIMARY KEY,
        model_id               TEXT NOT NULL,
        expected_baseline_hash TEXT NOT NULL,
        observed_baseline_hash TEXT NOT NULL,
        proposed_hash          TEXT,
        proposed_canonical_json TEXT,
        state                  TEXT NOT NULL,
        validation_code        TEXT,
        validation_message     TEXT,
        note                   TEXT,
        proposed_by            TEXT NOT NULL,
        created_utc            TEXT NOT NULL,
        decided_by             TEXT,
        decided_utc            TEXT,
        applied_baseline_hash  TEXT
    )
    """,
    "CREATE INDEX idx_baselines_model ON baselines(model_id)",
    "CREATE INDEX idx_runs_model ON runs(model_id)",
    "CREATE INDEX idx_runs_baseline ON runs(baseline_hash)",
    "CREATE INDEX idx_runs_stale ON runs(stale)",
    "CREATE INDEX idx_importance_run ON importance(run_id)",
    "CREATE INDEX idx_reviews_model ON reviews(model_id)",
    "CREATE INDEX idx_reviews_state ON reviews(state)",
)

MIGRATIONS: dict[int, tuple[str, ...]] = {1: _MIGRATION_1}


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'"
    ).fetchone()
    if has_meta is None:
        return 0
    row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    return int(row["value"]) if row else 0


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Bring the database up to STORE_SCHEMA_VERSION. Returns the final version."""
    version = current_version(conn)
    for target in sorted(MIGRATIONS):
        if target <= version:
            continue
        for statement in MIGRATIONS[target]:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(target),),
        )
        version = target
    conn.commit()
    return version


def audit_no_float_columns(conn: sqlite3.Connection) -> list[str]:
    """Return a list of declared columns whose type affinity could hold a float.

    Part of §231 enforcement: a stored result must never round-trip through
    binary floating point. Declared types are checked, because SQLite's
    dynamic typing would otherwise let a REAL value land in a NUMERIC column.
    """
    offenders: list[str] = []
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for table in tables:
        name = table["name"]
        for column in conn.execute(f"PRAGMA table_info({name})"):
            declared = (column["type"] or "").upper()
            # BLOB is byte storage, not float storage; INTEGER/TEXT are safe.
            if any(token in declared for token in ("REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL")):
                offenders.append(f"{name}.{column['name']} declared {declared}")
    return offenders
