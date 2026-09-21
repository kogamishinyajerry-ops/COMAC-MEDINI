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
"""
from __future__ import annotations

import hashlib
import json

from .model import StaticFtaModel
from .ratnum import exact_decimal_or_fraction

CANONICALIZATION_VERSION = "canonical-v1"


def semantic_model_hash(model: StaticFtaModel) -> str:
    canonical = {
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
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
