"""adapters.fta_diagram — 从落盘 .fta 生成 GMF notation 图（P1.5 GUI 可见性）。

背景
----
medini 的项目树 / Model Browser 只呈现 **.project.medini 中注册为 PJDiagram 的图**
（.fta_diagram）。仅落盘 .fta 而不建图 = 「孤儿模型」：实测既有工程 fta/ 下的
DBG1 / PROTO-1 / DEMO-* .fta 全部无图、未注册，在 GUI 中看不到。

本模块把「已保存的 .fta」变成「GUI 里可打开的树」：

  1. ``parse_fta``        解析 .fta（medini 亲笔序列化，格式稳定）→ 元素真实 xmi:id + 拓扑
  2. ``layout_tree``      自动树布局（TOP 在上、输入在下，父居中于子）
  3. ``render_diagram``   生成 .fta_diagram 文本

注册进 .project.medini 由 ``application/visibility.py`` 负责（文本级插入，
不用 ElementTree 重写——那会把命名空间前缀改成 ns0:，medini 可能不认）。

编码契约（逐字段对齐既有可打开样例 T1-001 / C01-MAIN）
------------------------------------------------------
Diagram      ``type="FaultTreeAnalysis" measurementUnit="Pixel"``
Element      ``<element xmi:type="fta:FTAModel" href="<file>.fta#<rootId>"/>``
EventNode    Shape ``type=2005``（DecorationNode 5008/5009/5010/5015）
LogicalGate  Shape ``type=2006``（DecorationNode 5012）
TransferGate Shape ``type=2008``（DecorationNode 5014）
Connection   ``<edges xmi:type="notation:Connector" type="4003" routing="Rectilinear">``
             ``source`` = Connection.outputNode（视觉在下）
             ``target`` = Connection.inputNode（视觉在上）
href         图文件同目录的相对路径 ``"<file>.fta#<xmi:id>"``

两个易踩的坑（均已由既有样例 + 实机加载双重确证）
--------------------------------------------------
1. **类型标记是 ``xmi:type`` 而不是 ``xsi:type``**。notation（GMF）图文件通篇用
   ``xmi:type``（既有 9 个样例共 625 处），只有 ``.fta`` 模型文件才用 ``xsi:type``。
2. **``Bounds`` 的 x/y 是 EInt，必须整数**——实测写 ``x="404.5"`` 会让 medini 抛
   ``IllegalValueException: Value '404.5' is not legal``，图直接加载失败。

注：既有 9 个样例图一律把 Connector 的 ``eAnnotations xsi:type`` 误写成
``"event-PLACEHOLDER"``（历史批量生成的替换错位）。本模块写正确的
``"ecore:EAnnotation"``。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "FtaElement", "FtaConnection", "FtaGraph", "FtaParseError", "Placement",
    "EVENT_NODE_W", "EVENT_NODE_H", "GATE_W", "GATE_H",
    "parse_fta", "layout_tree", "render_diagram", "slugify",
]

_XMI_ID = "{http://www.omg.org/XMI}id"
_XMI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

# 节点视觉尺寸（对齐既有样例：EventNode 166x47，门 55x55）
EVENT_NODE_W, EVENT_NODE_H = 166.0, 47.0
GATE_W, GATE_H = 55.0, 55.0

# GMF 视觉类型 ID
_SHAPE_EVENT_NODE = "2005"
_SHAPE_LOGICAL_GATE = "2006"
_SHAPE_TRANSFER_GATE = "2008"
_EDGE_CONNECTION = "4003"
_DECO = {
    "eventNode": ("5008", "5009", "5010", "5015"),
    "gate": ("5012",),
    "transferGate": ("5014",),
}
_ELEMENT_TYPE = {
    "eventNode": "fta:EventNode",
    "gate": "fta:LogicalGate",
    "transferGate": "fta:TransferGate",
}
_SHAPE_TYPE = {
    "eventNode": _SHAPE_EVENT_NODE,
    "gate": _SHAPE_LOGICAL_GATE,
    "transferGate": _SHAPE_TRANSFER_GATE,
}
# 折点提示：与既有可打开样例同值（GMF 仍按 routing=Rectilinear 自行重路由）
_BEND_POINTS = "[7, 95, 21, -45&#x5d;$[7, 140, 21, 0&#x5d;"

_PROLOGUE = '<?xml version="1.1" encoding="UTF-8"?>'
_DIAGRAM_NS = (
    'xmlns:xmi="http://www.omg.org/XMI"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    ' xmlns:ecore="http://www.eclipse.org/emf/2002/Ecore"'
    ' xmlns:fta="http://www.ikv.de/medini/metamodels/FTA/2.0"'
    ' xmlns:notation="http://www.eclipse.org/gmf/runtime/1.0.2/notation"'
)

_SLUG_RE = re.compile(r"[^0-9A-Za-z_.-]+")


class FtaParseError(ValueError):
    """图生成前置校验失败（fail-closed：结构异常时拒绝生成半成品图）。"""


# ------------------------------------------------------------------ 模型
@dataclass(frozen=True)
class FtaElement:
    xmi_id: str
    kind: str          # eventNode | gate | transferGate


@dataclass(frozen=True)
class FtaConnection:
    xmi_id: str
    output_node: str   # 源（视觉在下）
    input_node: str    # 目标（视觉在上）


@dataclass(frozen=True)
class FtaGraph:
    model_id: str
    elements: tuple[FtaElement, ...]
    connections: tuple[FtaConnection, ...]

    @property
    def counts(self) -> dict[str, int]:
        c = defaultdict(int)
        for e in self.elements:
            c[e.kind] += 1
        c["connection"] = len(self.connections)
        return dict(sorted(c.items()))

    def kind_of(self, xmi_id: str) -> str:
        for e in self.elements:
            if e.xmi_id == xmi_id:
                return e.kind
        raise KeyError(xmi_id)


@dataclass(frozen=True)
class Placement:
    x: int             # 形状左上角（notation Bounds 是 EInt，必须整数）
    y: int
    width: float
    height: float
    depth: int


# ----------------------------------------------------------------- 工具
def slugify(s: str) -> str:
    """case 名 → 安全标识符（文件名 / xmi:id 片段）。

    medini 的 platform URI 不接受空格、括号、等号；xmi:id 还必须满足 NCName。
    """
    out = _SLUG_RE.sub("_", s).strip("_")
    if not out:
        out = "case"
    if not (out[0].isalpha() or out[0] == "_"):
        out = "_" + out
    return out


def _attr(value: str) -> str:
    """属性值转义（& < > "）。"""
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ----------------------------------------------------------------- 解析
def parse_fta(path: str | Path) -> FtaGraph:
    """解析 medini 落盘的 .fta，抽取图所需的元素与拓扑。

    只做「结构可用性」校验（fail-closed）：根元素/ID 缺失、ID 重复、
    Connection 端点悬空、未知门类型 —— 任一命中即抛 FtaParseError。
    """
    p = Path(path)
    if not p.exists():
        raise FtaParseError(f".fta 不存在: {p}")
    try:
        root = ET.parse(p).getroot()
    except ET.ParseError as e:
        raise FtaParseError(f".fta XML 解析失败: {e}") from e

    if not root.tag.endswith("FTAModel"):
        raise FtaParseError(f"根元素不是 FTAModel: {root.tag}")
    model_id = root.get(_XMI_ID)
    if not model_id:
        raise FtaParseError("FTAModel 缺 xmi:id")

    elements: list[FtaElement] = []
    seen: set[str] = set()

    def _add(xmi_id: str | None, kind: str, where: str) -> None:
        if not xmi_id:
            raise FtaParseError(f"{where} 缺 xmi:id")
        if xmi_id in seen:
            raise FtaParseError(f"xmi:id 重复: {xmi_id}")
        seen.add(xmi_id)
        elements.append(FtaElement(xmi_id, kind))

    for el in root.findall("eventNodes"):
        _add(el.get(_XMI_ID), "eventNode", "eventNodes")

    for el in root.findall("gates"):
        t = el.get(_XMI_TYPE) or ""
        if t.endswith("LogicalGate"):
            kind = "gate"
        elif t.endswith("TransferGate"):
            kind = "transferGate"
        else:
            raise FtaParseError(f"未支持的 gate 类型: {t!r}（fail-closed）")
        _add(el.get(_XMI_ID), kind, "gates")

    connections: list[FtaConnection] = []
    for el in root.findall("connections"):
        cid = el.get(_XMI_ID)
        if not cid:
            raise FtaParseError("connections 缺 xmi:id")
        if cid in seen:
            raise FtaParseError(f"xmi:id 重复: {cid}")
        seen.add(cid)
        out_n, in_n = el.get("outputNode"), el.get("inputNode")
        if not out_n or not in_n:
            raise FtaParseError(f"Connection {cid} 缺 outputNode/inputNode")
        connections.append(FtaConnection(cid, out_n, in_n))

    if not elements:
        raise FtaParseError("无 eventNodes/gates，无可绘制的树")
    for c in connections:
        for n in (c.output_node, c.input_node):
            if n not in seen:
                raise FtaParseError(f"Connection {c.xmi_id} 端点悬空: {n}")

    return FtaGraph(model_id, tuple(elements), tuple(connections))


