"""P1.5 单元测试：.fta_diagram 生成 + .project.medini 登记。

全部 synthetic：用 tests/fixtures/abc.fta（medini 真实落盘产物）与最小工程描述，
不依赖 medini 进程 / 许可。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from medini_automation.adapters.fta_diagram import (
    EVENT_NODE_H, EVENT_NODE_W, GATE_H, GATE_W, FtaParseError,
    layout_tree, parse_fta, render_diagram, slugify,
)
from medini_automation.application.visibility import (
    list_orphans, publish_diagram, register_in_project, verify_diagram,
    write_diagram,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures"
ABC = FIX / "abc.fta"

_XMI = "{http://www.omg.org/XMI}id"

# Eclipse 工程描述：medini 靠它识别工程（缺了 headless 会静默退出）
ECLIPSE_PROJECT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<projectDescription>
\t<name>PROJ</name>
\t<comment></comment>
\t<projects>
\t</projects>
\t<buildSpec>
\t</buildSpec>
\t<natures>
\t\t<nature>de.ikv.medini.cockpit.core.mediniNature</nature>
\t</natures>
</projectDescription>
"""

MIN_PROJECT = """<?xml version="1.1" encoding="UTF-8"?>
<pjm:MediniProject xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" \
xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" \
xmlns:pjm="http://www.ikv.de/medini/metamodels/ProjectModel" \
xmi:id="_prj" name="T">
  <containedElements xsi:type="pjm:PJResource" xmi:id="_fta" \
mediniIdentifier="_fta" name="fta" resourceURI="fta" folder="true"/>
</pjm:MediniProject>
"""

EXISTING_PJDIAGRAM = """        <containedElements xsi:type="pjm:PJDiagram" xmi:id="_old_pjd" \
mediniIdentifier="_old_pjd" name="old" uri="fta/old.fta_diagram" \
modelID="FaultTreeAnalysis" editorID="X">
          <canvasElement href="fta/old.fta#_old_root"/>
          <diagram href="fta/old.fta_diagram#_old_diag"/></containedElements>
"""


def make_project(tmp_path: Path, extra: str = "") -> Path:
    """最小工程：.project + .project.medini + fta/ 目录 + abc.fta。"""
    pdir = tmp_path / "PROJ"
    (pdir / "fta").mkdir(parents=True)
    text = MIN_PROJECT
    if extra:
        text = text.replace("</pjm:MediniProject>", extra + "</pjm:MediniProject>")
    (pdir / ".project").write_text(ECLIPSE_PROJECT_XML, encoding="utf-8")
    (pdir / ".project.medini").write_text(text, encoding="utf-8")
    (pdir / "fta" / "abc.fta").write_bytes(ABC.read_bytes())
    return pdir


# ------------------------------------------------------------ slugify
@pytest.mark.parametrize("raw,expect", [
    ("abc", "abc"),
    ("a b", "a_b"),
    ("T1-001", "T1-001"),
    ("含中文", "case"),
    ("1x", "_1x"),
    ("", "case"),
    ("a(b)=c", "a_b_c"),
])
def test_slugify(raw, expect):
    assert slugify(raw) == expect


# --------------------------------------------------------------- parse
def test_parse_abc_structure():
    g = parse_fta(ABC)
    assert g.model_id == "_eWtJgLWtEfGqefUoQIjM5A"
    assert g.counts == {"connection": 9, "eventNode": 7, "gate": 3}
    assert len(g.elements) == 10
    assert len(g.connections) == 9


def test_parse_abc_topology_direction():
    """inputNode 是父（视觉在上），outputNode 是子（视觉在下）——实测自 T1-001。"""
    g = parse_fta(ABC)
    # TOP 事件节点 _eX2ZC7 由 Gate TOP _eX2ZDL 驱动
    parents = {c.input_node: c.output_node for c in g.connections}
    assert parents["_eX2ZC7WtEfGqefUoQIjM5A"] == "_eX2ZDLWtEfGqefUoQIjM5A"
    assert g.kind_of("_eX2ZDLWtEfGqefUoQIjM5A") == "gate"
    assert g.kind_of("_eX2ZC7WtEfGqefUoQIjM5A") == "eventNode"


def test_parse_missing_file(tmp_path):
    with pytest.raises(FtaParseError, match="不存在"):
        parse_fta(tmp_path / "nope.fta")


