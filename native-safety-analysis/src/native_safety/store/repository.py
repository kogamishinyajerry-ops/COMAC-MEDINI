"""SQLite repository: baselines, runs, importance, reviews.

Storage discipline (B_核心规划 §201-203, §231):
  - one writer, optimistic concurrency: a caller that names an expected
    baseline hash is refused if the stored one moved;
  - a baseline is immutable. A model change registers a NEW baseline hash and
    the dependent runs are flagged stale; their payloads stay readable;
  - `run_id` is idempotent: re-recording identical content is a no-op, and
    re-recording different content under the same id is refused, never an
    overwrite;
  - every stored number is an exact decimal or an exact `n/d` rational.

This layer does no file IO and no validation: it receives already-validated
domain objects and already-computed results. Front ends (CLI today, API/UI
later) own the orchestration.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction

from ..domain.model import StaticFtaModel
from ..domain.ratnum import exact_decimal, exact_decimal_or_fraction
from ..domain.semantic_hash import (
    canonical_model_form,
    hash_canonical_form,
    model_provenance,
    semantic_model_hash,
)
from . import errors
from .errors import StoreError
from .schema import (
    STORE_SCHEMA_VERSION,
    apply_migrations,
    audit_no_float_columns,
    connect,
    current_version,
)

# Identities that may propose but never approve. B_核心规划 §181: an agent may
# not approve failure-condition classification, independence or applicability.
NON_APPROVING_IDENTITIES = frozenset(
    {"", "agent", "assistant", "ai", "bot", "claude", "copilot", "local-cli", "unknown", "none"}
)

RUN_STATUSES = frozenset({"succeeded", "failed", "unsupported", "resource_limited"})
REVIEW_STATES = ("proposed", "invalid", "approved", "rejected", "applied")

# Table -> columns forming a stable dump order (primary keys first).
_DUMP_ORDER = {
    "schema_meta": ("key",),
    "models": ("model_id",),
    "baselines": ("baseline_hash",),
    "runs": ("run_id",),
    "importance": ("run_id", "event_id"),
    "reviews": ("review_id",),
}

IMPORTANCE_SORT_KEYS = (
    "fussell_vesely",
    "birnbaum_importance",
    "risk_achievement_worth",
    "risk_reduction_worth",
    "event_probability",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _exact(value: Fraction | None) -> str | None:
    """Exact text for a stored number. Never a float, never display-rounded."""
    return None if value is None else exact_decimal_or_fraction(value)


def _rendering_kind(value: Fraction) -> str:
    """'exact_decimal' when the value has a terminating decimal, else 'exact_rational'."""
    return "exact_decimal" if exact_decimal(value) is not None else "exact_rational"


def _assert_approver(actor: str | None, what: str) -> str:
    name = (actor or "").strip()
    if name.lower() in NON_APPROVING_IDENTITIES:
        raise StoreError(
            errors.APPROVAL_AUTHORITY,
            f"{what} requires an explicit human reviewer; {name or '(none given)'!r} has no "
            "approval authority. An agent may propose a change but never approve or apply it.",
        )
    return name


@dataclass(frozen=True)
class RecordOutcome:
    run_id: str
    created: bool
    reason: str
    baseline_hash: str
    fingerprint: str
    stale_run_ids: tuple[str, ...] = field(default=())


class SqliteRepository:
    """Single-writer repository over one SQLite file."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = connect(path)
        version = apply_migrations(self.conn)
        if version != STORE_SCHEMA_VERSION:
            self.conn.close()  # do not leak a handle on a store we refuse to use
            raise StoreError(
                errors.SCHEMA_MISMATCH,
                f"store at {path!r} is schema v{version}; this engine writes v{STORE_SCHEMA_VERSION}",
            )

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> SqliteRepository:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ baselines
    def model_row(self, model_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM models WHERE model_id=?", (model_id,)).fetchone()
        return dict(row) if row else None

    def current_baseline_hash(self, model_id: str) -> str | None:
        row = self.model_row(model_id)
        return row["current_baseline_hash"] if row else None

    def current_baseline(self, model_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT b.* FROM baselines b JOIN models m ON m.current_baseline_hash = b.baseline_hash "
            "WHERE m.model_id=?",
            (model_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_baselines(self, model_id: str | None = None) -> list[dict]:
        if model_id is None:
            rows = self.conn.execute("SELECT * FROM baselines ORDER BY model_id, created_utc").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM baselines WHERE model_id=? ORDER BY created_utc", (model_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def register_model_baseline(
        self,
        model: StaticFtaModel,
        *,
        actor: str = "local-cli",
        expected_baseline_hash: str | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        """Register `model`'s semantics as the current baseline for its model_id.

        Immutable by construction: the baseline row is inserted once and never
        rewritten (§203). Returns (baseline_hash, run_ids_newly_marked_stale).
        """
        with self.conn:
            return self._register_baseline_locked(
                model, actor=actor, expected_baseline_hash=expected_baseline_hash
            )

    def _register_baseline_locked(
        self,
        model: StaticFtaModel,
        *,
        actor: str,
        expected_baseline_hash: str | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        """Body of `register_model_baseline`; assumes a transaction is OPEN.

        Split out so `record_run` can register-and-record atomically: a refused
        run must not leave a freshly created model/baseline behind.
        """
        baseline_hash = semantic_model_hash(model)
        canonical = canonical_model_form(model)
        provenance = model_provenance(model)
        now = _utc_now()

        existing = self.conn.execute(
            "SELECT * FROM baselines WHERE baseline_hash=?", (baseline_hash,)
        ).fetchone()
        if existing is None:
            self.conn.execute(
                "INSERT INTO models(model_id, baseline_id, schema_version, current_baseline_hash,"
                " first_seen_utc, last_seen_utc)"
                " VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(model_id) DO UPDATE SET"
                " baseline_id=excluded.baseline_id,"
                " schema_version=excluded.schema_version,"
                " last_seen_utc=excluded.last_seen_utc",
                (model.model_id, model.baseline_id, model.schema_version, None, now, now),
            )
            self.conn.execute(
                "INSERT INTO baselines(baseline_hash, model_id, schema_version, top_event,"
                " basic_event_count, gate_count, probability_semantics, rate_precision_digits,"
                " canonical_json, provenance_json, created_utc, created_by)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    baseline_hash, model.model_id, model.schema_version, model.top_event,
                    len(model.basic_events), len(model.gates),
                    model.assumptions.probability_semantics, model.rate_precision_digits,
                    json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                    json.dumps(provenance, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                    now, actor,
                ),
            )
        else:
            # Same hash under a different model_id would be a canonicalization
            # defect; refuse rather than merge two models into one baseline.
            if existing["model_id"] != model.model_id:
                raise StoreError(
                    errors.INTEGRITY,
                    f"baseline {baseline_hash[:12]}… already belongs to model "
                    f"{existing['model_id']!r}, not {model.model_id!r}",
                )
            self.conn.execute(
                "UPDATE models SET last_seen_utc=?, baseline_id=?, schema_version=? WHERE model_id=?",
                (now, model.baseline_id, model.schema_version, model.model_id),
            )

        row = self.conn.execute(
            "SELECT current_baseline_hash FROM models WHERE model_id=?", (model.model_id,)
        ).fetchone()
        current = row["current_baseline_hash"]

        if current == baseline_hash:
            return baseline_hash, ()

        if current is not None:
            if expected_baseline_hash is None:
                raise StoreError(
                    errors.BASELINE_CONFLICT,
                    f"model {model.model_id!r} is anchored at baseline {current[:12]}…; "
                    "re-anchoring requires an explicit expected_baseline_hash",
                )
            if expected_baseline_hash != current:
                raise StoreError(
                    errors.BASELINE_CONFLICT,
                    f"expected baseline {expected_baseline_hash[:12]}… but the store holds "
                    f"{current[:12]}… for model {model.model_id!r}",
                )
        elif expected_baseline_hash:
            raise StoreError(
                errors.BASELINE_CONFLICT,
                f"expected baseline {expected_baseline_hash[:12]}… but model "
                f"{model.model_id!r} has no baseline yet",
            )

        self.conn.execute(
            "UPDATE models SET current_baseline_hash=? WHERE model_id=?",
            (baseline_hash, model.model_id),
        )
        self.conn.execute(
            "UPDATE runs SET stale=1, stale_reason=? WHERE model_id=? AND baseline_hash<>? AND stale=0",
            (f"baseline superseded by {baseline_hash} on {now}", model.model_id, baseline_hash),
        )
        return baseline_hash, self._stale_run_ids(model.model_id)

    def _stale_run_ids(self, model_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                r["run_id"]
                for r in self.conn.execute(
                    "SELECT run_id FROM runs WHERE model_id=? AND stale=1", (model_id,)
                )
            )
        )

    # ------------------------------------------------------------------ runs
    def record_run(
        self,
        *,
        run_id: str,
        model: StaticFtaModel,
        solution,
        payload: dict,
        actor: str = "local-cli",
        calculation_mode: str = "exact_bdd_shannon_fraction",
    ) -> RecordOutcome:
        """Persist one analysis result. Idempotent on identical content.

        `solution` may be None for a run that did not produce a result
        (validation/internal failure); the run is still recorded honestly.

        One transaction covers "first sighting" baseline registration AND the
        run insert, so a refused run never leaves a model/baseline behind.
        """
        current = self.current_baseline_hash(model.model_id)
        row = self._run_row(
            run_id=run_id, model=model, solution=solution, payload=payload,
            baseline_hash=semantic_model_hash(model), actor=actor,
            calculation_mode=calculation_mode,
        )
        with self.conn:
            if current is None:
                baseline_hash, _ = self._register_baseline_locked(model, actor=actor)
            else:
                baseline_hash = row["baseline_hash"]
                if current != baseline_hash:
                    raise StoreError(
                        errors.BASELINE_NOT_CURRENT,
                        f"model {model.model_id!r} is anchored at baseline {current[:12]}… but this "
                        f"model hashes to {baseline_hash[:12]}…. A changed model must be approved "
                        "through the review loop (propose -> decide -> apply) before its results are "
                        "recorded; a re-run must not silently move the baseline.",
                    )
            row["baseline_hash"] = baseline_hash

            existing = self.conn.execute(
                "SELECT content_fingerprint FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing is not None:
                if existing["content_fingerprint"] == row["content_fingerprint"]:
                    return RecordOutcome(
                        run_id=run_id, created=False, reason="identical re-run (idempotent no-op)",
                        baseline_hash=baseline_hash, fingerprint=row["content_fingerprint"],
                    )
                raise StoreError(
                    errors.RUN_ID_CONFLICT,
                    f"run_id {run_id!r} already exists with different content "
                    f"(stored {existing['content_fingerprint'][:12]}…, new "
                    f"{row['content_fingerprint'][:12]}…). Run ids are immutable; choose another id.",
                )
            self.conn.execute(
                "INSERT INTO runs(run_id, model_id, baseline_hash, engine_version, contract_version,"
                " model_schema_version, status, top_event_probability, top_event_probability_kind,"
                " probability_interpretation, cut_sets_json, cut_sets_complete, minimal_cut_set_count,"
                " bdd_node_count, variable_count, importance_status, content_fingerprint, stale,"
                " stale_reason, created_utc, created_by, payload_json)"
                " VALUES(:run_id,:model_id,:baseline_hash,:engine_version,:contract_version,"
                ":model_schema_version,:status,:top_event_probability,:top_event_probability_kind,"
                ":probability_interpretation,:cut_sets_json,:cut_sets_complete,:minimal_cut_set_count,"
                ":bdd_node_count,:variable_count,:importance_status,:content_fingerprint,0,NULL,"
                ":created_utc,:created_by,:payload_json)",
                row,
            )
            for entry in self._importance_rows(run_id, solution):
                self.conn.execute(
                    "INSERT INTO importance(run_id, event_id, rank_in_run, in_support,"
                    " event_probability, top_given_failed, top_given_working, birnbaum_importance,"
                    " fussell_vesely, risk_achievement_worth, risk_reduction_worth, undefined_json)"
                    " VALUES(:run_id,:event_id,:rank_in_run,:in_support,:event_probability,"
                    ":top_given_failed,:top_given_working,:birnbaum_importance,:fussell_vesely,"
                    ":risk_achievement_worth,:risk_reduction_worth,:undefined_json)",
                    entry,
                )
        return RecordOutcome(
            run_id=run_id, created=True, reason="recorded",
            baseline_hash=baseline_hash, fingerprint=row["content_fingerprint"],
        )

    def _run_row(self, *, run_id, model, solution, payload, baseline_hash, actor, calculation_mode) -> dict:
        importance = list(getattr(solution, "importance", None) or [])
        ranked = _rank(importance)
        top = getattr(solution, "top_probability", None) if solution is not None else None
        if top is None:
            top = Fraction(payload["top_probability"]) if payload.get("top_probability") else None
        cut_sets = getattr(solution, "minimal_cut_sets", None) if solution is not None else None
        if cut_sets is None:
            cut_sets = payload.get("minimal_cut_sets") or []
        complete = getattr(solution, "cut_sets_complete", None) if solution is not None else None
        if complete is None:
            complete = bool(payload.get("cut_sets_complete"))

        material = {
            "run_id": run_id,
            "baseline_hash": baseline_hash,
            "engine_version": payload.get("engine_version"),
            "contract_version": payload.get("schema_version"),
            "model_schema_version": payload.get("model_schema_version"),
            "calculation_mode": calculation_mode,
            "status": payload.get("status"),
            "top_event_probability": _exact(top),
            "cut_sets": cut_sets,
            "cut_sets_complete": complete,
            "importance_status": payload.get("importance_status"),
            "importance": [
                [r.event_id, r.in_support, _exact(r.probability), _exact(r.top_given_failed),
                 _exact(r.top_given_working), _exact(r.birnbaum), _exact(r.fussell_vesely),
                 _exact(r.risk_achievement_worth), _exact(r.risk_reduction_worth)]
                for r in ranked
            ],
        }
        fingerprint = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        return {
            "run_id": run_id,
            "model_id": model.model_id,
            "baseline_hash": baseline_hash,
            "engine_version": payload.get("engine_version") or "",
            "contract_version": payload.get("schema_version") or "",
            "model_schema_version": payload.get("model_schema_version") or model.schema_version,
            "status": payload.get("status") or "unknown",
            "top_event_probability": _exact(top),
            "top_event_probability_kind": _rendering_kind(top) if top is not None else "unavailable",
            "probability_interpretation": payload.get("probability_interpretation"),
            "cut_sets_json": json.dumps(cut_sets, ensure_ascii=False),
            "cut_sets_complete": 1 if complete else 0,
            "minimal_cut_set_count": len(cut_sets),
            "bdd_node_count": (payload.get("engine_stats") or {}).get("bdd_node_count"),
            "variable_count": len((payload.get("engine_stats") or {}).get("variable_order") or []),
            "importance_status": payload.get("importance_status") or "not_requested",
            "content_fingerprint": fingerprint,
            "created_utc": _utc_now(),
            "created_by": actor,
            "payload_json": json.dumps(payload, ensure_ascii=False),
        }

    def _importance_rows(self, run_id: str, solution) -> list[dict]:
        records = list(getattr(solution, "importance", None) or [])
        rows = []
        for index, record in enumerate(_rank(records)):
            rows.append(
                {
                    "run_id": run_id,
                    "event_id": record.event_id,
                    "rank_in_run": index,
                    "in_support": 1 if record.in_support else 0,
                    "event_probability": _exact(record.probability),
                    "top_given_failed": _exact(record.top_given_failed),
                    "top_given_working": _exact(record.top_given_working),
                    "birnbaum_importance": _exact(record.birnbaum),
                    "fussell_vesely": _exact(record.fussell_vesely),
                    "risk_achievement_worth": _exact(record.risk_achievement_worth),
                    "risk_reduction_worth": _exact(record.risk_reduction_worth),
                    "undefined_json": (
                        json.dumps([{"measure": m, "reason": r} for m, r in record.undefined])
                        if record.undefined
                        else None
                    ),
                }
            )
        return rows

    def get_run(self, run_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, model_id: str | None = None, *, include_stale: bool = True) -> list[dict]:
        sql = "SELECT * FROM runs"
        params: list[object] = []
        clauses = []
        if model_id is not None:
            clauses.append("model_id=?")
            params.append(model_id)
        if not include_stale:
            clauses.append("stale=0")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_utc, run_id"
        return [dict(r) for r in self.conn.execute(sql, params)]

    def run_importance(self, run_id: str, *, by: str = "fussell_vesely", top: int | None = None) -> list[dict]:
        """Importance rows sorted by an exact measure (undefined values last).

        Sorting happens in Python on exact Fractions: the stored text is exact
        but its lexicographic order is not its numeric order.
        """
        if by not in IMPORTANCE_SORT_KEYS:
            raise StoreError(errors.BAD_ARGUMENT, f"unknown importance sort key {by!r}")
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM importance WHERE run_id=?", (run_id,))]
        rows.sort(
            key=lambda r: (
                0 if r[by] is not None else 1,
                -(Fraction(r[by]) if r[by] is not None else Fraction(0)),
                r["event_id"],
            )
        )
        return rows[:top] if top is not None else rows

    # ------------------------------------------------------------------ reviews
    def propose_review(
        self,
        *,
        review_id: str,
        model_id: str,
        expected_baseline_hash: str,
        proposed_model: StaticFtaModel | None,
        proposed_by: str,
        note: str | None = None,
        validation_error: dict | None = None,
    ) -> dict:
        """Record a proposed change. Never applies it (§195, §181)."""
        observed = self.current_baseline_hash(model_id)
        if expected_baseline_hash != observed:
            raise StoreError(
                errors.BASELINE_CONFLICT,
                f"proposal was written against baseline {expected_baseline_hash[:12]}… but the store "
                f"holds {(observed or '(none)')[:12]}… for model {model_id!r}; rebase the proposal",
            )
        now = _utc_now()
        if proposed_model is None:
            failure = validation_error or {"code": errors.INTEGRITY, "message": "no model supplied"}
            state, proposed_hash, canonical_json = "invalid", None, None
            code, message = failure["code"], failure["message"]
        else:
            state = "proposed"
            code = message = None
            proposed_hash = semantic_model_hash(proposed_model)
            canonical_json = json.dumps(
                canonical_model_form(proposed_model), sort_keys=True, separators=(",", ":"),
                ensure_ascii=False,
            )
        with self.conn:
            self.conn.execute(
                "INSERT INTO reviews(review_id, model_id, expected_baseline_hash, observed_baseline_hash,"
                " proposed_hash, proposed_canonical_json, state, validation_code, validation_message,"
                " note, proposed_by, created_utc)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (review_id, model_id, expected_baseline_hash, observed or "", proposed_hash,
                 canonical_json, state, code, message, note, proposed_by, now),
            )
        return self.get_review(review_id)

    def get_review(self, review_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM reviews WHERE review_id=?", (review_id,)).fetchone()
        return dict(row) if row else None

    def list_reviews(self, *, state: str | None = None, model_id: str | None = None) -> list[dict]:
        sql, params, clauses = "SELECT * FROM reviews", [], []
        if state is not None:
            clauses.append("state=?")
            params.append(state)
        if model_id is not None:
            clauses.append("model_id=?")
            params.append(model_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_utc, review_id"
        return [dict(r) for r in self.conn.execute(sql, params)]

    def decide_review(self, review_id: str, *, approve: bool, reviewer: str, note: str | None = None) -> dict:
        """Approve or reject a proposal. Requires an explicit human reviewer."""
        actor = _assert_approver(reviewer, "deciding a review")
        record = self.get_review(review_id)
        if record is None:
            raise StoreError(errors.REVIEW_NOT_FOUND, f"no review {review_id!r}")
        if record["state"] not in ("proposed",):
            raise StoreError(
                errors.REVIEW_STATE,
                f"review {review_id!r} is in state {record['state']!r}; only a 'proposed' review can be decided",
            )
        if approve and record["proposed_hash"] is None:
            raise StoreError(errors.REVIEW_STATE, f"review {review_id!r} carries no valid proposed model")
        target = "approved" if approve else "rejected"
        with self.conn:
            self.conn.execute(
                "UPDATE reviews SET state=?, decided_by=?, decided_utc=?, note=COALESCE(?, note)"
                " WHERE review_id=?",
                (target, actor, _utc_now(), note, review_id),
            )
        return self.get_review(review_id)

    def apply_review(self, review_id: str, *, reviewer: str) -> tuple[dict, tuple[str, ...]]:
        """Apply an approved proposal: register its baseline, flag old runs stale."""
        actor = _assert_approver(reviewer, "applying a review")
        record = self.get_review(review_id)
        if record is None:
            raise StoreError(errors.REVIEW_NOT_FOUND, f"no review {review_id!r}")
        if record["state"] != "approved":
            raise StoreError(
                errors.REVIEW_STATE,
                f"review {review_id!r} is in state {record['state']!r}; only an approved review can be applied",
            )
        expected = record["expected_baseline_hash"]
        observed = self.current_baseline_hash(record["model_id"])
        if expected != observed:
            raise StoreError(
                errors.BASELINE_CONFLICT,
                f"review was approved against baseline {expected[:12]}… but the store now holds "
                f"{(observed or '(none)')[:12]}…; the approval is stale and must be re-decided",
            )
        # Anchor exactly the approved canonical form: re-hash it, so an
        # out-of-band edit of the proposal can never reach the baseline table.
        canonical = json.loads(record["proposed_canonical_json"])
        anchored = hash_canonical_form(canonical)
        if anchored != record["proposed_hash"]:
            raise StoreError(
                errors.INTEGRITY,
                f"stored proposal {review_id!r} no longer hashes to its recorded hash; refusing to apply",
            )
        with self.conn:
            stale_ids = self._anchor_baseline_locked(record["model_id"], anchored, canonical, actor)
            self.conn.execute(
                "UPDATE reviews SET state='applied', applied_baseline_hash=?, decided_by=?, decided_utc=?"
                " WHERE review_id=?",
                (anchored, actor, _utc_now(), review_id),
            )
        return self.get_review(review_id), stale_ids

    def _anchor_baseline_locked(
        self, model_id: str, baseline_hash: str, canonical: dict, actor: str
    ) -> tuple[str, ...]:
        """Move the current baseline to an already-approved canonical form.

        Assumes a transaction is OPEN so the baseline move and the review state
        change commit together.
        """
        now = _utc_now()
        existing = self.conn.execute(
            "SELECT model_id FROM baselines WHERE baseline_hash=?", (baseline_hash,)
        ).fetchone()
        if existing is not None and existing["model_id"] != model_id:
            raise StoreError(
                errors.INTEGRITY,
                f"baseline {baseline_hash[:12]}… belongs to model {existing['model_id']!r}",
            )
        if existing is None:
            events = canonical["basic_events"]
            gates = canonical["gates"]
            self.conn.execute(
                "INSERT INTO baselines(baseline_hash, model_id, schema_version, top_event,"
                " basic_event_count, gate_count, probability_semantics, rate_precision_digits,"
                " canonical_json, provenance_json, created_utc, created_by)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    baseline_hash, model_id, canonical["schema_version"], canonical["top_event"],
                    len(events), len(gates),
                    canonical["assumptions"]["probability_semantics"], 0,
                    json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                    json.dumps({"model_id": model_id, "origin": "review_apply"}, ensure_ascii=False),
                    now, actor,
                ),
            )
        self.conn.execute(
            "UPDATE models SET current_baseline_hash=?, last_seen_utc=? WHERE model_id=?",
            (baseline_hash, now, model_id),
        )
        self.conn.execute(
            "UPDATE runs SET stale=1, stale_reason=? WHERE model_id=? AND baseline_hash<>? AND stale=0",
            (f"baseline superseded by {baseline_hash} on {now}", model_id, baseline_hash),
        )
        return self._stale_run_ids(model_id)

    # ------------------------------------------------------------------ integrity
    def status(self) -> dict:
        counts = {
            table: self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in ("models", "baselines", "runs", "importance", "reviews")
        }
        stale = self.conn.execute("SELECT COUNT(*) AS n FROM runs WHERE stale=1").fetchone()["n"]
        return {
            "store_path": str(self.path),
            "store_schema_version": current_version(self.conn),
            "counts": counts,
            "stale_runs": stale,
            "pending_reviews": self.conn.execute(
                "SELECT COUNT(*) AS n FROM reviews WHERE state IN ('proposed','approved')"
            ).fetchone()["n"],
            "float_columns": audit_no_float_columns(self.conn),
        }

    def verify(self) -> list[str]:
        """Return integrity problems; empty list means the store is self-consistent.

        Checks data, not code: the canonical form of every baseline is
        re-hashed, the two serializations of every result are compared, every
        stored number is re-parsed exactly, and the stored importance values
        are re-derived against the stored top probability using only stored
        numbers.
        """
        problems: list[str] = []

        version = current_version(self.conn)
        if version != STORE_SCHEMA_VERSION:
            problems.append(f"schema version {version} != engine {STORE_SCHEMA_VERSION}")

        for offender in audit_no_float_columns(self.conn):
            problems.append(f"float-capable column: {offender}")

        for row in self.conn.execute("PRAGMA foreign_key_check"):
            problems.append(f"foreign key violation: table={row[0]} rowid={row[1]} parent={row[2]}")

        baselines = {r["baseline_hash"]: dict(r) for r in self.conn.execute("SELECT * FROM baselines")}
        for baseline_hash, row in baselines.items():
            try:
                canonical = json.loads(row["canonical_json"])
            except json.JSONDecodeError as exc:
                problems.append(f"baseline {baseline_hash[:12]}…: canonical_json is not JSON ({exc})")
                continue
            recomputed = hash_canonical_form(canonical)
            if recomputed != baseline_hash:
                problems.append(
                    f"baseline {baseline_hash[:12]}…: canonical_json re-hashes to {recomputed[:12]}…"
                )
            for event in canonical.get("basic_events", []):
                try:
                    Fraction(event["p"])
                except (KeyError, ValueError, ZeroDivisionError):
                    problems.append(
                        f"baseline {baseline_hash[:12]}…: event {event.get('id')!r} p is not exact"
                    )

        current = {
            r["model_id"]: r["current_baseline_hash"]
            for r in self.conn.execute("SELECT model_id, current_baseline_hash FROM models")
        }

        for row in self.conn.execute("SELECT * FROM runs"):
            run = dict(row)
            tag = f"run {run['run_id']!r}"
            if run["baseline_hash"] not in baselines:
                problems.append(f"{tag}: baseline_hash is not in the baselines table")
                continue
            if run["status"] not in RUN_STATUSES:
                problems.append(f"{tag}: unknown status {run['status']!r}")
            try:
                payload = json.loads(run["payload_json"])
            except json.JSONDecodeError as exc:
                problems.append(f"{tag}: payload_json is not JSON ({exc})")
                continue
            if payload.get("run_id") != run["run_id"]:
                problems.append(f"{tag}: payload run_id {payload.get('run_id')!r} disagrees")
            if payload.get("model_id") != run["model_id"]:
                problems.append(f"{tag}: payload model_id {payload.get('model_id')!r} disagrees")
            if payload.get("semantic_model_hash") != run["baseline_hash"]:
                problems.append(f"{tag}: payload semantic_model_hash disagrees with the anchored baseline")

            expected_top = (payload.get("engine_stats") or {}).get("top_probability_exact") or payload.get(
                "top_probability"
            )
            if run["top_event_probability"] is None:
                if expected_top is not None:
                    problems.append(f"{tag}: payload has a top probability but none is stored")
            else:
                try:
                    stored = Fraction(run["top_event_probability"])
                except (ValueError, ZeroDivisionError):
                    problems.append(f"{tag}: stored top probability is not an exact number")
                    stored = None
                if stored is not None:
                    if expected_top is None:
                        problems.append(f"{tag}: top probability stored but absent from the payload")
                    else:
                        try:
                            if Fraction(str(expected_top)) != stored:
                                problems.append(f"{tag}: stored top probability != payload value")
                        except (ValueError, ZeroDivisionError):
                            problems.append(f"{tag}: payload top probability is not an exact number")
                    kind = _rendering_kind(stored)
                    if run["top_event_probability_kind"] != kind:
                        problems.append(
                            f"{tag}: rendering kind {run['top_event_probability_kind']!r} != {kind!r}"
                        )

            try:
                cut_sets = json.loads(run["cut_sets_json"])
            except json.JSONDecodeError:
                problems.append(f"{tag}: cut_sets_json is not JSON")
                cut_sets = None
            if cut_sets is not None:
                if len(cut_sets) != run["minimal_cut_set_count"]:
                    problems.append(f"{tag}: cut set count {run['minimal_cut_set_count']} != {len(cut_sets)}")

            is_current = current.get(run["model_id"]) == run["baseline_hash"]
            if is_current and run["stale"]:
                problems.append(f"{tag}: anchored at the current baseline but flagged stale")
            if not is_current and not run["stale"]:
                problems.append(f"{tag}: superseded baseline but not flagged stale")
            if run["stale"] and not run["stale_reason"]:
                problems.append(f"{tag}: stale without a reason")

            problems.extend(self._verify_importance(run, payload))

        for row in self.conn.execute("SELECT state FROM reviews"):
            if row["state"] not in REVIEW_STATES:
                problems.append(f"review state {row['state']!r} is not a known state")

        return problems

    def _verify_importance(self, run: dict, payload: dict) -> list[str]:
        """Re-derive the stored importance relationships from stored numbers only.

        This is a data-level check: it cannot look at the solver. For every
        event it re-establishes the multilinearity identity
        Q = q_i*q+_i + (1-q_i)*q-_i and the definitions of FV/RAW/RRW.
        """
        problems: list[str] = []
        tag = f"run {run['run_id']!r}"
        rows = [dict(r) for r in self.conn.execute(
            "SELECT * FROM importance WHERE run_id=? ORDER BY rank_in_run", (run["run_id"],)
        )]
        if run["importance_status"] == "computed":
            if not rows:
                problems.append(f"{tag}: importance_status is 'computed' but no importance rows exist")
            elif run["variable_count"] and len(rows) != run["variable_count"]:
                # A deleted row leaves the remaining ones perfectly consistent
                # with each other, so the count is the only thing that catches it.
                problems.append(
                    f"{tag}: {len(rows)} importance rows for {run['variable_count']} variables"
                )
            expected_ranks = list(range(len(rows)))
            actual_ranks = sorted(row["rank_in_run"] for row in rows)
            if actual_ranks != expected_ranks:
                problems.append(f"{tag}: importance ranks are not a contiguous 0..n-1 sequence")

        top_text = run["top_event_probability"]
        if top_text is None:
            return problems
        try:
            top = Fraction(top_text)
        except (ValueError, ZeroDivisionError):
            return problems  # already reported

        for row in rows:
            label = f"{tag}/{row['event_id']!r}"
            try:
                q = Fraction(row["event_probability"])
                q_plus = Fraction(row["top_given_failed"])
                q_minus = Fraction(row["top_given_working"])
                birnbaum = Fraction(row["birnbaum_importance"])
            except (ValueError, ZeroDivisionError):
                problems.append(f"{label}: an exact value failed to parse")
                continue
            if birnbaum != q_plus - q_minus:
                problems.append(f"{label}: BI != q+ - q-")
            if q * q_plus + (1 - q) * q_minus != top:
                problems.append(f"{label}: multilinearity Q = q*q+ + (1-q)*q- violated")

            def optional(column: str) -> Fraction | None:
                return None if row[column] is None else Fraction(row[column])

            for column, expected, guard in (
                ("fussell_vesely", None if top == 0 else q * birnbaum / top, top != 0),
                ("risk_achievement_worth", None if top == 0 else q_plus / top, top != 0),
                ("risk_reduction_worth", None if q_minus == 0 else top / q_minus, q_minus != 0),
            ):
                stored = optional(column)
                if guard and stored != expected:
                    problems.append(f"{label}: {column} != its stored definition")
                if not guard and stored is not None:
                    problems.append(f"{label}: {column} is defined but the definition is undefined")
        return problems

    # ------------------------------------------------------------------ portability
    def dump(self) -> dict:
        """Full JSON dump, ordered deterministically (migration / round-trip)."""
        tables = {
            name: [
                dict(row)
                for row in self.conn.execute(
                    f"SELECT * FROM {name} ORDER BY " + ", ".join(keys)
                )
            ]
            for name, keys in _DUMP_ORDER.items()
        }
        return {"store_schema_version": current_version(self.conn), "tables": tables}

    @classmethod
    def restore(cls, path: str, dump: dict) -> SqliteRepository:
        """Build a repository at `path` from a dump produced by `dump()`."""
        repo = cls(path)
        order = ("models", "baselines", "runs", "importance", "reviews")
        with repo.conn:
            for name in order:
                for row in dump["tables"].get(name, []):
                    columns = list(row)
                    placeholders = ", ".join(f":{c}" for c in columns)
                    repo.conn.execute(
                        f"INSERT INTO {name}({', '.join(columns)}) VALUES({placeholders})", row
                    )
        return repo


def _rank(records) -> list:
    """Deterministic ranking: Fussell-Vesely descending, undefined last, ties by id."""
    return sorted(
        records,
        key=lambda r: (
            0 if r.fussell_vesely is not None else 1,
            -(r.fussell_vesely or Fraction(0)),
            r.event_id,
        ),
    )
