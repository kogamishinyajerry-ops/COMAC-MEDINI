"""Kernel solve pipeline: model -> ROBDD -> exact probability + cut sets + importance.

Variable order: deterministic reverse top-down DFS discovery order from the
top event (children visited in declared input order; unreachable events
appended sorted). Stable across runs and machines, and recorded in run output.

Probability: exact rational Shannon expansion over the reduced BDD —
P(node) = (1-p_v)·P(low) + p_v·P(high), memoized, computed bottom-up by
level. No floating point in the compute path.

Importance: Birnbaum / Fussell-Vesely / RAW / RRW from the SAME probability
table plus one top-down reach pass (see kernel/importance.py).

Minimal cut sets: every root-to-ONE path's positive variable set is a
satisfying assignment (structure-only, probabilities NOT considered);
the inclusion-minimal family of these sets equals the minimal cut sets
of the coherent function. Path enumeration is capped; exceeding the cap
raises ResourceLimitError and the run is reported as truncated — never
silently cut.

K_OF_N compiles via the negation-free threshold recursion
T(j, c) = (x_j AND T(j+1, c+1)) OR T(j+1, c), correct for arbitrary BDD
inputs and O(k·n) for plain variables.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from fractions import Fraction

from ..domain.errors import ResourceLimitError
from ..domain.model import StaticFtaModel
from .bdd import ONE, ZERO, BddManager, _is_leaf
from .importance import ImportanceRecord, compute_importance

DEFAULT_MAX_NODES = 1_000_000
DEFAULT_MAX_MCS_PATHS = 200_000
# Importance is O(#nodes) but emits one record per basic event; auto mode
# skips very wide models so the result payload stays reviewable.
DEFAULT_IMPORTANCE_MAX_EVENTS = 1000

IMPORTANCE_AUTO = "auto"
IMPORTANCE_MODES = ("auto", "on", "off")

STATUS_COMPUTED = "computed"
STATUS_NOT_REQUESTED = "not_requested"
STATUS_SKIPPED_WIDE = "skipped_model_too_wide"


@dataclass
class SolveResult:
    top_probability: Fraction
    minimal_cut_sets: list[list[str]]
    bdd_node_count: int
    variable_order: tuple[str, ...]
    elapsed_seconds: float
    cut_sets_complete: bool = True
    limits: dict[str, int] | None = None
    importance: list[ImportanceRecord] | None = None
    importance_status: str = STATUS_NOT_REQUESTED
    importance_note: str | None = None


def solve_model(
    model: StaticFtaModel,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_mcs_paths: int = DEFAULT_MAX_MCS_PATHS,
    importance: str = IMPORTANCE_AUTO,
    importance_max_events: int = DEFAULT_IMPORTANCE_MAX_EVENTS,
) -> SolveResult:
    """Compile and solve a validated model. Raises on resource limits."""
    if importance not in IMPORTANCE_MODES:
        raise ValueError(f"unknown importance mode {importance!r}")
    started = time.perf_counter()
    var_order = _variable_order(model)
    mgr = BddManager(var_order=var_order, max_nodes=max_nodes)
    root = _compile(model, mgr)

    # One table serves both the top-event probability and the importance
    # cofactors, so the two can never disagree about the same diagram.
    table = _probability_table(root, mgr, model.probabilities)
    probability = table[root]
    cut_sets, complete = _minimal_cut_sets(root, mgr, max_mcs_paths)

    records: list[ImportanceRecord] | None = None
    status = STATUS_NOT_REQUESTED
    note: str | None = None
    if importance == "off":
        note = "importance computation disabled by request"
    elif importance == "auto" and len(var_order) > importance_max_events:
        status = STATUS_SKIPPED_WIDE
        note = (
            f"auto mode skipped importance for {len(var_order)} basic events "
            f"(limit {importance_max_events}); re-run with --importance on to force it"
        )
    else:
        records = compute_importance(root, mgr, model.probabilities, table, probability)
        status = STATUS_COMPUTED

    return SolveResult(
        top_probability=probability,
        minimal_cut_sets=cut_sets,
        bdd_node_count=mgr.node_count(root),
        variable_order=var_order,
        elapsed_seconds=time.perf_counter() - started,
        cut_sets_complete=complete,
        limits={"max_nodes": max_nodes, "max_mcs_paths": max_mcs_paths},
        importance=records,
        importance_status=status,
        importance_note=note,
    )


def _variable_order(model: StaticFtaModel) -> tuple[str, ...]:
    """Event IDs ordered by reverse top-down DFS discovery from the top event.

    Heuristic: variables closer to the top event decide first. Keeps
    chain-structured models linear (each new gate extends the BDD at the
    root side) and is fully deterministic (children visited in declared
    input order; ties broken by sorted gate IDs). Events unreachable from
    the top event are appended sorted at the end (they cannot affect the
    function but stay in the order for reproducibility).
    """
    gate_by_id = {g.id: g for g in model.gates}
    order: list[str] = []
    seen: set[str] = set()
    stack: list[str] = [model.top_event]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        gate = gate_by_id.get(node)
        if gate is None:
            order.append(node)  # basic event
            continue
        for child in reversed(gate.inputs):
            if child not in seen:
                stack.append(child)
    unreachable = sorted(set(model.probabilities) - set(order))
    return tuple(order + unreachable)


# -- compilation ---------------------------------------------------------


def _compile(model: StaticFtaModel, mgr: BddManager) -> tuple:
    gate_by_id = {g.id: g for g in model.gates}
    if not gate_by_id:
        return mgr.var_node(model.top_event)  # top is a basic event

    # Only gates reachable from the top event participate in the computation.
    reachable: set[str] = set()
    stack = [model.top_event]
    while stack:
        node = stack.pop()
        if node in reachable:
            continue
        reachable.add(node)
        gate = gate_by_id.get(node)
        if gate is not None:
            stack.extend(gate.inputs)
    active_gates = {gid: g for gid, g in gate_by_id.items() if gid in reachable}

    # Same-kind flattening: OR(OR(a,b),c) builds as OR(a,b,c) by
    # associativity. A gate is spliced into its parent ONLY when the parent
    # is AND/OR of the same kind; every other gate reference needs its own
    # BDD (in particular nested K_OF_N gates are never spliced).
    needed: set[str] = {model.top_event}
    for g in active_gates.values():
        for ref in g.inputs:
            child = active_gates.get(ref)
            if child is None:
                continue
            spliced = g.kind in ("AND", "OR") and child.kind == g.kind
            if not spliced:
                needed.add(ref)

    flat_memo: dict[str, list[str]] = {}

    def flat_inputs(gate) -> list[str]:
        """Same-kind flattening, iterative (deep chains must not recurse).

        A gate's flat operand list splices in the flat lists of same-kind
        gate children (AND/OR associativity); other refs stay as-is.
        """
        if gate.id in flat_memo:
            return flat_memo[gate.id]
        # collect the same-kind spine, then fill bottom-up
        spine: list[str] = []
        seen: set[str] = set()
        work: list[str] = [gate.id]
        while work:
            gid = work.pop()
            if gid in seen:
                continue
            seen.add(gid)
            g = active_gates[gid]
            spine.append(gid)
            for ref in g.inputs:
                child = active_gates.get(ref)
                if child is not None and child.kind == gate.kind and gate.kind in ("AND", "OR"):
                    work.append(ref)
        for gid in spine:  # spine is rough bottom-up; fill any missing
            if gid in flat_memo:
                continue
            g = active_gates[gid]
            flat: list[str] = []
            ready = True
            for ref in g.inputs:
                child = active_gates.get(ref)
                if child is not None and child.kind == gate.kind and gate.kind in ("AND", "OR"):
                    if child.id in flat_memo:
                        flat.extend(flat_memo[child.id])
                    else:
                        ready = False
                        break
                else:
                    flat.append(ref)
            if ready:
                flat_memo[gid] = flat
        # multi-pass until the root memo exists (spine order is approximate)
        while gate.id not in flat_memo:
            progress = False
            for gid in spine:
                if gid in flat_memo:
                    continue
                g = active_gates[gid]
                flat = []
                ready = True
                for ref in g.inputs:
                    child = active_gates.get(ref)
                    if child is not None and child.kind == gate.kind and gate.kind in ("AND", "OR"):
                        if child.id in flat_memo:
                            flat.extend(flat_memo[child.id])
                        else:
                            ready = False
                            break
                    else:
                        flat.append(ref)
                if ready:
                    flat_memo[gid] = flat
                    progress = True
            if not progress:
                raise ResourceLimitError("same-kind flattening stalled (cycle escaped validation)")
        return flat_memo[gate.id]

    # Kahn topological order over active gate->gate edges (validation guarantees DAG)
    dependents: dict[str, list[str]] = {}
    indeg: dict[str, int] = {}
    for g in active_gates.values():
        gate_children = [c for c in g.inputs if c in active_gates]
        indeg[g.id] = len(gate_children)
        for c in gate_children:
            dependents.setdefault(c, []).append(g.id)
    queue = sorted(gid for gid, d in indeg.items() if d == 0)
    built: dict[str, tuple] = {}
    while queue:
        gid = queue.pop(0)
        if gid in needed:
            gate = active_gates[gid]
            flat = flat_inputs(gate)
            built[gid] = _build_flat(gate.kind, gate.k, flat, built, mgr)
        # gates not in `needed` are spliced into same-kind parents; no BDD
        for dep in dependents.get(gid, ()):
            indeg[dep] -= 1
            if indeg[dep] == 0:
                queue.append(dep)
    if len(built) != len(needed):
        raise ResourceLimitError("gate dependency resolution stalled (cycle escaped validation)")
    return built[model.top_event]


def _build_flat(kind: str, k: int | None, refs: list[str], built: dict[str, tuple], mgr: BddManager) -> tuple:
    resolved = [built.get(ref, None) or mgr.var_node(ref) for ref in refs]
    if kind == "AND":
        return _combine(mgr, "and", resolved)
    if kind == "OR":
        return _combine(mgr, "or", resolved)
    if kind == "K_OF_N":
        return _k_of_n_bdd(k, resolved, mgr)
    raise ValueError(f"unsupported gate kind {kind!r} escaped validation")


def _combine(mgr: BddManager, op: str, nodes: list) -> tuple:
    """Balanced pairwise reduction (associative op) with inter-round GC.

    Sequential folding rebuilds the whole chain per step (O(n^2) nodes);
    balanced merging plus mark-sweep compaction keeps the unique table
    proportional to live nodes. Semantics identical by associativity.
    """
    items = list(nodes)
    while len(items) > 1:
        nxt = [mgr.apply(op, items[i], items[i + 1]) for i in range(0, len(items) - 1, 2)]
        if len(items) % 2:
            nxt.append(items[-1])
        items = nxt
        mgr.compact(items)  # drop nodes unreachable from current partials
    return items[0]


def _k_of_n_bdd(k: int, resolved: list, mgr: BddManager) -> tuple:
    """T(j, c) = (x_j AND T(j+1, c+1)) OR T(j+1, c) — negation-free threshold."""
    n = len(resolved)
    dp_next: dict[int, tuple] = {c: (ONE if c >= k else ZERO) for c in range(k + 1)}
    for j in range(n - 1, -1, -1):
        dp_cur: dict[int, tuple] = {}
        for c in range(k + 1):
            if c >= k:
                dp_cur[c] = ONE
            elif k - c > n - j:
                dp_cur[c] = ZERO
            else:
                hi = mgr.apply("and", resolved[j], dp_next[c + 1])
                lo = dp_next[c]
                dp_cur[c] = mgr.apply("or", hi, lo)
        dp_next = dp_cur
    return dp_next[0]


# -- probability ----------------------------------------------------------


def _probability_table(root: tuple, mgr: BddManager, probs: dict[str, Fraction]) -> dict:
    """Exact Shannon expansion over the BDD, iterative bottom-up by level.

    Returns node -> probability for every node reachable from `root`, with
    the two terminals included (ONE -> 1, ZERO -> 0). Shared by the top-event
    probability and by the importance cofactor pass.
    """
    memo: dict[tuple, Fraction] = {ONE: Fraction(1), ZERO: Fraction(0)}
    if _is_leaf(root):
        return memo
    nodes: list[tuple] = []
    seen: set = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if _is_leaf(node) or node in seen:
            continue
        seen.add(node)
        nodes.append(node)
        stack.append(node[1])
        stack.append(node[2])
    for node in sorted(nodes, key=lambda n: n[0], reverse=True):  # deepest first
        var = mgr.var_order[node[0]]
        p = probs[var]
        memo[node] = (1 - p) * memo[node[1]] + p * memo[node[2]]
    return memo


def _probability(root: tuple, mgr: BddManager, probs: dict[str, Fraction]) -> Fraction:
    return _probability_table(root, mgr, probs)[root]


# -- minimal cut sets ------------------------------------------------------


def _minimal_cut_sets(root: tuple, mgr: BddManager, max_paths: int) -> tuple[list[list[str]], bool]:
    """Structure-only MCS from ONE-paths; capped, truncation reported honestly.

    Positives are tracked as BIT MASKS (bit i = variable at level i) so low
    branches share one immutable int and high branches cost a single OR.
    Minimality via popcount sort + bitwise subset tests. Iterative — no
    recursion limit issues on deep chains.
    """
    if _is_leaf(root):
        return ([[]] if root == ONE else []), True
    collected: set[int] = set()
    truncated = False
    # stack of (node, positives_mask)
    stack: list[tuple[tuple, int]] = [(root, 0)]
    while stack:
        node, mask = stack.pop()
        if node == ONE:
            collected.add(mask)
            if len(collected) > max_paths:
                truncated = True
                break
            continue
        if node == ZERO:
            continue
        bit = 1 << node[0]
        stack.append((node[2], mask | bit))  # high: var true
        stack.append((node[1], mask))  # low: var false

    minimal_masks = _minimize_masks(collected)
    mcs = [
        sorted(mgr.var_order[i] for i in range(m.bit_length()) if mask_bit(m, i))
        for m in minimal_masks
    ]
    return sorted(mcs), not truncated


def mask_bit(m: int, i: int) -> bool:
    return bool(m & (1 << i))


def _minimize_masks(masks: set[int]) -> list[int]:
    """Inclusion-minimal subfamily via popcount sort + bitwise subset tests."""
    ordered = sorted(masks, key=lambda m: (m.bit_count(), m))
    minimal: list[int] = []
    for m in ordered:
        if not any((x & m) == x for x in minimal):
            minimal.append(m)
    return minimal


def _minimize(families: list[set[str]]) -> list[list[str]]:
    """Inclusion-minimal subfamily (small sets first, dedupe, drop supersets)."""
    unique: list[set[str]] = []
    seen: set[frozenset[str]] = set()
    for s in families:
        fs = frozenset(s)
        if fs not in seen:
            seen.add(fs)
            unique.append(s)
    unique.sort(key=len)
    minimal: list[set[str]] = []
    for s in unique:
        if not any(m <= s for m in minimal):
            minimal.append(s)
    return sorted(sorted(s) for s in minimal)