def test_parse_dangling_connection(tmp_path):
    bad = tmp_path / "bad.fta"
    bad.write_text(
        '<?xml version="1.1" encoding="UTF-8"?>\n'
        '<fta:FTAModel xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:fta="http://www.ikv.de/medini/metamodels/FTA/2.0" xmi:id="_m">\n'
        '  <eventNodes xmi:id="_n1"/>\n'
        '  <connections xmi:id="_c1" outputNode="_n1" inputNode="_ghost"/>\n'
        '</fta:FTAModel>\n', encoding="utf-8")
    with pytest.raises(FtaParseError, match="端点悬空"):
        parse_fta(bad)


def test_parse_unknown_gate_type_fail_closed(tmp_path):
    bad = tmp_path / "bad.fta"
    bad.write_text(
        '<?xml version="1.1" encoding="UTF-8"?>\n'
        '<fta:FTAModel xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:fta="http://www.ikv.de/medini/metamodels/FTA/2.0" xmi:id="_m">\n'
        '  <eventNodes xmi:id="_n1"/>\n'
        '  <gates xsi:type="fta:WeirdGate" xmi:id="_g1"/>\n'
        '</fta:FTAModel>\n', encoding="utf-8")
    with pytest.raises(FtaParseError, match="未支持的 gate 类型"):
        parse_fta(bad)


def test_parse_duplicate_id(tmp_path):
    bad = tmp_path / "bad.fta"
    bad.write_text(
        '<?xml version="1.1" encoding="UTF-8"?>\n'
        '<fta:FTAModel xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:fta="http://www.ikv.de/medini/metamodels/FTA/2.0" xmi:id="_m">\n'
        '  <eventNodes xmi:id="_dup"/>\n'
        '  <eventNodes xmi:id="_dup"/>\n'
        '</fta:FTAModel>\n', encoding="utf-8")
    with pytest.raises(FtaParseError, match="重复"):
        parse_fta(bad)


# -------------------------------------------------------------- layout
def test_layout_depths_and_non_negative():
    g = parse_fta(ABC)
    pos = layout_tree(g)
    assert set(pos) == {e.xmi_id for e in g.elements}
    # 实测拓扑：EN(TOP) 0 -> Gate(TOP) 1 -> EN(G_AB/G_AC) 2 -> Gate 3 -> 基本事件 4
    assert pos["_eX2ZC7WtEfGqefUoQIjM5A"].depth == 0     # TOP 事件节点
    assert pos["_eX2ZDLWtEfGqefUoQIjM5A"].depth == 1     # Gate TOP
    assert pos["_eX2ZA7WtEfGqefUoQIjM5A"].depth == 2     # EN G_AB
    assert pos["_eXz8wLWtEfGqefUoQIjM5A"].depth == 4     # EN A
    assert all(p.x >= 0 and p.y >= 0 for p in pos.values())
    assert max(p.depth for p in pos.values()) == 4


def test_layout_integer_coords():
    """notation Bounds 是 EInt —— 小数会让 medini 抛 IllegalValueException。"""
    g = parse_fta(ABC)
    for p in layout_tree(g).values():
        assert isinstance(p.x, int) and isinstance(p.y, int)


def test_layout_same_level_no_overlap():
    """同层任意两节点不得水平重叠（槽宽取最宽节点）。"""
    g = parse_fta(ABC)
    pos = layout_tree(g)
    by_depth: dict[int, list] = {}
    for p in pos.values():
        by_depth.setdefault(p.depth, []).append(p)
    for depth, pls in by_depth.items():
        pls.sort(key=lambda p: p.x)
        for a, b in zip(pls, pls[1:]):
            assert a.x + a.width <= b.x, f"depth={depth} 重叠: {a} vs {b}"


def test_layout_parent_centered_over_children():
    """Gate TOP 应水平居中于其两个下游事件节点之上。"""
    g = parse_fta(ABC)
    pos = layout_tree(g)
    gate = pos["_eX2ZDLWtEfGqefUoQIjM5A"]
    kids = [pos["_eX2ZA7WtEfGqefUoQIjM5A"], pos["_eX2ZB7WtEfGqefUoQIjM5A"]]
    gc = gate.x + gate.width / 2
    kc = [(k.x + k.width / 2) for k in kids]
    assert abs(gc - (min(kc) + max(kc)) / 2) <= 1.0


def test_layout_sizes_match_kinds():
    g = parse_fta(ABC)
    pos = layout_tree(g)
    for e in g.elements:
        p = pos[e.xmi_id]
        if e.kind == "eventNode":
            assert (p.width, p.height) == (EVENT_NODE_W, EVENT_NODE_H)
        else:
            assert (p.width, p.height) == (GATE_W, GATE_H)


# -------------------------------------------------------------- render
def _render_abc() -> str:
    g = parse_fta(ABC)
    return render_diagram(g, layout_tree(g), case="abc", fta_filename="abc.fta")