# ----------------------------------------------------------------- 布局
def layout_tree(
    graph: FtaGraph,
    *,
    h_gap: float = 40.0,
    v_gap: float = 100.0,
    margin: float = 40.0,
) -> dict[str, Placement]:
    """自顶向下树布局。

    方向语义（实测自 T1-001）：``Connection.inputNode`` 是父（视觉在上），
    ``Connection.outputNode`` 是子（视觉在下）。故 children(N) =
    ``{c.outputNode | c.inputNode == N}``。

    横坐标用「槽位」分配：叶子按遍历序逐个占槽，父取子槽位中值；
    槽宽 = 最宽节点(EventNode) + 间隙 → 同层任意两节点必然不重叠。
    """
    children: dict[str, list[str]] = defaultdict(list)
    order = {e.xmi_id: i for i, e in enumerate(graph.elements)}
    for c in graph.connections:
        children[c.input_node].append(c.output_node)
    for k in children:
        # 保序，保证布局可复现
        children[k].sort(key=lambda n: order.get(n, 1 << 30))

    has_upstream = {c.output_node for c in graph.connections}
    roots = [e.xmi_id for e in graph.elements if e.xmi_id not in has_upstream]

    # 深度：从根 BFS（环/多父时取最先到达的最小深度）
    depth: dict[str, int] = {}
    frontier = list(roots)
    for r in frontier:
        depth[r] = 0
    while frontier:
        nxt: list[str] = []
        for n in frontier:
            for k in children.get(n, ()):
                if k not in depth:
                    depth[k] = depth[n] + 1
                    nxt.append(k)
        frontier = nxt
    for e in graph.elements:          # 兜底：环内节点
        depth.setdefault(e.xmi_id, 0)

    # 槽位：后序 DFS
    slot_w = EVENT_NODE_W + h_gap
    slot_of: dict[str, float] = {}
    cursor = [0]
    visiting: set[str] = set()

    def visit(nid: str) -> float | None:
        if nid in visiting:            # 环保护
            return None
        visiting.add(nid)
        kids = [k for k in children.get(nid, ())
                if k not in visiting and k not in slot_of]
        if kids:
            got = [s for s in (visit(k) for k in kids) if s is not None]
            s = (min(got) + max(got)) / 2.0 if got else float(cursor[0])
        else:
            s = float(cursor[0])
            cursor[0] += 1
        slot_of[nid] = s
        visiting.discard(nid)
        return s

    for r in roots:
        if r not in slot_of:
            visit(r)
    for e in graph.elements:           # 兜底：未被遍历到的节点
        if e.xmi_id not in slot_of:
            slot_of[e.xmi_id] = float(cursor[0])
            cursor[0] += 1

    out: dict[str, Placement] = {}
    # 以最宽/最高节点定基准，保证全部坐标非负且同层中心对齐
    base_x = margin + EVENT_NODE_W / 2.0
    base_y = margin + GATE_H / 2.0
    for e in graph.elements:
        d = depth[e.xmi_id]
        w, h = ((EVENT_NODE_W, EVENT_NODE_H) if e.kind == "eventNode"
                else (GATE_W, GATE_H))
        cx = base_x + slot_of[e.xmi_id] * slot_w
        cy = base_y + d * v_gap
        # notation 的 Bounds.x/y 是 EInt —— 实测传小数会报 IllegalValueException
        out[e.xmi_id] = Placement(
            x=round(cx - w / 2.0), y=round(cy - h / 2.0),
            width=w, height=h, depth=d)
    return out


