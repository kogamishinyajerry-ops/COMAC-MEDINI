"""integration 标记样板：真机测试（real_medini）。

当前许可服务 STOPPED（2026-09-21 审计）→ collect 阶段 skip 并诚实说明。
许可恢复后自动实跑。无环境必须 skipped/blocked，不许 mock 冒充。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from medini_automation.application.slice import license_service_running, run_slice

FIX = Path(__file__).resolve().parents[1] / "fixtures"
MEDINI_EXE = Path(r"E:\ANSYS Inc\Medini Analyze 2023 R2\Program\mediniAnalyze.exe")

needs_medini = pytest.mark.real_medini


@needs_medini
def test_slice_abc_on_real_medini(tmp_path):
    """A/B/C 切片实机：T=(A∧B)∨(A∧C) → Q=0.044，MCS={AB,AC}。"""
    if not (MEDINI_EXE.exists() and license_service_running()):
        pytest.skip(
            "blocked: medini exe 缺失或许可服务 STOPPED"
            "（需管理员 Start-Service 'ANSYS, Inc. License Manager'）")
    res = run_slice(FIX / "slice_abc.json", tmp_path, case="abc_real",
                    execute=True)
    assert res.verdict == "pass", res.verdict_detail
    assert res.medini is not None
    # Q 对照独立参考 0.044（容差由 _compare 的 1e-9 保证）
    assert res.medini.get("Q_top") is not None


@needs_medini
def test_slice_or_save_reopen(tmp_path):
    """OR 树实机（保存重开链路的首段：headless 计算 + 结果回读）。"""
    if not (MEDINI_EXE.exists() and license_service_running()):
        pytest.skip("blocked: 许可服务 STOPPED")
    res = run_slice(FIX / "slice_or_save.json", tmp_path, case="or_real",
                    execute=True)
    assert res.verdict == "pass", res.verdict_detail


@needs_medini
def test_persist_abc_save_reopen_on_real_medini(tmp_path):
    """P1 保存→重开→回读实机四重校核。

    阶段A/B 是两个独立 medini 进程（真正的关闭→重开），要求：
    语义摘要一致、Q0==Q1、磁盘字节哈希一致、结构计数一致。
    只写本仓工作副本 workcopy/AUTO-WC（不更新既有工程）。
    """
    from medini_automation.application.persistence import run_reopen_check
    if not (MEDINI_EXE.exists() and license_service_running()):
        pytest.skip("blocked: medini exe 缺失或许可 1055 未监听（需用户态拉起 lmgrd）")
    res = run_reopen_check(FIX / "slice_abc.json", tmp_path, case="abc_persist",
                           execute=True)
    assert res.verdict == "pass", res.verdict_detail
    c = res.checks
    assert c["semantic_digest_equal"] and c["Q_equal"]
    assert c["bytes_sha256_equal"] and c["counts_equal"]
    assert res.save and res.save.get("Q0") == "0.044"
    assert res.reopen and res.reopen.get("Q1") == "0.044"


# ------------------------------------------------------------ P1.5 可见性
# 为何用「完整工程的副本」而不是手工造最小工程：
# 实测只含 .project + .project.medini + fta/ 的最小工程会让 medini 在加载
# -files 工程阶段抛 InterruptedException（ProjectCompareInput.doLoadFrom），
# 进程 exit=0 但 JS 完全不执行、无任何结果文件。必须用真实完整工程。
_WORKCOPY = Path(r"D:\COMAC MEDINI\medini-agent-automation\workcopy\AUTO-WC")


def _copy_project(tmp_path: Path) -> Path:
    """复制完整工作副本工程到 tmp（含 .commons.medini/.projectMapping 等域配置）。"""
    dest = tmp_path / "AUTO-WC"
    shutil.copytree(_WORKCOPY, dest)
    return dest


@needs_medini
def test_publish_and_verify_diagram_on_real_medini(tmp_path):
    """P1.5 实机闭环：生成 .fta_diagram + 登记 → medini 加载图并解析全部引用。

    这是"GUI 会看到这棵树"的强代理证据：medini 的专属 GMF 资源类
    （MediniGMFResource）成功实例化 Diagram，且每个 element href 都 resolve 到
    .fta 里的真实元素（0 个 proxy）。
    """
    from medini_automation.application.visibility import (
        publish_diagram, verify_diagram,
    )
    if not (MEDINI_EXE.exists() and license_service_running()):
        pytest.skip("blocked: medini exe 缺失或许可 1055 未监听")
    if not _WORKCOPY.exists():
        pytest.skip(f"blocked: 工作副本工程不存在（先跑 scripts/setup-workcopy.py）: {_WORKCOPY}")

    pdir = _copy_project(tmp_path)
    (pdir / "fta" / "gui_case.fta").write_bytes((FIX / "abc.fta").read_bytes())

    pub = publish_diagram(project_dir=pdir, case="gui_case")
    assert pub.ok, (pub.errors, pub.notes)
    assert pub.registration and pub.registration.action == "created"
    assert not pub.notes, pub.notes        # 完整工程不应产生结构告警

    ver = verify_diagram("gui_case", project_dir=pdir, out_root=tmp_path / "runs")
    assert ver["verdict"] == "pass", ver
    assert all(ver["checks"].values()), ver["checks"]
    raw = ver["raw"]
    assert raw["diagram_etype"] == "Diagram"
    assert raw["children_total"] == 10 and raw["children_proxy"] == 0
    assert raw["edges_total"] == 9 and raw["edges_proxy"] == 0
    assert raw["top_element_type"] == "FTAModel"


@needs_medini
def test_verify_diagram_diagnoses_incomplete_project(tmp_path):
    """不完整工程 → blocked 且给出可操作诊断（不是含糊的"无输出"）。"""
    from medini_automation.application.visibility import (
        publish_diagram, verify_diagram,
    )
    if not (MEDINI_EXE.exists() and license_service_running()):
        pytest.skip("blocked: medini exe 缺失或许可 1055 未监听")
    pdir = tmp_path / "MINIMAL"
    (pdir / "fta").mkdir(parents=True)
    (pdir / ".project").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<projectDescription>\n'
        '\t<name>MINIMAL</name>\n\t<natures>\n'
        '\t\t<nature>de.ikv.medini.cockpit.core.mediniNature</nature>\n'
        '\t</natures>\n</projectDescription>\n', encoding="utf-8")
    (pdir / ".project.medini").write_text(
        '<?xml version="1.1" encoding="UTF-8"?>\n'
        '<pjm:MediniProject xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:pjm="http://www.ikv.de/medini/metamodels/ProjectModel" '
        'xmi:id="_p" name="MINIMAL">\n</pjm:MediniProject>\n', encoding="utf-8")
    (pdir / "fta" / "abc.fta").write_bytes((FIX / "abc.fta").read_bytes())
    pub = publish_diagram(project_dir=pdir, case="abc")
    assert pub.ok
    assert any("域配置" in n for n in pub.notes), pub.notes

    ver = verify_diagram("abc", project_dir=pdir, out_root=tmp_path / "runs")
    assert ver["verdict"] == "blocked"
    # 要么被前置检查拦下，要么由 stdout 诊断给出根因
    blob = json.dumps(ver, ensure_ascii=False)
    assert "域配置" in blob or "ProjectCompareInput" in blob, ver


@needs_medini
def test_reopen_check_with_publish_flag(tmp_path):
    """reopen-check --publish：保存成功后自动建图并登记（P1 → P1.5 一条命令）。"""
    from medini_automation.application.persistence import run_reopen_check
    if not (MEDINI_EXE.exists() and license_service_running()):
        pytest.skip("blocked: medini exe 缺失或许可 1055 未监听")
    res = run_reopen_check(FIX / "slice_abc.json", tmp_path, case="abc_pub",
                           execute=True, publish=True)
    assert res.verdict == "pass", res.verdict_detail
    assert res.publish and res.publish["ok"], res.publish
    assert res.publish["registration"] in ("created", "updated", "unchanged")
    assert res.publish["counts"]["eventNode"] == 7
    assert res.publish["counts"]["connection"] == 9