def test_render_is_wellformed_xml():
    ET.fromstring(_render_abc().encode("utf-8"))


def test_render_shape_and_edge_counts():
    root = ET.fromstring(_render_abc().encode("utf-8"))
    # 注意 1：<children>/<edges>/<element> 无命名空间前缀（根用了 notation: 前缀，
    #         未声明默认命名空间——与既有样例一致），故按裸 tag 名查找。
    # 注意 2：notation 图文件的类型标记是 **xmi:type**（不是 xsi:type）——
    #         既有 9 个样例图 625 处全用 xmi:type（.fta 模型文件才用 xsi:type）。
    xmi = "{http://www.omg.org/XMI}type"
    shapes = [c for c in root.findall("children")
              if c.get(xmi) == "notation:Shape"]
    edges = root.findall("edges")
    assert len(shapes) == 10          # 7 EventNode + 3 LogicalGate
    assert len(edges) == 9
    assert {s.get("type") for s in shapes} == {"2005", "2006"}
    assert {e.get("type") for e in edges} == {"4003"}


def test_render_uses_xmi_type_not_xsi_type():
    """契约回归：notation 图文件必须用 xmi:type；写成 xsi:type 会与既有样例不符。"""
    text = _render_abc()
    assert 'xmi:type="notation:Shape"' in text
    assert 'xmi:type="fta:EventNode"' in text
    assert 'xsi:type="notation:Shape"' not in text
    assert 'xsi:type="fta:EventNode"' not in text
    # 根元素仍须声明 xsi 命名空间（既有样例如此，供 ecore:EStringToStringMapEntry 等使用）
    assert 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"' in text


def test_render_ids_unique():
    text = _render_abc()
    ids = re.findall(r'xmi:id="([^"]+)"', text)
    assert len(ids) == len(set(ids))


def test_render_elements_integer_bounds():
    text = _render_abc()
    for x, y in re.findall(r'type="notation:Bounds"[^>]*\bx="(-?[\d.]+)" y="(-?[\d.]+)"',
                           text):
        assert "." not in x and "." not in y, f"Bounds 非整数: x={x} y={y}"


def test_render_hrefs_point_into_fta():
    """每个 element href 的 fragment 必须真实存在于 .fta。"""
    ids = set(re.findall(r'xmi:id="([^"]+)"', ABC.read_text(encoding="utf-8")))
    text = _render_abc()
    hrefs = re.findall(r'<element xmi:type="fta:(\w+)" href="([^"]+)"', text)
    # 1 FTAModel + 10 Shape 元素 + 9 edge 的 Connection
    assert len(hrefs) == 20
    kinds = [k for k, _ in hrefs]
    assert kinds.count("FTAModel") == 1
    assert kinds.count("EventNode") == 7
    assert kinds.count("LogicalGate") == 3
    assert kinds.count("Connection") == 9
    for _kind, href in hrefs:
        fname, _, frag = href.partition("#")
        assert fname == "abc.fta"
        assert frag in ids, f"href 指向不存在的元素: {href}"


def test_render_edge_endpoints_exist():
    text = _render_abc()
    shape_ids = set(re.findall(
        r'<children xmi:type="notation:Shape" xmi:id="([^"]+)"', text))
    for m in re.finditer(
            r'<edges xmi:type="notation:Connector"[^>]*source="([^"]+)"'
            r' target="([^"]+)"', text):
        assert m.group(1) in shape_ids
        assert m.group(2) in shape_ids


def test_render_edge_source_is_output_node():
    """source=outputNode（视觉在下）、target=inputNode（视觉在上）。"""
    g = parse_fta(ABC)
    text = render_diagram(g, layout_tree(g), case="abc", fta_filename="abc.fta")
    vid = {e.xmi_id: f"_abc_v{i}" for i, e in enumerate(g.elements)}
    for i, c in enumerate(g.connections):
        m = re.search(rf'<edges xmi:type="notation:Connector" xmi:id="_abc_e{i}"'
                      rf'[^>]*source="([^"]+)" target="([^"]+)"', text)
        assert m, f"缺 edge _abc_e{i}"
        assert m.group(1) == vid[c.output_node]
        assert m.group(2) == vid[c.input_node]


def test_render_name_escaped():
    g = parse_fta(ABC)
    text = render_diagram(g, layout_tree(g), case='a"b<c', fta_filename="x.fta")
    assert 'name="a&quot;b&lt;c"' in text
    ET.fromstring(text.encode("utf-8"))