# ----------------------------------------------------------------- 渲染
def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _shape_xml(kind: str, vid: str, xmi_id: str, href_base: str,
               place: Placement) -> list[str]:
    decos = [
        f'    <children xmi:type="notation:DecorationNode"'
        f' xmi:id="{vid}_d{i + 1}" type="{t}"/>'
        for i, t in enumerate(_DECO[kind])
    ]
    return [
        f'  <children xmi:type="notation:Shape" xmi:id="{vid}"'
        f' type="{_SHAPE_TYPE[kind]}" fontName="Segoe UI"'
        f' lineColor="0" lineWidth="1">',
        f'    <eAnnotations xmi:type="ecore:EAnnotation" xmi:id="{vid}_sa"'
        f' source="Shortcut">',
        f'      <details xmi:type="ecore:EStringToStringMapEntry"'
        f' xmi:id="{vid}_sd" key="modelID" value="FaultTreeAnalysis"/>',
        '    </eAnnotations>',
        *decos,
        f'    <styles xmi:type="notation:TextStyle" xmi:id="{vid}_ts"'
        f' textAlignment="Center"/>',
        f'    <styles xmi:type="notation:LineTypeStyle" xmi:id="{vid}_ls"/>',
        f'    <element xmi:type="{_ELEMENT_TYPE[kind]}"'
        f' href="{href_base}#{xmi_id}"/>',
        f'    <layoutConstraint xmi:type="notation:Bounds" xmi:id="{vid}_b"'
        f' x="{_fmt(place.x)}" y="{_fmt(place.y)}"'
        f' width="{_fmt(place.width)}" height="{_fmt(place.height)}"/>',
        '  </children>',
    ]


