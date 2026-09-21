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

from . import errors
from .errors import ModelError
from .model import ID_PATTERN

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