# -------------------------------------------------------- register
def test_register_creates_entry(tmp_path):
    pdir = make_project(tmp_path)
    out = register_in_project(pdir / ".project.medini", case="abc",
                              root_id="_eWtJgLWtEfGqefUoQIjM5A")
    assert out.action == "created"
    text = (pdir / ".project.medini").read_text(encoding="utf-8")
    assert 'xmi:id="_abc_pjd"' in text
    assert 'uri="fta/abc.fta_diagram"' in text
    assert '<canvasElement href="fta/abc.fta#_eWtJgLWtEfGqefUoQIjM5A"/>' in text
    assert '<diagram href="fta/abc.fta_diagram#_abc_diag"/>' in text
    ET.fromstring(text.encode("utf-8"))


def test_register_is_idempotent(tmp_path):
    pdir = make_project(tmp_path)
    pf = pdir / ".project.medini"
    register_in_project(pf, case="abc", root_id="_r1")
    out2 = register_in_project(pf, case="abc", root_id="_r1")
    assert out2.action == "unchanged"
    text = pf.read_text(encoding="utf-8")
    assert text.count('xmi:id="_abc_pjd"') == 1
    assert text.count('xsi:type="pjm:PJDiagram"') == 1


def test_register_updates_when_root_changed(tmp_path):
    """重新保存后 .fta 的 xmi:id 会变 —— 登记条目必须跟着更新 root_id。"""
    pdir = make_project(tmp_path)
    pf = pdir / ".project.medini"
    register_in_project(pf, case="abc", root_id="_old")
    out = register_in_project(pf, case="abc", root_id="_new")
    assert out.action == "updated"
    text = pf.read_text(encoding="utf-8")
    assert '<canvasElement href="fta/abc.fta#_new"/>' in text
    assert "_old" not in text
    assert text.count('xsi:type="pjm:PJDiagram"') == 1


def test_register_appends_after_existing(tmp_path):
    pdir = make_project(tmp_path, extra=EXISTING_PJDIAGRAM)
    register_in_project(pdir / ".project.medini", case="abc", root_id="_r")
    text = (pdir / ".project.medini").read_text(encoding="utf-8")
    # 新条目必须在既有条目之后
    assert text.index('name="old"') < text.index('name="abc"')
    assert text.count('xsi:type="pjm:PJDiagram"') == 2
    assert text.index('name="abc"') < text.rindex("</pjm:MediniProject>")
    ET.fromstring(text.encode("utf-8"))


def test_register_missing_project(tmp_path):
    with pytest.raises(FileNotFoundError):
        register_in_project(tmp_path / "nope" / ".project.medini",
                            case="abc", root_id="_r")


def test_register_bad_project(tmp_path):
    p = tmp_path / ".project.medini"
    p.write_text("<not-a-project/>", encoding="utf-8")
    with pytest.raises(ValueError, match="不是合法"):
        register_in_project(p, case="abc", root_id="_r")


# -------------------------------------------------------- publish / audit
def test_publish_end_to_end(tmp_path):
    pdir = make_project(tmp_path)
    res = publish_diagram(project_dir=pdir, case="abc")
    assert res.ok, res.errors
    assert Path(res.diagram_path).exists()
    assert res.diagram_sha256
    assert res.counts == {"connection": 9, "eventNode": 7, "gate": 3}
    assert res.registration is not None and res.registration.action == "created"
    assert res.bbox["x_min"] >= 0 and res.bbox["y_min"] >= 0


def test_publish_idempotent(tmp_path):
    pdir = make_project(tmp_path)
    a = publish_diagram(project_dir=pdir, case="abc")
    b = publish_diagram(project_dir=pdir, case="abc")
    assert a.diagram_sha256 == b.diagram_sha256      # 布局确定性
    assert b.registration.action == "unchanged"
    text = (pdir / ".project.medini").read_text(encoding="utf-8")
    assert text.count('xsi:type="pjm:PJDiagram"') == 1


def test_publish_no_register(tmp_path):
    pdir = make_project(tmp_path)
    res = publish_diagram(project_dir=pdir, case="abc", register=False)
    assert res.ok and res.registration is None
    assert Path(res.diagram_path).exists()
    assert 'xsi:type="pjm:PJDiagram"' not in (
        pdir / ".project.medini").read_text(encoding="utf-8")


def test_publish_missing_fta(tmp_path):
    pdir = make_project(tmp_path)
    res = publish_diagram(project_dir=pdir, case="ghost")
    assert not res.ok
    assert any("不存在" in e for e in res.errors)


def test_write_diagram_rejects_bad_fta(tmp_path):
    pdir = make_project(tmp_path)
    bad = pdir / "fta" / "bad.fta"
    bad.write_text("<?xml version='1.1'?><nope/>", encoding="utf-8")
    with pytest.raises(FtaParseError):
        write_diagram(bad, project_dir=pdir, case="bad")


