"""domain.model — 静态FTA契约对象（对齐 03_contracts/static_fta.schema.json）。

契约语义（窄域）：固定概率、独立基本变量、相干静态FTA。
- 一个 BasicEvent id = 一个失效事实 = 一个随机变量；多处引用同一 id 是同一变量
- 门与引用构成 DAG；重复引用保留同一变量（不复制）
- AND/OR 允许重复引用；VOTE(k/n) 要求不同输入引用且 k<=n
- 概率必须附条件/来源；不支持的门/分布必须显式拒绝（P0 纪律：不静默简化）
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction
from typing import Any, Iterable


class GateType(str, Enum):
    AND = "AND"
    OR = "OR"
    VOTE = "VOTE"  # k-of-n，仅接受互异输入引用

    @classmethod
    def supported(cls) -> frozenset[str]:
        return frozenset(t.value for t in cls)


SUPPORTED_GATES = GateType.supported()
# 契约显式阻塞的门语义（开工提示词边界 6：不静默简化）
BLOCKED_GATES = frozenset({
    "PAND", "SEQ", "XOR", "NOT", "INHIBIT", "PRIORITY",
    "FDEP", "CSP", "WSP", "PDP",  # 动态/共因/修复语义
})


class ContractError(ValueError):
    """契约违规（非法模型）。message 形如 'CYCLE: TOP'，与种子验证器对齐。"""


@dataclass
class BasicEvent:
    id: str
    probability: Fraction  # 固定概率；失效率/修复率必须先显式映射，禁止隐式转换
    description: str = ""
    source: str = ""       # 概率条件/来源（契约：输入概率必须附条件/来源）
    condition: str = ""    # 适用条件（如任务时长假设）

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "probability": str(self.probability),
            "description": self.description, "source": self.source,
            "condition": self.condition,
        }


@dataclass
class GateRef:
    """门输入引用：event_id 或 gate_id（同 id 多处出现=同一变量）。"""
    ref: str


@dataclass
class Gate:
    id: str
    type: GateType
    inputs: list[str] = field(default_factory=list)  # 引用 id 序列（可重复）
    vote_k: int | None = None                        # VOTE 专用
    description: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.type, str):
            t = self.type.upper()
            if t in BLOCKED_GATES:
                raise ContractError(f"UNSUPPORTED_GATE: {t}")
            if t not in SUPPORTED_GATES:
                raise ContractError(f"UNSUPPORTED_GATE: {t}")
            self.type = GateType(t)
        if self.type is GateType.VOTE:
            if self.vote_k is None:
                raise ContractError(f"VOTE_NO_K: {self.id}")
            if len(set(self.inputs)) != len(self.inputs):
                raise ContractError(f"VOTE_DUPLICATE_INPUT: {self.id}")
            if not 1 <= self.vote_k <= len(self.inputs):
                raise ContractError(
                    f"VOTE_K_OUT_OF_RANGE: {self.id} k={self.vote_k} n={len(self.inputs)}")


@dataclass
class FaultTree:
    name: str
    top_gate_id: str
    events: dict[str, BasicEvent] = field(default_factory=dict)
    gates: dict[str, Gate] = field(default_factory=dict)

    # ---------- 构造 ----------
    def add_event(self, ev: BasicEvent) -> None:
        if ev.id in self.events:
            raise ContractError(f"DUPLICATE_ID: {ev.id}")
        if not 0 <= ev.probability <= 1:
            raise ContractError(
                f"PROBABILITY: Invalid probability for {ev.id}: '{ev.probability}'")
        self.events[ev.id] = ev

    def add_gate(self, g: Gate) -> None:
        if g.id in self.gates:
            raise ContractError(f"DUPLICATE_ID: {g.id}")
        if g.id in self.events:
            raise ContractError(f"DUPLICATE_ID: {g.id}")
        self.gates[g.id] = g

    # ---------- 校验 ----------
    def validate(self) -> list[str]:
        """全量校验；返回错误列表（空=合法）。与种子验证器错误码对齐。"""
        errors: list[str] = []
        known = set(self.events) | set(self.gates)
        # 引用存在性
        for g in self.gates.values():
            for ref in g.inputs:
                if ref not in known:
                    errors.append(f"UNKNOWN_REFERENCE: {ref}")
        # 顶门存在
        if self.top_gate_id not in self.gates:
            errors.append(f"UNKNOWN_REFERENCE: {self.top_gate_id}")
        # 环检测（DAG 契约）
        if not errors:
            WHITE, GRAY, BLACK = 0, 1, 2
            color: dict[str, int] = {gid: WHITE for gid in self.gates}

            def dfs(gid: str) -> str | None:
                color[gid] = GRAY
                for ref in self.gates[gid].inputs:
                    if ref in self.gates:
                        if color[ref] == GRAY:
                            return ref
                        if color[ref] == WHITE:
                            found = dfs(ref)
                            if found:
                                return found
                color[gid] = BLACK
                return None

            for gid in self.gates:
                if color[gid] == WHITE:
                    cyc = dfs(gid)
                    if cyc:
                        errors.append(f"CYCLE: {cyc}")
                        break
        return errors

    # ---------- 序列化 ----------
    def to_contract_json(self) -> dict[str, Any]:
        return {
            "schema": "static-fta",
            "version": "0.1.0",
            "name": self.name,
            "top_gate_id": self.top_gate_id,
            "events": [e.to_dict() for e in self.events.values()],
            "gates": [{
                "id": g.id, "type": g.type.value, "inputs": list(g.inputs),
                **({"vote_k": g.vote_k} if g.vote_k is not None else {}),
                "description": g.description,
            } for g in self.gates.values()],
        }

    def semantic_hash(self) -> str:
        """semantic_model_hash：规范化语义哈希（契约 §3.2）。

        规范化：事件按 id 排序、门按 id 排序、概率用 Fraction 字符串、
        排除 description/布局等非语义字段。算法 v1。
        """
        canon = {
            "v": 1,
            "top": self.top_gate_id,
            "events": sorted(
                (e.id, str(e.probability)) for e in self.events.values()),
            "gates": sorted(
                (g.id, g.type.value, list(g.inputs),
                 g.vote_k if g.type is GateType.VOTE else None)
                for g in self.gates.values()),
        }
        blob = json.dumps(canon, sort_keys=True, ensure_ascii=True,
                          separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()


# ---------- 契约 JSON → FaultTree ----------
def from_contract_json(data: dict[str, Any]) -> FaultTree:
    """解析契约 JSON（cases.json 中案例结构）。违规即抛 ContractError。"""
    ft = FaultTree(name=data.get("name", "unnamed"),
                   top_gate_id=data.get("top_gate_id", ""))
    for ev in data.get("events", []):
        try:
            prob = Fraction(str(ev["probability"]))
        except (ValueError, ZeroDivisionError) as e:
            raise ContractError(
                f"PROBABILITY: Invalid probability for {ev.get('id')}: "
                f"{ev.get('probability')!r}") from e
        ft.add_event(BasicEvent(
            id=ev["id"], probability=prob,
            description=ev.get("description", ""),
            source=ev.get("source", ""), condition=ev.get("condition", "")))
    for g in data.get("gates", []):
        ft.add_gate(Gate(
            id=g["id"], type=g["type"], inputs=list(g.get("inputs", [])),
            vote_k=g.get("vote_k"), description=g.get("description", "")))
    errors = ft.validate()
    if errors:
        raise ContractError(errors[0])
    return ft
