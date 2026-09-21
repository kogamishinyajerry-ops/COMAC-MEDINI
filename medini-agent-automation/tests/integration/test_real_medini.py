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
