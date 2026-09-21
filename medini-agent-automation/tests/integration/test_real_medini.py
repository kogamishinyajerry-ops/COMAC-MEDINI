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
