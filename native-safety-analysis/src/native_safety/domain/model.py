"""Domain model for coherent static fault trees with fixed probabilities.

Identity rule: one BasicEvent ID == one failure fact == one Boolean random
variable. Gates reference IDs; the same ID appearing in several branches is
the SAME variable (repeated event), never a fresh independent copy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

from . import errors

ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}")
PROBABILITY_PATTERN = re.compile(r"(0(\.[0-9]+)?|1(\.0+)?)")

GATE_KINDS = frozenset({"AND", "OR", "K_OF_N"})


@dataclass(frozen=True)
class BasicEvent:
    id: str
    label: str
    probability: Fraction
    probability_text: str
    source: str
    derivation: str = "declared"  # "declared" | "rate_converted"


@dataclass(frozen=True)
class Gate:
    id: str
    kind: str  # AND | OR | K_OF_N
    inputs: tuple[str, ...]
    k: int | None = None


@dataclass(frozen=True)
class Assumptions:
    basic_events_independent: bool
    probability_semantics: str
    condition: str


@dataclass(frozen=True)
class StaticFtaModel:
    schema_version: str
    model_id: str
    baseline_id: str
    assumptions: Assumptions
    basic_events: tuple[BasicEvent, ...]
    gates: tuple[Gate, ...]
    top_event: str
    # Populated by validation (post-init); kept on the model for one-pass use.
    probabilities: dict[str, Fraction] = field(default_factory=dict, repr=False, compare=False)
    # Per-event rate provenance for failure_rate-derived events (id -> record).
    rates: dict[str, dict] = field(default_factory=dict, repr=False, compare=False)
    rate_precision_digits: int = 0

    @property
    def event_ids(self) -> frozenset[str]:
        return frozenset(e.id for e in self.basic_events)

    @property
    def gate_ids(self) -> frozenset[str]:
        return frozenset(g.id for g in self.gates)

    @property
    def all_ids(self) -> frozenset[str]:
        return self.event_ids | self.gate_ids
