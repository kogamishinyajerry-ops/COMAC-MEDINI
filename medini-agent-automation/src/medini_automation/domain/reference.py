"""domain.reference — 独立数学参考（有理数穷举，Bessel 窗口上限 20 基本事件）。

角色：独立预期值生成器（种子包同款语义），用于对 medini 实机结果的三角校核。
这不是生产求解器，也不是 medini 替代品；>20 事件显式拒绝。
"""
from __future__ import annotations

from fractions import Fraction

from .model import FaultTree, GateType

MAX_EVENTS = 20  # 种子包同款上限


def evaluate_gate(ft: FaultTree, gid: str, assign: dict[str, bool]) -> bool:
    """在给定基本事件真值指派下求门布尔值（重复引用=同一变量）。"""
    g = ft.gates[gid]
    vals: list[bool] = []
    for ref in g.inputs:
        if ref in ft.events:
            vals.append(assign[ref])
        else:
            vals.append(evaluate_gate(ft, ref, assign))
    if g.type is GateType.AND:
        return all(vals)
    if g.type is GateType.OR:
        return any(vals)
    # VOTE k-of-n（inputs 互异已由契约保证）
    assert g.vote_k is not None
    return sum(vals) >= g.vote_k


def exact_q(ft: FaultTree) -> Fraction:
    """顶事件精确概率：2^n 穷举（n<=20），全有理数。"""
    n = len(ft.events)
    if n > MAX_EVENTS:
        raise ValueError(
            f"REFERENCE_LIMIT: {n} events > {MAX_EVENTS}（穷举参考上限，不是求解器）")
    ids = sorted(ft.events)
    total = Fraction(0)
    for mask in range(1 << n):
        assign = {ids[i]: bool((mask >> i) & 1) for i in range(n)}
        if evaluate_gate(ft, ft.top_gate_id, assign):
            p = Fraction(1)
            for i, eid in enumerate(ids):
                p *= ft.events[eid].probability if assign[eid] \
                    else (1 - ft.events[eid].probability)
            total += p
    return total


def minimal_cut_sets(ft: FaultTree) -> list[tuple[str, ...]]:
    """MCS：按事件数升序枚举割集并吸收去重（幂等：同一 id 的多次引用先去重）。"""
    n = len(ft.events)
    if n > MAX_EVENTS:
        raise ValueError(f"REFERENCE_LIMIT: {n} events > {MAX_EVENTS}")
    ids = sorted(ft.events)
    cut_sets: list[frozenset[str]] = []
    for mask in range(1, 1 << n):
        assign = {ids[i]: bool((mask >> i) & 1) for i in range(n)}
        if evaluate_gate(ft, ft.top_gate_id, assign):
            cs = frozenset(e for e in ids if assign[e])
            # 吸收：只保留极小割集
            if not any(other < cs for other in cut_sets):
                cut_sets = [o for o in cut_sets if not cs < o] + [cs]
    # 排序：基数升序、字典序
    cut_sets.sort(key=lambda s: (len(s), sorted(s)))
    return [tuple(sorted(s)) for s in cut_sets]


def q_of_cut_sets(ft: FaultTree, mcs: list[tuple[str, ...]]) -> Fraction:
    """割集概率之和（稀有事件近似上界）——仅作对照，不替代 exact_q。"""
    total = Fraction(0)
    for cs in mcs:
        p = Fraction(1)
        for e in cs:
            p *= ft.events[e].probability
        total += p
    return total
