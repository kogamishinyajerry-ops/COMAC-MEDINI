"""Reduced Ordered Binary Decision Diagram (ROBDD) — production kernel core.

Classic Bryant-style implementation: unique-node table, terminal nodes 0/1,
apply (ite) operator with computed cache, per-manager node ceiling. All
identities canonical, hashable as tuples. Pure computation: no I/O, no
model parsing, no external calls.

apply is iterative (explicit stack) so deep gate chains cannot overflow
the Python call stack.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.errors import ResourceLimitError

# Terminal node sentinels (never in the variable table)
ZERO = ("leaf", 0)
ONE = ("leaf", 1)

_INF_LEVEL = 1 << 30


class BddNodeLimit(ResourceLimitError):
    def __init__(self, count: int, limit: int) -> None:
        self.count = count
        self.limit = limit
        super().__init__(f"BDD node limit exceeded: {count} > {limit}", node_count=count)


def _is_leaf(f) -> bool:
    return isinstance(f[0], str)


def _top_level(f) -> int:
    """Level index of the top variable of f (terminals -> +inf)."""
    return _INF_LEVEL if _is_leaf(f) else f[0]


def _cofactors(f, top: int):
    """Cofactors of f w.r.t. the variable at level `top` (f must mention it)."""
    if _is_leaf(f) or f[0] != top:
        return f, f
    return f[1], f[2]


@dataclass
class BddManager:
    """One manager owns one variable order and one unique table."""

    var_order: tuple[str, ...]  # variable name at each level (level 0 = root-most)
    max_nodes: int = 1_000_000

    unique: dict = field(default_factory=dict, repr=False)
    computed: dict = field(default_factory=dict, repr=False)
    _var_level: dict[str, int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._var_level = {name: i for i, name in enumerate(self.var_order)}
        if len(self._var_level) != len(self.var_order):
            raise ValueError("duplicate variable in var_order")

    # -- construction ----------------------------------------------------
    def mk(self, var_index: int, low, high) -> tuple:
        """Make node with reduction rules; var_index is a LEVEL index."""
        if low == high:
            return low
        key = (var_index, low, high)
        node = self.unique.get(key)
        if node is None:
            if len(self.unique) + 1 > self.max_nodes:
                raise BddNodeLimit(len(self.unique), self.max_nodes)
            node = key
            self.unique[key] = node
        return node

    def var_node(self, name: str) -> tuple:
        return self.mk(self._var_level[name], ZERO, ONE)

    # -- operators -------------------------------------------------------
    def apply(self, op: str, f, g):
        """Iterative Boolean operator on BDDs with memoized results.

        op ∈ {"and", "or"}; cache keyed by canonical (op, min(f,g), max(f,g)).
        """
        if op not in ("and", "or"):
            raise ValueError(f"unknown op {op!r}")

        # Explicit terminal shortcuts, then iterative expansion.
        stack = [(op, f, g)]
        result: dict = {}
        order: list = []
        while stack:
            frame = stack.pop()
            key = (frame[0], frame[1], frame[2])
            if key in result:
                continue
            _, ff, gg = frame
            # terminal simplifications
            if op == "and":
                if ff == ZERO or gg == ZERO:
                    result[key] = ZERO
                    continue
                if ff == ONE:
                    result[key] = gg
                    continue
                if gg == ONE:
                    result[key] = ff
                    continue
                if ff == gg:
                    result[key] = ff
                    continue
            else:
                if ff == ONE or gg == ONE:
                    result[key] = ONE
                    continue
                if ff == ZERO:
                    result[key] = gg
                    continue
                if gg == ZERO:
                    result[key] = ff
                    continue
                if ff == gg:
                    result[key] = ff
                    continue
            # canonical unordered memo key (commutative op); avoids any
            # ordering between structurally different node tuples
            memo = (op, ff, gg)
            hit = self.computed.get(memo)
            if hit is None and gg != ff:
                hit = self.computed.get((op, gg, ff))
                if hit is not None:
                    memo = (op, gg, ff)
            if hit is not None:
                result[key] = hit
                continue
            top = min(_top_level(ff), _top_level(gg))
            flo, fhi = _cofactors(ff, top)
            glo, ghi = _cofactors(gg, top)
            lo_key = (op, flo, glo)
            hi_key = (op, fhi, ghi)
            pending = []
            if lo_key not in result:
                pending.append(lo_key)
            if hi_key not in result:
                pending.append(hi_key)
            if pending:
                stack.append(frame)  # revisit after children
                for p in pending:
                    stack.append(p)
                continue
            value = self.mk(top, result[lo_key], result[hi_key])
            self.computed[memo] = value
            result[key] = value
        return result[(op, f, g)]

    # -- traversal helpers -------------------------------------------------
    def node_count(self, f) -> int:
        seen: set = set()
        stack = [f]
        while stack:
            node = stack.pop()
            if _is_leaf(node) or node in seen:
                continue
            seen.add(node)
            stack.append(node[1])
            stack.append(node[2])
        return len(seen)

    def compact(self, roots) -> int:
        """Mark-sweep: keep only nodes reachable from live roots.

        Returns the new table size. The computed cache is cleared (it may
        reference dead nodes; correctness unaffected — apply recomputes).
        """
        reachable: set = set()
        stack = [r for r in roots if not _is_leaf(r)]
        while stack:
            node = stack.pop()
            if node in reachable:
                continue
            reachable.add(node)
            for child in (node[1], node[2]):
                if not _is_leaf(child) and child not in reachable:
                    stack.append(child)
        self.unique = {node: node for node in reachable}
        self.computed.clear()
        return len(self.unique)

    def satisfying_paths(self, f, max_paths: int = 1_000_000):
        """Yield (positive_vars, negative_vars) for each root-to-ONE path.

        Output count is bounded by max_paths; exceeding it raises
        ResourceLimitError (callers must mark results incomplete, never
        silently truncate).
        """
        emitted: list = []

        def walk(node, pos: tuple, neg: tuple) -> None:
            if node == ONE:
                emitted.append((pos, neg))
                if len(emitted) > max_paths:
                    raise ResourceLimitError(
                        f"satisfying path count exceeds limit {max_paths}", node_count=len(emitted)
                    )
                return
            if node == ZERO:
                return
            var = self.var_order[node[0]]
            walk(node[1], pos, neg + (var,))
            walk(node[2], pos + (var,), neg)

        walk(f, (), ())
        return emitted
