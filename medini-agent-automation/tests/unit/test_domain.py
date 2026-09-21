"""domain 层单测（全 synthetic——不触 medini）。"""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from medini_automation.domain.model import (
    BasicEvent, ContractError, FaultTree, Gate, from_contract_json,
)
from medini_automation.domain import reference

FIX = Path(__file__).resolve().parents[1] / "fixtures"


# ------------------------------------------------------------ 构造助手
def abc_tree() -> FaultTree:
    ft = FaultTree(name="slice_abc", top_gate_id="TOP")
    ft.add_event(BasicEvent("A", Fraction(1, 10)))
    ft.add_event(BasicEvent("B", Fraction(1, 5)))
    ft.add_event(BasicEvent("C", Fraction(3, 10)))
    ft.add_gate(Gate("G_AB", "AND", ["A", "B"]))
    ft.add_gate(Gate("G_AC", "AND", ["A", "C"]))
    ft.add_gate(Gate("TOP", "OR", ["G_AB", "G_AC"]))
    return ft


# ------------------------------------------------------------ 契约校验
class TestContract:
    def test_abc_valid(self):
        ft = abc_tree()
        assert ft.validate() == []

    def test_unknown_reference(self):
        ft = abc_tree()
        ft.gates["TOP"].inputs.append("GHOST")
        errs = ft.validate()
        assert any("UNKNOWN_REFERENCE" in e for e in errs)

    def test_cycle_detected(self):
        ft = FaultTree(name="cyc", top_gate_id="TOP")
        ft.add_event(BasicEvent("A", Fraction("0.1")))
        ft.add_gate(Gate("TOP", "OR", ["G1"]))
        ft.add_gate(Gate("G1", "OR", ["TOP"]))
        errs = ft.validate()
        assert any("CYCLE" in e for e in errs)

    def test_duplicate_id(self):
        ft = abc_tree()
        with pytest.raises(ContractError, match="DUPLICATE_ID"):
            ft.add_event(BasicEvent("A", Fraction("0.5")))

    def test_probability_out_of_range(self):
        ft = FaultTree(name="p", top_gate_id="T")
        with pytest.raises(ContractError, match="PROBABILITY"):
            ft.add_event(BasicEvent("A", Fraction("1.1")))

    def test_unsupported_gate_blocked(self):
        with pytest.raises(ContractError, match="UNSUPPORTED_GATE"):
            Gate("G", "PAND", ["A"])          # 动态门显式阻塞
        with pytest.raises(ContractError, match="UNSUPPORTED_GATE"):
            Gate("G", "XOR", ["A", "B"])      # 非相干门显式阻塞

    def test_vote_duplicate_input_rejected(self):
        with pytest.raises(ContractError, match="VOTE_DUPLICATE_INPUT"):
            Gate("G", "VOTE", ["A", "A"], vote_k=1)

    def test_vote_k_range(self):
        with pytest.raises(ContractError, match="VOTE_K_OUT_OF_RANGE"):
            Gate("G", "VOTE", ["A", "B"], vote_k=3)

    def test_from_contract_json_valid(self):
        ft = from_contract_json(json.loads(
            (FIX / "slice_abc.json").read_text(encoding="utf-8")))
        assert ft.name == "slice_abc"
        assert ft.validate() == []

    def test_same_id_same_variable(self):
        """同一事件 ID 多处引用 = 同一随机变量（契约 §3.1）。"""
        ft = abc_tree()
        assert ft.gates["G_AB"].inputs.count("A") == 1
        # 事件表中只有一个 A
        assert list(ft.events) == ["A", "B", "C"]


# ------------------------------------------------------------ 独立参考
class TestReference:
    def test_abc_exact_q(self):
        """T=(A∧B)∨(A∧C)，p=0.1/0.2/0.3 → 0.044（分支独立错误值 0.0494）。"""
        ft = abc_tree()
        q = reference.exact_q(ft)
        assert q == Fraction(44, 1000)          # 0.044 精确
        assert q != Fraction(494, 10000)        # 不是 0.0494

    def test_abc_mcs(self):
        ft = abc_tree()
        mcs = reference.minimal_cut_sets(ft)
        assert [list(c) for c in mcs] == [["A", "B"], ["A", "C"]]

    def test_or_tree(self):
        ft = from_contract_json(json.loads(
            (FIX / "slice_or_save.json").read_text(encoding="utf-8")))
        assert reference.exact_q(ft) == Fraction(7, 25)     # 0.28
        assert [list(c) for c in reference.minimal_cut_sets(ft)] == \
            [["A"], ["B"]]

    def test_seed_m03_repeated_event(self):
        """种子 M03 同款：重复引用保留同一变量。"""
        ft = FaultTree(name="m03", top_gate_id="TOP")
        ft.add_event(BasicEvent("A", Fraction("0.1")))
        ft.add_event(BasicEvent("B", Fraction("0.2")))
        ft.add_event(BasicEvent("C", Fraction("0.3")))
        ft.add_gate(Gate("G_AB", "AND", ["A", "B"]))
        ft.add_gate(Gate("G_AC", "AND", ["A", "C"]))
        ft.add_gate(Gate("TOP", "OR", ["G_AB", "G_AC"]))
        assert reference.exact_q(ft) == Fraction(11, 250)   # 0.044

    def test_seed_m05_vote(self):
        ft = FaultTree(name="m05", top_gate_id="TOP")
        for eid, p in (("A", "0.1"), ("B", "0.2"), ("C", "0.3")):
            ft.add_event(BasicEvent(eid, Fraction(p)))
        ft.add_gate(Gate("TOP", "VOTE", ["A", "B", "C"], vote_k=2))
        assert reference.exact_q(ft) == Fraction(49, 500)   # 0.098

    def test_reference_limit_over_20(self):
        ft = FaultTree(name="big", top_gate_id="TOP")
        for i in range(21):
            ft.add_event(BasicEvent(f"E{i}", Fraction("0.01")))
        ft.add_gate(Gate("TOP", "OR", [f"E{i}" for i in range(21)]))
        with pytest.raises(ValueError, match="REFERENCE_LIMIT"):
            reference.exact_q(ft)


# ------------------------------------------------------------ 语义哈希
class TestSemanticHash:
    def test_deterministic(self):
        assert abc_tree().semantic_hash() == abc_tree().semantic_hash()

    def test_insensitive_to_description(self):
        ft1 = abc_tree()
        ft2 = abc_tree()
        ft2.events["A"].description = "改了描述不影响语义"
        assert ft1.semantic_hash() == ft2.semantic_hash()

    def test_sensitive_to_probability(self):
        ft1 = abc_tree()
        ft2 = abc_tree()
        ft2.events["A"] = BasicEvent("A", Fraction(2, 10))
        assert ft1.semantic_hash() != ft2.semantic_hash()
