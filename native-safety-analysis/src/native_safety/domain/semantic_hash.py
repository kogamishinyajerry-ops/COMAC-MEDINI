"""Semantic model hash (canonical form -> SHA-256).

The hash covers ONLY computation-relevant semantics: event identity,
gate logic, probabilities (exact rational values, not lexical forms),
applicability condition, and analysis-affecting assumptions. Layout,
labels, timestamps and raw serialization are excluded by rule.

Canonicalization (v1), recorded for cross-tool agreement:
  - events sorted by ID; probability emitted as exact decimal of the
    Fraction (so "0.10" and "0.1" hash identically);
  - gates sorted by ID; inputs kept in declared order (order is semantic
    for K_OF_N presentation but Boolean-equivalent; v1 keeps declared
    order and documents this);
  - JSON dumped with sort_keys, separators without whitespace, UTF-8.

Two callers share this module so the canonical form exists once:
  - the run payload (semantic_model_hash)
  - the store (baselines.canonical_json, re-hashed at verification time to
    prove a stored baseline still matches its advertised hash)
"""
from __future__ import annotations

import hashlib
import json

from .model import StaticFtaModel
from .ratnum import exact_decimal_or_fraction

CANONICALIZATION_VERSION = "canonical-v1"


def canonical_model_form(model: StaticFtaModel) -> dict:
    """The exact JSON structure covered by the semantic hash.

    Anything added here changes every hash in the field; keep it minimal and
    computation-relevant only.
    """
    return {
        "canonicalization": CANONICALIZATION_VERSION,
        "schema_version": model.schema_version,
        "model_id": model.model_id,
        "assumptions": {
            "basic_events_independent": model.assumptions.basic_events_independent,
            "probability_semantics": model.assumptions.probability_semantics,
            "condition": model.assumptions.condition,
        },
        "basic_events": [
            {
                "id": e.id,
                "p": exact_decimal_or_fraction(e.probability),
                **({"rate": model.rates[e.id]} if e.id in model.rates else {}),
            }
            for e in sorted(model.basic_events, key=lambda e: e.id)
        ],
        "gates": [
            {
                "id": g.id,
                "kind": g.kind,
                "inputs": list(g.inputs),
                **({"k": g.k} if g.k is not None else {}),
            }
            for g in sorted(model.gates, key=lambda g: g.id)
        ],
        "top_event": model.top_event,
    }


def hash_canonical_form(canonical: dict) -> str:
    """SHA-256 of a canonical form. Public so the store can re-verify a
    persisted baseline without rebuilding the domain model."""
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def semantic_model_hash(model: StaticFtaModel) -> str:
    return hash_canonical_form(canonical_model_form(model))


def model_provenance(model: StaticFtaModel) -> dict:
    """Non-hashed traceability sidecar: labels, sources, derivations.

    Deliberately outside the hash (a label edit is not a semantic change) but
    persisted alongside the canonical form so a stored baseline still says
    where every number came from.
    """
    return {
        "model_id": model.model_id,
        "baseline_id": model.baseline_id,
        "schema_version": model.schema_version,
        "rate_precision_digits": model.rate_precision_digits,
        "basic_events": [
            {
                "id": e.id,
                "label": e.label,
                "source": e.source,
                "derivation": e.derivation,
                "probability_text": e.probability_text,
            }
            for e in sorted(model.basic_events, key=lambda e: e.id)
        ],
        "rate_events": {eid: model.rates[eid] for eid in sorted(model.rates)},
    }
