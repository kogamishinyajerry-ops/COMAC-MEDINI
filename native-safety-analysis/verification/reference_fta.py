"""INDEPENDENT reference implementation — deliberately NOT the production kernel.

This module shares NOTHING with src/native_safety/kernel: no BDD, no Shannon
expansion, no apply/ite. It brute-force enumerates the full truth table
(2^n assignments, n <= 20) and multiplies exact Fractions per assignment,
then extracts minimal cut sets by inclusion filtering of satisfying
assignments. It exists to cross-check the production ROBDD kernel; the two
implementations must remain algorithmically distinct.

Written from the contract description alone (reference/03_contracts), not
by copying the production code or the seed verify_seed_cases.py (the
latter is the package's own oracle; this file is OUR independent oracle).
"""
from __future__ import annotations

import re
from fractions import Fraction

ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}")
PROB_RE = re.compile(r"(0(\.[0-9]+)?|1(\.0+)?)")


class RefError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def ref_validate(model: dict):
    """Structural/semantic validation (independent re-implementation)."""
    if model.get("schema_version") != "0.1.0":
        raise RefError("VERSION", "expected 0.1.0")
    a = model.get("assumptions", {})
    if not a or a.get("basic_events_independent") is not True:
        raise RefError("ASSUMPTIONS", "independence must be declared true")
    if a.get("probability_semantics") != "fixed_conditioned_probability":
        raise RefError("ASSUMPTIONS", "fixed conditioned probability required")
    if not a.get("condition"):
        raise RefError("ASSUMPTIONS", "condition required")

    events = model.get("basic_events")
    if not isinstance(events, list) or not 1 <= len(events) <= 20:
        raise RefError("SIZE_LIMIT", "1..20 basic events")

    probs = {}
    for e in events:
        eid = e.get("id")
        if not isinstance(eid, str) or not ID_RE.fullmatch(eid):
            raise RefError("ID", f"bad id {eid!r}")
        if eid in probs:
            raise RefError("DUPLICATE_ID", eid)
        t = e.get("probability")
        if not isinstance(t, str) or not PROB_RE.fullmatch(t):
            raise RefError("PROBABILITY", f"{eid}: {t!r}")
        if not e.get("source"):
            raise RefError("SOURCE", eid)
        probs[eid] = Fraction(t)

    gates = {}
    for g in model.get("gates", []):
        gid = g.get("id")
        if not isinstance(gid, str) or not ID_RE.fullmatch(gid):
            raise RefError("ID", f"bad gate id {gid!r}")
        if gid in gates or gid in probs:
            raise RefError("DUPLICATE_ID", gid)
        kind = g.get("kind")
        if kind not in ("AND", "OR", "K_OF_N"):
            raise RefError("UNSUPPORTED_GATE", str(kind))
        inputs = g.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise RefError("INPUTS", gid)
        if kind == "K_OF_N":
            k = g.get("k")
            if type(k) is not int or not 1 <= k <= len(inputs) or len(set(inputs)) != len(inputs):
                raise RefError("K_OF_N", gid)
        elif "k" in g:
            raise RefError("K_OF_N", f"gate {gid}: k only on K_OF_N")
        gates[gid] = g

    all_ids = set(probs) | set(gates)
    top = model.get("top_event")
    if top not in all_ids:
        raise RefError("UNKNOWN_REFERENCE", f"top_event {top!r}")
    for g in gates.values():
        for ref in g["inputs"]:
            if ref not in all_ids:
                raise RefError("UNKNOWN_REFERENCE", ref)

    # cycle check: repeated passes removing leaf-fed gates
    remaining = dict(gates)
    while remaining:
        progress = False
        for gid in list(remaining):
            if all((c in probs) or (c not in remaining) for c in remaining[gid]["inputs"]):
                del remaining[gid]
                progress = True
        if not progress:
            raise RefError("CYCLE", sorted(remaining)[0])
    return probs, gates


def ref_solve(model: dict):
    """Truth-table brute force. Returns (Fraction probability, sorted MCS)."""
    probs, gates = ref_validate(model)
    ids = sorted(probs)
    n = len(ids)

    def evaluate(node_id: str, assignment: tuple[bool, ...]) -> bool:
        if node_id in probs:
            return assignment[ids.index(node_id)]
        g = gates[node_id]
        vals = [evaluate(c, assignment) for c in g["inputs"]]
        if g["kind"] == "AND":
            return all(vals)
        if g["kind"] == "OR":
            return any(vals)
        return sum(vals) >= g["k"]  # K_OF_N

    probability = Fraction(0)
    satisfying: list[frozenset[str]] = []
    for mask in range(1 << n):
        assignment = tuple(bool(mask & (1 << i)) for i in range(n))
        if not evaluate(model["top_event"], assignment):
            continue
        weight = Fraction(1)
        for i, eid in enumerate(ids):
            weight *= probs[eid] if assignment[i] else 1 - probs[eid]
        probability += weight
        satisfying.append(frozenset(eid for i, eid in enumerate(ids) if assignment[i]))

    minimal = []
    for s in sorted(satisfying, key=len):
        if not any(m <= s for m in minimal):
            minimal.append(s)
    return probability, sorted(sorted(s) for s in minimal)
