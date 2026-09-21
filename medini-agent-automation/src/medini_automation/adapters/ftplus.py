"""adapters.ftplus — FaultTree XML 生成器（契约 FaultTree → medini FaultTreePlus 格式）。

编码契约（FTMinimal.xsd + importer 反编译 + 2026-08-19 6/6 实测导入实证）：
- 根 <XMLExport>；四大块 FailureModels / PrimaryEvents / Gates / GateInputs
- ObjectType ∈ {"Gate", "Primary event"}（带空格）
- ObjectIndex = PrimaryEvents/Gates 列表 0-based 文档序索引
- SubIndex = 每门运行序号（importer 重算，从 0 计）
- ModelType=Fixed + <Unavailability> 直接收概率值（有理数转十进制，保 12 位有效）
- VOTE 阈值元素是 <Vote>（xs:int）
重复引用：同一基本事件在多处被引用时，只出一个 PrimaryEvents 条目、
多个 GateInputs 指向同一 ObjectIndex（同一随机变量契约）。
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from ..domain.model import FaultTree, GateType

GATE_TYPE_XML = {GateType.AND: "AND", GateType.OR: "OR", GateType.VOTE: "VOTE"}


def _prob_to_decimal(p: Fraction, sig: int = 12) -> str:
    """有理概率 → 定点十进制字符串（默认 12 位有效，规避导入端舍入）。

    例：Fraction(1,10) → "1.00000000000E-01"（尾数满 12 位有效数字）。
    """
    if p == 0:
        return "0.00000000000E+00"
    if p == 1:
        return "1.00000000000E+00"
    # 十进制浮点展开（Fraction 精确 → float 64bit 足够 12 位有效）
    s = f"{float(p):.{sig}E}"
    return s


def generate_faulttreeplus_xml(ft: FaultTree) -> str:
    """生成 medini 可导入的 FaultTreePlus XML 文本。"""
    # 文档序 = 插入序（dict 保序）；ObjectIndex 按此序
    event_ids = list(ft.events.keys())
    gate_ids = list(ft.gates.keys())
    ev_index = {eid: i for i, eid in enumerate(event_ids)}
    gate_index = {gid: i for i, gid in enumerate(gate_ids)}

    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append("<XMLExport>")

    # ---- FailureModels：每基本事件一个 Fixed 模型 ----
    for eid in event_ids:
        ev = ft.events[eid]
        lines.append("  <FailureModels>")
        lines.append(f"    <Id>FM_{eid}</Id>")
        lines.append("    <ModelType>Fixed</ModelType>")
        lines.append(f"    <Unavailability>{_prob_to_decimal(ev.probability)}</Unavailability>")
        desc = (ev.description or "").replace("&", "&amp;").replace("<", "&lt;")
        lines.append(f"    <Description>{desc}</Description>")
        lines.append("  </FailureModels>")

    # ---- PrimaryEvents ----
    for eid in event_ids:
        ev = ft.events[eid]
        lines.append("  <PrimaryEvents>")
        lines.append(f"    <Id>{eid}</Id>")
        lines.append("    <EventType>Basic</EventType>")
        lines.append(f"    <FailureModel>FM_{eid}</FailureModel>")
        desc = (ev.description or "").replace("&", "&amp;").replace("<", "&lt;")
        lines.append(f"    <Description>{desc}</Description>")
        lines.append("  </PrimaryEvents>")

    # ---- Gates ----
    for gid in gate_ids:
        g = ft.gates[gid]
        lines.append("  <Gates>")
        lines.append(f"    <Id>{gid}</Id>")
        lines.append(f"    <Type>{GATE_TYPE_XML[g.type]}</Type>")
        if g.type is GateType.VOTE:
            lines.append(f"    <Vote>{g.vote_k}</Vote>")
        lines.append(f"    <TotalInputCount>{len(g.inputs)}</TotalInputCount>")
        lines.append("    <DependentETs>0</DependentETs>")
        lines.append("    <DependentGates>0</DependentGates>")
        lines.append("    <NoDependentETs>0</NoDependentETs>")
        lines.append("    <NoDependentGates>0</NoDependentGates>")
        desc = (g.description or "").replace("&", "&amp;").replace("<", "&lt;")
        lines.append(f"    <Description>{desc}</Description>")
        lines.append("  </Gates>")

    # ---- GateInputs（SubIndex 每门从 0 递增）----
    for gid in gate_ids:
        g = ft.gates[gid]
        for sub, ref in enumerate(g.inputs):
            if ref in ev_index:
                obj_index, obj_type = ev_index[ref], "Primary event"
            else:
                obj_index, obj_type = gate_index[ref], "Gate"
            lines.append("  <GateInputs>")
            lines.append(f"    <Gate>{gid}</Gate>")
            lines.append(f"    <ObjectIndex>{obj_index}</ObjectIndex>")
            lines.append(f"    <SubIndex>{sub}</SubIndex>")
            lines.append(f"    <ObjectType>{obj_type}</ObjectType>")
            lines.append("  </GateInputs>")

    lines.append("</XMLExport>")
    return "\n".join(lines) + "\n"


def write_xml(ft: FaultTree, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_faulttreeplus_xml(ft), encoding="utf-8")
    return path
