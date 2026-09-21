"""适配器层单测：XML 生成 + CLI 桥参数（synthetic，不触 medini）。"""
from __future__ import annotations

import json
import re
from fractions import Fraction
from pathlib import Path

import pytest

from medini_automation.adapters.ftplus import generate_faulttreeplus_xml
from medini_automation.domain.model import (
    BasicEvent, FaultTree, Gate, from_contract_json,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _xml(case: str) -> str:
    ft = from_contract_json(json.loads(
        (FIX / case).read_text(encoding="utf-8")))
    return generate_faulttreeplus_xml(ft)


class TestXmlContract:
    """编码契约逐条对齐（FTMinimal.xsd + importer 反编译实证）。"""

    def test_root_and_blocks(self):
        xml = _xml("slice_abc.json")
        assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert "<XMLExport>" in xml and "</XMLExport>" in xml
        for tag in ("FailureModels", "PrimaryEvents", "Gates", "GateInputs"):
            assert f"<{tag}>" in xml, tag

    def test_fixed_model_unavailability(self):
        xml = _xml("slice_abc.json")
        assert "<ModelType>Fixed</ModelType>" in xml
        # A=0.1 → 1.000000000000E-01（.12E = 13 位有效 ≥ 12 位要求）
        assert "<Unavailability>1.000000000000E-01</Unavailability>" in xml

    def test_object_type_with_space(self):
        xml = _xml("slice_abc.json")
        assert "<ObjectType>Primary event</ObjectType>" in xml
        assert "<ObjectType>Gate</ObjectType>" in xml

    def test_vote_uses_vote_element(self):
        ft = FaultTree(name="v", top_gate_id="TOP")
        for eid, p in (("A", "0.1"), ("B", "0.2"), ("C", "0.3")):
            ft.add_event(BasicEvent(eid, Fraction(p)))
        ft.add_gate(Gate("TOP", "VOTE", ["A", "B", "C"], vote_k=2))
        xml = generate_faulttreeplus_xml(ft)
        assert "<Vote>2</Vote>" in xml
        assert "VoteInputCount" not in xml      # 错误元素名不得出现

    def test_shared_event_single_primary_entry(self):
        """A 被两个门引用 → PrimaryEvents 只一条，两个 GateInputs 同 ObjectIndex。"""
        xml = _xml("slice_abc.json")
        assert len(re.findall(r"<PrimaryEvents>", xml)) == 3   # A/B/C 各一条
        # A 的 ObjectIndex=0；G_AB 与 G_AC 的输入 0 都指向 0
        a_refs = re.findall(
            r"<GateInputs>\s*<Gate>G_A[BC]</Gate>\s*<ObjectIndex>0</ObjectIndex>",
            xml)
        assert len(a_refs) == 2

    def test_object_index_matches_doc_order(self):
        xml = _xml("slice_abc.json")
        # 文档序：events A,B,C → idx 0,1,2；gates G_AB,G_AC,TOP → idx 0,1,2
        m = re.search(
            r"<GateInputs>\s*<Gate>TOP</Gate>\s*<ObjectIndex>0</ObjectIndex>"
            r"\s*<SubIndex>0</SubIndex>\s*<ObjectType>Gate</ObjectType>",
            xml)
        assert m, "TOP 的第一个输入应指向 gate idx 0 (G_AB)"

    def test_xml_is_parseable(self):
        import xml.etree.ElementTree as ET
        ET.fromstring(_xml("slice_abc.json"))
        ET.fromstring(_xml("slice_or_save.json"))

    def test_desc_escaped(self):
        ft = FaultTree(name="e", top_gate_id="TOP")
        ft.add_event(BasicEvent("A", Fraction("0.1"),
                                description="含<与&符号"))
        ft.add_gate(Gate("TOP", "OR", ["A"]))
        xml = generate_faulttreeplus_xml(ft)
        assert "含&lt;与&amp;符号" in xml


class TestCliBridge:
    def test_medini_unavailable_when_exe_missing(self, tmp_path):
        from medini_automation.adapters.medini_cli import (
            MediniUnavailable, run_headless)
        with pytest.raises(MediniUnavailable, match="MEDINI_EXE_MISSING"):
            run_headless(
                tmp_path / "no.js", exe=tmp_path / "no.exe",
                workspace=tmp_path, project=tmp_path)

    def test_application_id_contract(self):
        """application ID 契约固定（de.ikv.analyze.cli.script 不存在）。"""
        from medini_automation.adapters.medini_cli import APPLICATION_ID
        assert APPLICATION_ID == "de.ikv.analyze.product.analyzeApplication"
