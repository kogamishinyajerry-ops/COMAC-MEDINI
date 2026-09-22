"""INDEPENDENT reference for importance measures — deliberately NOT the kernel.

Shares NOTHING with src/native_safety/kernel/importance.py: no BDD, no reach
pass, no difference array, no level reasoning, no support bookkeeping from a
diagram. For every basic event i it brute-forces the FULL assignment space of
the remaining n-1 variables with x_i forced true and forced false
(2^(n-1) assignments each), evaluates the gate tree directly, and accumulates
exact Fractions. The four measures are then formed from the textbook
definitions.

Support is decided independently too: x_i is relevant iff some pair of
assignments differing only in x_i yields different top-event values (a truth
table sweep, not a diagram inspection).

Philosophy matches reference_fta.ref_solve: the production kernel never
enumerates assignments, and this oracle has no diagram at all. Both use exact
rational arithmetic, so the two must agree EXACTLY — the comparison needs no
tolerance whatsoever, unlike the rate-conversion cross-check where a
transcendental function forces a declared tolerance.

Applies to models already reduced to declared probabilities (schema 0.1.0).
"""
from __future__ import annotations

from fractions import Fraction

from reference_fta import ref_validate


def ref_importance(model: dict) -> tuple[Fraction, dict[str, dict], set[str]]:
    """Return (top_probability, measures_by_event, support_set).

    Each measure dict has keys: q, q_plus, q_minus, birnbaum, fussell_vesely,
    risk_achievement_worth, risk_reduction_worth. A mathematically undefined
    measure is None, matching the production contract.
    """
    probs, gates = ref_validate(model)
    ids = sorted(probs)
    n = len(ids)
    index = {eid: i for i, eid in enumerate(ids)}

    def evaluate(node_id: str, assignment: dict[int, bool]) -> bool:
        if node_id in probs:
            return assignment[index[node_id]]
        gate = gates[node_id]
        values = [evaluate(child, assignment) for child in gate["inputs"]]
        if gate["kind"] == "AND":
            return all(values)
        if gate["kind"] == "OR":
            return any(values)
        return sum(values) >= gate["k"]  # K_OF_N

    def probability_with(
        free: list[int], forced: dict[int, bool], skip: int | None = None
    ) -> Fraction:
        """Probability over the `free` variables with the forced ones pinned.

        `skip` excludes that variable's factor from the weight product, which
        turns the enumeration over the remaining variables into the COFACTOR
        probability P(f | x_skip = value) rather than the joint P(f and x).
        Excluding the factor is equivalent to dividing by q_skip, but stays
        well-defined when q_skip = 0.
        """
        total = Fraction(0)
        for mask in range(1 << len(free)):
            assignment = dict(forced)
            for slot, target in enumerate(free):
                assignment[target] = bool(mask & (1 << slot))
            if not evaluate(model["top_event"], assignment):
                continue
            weight = Fraction(1)
            for var in range(n):
                if var == skip:
                    continue
                q = probs[ids[var]]
                weight *= q if assignment[var] else 1 - q
            total += weight
        return total

    # -- independent support test (no diagram involved) -------------------
    truth = [
        evaluate(
            model["top_event"],
            {var: bool(mask & (1 << var)) for var in range(n)},
        )
        for mask in range(1 << n)
    ]
    support = set()
    for var in range(n):
        bit = 1 << var
        for mask in range(1 << n):
            if mask & bit and truth[mask] != truth[mask ^ bit]:
                support.add(ids[var])
                break

    top = probability_with(list(range(n)), {})
    measures: dict[str, dict] = {}
    for eid in ids:
        var = index[eid]
        others = [other for other in range(n) if other != var]
        q_plus = probability_with(others, {var: True}, skip=var)
        q_minus = probability_with(others, {var: False}, skip=var)
        birnbaum = q_plus - q_minus
        if top == 0:
            fussell_vesely = None
            raw = None
            rrw = None
        else:
            fussell_vesely = probs[eid] * birnbaum / top
            raw = q_plus / top
            rrw = None if q_minus == 0 else top / q_minus
        measures[eid] = {
            "q": probs[eid],
            "q_plus": q_plus,
            "q_minus": q_minus,
            "birnbaum": birnbaum,
            "fussell_vesely": fussell_vesely,
            "risk_achievement_worth": raw,
            "risk_reduction_worth": rrw,
        }
    return top, measures, support
