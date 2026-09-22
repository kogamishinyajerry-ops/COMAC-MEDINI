"""FMEA base table: rows, traceability links, and the provenance rules that
keep machine-inferred rows out of the official table until a human confirms them.

Scope boundary (B_核心规划 §55, §102): this is the FMEA **base** table, not
FMECA. A row carries component / function / failure mode / cause / local effect /
system effect / controls / evidence, plus stable references to basic events and
requirements. There is deliberately no severity / occurrence / detection / RPN
here: those are FMECA extensions, and each one needs its own semantics and its
own verification before it may appear.

Provenance rules (B_核心规划 §110, §198):
  * ``source`` records where a row came from: ``human`` | ``inference`` |
    ``import``.
  * A row whose source is ``inference`` MUST carry a non-empty
    ``inference_note`` stating what still has to be confirmed. A self-reported
    certainty is a descriptive field, never an engineering acceptance
    criterion.
  * A candidate does not enter the official table until a NAMED HUMAN approves
    and applies it. The store keeps the original ``source`` and the note after
    promotion, so the fact that a row was machine-proposed is never erased and
    an approved fact is never overwritten in place.
  * ``certainty`` is descriptive and is excluded from the content hash:
    re-wording a confidence statement is not a semantic change.

Identity (B_核心规划 §100): ``fmea_id`` is a stable identifier, never a display
name. One row may link 0..n basic events and one basic event may be linked by
0..n rows — the association is many-to-many and is never forced to be
one-to-one (§102).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from fractions import Fraction
from typing import Collection, Mapping, Sequence

from . import errors
from .errors import ModelError
from .model import ID_PATTERN
from .ratnum import exact_decimal_or_fraction

FMEA_CANONICALIZATION_VERSION = "fmea-canonical-v1"

# Where a row came from. `inference` is the machine-proposed bucket and is the
# only one that must declare what is still unconfirmed.
FMEA_SOURCES = ("human", "inference", "import")
INFERENCE_SOURCE = "inference"

EVIDENCE_KINDS = ("document", "analysis", "test", "inspection", "reference", "other")

MAX_TEXT = 2000
MAX_CONTROLS = 64
MAX_EVIDENCE = 64
MAX_REQUIREMENTS = 64
MAX_LINKS = 256

# ---------------------------------------------------------------------------
# Importance-driven attention drafts
# ---------------------------------------------------------------------------
# The engine can rank basic events by importance, but it does NOT know a failure
# mode, a cause, or a control. So the only honest thing it can produce is a WORK
# ITEM: "this event deserves an FMEA row; here is the exact quantitative reason;
# every descriptive field is still empty". Every descriptive field is prefixed
# with a marker so the fact that the row is unfinished is machine-visible, the
# store refuses to promote such a row, and `verify()` flags one that somehow
# reached the official table anyway (§110: an inference is never settled).
ATTENTION_MARKER = "[UNCONFIRMED]"
ATTENTION_FMEA_PREFIX = "FMEA-ATTN-"
UNASSIGNED_COMPONENT = "COMPONENT-UNASSIGNED"
UNASSIGNED_FUNCTION = "FUNCTION-UNASSIGNED"

#: The descriptive fields that must never still carry the marker in the official table.
ATTENTION_DESCRIPTIVE_FIELDS = ("failure_mode", "cause", "local_effect", "system_effect")

DEFAULT_ATTENTION_TOP = 10


@dataclass(frozen=True)
class EvidenceRef:
    """A stable pointer to the evidence behind a row (never a copy of it)."""

    evidence_id: str
    kind: str
    locator: str
    note: str = ""

    def canonical(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "locator": self.locator,
            "note": self.note,
        }


@dataclass(frozen=True)
class FmeaRow:
    fmea_id: str
    model_id: str
    component_id: str
    function_id: str
    failure_mode: str
    cause: str
    local_effect: str
    system_effect: str
    controls: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]
    requirement_ids: tuple[str, ...]
    linked_event_ids: tuple[str, ...]
    source: str
    certainty: str = ""
    inference_note: str = ""

    @property
    def is_inference(self) -> bool:
        return self.source == INFERENCE_SOURCE


def validate_fmea_row(data: dict, *, known_event_ids=None) -> FmeaRow:
    """Parse a raw dict into a validated FmeaRow, or raise ModelError.

    ``known_event_ids`` (when supplied) is the set of basic events of the
    baseline this row is proposed against; every linked event must resolve.
    """
    if not isinstance(data, dict):
        raise ModelError(errors.FMEA_INPUTS, "an FMEA row must be a JSON object")

    fmea_id = _stable_id(data, "fmea_id")
    model_id = data.get("model_id")
    if not isinstance(model_id, str) or not ID_PATTERN.fullmatch(model_id):
        raise ModelError(errors.FMEA_ID, f"model_id must be a stable identifier, got {model_id!r}")
    component_id = _stable_id(data, "component_id")
    function_id = _stable_id(data, "function_id")

    failure_mode = _text(data, "failure_mode")
    cause = _text(data, "cause")
    local_effect = _text(data, "local_effect")
    system_effect = _text(data, "system_effect")

    controls = _text_list(data.get("controls", []), "controls", MAX_CONTROLS)
    evidence = _evidence(data.get("evidence", []))
    requirement_ids = _id_list(data.get("requirement_ids", []), "requirement_ids", MAX_REQUIREMENTS)
    linked_event_ids = _id_list(data.get("linked_event_ids", []), "linked_event_ids", MAX_LINKS)

    source = data.get("source")
    if source not in FMEA_SOURCES:
        raise ModelError(
            errors.FMEA_SOURCE,
            f"source must be one of {FMEA_SOURCES}, got {source!r}",
        )
    certainty = data.get("certainty") or ""
    inference_note = data.get("inference_note") or ""
    if not isinstance(certainty, str) or not isinstance(inference_note, str):
        raise ModelError(errors.FMEA_INPUTS, "certainty and inference_note must be strings")
    if source == INFERENCE_SOURCE and not inference_note.strip():
        raise ModelError(
            errors.FMEA_SOURCE,
            f"row {fmea_id} declares source 'inference' and must state what still needs "
            "confirmation in 'inference_note'; an inference may never be recorded as settled",
        )

    if known_event_ids is not None:
        unknown = sorted(set(linked_event_ids) - set(known_event_ids))
        if unknown:
            raise ModelError(
                errors.FMEA_LINK,
                f"row {fmea_id} links event(s) {unknown} that do not exist in the referenced baseline",
            )

    return FmeaRow(
        fmea_id=fmea_id,
        model_id=model_id,
        component_id=component_id,
        function_id=function_id,
        failure_mode=failure_mode,
        cause=cause,
        local_effect=local_effect,
        system_effect=system_effect,
        controls=controls,
        evidence=evidence,
        requirement_ids=requirement_ids,
        linked_event_ids=linked_event_ids,
        source=source,
        certainty=certainty,
        inference_note=inference_note,
    )


def canonical_fmea_form(row: FmeaRow) -> dict:
    """The exact JSON structure covered by the FMEA content hash.

    Collections are order-insensitive: they are sorted here, so two rows that
    differ only in the order a list happened to be authored hash identically.
    Adding anything to this dict changes every FMEA hash in the field.
    """
    return {
        "canonicalization": FMEA_CANONICALIZATION_VERSION,
        "model_id": row.model_id,
        "fmea_id": row.fmea_id,
        "component_id": row.component_id,
        "function_id": row.function_id,
        "failure_mode": row.failure_mode,
        "cause": row.cause,
        "local_effect": row.local_effect,
        "system_effect": row.system_effect,
        "controls": sorted(row.controls),
        "evidence": [ref.canonical() for ref in sorted(row.evidence, key=lambda r: r.evidence_id)],
        "requirement_ids": sorted(row.requirement_ids),
        "linked_event_ids": sorted(row.linked_event_ids),
        "source": row.source,
    }


def hash_canonical_fmea(canonical: dict) -> str:
    """SHA-256 of a canonical FMEA form. Public so the store can re-verify a
    promoted row without rebuilding the domain object."""
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fmea_content_hash(row: FmeaRow) -> str:
    return hash_canonical_fmea(canonical_fmea_form(row))


def fmea_provenance(row: FmeaRow) -> dict:
    """Non-hashed sidecar: descriptive fields that carry no semantic weight.

    Kept next to the hash so a promoted row still says who proposed it, on what
    certainty basis, and what remains open.
    """
    return {
        "model_id": row.model_id,
        "fmea_id": row.fmea_id,
        "source": row.source,
        "is_machine_inferred": row.is_inference,
        "certainty": row.certainty,
        "inference_note": row.inference_note,
    }


def _stable_id(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ModelError(errors.FMEA_ID, f"{key} must be a stable identifier, got {value!r}")
    return value


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ModelError(errors.FMEA_INPUTS, f"{key} is required and must be non-empty text")
    if len(value) > MAX_TEXT:
        raise ModelError(errors.FMEA_INPUTS, f"{key} exceeds {MAX_TEXT} characters")
    return value


def _text_list(raw, key: str, limit: int) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ModelError(errors.FMEA_INPUTS, f"{key} must be an array")
    if len(raw) > limit:
        raise ModelError(errors.FMEA_INPUTS, f"{key} holds more than {limit} entries")
    out: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise ModelError(errors.FMEA_INPUTS, f"{key}[{index}] must be non-empty text")
        if len(item) > MAX_TEXT:
            raise ModelError(errors.FMEA_INPUTS, f"{key}[{index}] exceeds {MAX_TEXT} characters")
        out.append(item)
    if len(set(out)) != len(out):
        raise ModelError(errors.FMEA_INPUTS, f"{key} contains duplicates")
    return tuple(out)


def _id_list(raw, key: str, limit: int) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ModelError(errors.FMEA_INPUTS, f"{key} must be an array")
    if len(raw) > limit:
        raise ModelError(errors.FMEA_INPUTS, f"{key} holds more than {limit} entries")
    out: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not ID_PATTERN.fullmatch(item):
            raise ModelError(errors.FMEA_ID, f"{key}[{index}] must be a stable identifier, got {item!r}")
        out.append(item)
    if len(set(out)) != len(out):
        raise ModelError(errors.FMEA_INPUTS, f"{key} contains duplicates")
    return tuple(out)


def attention_fmea_id(event_id: str) -> str:
    """The stable id of the attention draft that watches one basic event.

    Derived from the event id, so re-running the generator maps to the same
    identity instead of inventing a second work item for the same event.
    """
    return f"{ATTENTION_FMEA_PREFIX}{event_id}"


def is_attention_placeholder(text: str) -> bool:
    return ATTENTION_MARKER in (text or "")


def row_has_attention_placeholder(row: FmeaRow) -> bool:
    """True while any descriptive field is still a machine-written placeholder."""
    return any(is_attention_placeholder(getattr(row, field)) for field in ATTENTION_DESCRIPTIVE_FIELDS)


def canonical_has_attention_placeholder(canonical: dict) -> bool:
    """Same test, on a stored canonical form (the store never rebuilds rows)."""
    return any(
        is_attention_placeholder(canonical.get(field) or "")
        for field in ATTENTION_DESCRIPTIVE_FIELDS
    )


def attention_draft_row(
    *,
    model_id: str,
    event_id: str,
    measure: str,
    value_text: str,
    rank: int,
    considered: int,
    run_id: str,
    baseline_hash: str,
) -> dict:
    """Build a raw FMEA draft that records *why* the event deserves attention.

    The quantitative reason is exact and reproducible (measure, exact value,
    rank among comparable events, run, baseline). Everything the engine cannot
    know is left as a marked placeholder rather than fabricated.
    """
    note = (
        f"由重要度排序自动生成：度量 {measure} = {value_text}，在 {considered} 个可比值中排第 {rank}"
        f"（run {run_id}，基线 {baseline_hash[:12]}…）。本行仅指出关注对象，"
        "失效模式/原因/影响/控制/证据均未填写，须由人工补全后另行提出；"
        "未经确认的推断不得作为事实使用。"
    )
    return {
        "fmea_id": attention_fmea_id(event_id),
        "model_id": model_id,
        "component_id": UNASSIGNED_COMPONENT,
        "function_id": UNASSIGNED_FUNCTION,
        "failure_mode": f"{ATTENTION_MARKER} 失效模式待填写（关注事件 {event_id}）",
        "cause": f"{ATTENTION_MARKER} 失效原因待填写",
        "local_effect": f"{ATTENTION_MARKER} 局部影响待填写",
        "system_effect": f"{ATTENTION_MARKER} 系统影响待填写",
        "controls": [],
        "evidence": [],
        "requirement_ids": [],
        "linked_event_ids": [event_id],
        "source": INFERENCE_SOURCE,
        "certainty": "由重要度排序生成，未经人工确认",
        "inference_note": note,
    }


def plan_attention_drafts(
    rows: Sequence[dict],
    *,
    model_id: str,
    measure: str,
    run_id: str,
    baseline_hash: str,
    covered_event_ids: Collection[str] = (),
    drafted_fmea_states: Mapping[str, str] | None = None,
    top: int | None = None,
    min_value: Fraction | None = None,
) -> tuple[list[dict], list[dict], int]:
    """Turn an importance ranking into attention drafts.

    Returns ``(drafts, skipped, not_considered)``. Skips are reported with a
    reason so a caller can explain a shortfall instead of silently proposing
    fewer rows than asked. The function is pure: it never touches the store.

    ``rows`` must already be sorted by ``measure`` (descending, undefined last)
    — the store does that on exact ``Fraction``s, never on the stored text.
    """
    covered = set(covered_event_ids)
    drafted = dict(drafted_fmea_states or {})
    defined_total = sum(1 for row in rows if row.get(measure) is not None)

    drafts: list[dict] = []
    skipped: list[dict] = []
    defined_seen = 0
    not_considered = 0

    for row in rows:
        event_id = row.get("event_id")
        raw_value = row.get(measure)
        if raw_value is None:
            skipped.append(
                {
                    "event_id": event_id,
                    "reason": "measure_is_undefined",
                    "detail": f"{measure} is undefined for this event (stored as null with a reason); "
                              "an undefined measure is not an attention signal",
                }
            )
            continue
        value = Fraction(raw_value)
        defined_seen += 1

        if top is not None and len(drafts) >= top:
            # The caller asked for at most `top` NEW drafts; stop scanning and
            # report the remainder as a count rather than emitting noise.
            not_considered = defined_total - defined_seen + 1
            break

        if min_value is not None and value < min_value:
            skipped.append(
                {
                    "event_id": event_id,
                    "reason": "below_min_value",
                    "detail": f"{exact_decimal_or_fraction(value)} < "
                              f"{exact_decimal_or_fraction(min_value)}",
                }
            )
            continue

        if event_id in covered:
            skipped.append(
                {
                    "event_id": event_id,
                    "reason": "already_in_official_table",
                    "detail": "a current official FMEA row already cites this event",
                }
            )
            continue

        fmea_id = attention_fmea_id(event_id)
        if fmea_id in drafted:
            state = drafted[fmea_id]
            skipped.append(
                {
                    "event_id": event_id,
                    "reason": "already_pending" if state == "proposed" else "already_decided",
                    "detail": f"draft {fmea_id} already exists in state {state!r}; a second work "
                              "item for the same event would only repeat it",
                }
            )
            continue

        drafts.append(
            attention_draft_row(
                model_id=model_id,
                event_id=event_id,
                measure=measure,
                value_text=exact_decimal_or_fraction(value),
                rank=defined_seen,
                considered=defined_total,
                run_id=run_id,
                baseline_hash=baseline_hash,
            )
        )

    return drafts, skipped, not_considered


def _evidence(raw) -> tuple[EvidenceRef, ...]:
    if not isinstance(raw, list):
        raise ModelError(errors.FMEA_INPUTS, "evidence must be an array")
    if len(raw) > MAX_EVIDENCE:
        raise ModelError(errors.FMEA_INPUTS, f"evidence holds more than {MAX_EVIDENCE} entries")
    refs: list[EvidenceRef] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ModelError(errors.FMEA_INPUTS, f"evidence[{index}] must be an object")
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not ID_PATTERN.fullmatch(evidence_id):
            raise ModelError(
                errors.FMEA_ID,
                f"evidence[{index}].evidence_id must be a stable identifier, got {evidence_id!r}",
            )
        kind = item.get("kind")
        if kind not in EVIDENCE_KINDS:
            raise ModelError(
                errors.FMEA_INPUTS, f"evidence[{index}].kind must be one of {EVIDENCE_KINDS}, got {kind!r}"
            )
        locator = item.get("locator")
        if not isinstance(locator, str) or not locator.strip():
            raise ModelError(
                errors.FMEA_INPUTS,
                f"evidence[{index}].locator is required (page / section / file / path)",
            )
        note = item.get("note") or ""
        if not isinstance(note, str):
            raise ModelError(errors.FMEA_INPUTS, f"evidence[{index}].note must be text")
        refs.append(EvidenceRef(evidence_id=evidence_id, kind=kind, locator=locator, note=note))
    ids = [ref.evidence_id for ref in refs]
    if len(set(ids)) != len(ids):
        raise ModelError(errors.FMEA_INPUTS, "evidence ids must be unique within a row")
    return tuple(refs)