def test_list_orphans(tmp_path):
    pdir = make_project(tmp_path)
    before = list_orphans(pdir)
    assert before["unregistered"] == ["abc"] and before["registered"] == []
    publish_diagram(project_dir=pdir, case="abc")
    after = list_orphans(pdir)
    assert after == {"unregistered": [], "registered": ["abc"]}


def test_list_orphans_ignores_non_fta(tmp_path):
    pdir = make_project(tmp_path)
    (pdir / "fta" / "notes.txt").write_text("x", encoding="utf-8")
    (pdir / "fta" / "x.fta_diagram").write_text("<x/>", encoding="utf-8")
    assert list_orphans(pdir)["unregistered"] == ["abc"]


# ------------------------------------------------------- verify fail-fast
def test_verify_diagram_blocked_without_eclipse_project(tmp_path):
    """缺 .project 时 medini headless 会静默退出（exit=0、JS 不执行）——
    必须提前 fail-fast 报 blocked，而不是让调用方看到"无输出"。"""
    pdir = make_project(tmp_path)
    publish_diagram(project_dir=pdir, case="abc")      # 先让图就绪
    (pdir / ".project").unlink()
    res = verify_diagram("abc", project_dir=pdir, out_root=tmp_path / "runs")
    assert res["verdict"] == "blocked"
    assert ".project" in res["error"]


def test_verify_diagram_blocked_when_diagram_missing(tmp_path):
    pdir = make_project(tmp_path)
    res = verify_diagram("abc", project_dir=pdir, out_root=tmp_path / "runs")
    assert res["verdict"] == "blocked"
    assert "fta_diagram" in res["error"]


def test_verify_diagram_blocked_when_fta_missing(tmp_path):
    pdir = make_project(tmp_path)
    res = verify_diagram("ghost", project_dir=pdir, out_root=tmp_path / "runs")
    assert res["verdict"] == "blocked"
    assert ".fta 不存在" in res["error"]


# --------------------------------------------------- headless 失败诊断
def test_diagnose_headless_incomplete_project():
    from medini_automation.application.visibility import _diagnose_headless
    stdout = (
        "java.lang.InterruptedException\n"
        "\tat de.ikv.analyze.compare.ui.handler.ProjectCompareInput"
        ".doLoadFrom(ProjectCompareInput.java:137)\n"
        "\tat de.ikv.analyze.cli.uijob.ScriptJob.doWork(ScriptJob.java:88)\n")
    msg = _diagnose_headless(stdout)
    assert msg and msg.startswith("medini 在加载")
    assert "域配置" in msg


def test_diagnose_headless_missing_metamodel():
    from medini_automation.application.visibility import _diagnose_headless
    msg = _diagnose_headless(
        "java.lang.IllegalArgumentException: Package with uri "
        "'http://www.eclipse.org/gmf/runtime/1.0.2/notation' not found")
    assert msg and "元模型未注册" in msg


def test_diagnose_headless_script_error():
    from medini_automation.application.visibility import _diagnose_headless
    msg = _diagnose_headless(
        "Status ERROR: de.ikv.medini.scripting.js code=4 Failed to execute")
    assert msg and msg.startswith("脚本执行错误")


def test_diagnose_headless_none_when_clean():
    from medini_automation.application.visibility import _diagnose_headless
    assert _diagnose_headless("all fine, nothing to see") is None


def test_project_defects():
    from medini_automation.application.visibility import _project_defects
    pdir = Path("/nonexistent/xyz")
    assert _project_defects(pdir) == [".project", ".project.medini"]


def test_publish_notes_warn_on_incomplete_project(tmp_path):
    """不完整工程：图照常生成，但必须给出结构告警（不静默）。"""
    pdir = make_project(tmp_path)
    (pdir / ".project").unlink()          # make_project 本就未建域配置
    res = publish_diagram(project_dir=pdir, case="abc")
    assert res.ok, res.errors
    assert Path(res.diagram_path).exists()
    blob = " ".join(res.notes)
    assert ".project" in blob, res.notes        # 缺 Eclipse 工程描述
    assert "域配置" in blob, res.notes           # 缺 .commons.medini/.projectMapping


def test_publish_no_notes_on_complete_project(tmp_path):
    pdir = make_project(tmp_path)
    for f in (".commons.medini", ".projectMapping"):
        (pdir / f).write_text("x", encoding="utf-8")
    res = publish_diagram(project_dir=pdir, case="abc")
    assert res.ok and res.notes == []
