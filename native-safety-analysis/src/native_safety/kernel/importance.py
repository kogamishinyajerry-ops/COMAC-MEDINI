"""Importance measures over the compiled ROBDD (exact; no new Boolean engine).

Definitions for a coherent static system with independent basic events:

    Q      = P(top)
    q+_i   = P(top | x_i = 1)      event i known FAILED
    q-_i   = P(top | x_i = 0)      event i known WORKING

The Shannon expansion of the compiled function splits the assignment space
on x_i into two DISJOINT parts, so with the exact rational probabilities used
throughout this engine

    Q = q_i * q+_i + (1 - q_i) * q-_i          (multilinearity identity)

holds exactly — no approximation beyond the declared independence of the
basic events. The standard PSA measures follow directly:

    Birnbaum importance       BI_i  = dQ/dq_i = q+_i - q-_i
    Fussell-Vesely            FV_i  = q_i * BI_i / Q = 1 - q-_i / Q
    Risk Achievement Worth    RAW_i = q+_i / Q
    Risk Reduction Worth      RRW_i = Q / q-_i

RAW/RRW use the RATIO convention (DOE/NASA PSA); the incremental forms
1 + dQ/Q are deliberately NOT used. FV equals the criticality importance of
the event when basic events are independent (the declared semantic here).

Algorithm — O(#nodes) for ALL variables at once, not O(#events x #nodes):
one bottom-up probability table (shared with the top-event probability) plus
ONE top-down pass carrying reach r(v) = probability mass of the assignments
above v that arrive at node v. A path that jumps over level i, or that ends at
a constant before level i, never mentions x_i, so it contributes the SAME
amount to q+_i and q-_i; those level-free masses are accumulated with a
difference array (range add over the skipped levels) and cancel out of BI_i.

Undefined measures are reported as None together with an explicit reason —
never replaced by a placeholder number:

  * Q = 0    -> FV, RAW, RRW are 0/0 or x/0 : all three undefined
  * q-_i = 0 -> RRW = Q/0 is unbounded (removing event i cannot make the
                system safe); RAW/FV remain defined while Q > 0

A variable that does not appear in the compiled function is reported with
in_support=False, BI=0, FV=0, RAW=1, RRW=1 rather than omitted, so callers
can see that the event exists but does not influence the top event.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .bdd import ONE, ZERO, _is_leaf

TOP_PROBABILITY_ZERO = "top_probability_is_zero"
RRW_UNBOUNDED = "risk_reduction_worth_unbounded"


@dataclass(frozen=True)
class ImportanceRecord:
    """Exact importance of one basic event w.r.t. the top event."""

    event_id: str
    in_support: bool
    probability: Fraction
    top_given_failed: Fraction
    top_given_working: Fraction
    birnbaum: Fraction
    fussell_vesely: Fraction | None
    risk_achievement_worth: Fraction | None
    risk_reduction_worth: Fraction | None
    undefined: tuple[tuple[str, str], ...] = ()

    def reason_for(self, measure: str) -> str | None:
        for name, reason in self.undefined:
            if name == measure:
                return reason
        return None


def compute_importance(
    root,
    mgr,
    probs: dict[str, Fraction],
    table: dict,
    top_probability: Fraction,
) -> list[ImportanceRecord]:
    """Importance for every variable of the manager, in variable order.

    `table` must be the node -> probability map of the SAME root (see
    `solve._probability_table`), so the top-event probability and the
    cofactors are guaranteed to come from one and the same diagram.
    """
    nlev = len(mgr.var_order)

    if _is_leaf(root):
        value = Fraction(1) if root == ONE else Fraction(0)
        return [
            _record(name, probs[name], value, value, top_probability, False)
            for name in mgr.var_order
        ]

    # 1. reachable non-leaf nodes, grouped by level
    by_level: dict[int, list] = {}
    seen: set = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if _is_leaf(node) or node in seen:
            continue
        seen.add(node)
        by_level.setdefault(node[0], []).append(node)
        stack.append(node[1])
        stack.append(node[2])

    # 2. top-down reach; level-free mass via a difference array.
    #    diff[i] starts a range at level i that runs to the level of the
    #    jumped-to child (exclusive), i.e. exactly the levels the path skipped.
    reach: dict = {root: Fraction(1)}
    diff = [Fraction(0)] * (nlev + 1)
    for level in range(nlev):
        level_nodes = by_level.get(level)
        if not level_nodes:
            continue
        p = probs[mgr.var_order[level]]
        for node in level_nodes:
            r = reach.get(node, Fraction(0))
            if not r:
                continue
            for child, weight in ((node[1], 1 - p), (node[2], p)):
                if not weight:
                    continue
                contrib = r * weight
                if _is_leaf(child):
                    child_level = nlev
                    value = Fraction(1) if child == ONE else Fraction(0)
                else:
                    child_level = child[0]
                    value = table[child]
                    reach[child] = reach.get(child, Fraction(0)) + contrib
                if value and child_level > level + 1:
                    diff[level + 1] += contrib * value
                    diff[child_level] -= contrib * value

    level_free = [Fraction(0)] * (nlev + 1)
    acc = Fraction(0)
    for i in range(nlev + 1):
        acc += diff[i]
        level_free[i] = acc

    # 3. per-level cofactor sums (all nodes at a level test the same variable)
    plus_sum = [Fraction(0)] * (nlev + 1)
    minus_sum = [Fraction(0)] * (nlev + 1)
    for level, nodes in by_level.items():
        sp = Fraction(0)
        sm = Fraction(0)
        for node in nodes:
            r = reach.get(node, Fraction(0))
            if not r:
                continue
            sp += r * table[node[2]]
            sm += r * table[node[1]]
        plus_sum[level] = sp
        minus_sum[level] = sm

    # 4. assemble, checking the multilinearity identity exactly for every event
    #
    # A variable whose level sits ABOVE the root is never tested by the
    # diagram, so every path is level-free and the cofactor is f itself.
    # (Levels at or below the root are covered by the reach/bypass split:
    # every path either reaches a level-i node or jumps over level i.)
    root_level = root[0]
    records: list[ImportanceRecord] = []
    for level, name in enumerate(mgr.var_order):
        q_i = probs[name]
        if level < root_level:
            q_plus = q_minus = top_probability
        else:
            q_plus = plus_sum[level] + level_free[level]
            q_minus = minus_sum[level] + level_free[level]
        if q_i * q_plus + (1 - q_i) * q_minus != top_probability:
            raise AssertionError(
                f"importance cofactor identity violated for event {name!r}: "
                f"q*BI decomposition does not reproduce the top probability"
            )
        records.append(
            _record(name, q_i, q_plus, q_minus, top_probability, level in by_level)
        )
    return records


def _record(
    event_id: str,
    q_i: Fraction,
    q_plus: Fraction,
    q_minus: Fraction,
    top_probability: Fraction,
    in_support: bool,
) -> ImportanceRecord:
    birnbaum = q_plus - q_minus
    undefined: list[tuple[str, str]] = []
    if top_probability == 0:
        fussell_vesely = None
        raw = None
        rrw = None
        undefined = [
            ("fussell_vesely", TOP_PROBABILITY_ZERO),
            ("risk_achievement_worth", TOP_PROBABILITY_ZERO),
            ("risk_reduction_worth", TOP_PROBABILITY_ZERO),
        ]
    else:
        fussell_vesely = q_i * birnbaum / top_probability
        raw = q_plus / top_probability
        if q_minus == 0:
            rrw = None
            undefined = [("risk_reduction_worth", RRW_UNBOUNDED)]
        else:
            rrw = top_probability / q_minus
    return ImportanceRecord(
        event_id=event_id,
        in_support=in_support,
        probability=q_i,
        top_given_failed=q_plus,
        top_given_working=q_minus,
        birnbaum=birnbaum,
        fussell_vesely=fussell_vesely,
        risk_achievement_worth=raw,
        risk_reduction_worth=rrw,
        undefined=tuple(undefined),
    )
