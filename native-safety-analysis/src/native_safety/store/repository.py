"""SQLite repository: baselines, runs, importance, reviews, FMEA rows.

Storage discipline (B_核心规划 §201-203, §231):
  - one writer, optimistic concurrency: a caller that names an expected
    baseline hash is refused if the stored one moved;
  - a baseline is immutable. A model change registers a NEW baseline hash and
    the dependent runs are flagged stale; their payloads stay readable;
  - `run_id` is idempotent: re-recording identical content is a no-op, and
    re-recording different content under the same id is refused, never an
    overwrite;
  - every stored number is an exact decimal or an exact `n/d` rational.

FMEA discipline (B_核心规划 §102, §110, §198):
  - an official row is never rewritten in place; a revision is a new version
    and the older row is marked `superseded`, so the previous approved fact
    stays readable;
  - a candidate reaches the official table only through
    propose -> decide(named human) -> apply; the same state vocabulary and the
    same approval guard as `reviews` are reused, not re-invented;
  - the original `source` (`inference` included) and the proposer's note on
    what remains unconfirmed survive promotion, so a machine-proposed row never
    loses its provenance;
  - one row links 0..n basic events and one event is linked by 0..n rows. A
    link that stops resolving after a baseline move is a REPORTABLE traceability
    gap, not an integrity failure of the store — it is returned by
    `dangling_fmea_links()` and counted in `status()`, never hidden.

The FTA side of this layer does no validation: it receives already-validated
domain objects and already-computed results. FMEA rows are the exception, and
deliberately so: whether a linked event exists can only be decided against the
store's current baseline, so `propose_fmea_candidate` completes validation here
and records a rejected draft honestly instead of dropping it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction

from ..domain.errors import ModelError
from ..domain.fmea import (
    FMEA_SOURCES,
    INFERENCE_SOURCE,
    canonical_fmea_form,
    fmea_content_hash,
    fmea_provenance,
    hash_canonical_fmea,
    validate_fmea_row,
)
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
# The FMEA candidate queue uses the SAME state vocabulary as reviews.
FMEA_CANDIDATE_STATES = REVIEW_STATES

# Table -> columns forming a stable dump order (primary keys first).
_DUMP_ORDER = {
    "schema_meta": ("key",),
    "models": ("model_id",),
    "baselines": ("baseline_hash",),
    "runs": ("run_id",),
    "importance": ("run_id", "event_id"),
    "reviews": ("review_id",),
    "fmea_rows": ("model_id", "fmea_id", "version"),
    "fmea_links": ("model_id", "fmea_id", "version", "event_id"),
    "fmea_candidates": ("candidate_id",),
}

# Insertion order for restore(): every referenced row before its referrers.
_RESTORE_ORDER = (
    "models",
    "baselines",
    "runs",
    "importance",
    "reviews",
    "fmea_rows",
    "fmea_links",
    "fmea_candidates",
)

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

    # ------------------------------------------------------------------ FMEA
    def baseline_event_ids(self, model_id: str) -> frozenset[str] | None:
        """Event ids of the model's CURRENT baseline, or None if it has none.

        Read from the stored canonical form, not from a domain object, so a
        traceability check never depends on re-loading a model file.
        """
        row = self.conn.execute(
            "SELECT b.canonical_json AS c FROM baselines b"
            " JOIN models m ON m.current_baseline_hash = b.baseline_hash"
            " WHERE m.model_id=?",
            (model_id,),
        ).fetchone()
        if row is None:
            return None
        canonical = json.loads(row["c"])
        return frozenset(event["id"] for event in canonical.get("basic_events", []))

    def current_fmea_version(self, model_id: str, fmea_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT MAX(version) AS v FROM fmea_rows"
            " WHERE model_id=? AND fmea_id=? AND superseded=0",
            (model_id, fmea_id),
        ).fetchone()
        return row["v"] if row and row["v"] is not None else None

    def propose_fmea_candidate(
        self,
        *,
        candidate_id: str,
        raw_row: dict,
        expected_baseline_hash: str,
        proposed_by: str,
        expected_version: int | None = None,
        note: str | None = None,
    ) -> dict:
        """Record a draft FMEA row. Never promotes it.

        Mirrors `propose_review`: a row that fails validation is still recorded,
        with state `invalid` and the reason, so the rejected attempt is on the
        audit trail instead of silently vanishing. A row that passes validation
        is recorded as `proposed` and waits for a named human.
        """
        if not isinstance(raw_row, dict):
            raise StoreError(errors.BAD_ARGUMENT, "an FMEA row must be a JSON object")
        model_id = raw_row.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            raise StoreError(
                errors.BAD_ARGUMENT,
                "the FMEA row must carry a model_id so it can be traced to a baseline",
            )
        observed = self.current_baseline_hash(model_id)
        if observed is None:
            raise StoreError(
                errors.MODEL_NOT_FOUND,
                f"model {model_id!r} has no baseline in this store; run `analyze --db` on it "
                "first so an FMEA row can be traced against a baseline",
            )
        if expected_baseline_hash != observed:
            raise StoreError(
                errors.BASELINE_CONFLICT,
                f"the row was written against baseline {expected_baseline_hash[:12]}… but the store "
                f"holds {observed[:12]}… for model {model_id!r}; rebase the draft",
            )

        raw_fmea_id = raw_row.get("fmea_id")
        known_events = self.baseline_event_ids(model_id) or frozenset()
        try:
            row = validate_fmea_row(raw_row, known_event_ids=known_events)
        except ModelError as exc:
            # A structurally bad draft is recorded, not dropped — but the
            # revision ledger cannot be checked against an unreadable id.
            state, code, message = "invalid", exc.code, exc.message
            content_hash = canonical_json = provenance_json = None
            fmea_id = raw_fmea_id if isinstance(raw_fmea_id, str) and raw_fmea_id else "(unreadable)"
        else:
            fmea_id = row.fmea_id
            self._assert_fmea_revision_target(model_id, fmea_id, expected_version)
            state, code, message = "proposed", None, None
            content_hash = fmea_content_hash(row)
            canonical_json = json.dumps(
                canonical_fmea_form(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            # The descriptive sidecar is outside the hash but must survive
            # promotion, so it is stored on the candidate as well.
            provenance_json = json.dumps(
                fmea_provenance(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )

        with self.conn:
            self.conn.execute(
                "INSERT INTO fmea_candidates(candidate_id, model_id, fmea_id, expected_version,"
                " expected_baseline_hash, observed_baseline_hash, proposed_content_hash,"
                " proposed_canonical_json, proposed_provenance_json, state, validation_code,"
                " validation_message, note, proposed_by, created_utc)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (candidate_id, model_id, fmea_id, expected_version, expected_baseline_hash, observed,
                 content_hash, canonical_json, provenance_json, state, code, message, note,
                 proposed_by, _utc_now()),
            )
        return self.get_fmea_candidate(candidate_id)

    def _assert_fmea_revision_target(
        self, model_id: str, fmea_id: str, expected_version: int | None
    ) -> None:
        """A revision must name the exact version it revises; a new row must not."""
        current = self.current_fmea_version(model_id, fmea_id)
        if current is None:
            if expected_version is not None:
                raise StoreError(
                    errors.FMEA_REVISION_CONFLICT,
                    f"fmea_id {fmea_id!r} has no official row for model {model_id!r} yet, but "
                    f"expected_version={expected_version} was declared; a first row must not "
                    "declare a revision target",
                )
            return
        if expected_version != current:
            raise StoreError(
                errors.FMEA_REVISION_CONFLICT,
                f"fmea_id {fmea_id!r} is at version {current} for model {model_id!r}; a revision "
                f"must declare expected_version={current} (it declared {expected_version!r}), "
                "otherwise it would silently replace a fact someone else changed",
            )

    def get_fmea_candidate(self, candidate_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM fmea_candidates WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_fmea_candidates(
        self, *, state: str | None = None, model_id: str | None = None
    ) -> list[dict]:
        sql, params, clauses = "SELECT * FROM fmea_candidates", [], []
        if state is not None:
            clauses.append("state=?")
            params.append(state)
        if model_id is not None:
            clauses.append("model_id=?")
            params.append(model_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_utc, candidate_id"
        return [dict(r) for r in self.conn.execute(sql, params)]

    def decide_fmea_candidate(
        self, candidate_id: str, *, approve: bool, reviewer: str, note: str | None = None
    ) -> dict:
        """Approve or reject a draft. Requires an explicit named human."""
        actor = _assert_approver(reviewer, "deciding an FMEA candidate")
        record = self.get_fmea_candidate(candidate_id)
        if record is None:
            raise StoreError(errors.FMEA_NOT_FOUND, f"no FMEA candidate {candidate_id!r}")
        if record["state"] != "proposed":
            raise StoreError(
                errors.FMEA_STATE,
                f"FMEA candidate {candidate_id!r} is in state {record['state']!r}; only a "
                "'proposed' draft can be decided",
            )
        if approve and record["proposed_content_hash"] is None:
            raise StoreError(
                errors.FMEA_STATE, f"FMEA candidate {candidate_id!r} carries no valid row"
            )
        target = "approved" if approve else "rejected"
        with self.conn:
            self.conn.execute(
                "UPDATE fmea_candidates SET state=?, decided_by=?, decided_utc=?,"
                " note=COALESCE(?, note) WHERE candidate_id=?",
                (target, actor, _utc_now(), note, candidate_id),
            )
        return self.get_fmea_candidate(candidate_id)

    def apply_fmea_candidate(self, candidate_id: str, *, reviewer: str) -> tuple[dict, dict]:
        """Promote an approved draft into the official table.

        Returns (candidate, promoted_row). The approval is bound to BOTH the
        content hash and the baseline it was proposed against: if either moved,
        the apply is refused and the approval must be re-decided.
        """
        actor = _assert_approver(reviewer, "applying an FMEA candidate")
        record = self.get_fmea_candidate(candidate_id)
        if record is None:
            raise StoreError(errors.FMEA_NOT_FOUND, f"no FMEA candidate {candidate_id!r}")
        if record["state"] != "approved":
            raise StoreError(
                errors.FMEA_STATE,
                f"FMEA candidate {candidate_id!r} is in state {record['state']!r}; only an approved "
                "draft can be applied",
            )
        model_id = record["model_id"]
        observed = self.current_baseline_hash(model_id)
        if record["expected_baseline_hash"] != observed:
            raise StoreError(
                errors.BASELINE_CONFLICT,
                f"FMEA candidate {candidate_id!r} was approved against baseline "
                f"{record['expected_baseline_hash'][:12]}… but the store now holds "
                f"{(observed or '(none)')[:12]}…; the approval is stale and must be re-decided",
            )
        # Anchor exactly the approved canonical form: re-hash it so an
        # out-of-band edit of the stored draft can never reach the official table.
        canonical = json.loads(record["proposed_canonical_json"])
        anchored = hash_canonical_fmea(canonical)
        if anchored != record["proposed_content_hash"]:
            raise StoreError(
                errors.INTEGRITY,
                f"stored draft {candidate_id!r} no longer hashes to its recorded hash; refusing to apply",
            )
        linked = tuple(canonical["linked_event_ids"])
        event_ids = self.baseline_event_ids(model_id) or frozenset()
        unknown = sorted(set(linked) - event_ids)
        if unknown:
            # Cannot happen while the baseline check above holds; if it does,
            # the store contradicts itself and we stop rather than write it down.
            raise StoreError(
                errors.INTEGRITY,
                f"draft {candidate_id!r} links event(s) {unknown} absent from the baseline it was "
                "approved against; the store is inconsistent",
            )

        fmea_id = record["fmea_id"]
        current = self.current_fmea_version(model_id, fmea_id)
        self._assert_fmea_revision_target(model_id, fmea_id, record["expected_version"])
        new_version = 1 if current is None else current + 1
        now = _utc_now()
        provenance = json.loads(record["proposed_provenance_json"] or "{}")

        with self.conn:
            self.conn.execute(
                "INSERT INTO fmea_rows(model_id, fmea_id, version, component_id, function_id,"
                " failure_mode, cause, local_effect, system_effect, controls_json, evidence_json,"
                " requirement_ids_json, source, certainty, inference_note, content_hash,"
                " canonical_json, validated_baseline_hash, superseded, superseded_by_version,"
                " approved_by, approved_utc, created_utc, created_by)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL,?,?,?,?)",
                (
                    model_id, fmea_id, new_version,
                    canonical["component_id"], canonical["function_id"],
                    canonical["failure_mode"], canonical["cause"],
                    canonical["local_effect"], canonical["system_effect"],
                    json.dumps(canonical["controls"], ensure_ascii=False),
                    json.dumps(canonical["evidence"], ensure_ascii=False),
                    json.dumps(canonical["requirement_ids"], ensure_ascii=False),
                    canonical["source"],
                    provenance.get("certainty", "") or "",
                    provenance.get("inference_note", "") or "",
                    anchored,
                    record["proposed_canonical_json"],
                    record["expected_baseline_hash"],
                    actor, now, now, record["proposed_by"],
                ),
            )
            for event_id in linked:
                self.conn.execute(
                    "INSERT INTO fmea_links(model_id, fmea_id, version, event_id) VALUES(?,?,?,?)",
                    (model_id, fmea_id, new_version, event_id),
                )
            if current is not None:
                self.conn.execute(
                    "UPDATE fmea_rows SET superseded=1, superseded_by_version=?"
                    " WHERE model_id=? AND fmea_id=? AND version=?",
                    (new_version, model_id, fmea_id, current),
                )
            self.conn.execute(
                "UPDATE fmea_candidates SET state='applied', applied_version=?, decided_by=?,"
                " decided_utc=? WHERE candidate_id=?",
                (new_version, actor, now, candidate_id),
            )
        return self.get_fmea_candidate(candidate_id), self.get_fmea_row(model_id, fmea_id, new_version)

    def get_fmea_row(self, model_id: str, fmea_id: str, version: int | None = None) -> dict | None:
        if version is None:
            row = self.conn.execute(
                "SELECT * FROM fmea_rows WHERE model_id=? AND fmea_id=? AND superseded=0",
                (model_id, fmea_id),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM fmea_rows WHERE model_id=? AND fmea_id=? AND version=?",
                (model_id, fmea_id, version),
            ).fetchone()
        return dict(row) if row else None

    def list_fmea_rows(
        self, model_id: str | None = None, *, include_superseded: bool = False
    ) -> list[dict]:
        sql, params, clauses = "SELECT * FROM fmea_rows", [], []
        if model_id is not None:
            clauses.append("model_id=?")
            params.append(model_id)
        if not include_superseded:
            clauses.append("superseded=0")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY model_id, fmea_id, version"
        return [dict(r) for r in self.conn.execute(sql, params)]

    def fmea_links_of_row(self, model_id: str, fmea_id: str, version: int) -> list[str]:
        return sorted(
            r["event_id"]
            for r in self.conn.execute(
                "SELECT event_id FROM fmea_links WHERE model_id=? AND fmea_id=? AND version=?",
                (model_id, fmea_id, version),
            )
        )

    def fmea_rows_for_event(self, model_id: str, event_id: str) -> list[dict]:
        """Traceability: every current official row that claims this event."""
        rows = self.conn.execute(
            "SELECT r.* FROM fmea_rows r JOIN fmea_links l"
            " ON l.model_id=r.model_id AND l.fmea_id=r.fmea_id AND l.version=r.version"
            " WHERE l.model_id=? AND l.event_id=? AND r.superseded=0"
            " ORDER BY r.fmea_id",
            (model_id, event_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def fmea_rows_by_component(self, model_id: str, component_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM fmea_rows WHERE model_id=? AND component_id=? AND superseded=0"
            " ORDER BY fmea_id",
            (model_id, component_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def dangling_fmea_links(self, model_id: str | None = None) -> list[dict]:
        """Current rows whose linked event no longer exists in the current baseline.

        This is a legitimate business state after a fault tree changes: the FMEA
        text is human knowledge and must not evaporate, but the traceability gap
        has to be visible. It is therefore reported here, not raised as a store
        integrity failure.
        """
        sql = (
            "SELECT DISTINCT l.model_id, l.fmea_id, l.version, l.event_id FROM fmea_links l"
            " JOIN fmea_rows r ON r.model_id=l.model_id AND r.fmea_id=l.fmea_id"
            " AND r.version=l.version WHERE r.superseded=0"
        )
        params: list[object] = []
        if model_id is not None:
            sql += " AND l.model_id=?"
            params.append(model_id)
        sql += " ORDER BY l.model_id, l.fmea_id, l.version, l.event_id"
        cache: dict[str, frozenset[str]] = {}
        out: list[dict] = []
        for row in self.conn.execute(sql, params):
            mid = row["model_id"]
            if mid not in cache:
                cache[mid] = self.baseline_event_ids(mid) or frozenset()
            if row["event_id"] not in cache[mid]:
                out.append(
                    {
                        "model_id": mid,
                        "fmea_id": row["fmea_id"],
                        "version": row["version"],
                        "event_id": row["event_id"],
                    }
                )
        return out

    def fmea_requirements_of_row(self, row: dict) -> list[str]:
        try:
            return list(json.loads(row["requirement_ids_json"]))
        except (TypeError, json.JSONDecodeError):
            return []

    # ------------------------------------------------------------------ integrity
    def status(self) -> dict:
        counts = {
            table: self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in (
                "models", "baselines", "runs", "importance", "reviews",
                "fmea_rows", "fmea_links", "fmea_candidates",
            )
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
            "current_fmea_rows": self.conn.execute(
                "SELECT COUNT(*) AS n FROM fmea_rows WHERE superseded=0"
            ).fetchone()["n"],
            "pending_fmea_candidates": self.conn.execute(
                "SELECT COUNT(*) AS n FROM fmea_candidates WHERE state IN ('proposed','approved')"
            ).fetchone()["n"],
            # A traceability gap, not an integrity failure: reported, never hidden.
            "dangling_fmea_links": len(self.dangling_fmea_links()),
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

        problems.extend(self._verify_fmea())

        return problems

    def _verify_fmea(self) -> list[str]:
        """Data-level integrity of the FMEA tables.

        Deliberately NOT included: a link whose event disappeared after a
        baseline move. That is a legitimate traceability gap reported by
        `dangling_fmea_links()`, not a corruption of the store — folding it in
        here would make `store verify` fail after a perfectly legal model
        change and train everyone to ignore it.
        """
        problems: list[str] = []

        # Every row: content re-hashes, JSON sidecars agree, provenance intact,
        # the supersede chain is coherent, and exactly one version is current.
        # The full version set is collected first: a successor is looked up while
        # iterating an earlier version, so it cannot be discovered in place.
        versions: set[tuple[str, str, int]] = {
            (row["model_id"], row["fmea_id"], row["version"])
            for row in self.conn.execute("SELECT model_id, fmea_id, version FROM fmea_rows")
        }
        current_seen: dict[tuple[str, str], int] = {}
        for row in self.conn.execute("SELECT * FROM fmea_rows ORDER BY model_id, fmea_id, version"):
            record = dict(row)
            tag = f"fmea row {record['model_id']}/{record['fmea_id']}v{record['version']}"

            canonical = None
            try:
                canonical = json.loads(record["canonical_json"])
            except json.JSONDecodeError as exc:
                problems.append(f"{tag}: canonical_json is not JSON ({exc})")
            if canonical is not None:
                recomputed = hash_canonical_fmea(canonical)
                if recomputed != record["content_hash"]:
                    problems.append(f"{tag}: canonical_json re-hashes to {recomputed[:12]}…")
                for column, key in (
                    ("fmea_id", "fmea_id"),
                    ("component_id", "component_id"),
                    ("function_id", "function_id"),
                    ("failure_mode", "failure_mode"),
                    ("cause", "cause"),
                    ("local_effect", "local_effect"),
                    ("system_effect", "system_effect"),
                    ("source", "source"),
                ):
                    if canonical.get(key) != record[column]:
                        problems.append(f"{tag}: column {column} disagrees with the canonical form")
                for column, key in (
                    ("controls_json", "controls"),
                    ("evidence_json", "evidence"),
                    ("requirement_ids_json", "requirement_ids"),
                ):
                    try:
                        value = json.loads(record[column])
                    except json.JSONDecodeError:
                        problems.append(f"{tag}: {column} is not JSON")
                        continue
                    if value != canonical.get(key):
                        problems.append(f"{tag}: {column} disagrees with the canonical form")

            if record["source"] not in FMEA_SOURCES:
                problems.append(f"{tag}: unknown source {record['source']!r}")
            if record["source"] == INFERENCE_SOURCE and not (record["inference_note"] or "").strip():
                problems.append(
                    f"{tag}: a machine-inferred row was promoted without a record of what "
                    "remains unconfirmed"
                )
            if not (record["approved_by"] or "").strip():
                problems.append(f"{tag}: no approving human is recorded")
            elif record["approved_by"].strip().lower() in NON_APPROVING_IDENTITIES:
                problems.append(
                    f"{tag}: approved by a non-approving identity {record['approved_by']!r}"
                )
            if not record["validated_baseline_hash"]:
                problems.append(f"{tag}: no baseline recorded for its traceability check")

            stored_links = sorted(
                r["event_id"]
                for r in self.conn.execute(
                    "SELECT event_id FROM fmea_links WHERE model_id=? AND fmea_id=? AND version=?",
                    (record["model_id"], record["fmea_id"], record["version"]),
                )
            )
            if canonical is not None and stored_links != sorted(canonical.get("linked_event_ids", [])):
                problems.append(f"{tag}: link rows disagree with the canonical form")

            key = (record["model_id"], record["fmea_id"])
            if record["superseded"]:
                if record["superseded_by_version"] is None:
                    problems.append(f"{tag}: marked superseded without a successor")
                elif (record["model_id"], record["fmea_id"], record["superseded_by_version"]) not in versions:
                    problems.append(f"{tag}: successor version does not exist")
                elif record["superseded_by_version"] <= record["version"]:
                    problems.append(f"{tag}: successor version is not newer")
                if key in current_seen:
                    problems.append(f"{tag}: a superseded version appears after the current one")
            else:
                if key in current_seen:
                    problems.append(
                        f"{tag}: more than one current version for {record['model_id']}/{record['fmea_id']}"
                    )
                current_seen[key] = record["version"]

        # Orphan link rows cannot exist while foreign keys are enforced, but the
        # check is the point: it is what makes "delete the row, keep the links"
        # impossible rather than merely unlikely.
        for row in self.conn.execute(
            "SELECT l.model_id, l.fmea_id, l.version, l.event_id FROM fmea_links l"
            " LEFT JOIN fmea_rows r ON r.model_id=l.model_id AND r.fmea_id=l.fmea_id"
            " AND r.version=l.version WHERE r.version IS NULL"
        ):
            problems.append(
                f"orphan fmea link {row['model_id']}/{row['fmea_id']}v{row['version']} -> {row['event_id']}"
            )

        current_baselines = {
            r["model_id"]: r["current_baseline_hash"]
            for r in self.conn.execute("SELECT model_id, current_baseline_hash FROM models")
        }
        for row in self.conn.execute("SELECT * FROM fmea_candidates ORDER BY candidate_id"):
            record = dict(row)
            tag = f"fmea candidate {record['candidate_id']!r}"
            if record["state"] not in FMEA_CANDIDATE_STATES:
                problems.append(f"{tag}: unknown state {record['state']!r}")
                continue
            if record["proposed_content_hash"] is not None:
                try:
                    canonical = json.loads(record["proposed_canonical_json"])
                except (TypeError, json.JSONDecodeError):
                    problems.append(f"{tag}: proposed_canonical_json is not JSON")
                else:
                    if hash_canonical_fmea(canonical) != record["proposed_content_hash"]:
                        problems.append(f"{tag}: proposed canonical form no longer matches its hash")
            elif record["state"] in ("approved", "applied"):
                problems.append(f"{tag}: {record['state']} without a valid proposed row")
            if record["state"] == "applied":
                if record["applied_version"] is None:
                    problems.append(f"{tag}: applied without a version")
                elif (
                    record["model_id"], record["fmea_id"], record["applied_version"]
                ) not in versions:
                    problems.append(f"{tag}: applied version does not exist in the official table")
            if record["state"] in ("approved", "rejected", "applied"):
                if not (record["decided_by"] or "").strip():
                    problems.append(f"{tag}: decided without a named decider")
                elif record["decided_by"].strip().lower() in NON_APPROVING_IDENTITIES:
                    problems.append(f"{tag}: decided by a non-approving identity {record['decided_by']!r}")
            if record["state"] in ("proposed", "invalid"):
                if record["decided_by"] is not None:
                    problems.append(f"{tag}: undecided state {record['state']!r} has a decider")
            if record["model_id"] not in current_baselines:
                problems.append(f"{tag}: model {record['model_id']!r} is not in the store")

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
        with repo.conn:
            for name in _RESTORE_ORDER:
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