def _edge_xml(eid: str, conn: FtaConnection, src_vid: str, tgt_vid: str,
              href_base: str) -> list[str]:
    return [
        f'  <edges xmi:type="notation:Connector" xmi:id="{eid}"'
        f' type="{_EDGE_CONNECTION}" source="{src_vid}" target="{tgt_vid}"'
        f' routing="Rectilinear" lineColor="0" lineWidth="1">',
        f'    <eAnnotations xmi:type="ecore:EAnnotation" xmi:id="{eid}_sa"'
        f' source="Shortcut">',
        f'      <details xmi:type="ecore:EStringToStringMapEntry"'
        f' xmi:id="{eid}_sd" key="modelID" value="FaultTreeAnalysis"/>',
        '    </eAnnotations>',
        f'    <children xmi:type="notation:DecorationNode" xmi:id="{eid}_dc"'
        f' type="6001">',
        f'      <layoutConstraint xmi:type="notation:Location"'
        f' xmi:id="{eid}_lc" y="40"/>',
        '    </children>',
        f'    <styles xmi:type="notation:FontStyle" xmi:id="{eid}_fs"'
        f' fontName="Segoe UI"/>',
        f'    <styles xmi:type="notation:LineStyle" xmi:id="{eid}_ls"/>',
        f'    <element xmi:type="fta:Connection"'
        f' href="{href_base}#{conn.xmi_id}"/>',
        f'    <bendpoints xmi:type="notation:RelativeBendpoints"'
        f' xmi:id="{eid}_bp" points="{_BEND_POINTS}"/>',
        '  </edges>',
    ]


def render_diagram(
    graph: FtaGraph,
    placements: dict[str, Placement],
    *,
    case: str,
    fta_filename: str,
) -> str:
    """生成 .fta_diagram 文本。

    ``case``         图名（写入 ``name=`` 与 ``xmi:id`` 前缀）
    ``fta_filename`` 与图文件同目录的 .fta 文件名，用于 href 相对路径
    """
    sid = slugify(case)
    href_base = _attr(fta_filename)
    vid = {e.xmi_id: f"_{sid}_v{i}" for i, e in enumerate(graph.elements)}

    # 按深度输出形状，文件更易读
    ordered = sorted(graph.elements, key=lambda e: placements[e.xmi_id].depth)

    lines: list[str] = [
        _PROLOGUE,
        f'<notation:Diagram xmi:version="2.0" {_DIAGRAM_NS}'
        f' xmi:id="_{sid}_diag" type="FaultTreeAnalysis"'
        f' measurementUnit="Pixel" name="{_attr(case)}">',
        f'  <element xmi:type="fta:FTAModel"'
        f' href="{href_base}#{graph.model_id}"/>',
    ]
    for e in ordered:
        lines += _shape_xml(e.kind, vid[e.xmi_id], e.xmi_id, href_base,
                            placements[e.xmi_id])
    for i, c in enumerate(graph.connections):
        lines += _edge_xml(f"_{sid}_e{i}", c, vid[c.output_node],
                           vid[c.input_node], href_base)
    lines.append('</notation:Diagram>')
    return "\n".join(lines) + "\n"
