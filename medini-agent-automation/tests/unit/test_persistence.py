"""unit — P1 保存→重开→回读链（纯 synthetic，不依赖 medini）。

标记：
- 本文件全部为 synthetic 单测（不需要 medini / 许可）
- 实机路径由 tests/integration/test_real_medini.py 覆盖（marker: real_medini）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from medini_automation.application.persistence import (
    _evaluate, _q_equal, _slug, run_reopen_check,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "slice_abc.json"


# ---------------------------------------------------------------- _slug
@pytest.mark.parametrize("raw,expect", [
    ("abc", "abc"),
    ("or_save", "or_save"),
    ("T=(A AND B) OR (A AND C)", "T_A_AND_B_OR_A_AND_C"),
    ("案例 1", "1"),
    ("   ", "case"),
    ("a/b\\c:d", "a_b_c_d"),
])
def test_slug_sanitizes_case_names(raw, expect):
    assert _slug(raw) == expect


# ------------------------------------------------------------- _q_equal
def test_q_equal_exact_string():
    ok, note = _q_equal("0.044", "0.044")
    assert ok and note == "逐位一致"


def test_q_equal_within_tolerance():
    ok, note = _q_equal("0.044", "0.044000000000000001")
    assert ok and "rel=" in note


def test_q_equal_out_of_tolerance():
    ok, _ = _q_equal("0.044", "0.0494")
    assert not ok


def test_q_equal_missing_value():
    ok, note = _q_equal(None, "0.044")
    assert not ok and note == "缺值"


def test_q_equal_unparsable():
    ok, note = _q_equal("0.044", "not-a-number")
    assert not ok and "无法解析" in note


# ------------------------------------------------------------ _evaluate
def _save(status="ok", **kw):
    d = {"status": status, "Q0": "0.044",
         "semantic_digest": "d" * 64, "saved_sha256": "a" * 64,
         "counts": {"events": 6, "gates": 3, "eventNodes": 7, "connections": 9}}
    d.update(kw)
    return d


def _reopen(status="ok", **kw):
    d = {"status": status, "Q1": "0.044",
         "semantic_digest": "d" * 64, "loaded_sha256": "a" * 64,
         "counts": {"events": 6, "gates": 3, "eventNodes": 7, "connections": 9}}
    d.update(kw)
    return d


def test_evaluate_all_green_is_pass():
    verdict, detail, checks = _evaluate(_save(), _reopen())
    assert verdict == "pass"
    assert checks["semantic_digest_equal"] and checks["Q_equal"]
    assert checks["bytes_sha256_equal"] and checks["counts_equal"]
    assert "0.044" in detail


def test_evaluate_digest_mismatch_is_fail():
    verdict, detail, checks = _evaluate(_save(), _reopen(semantic_digest="e" * 64))
    assert verdict == "fail" and "semantic_digest_equal" in detail
    assert not checks["semantic_digest_equal"]


def test_evaluate_q_mismatch_is_fail():
    verdict, detail, _ = _evaluate(_save(), _reopen(Q1="0.0494"))
    assert verdict == "fail" and "Q_equal" in detail


def test_evaluate_bytes_mismatch_is_fail():
    verdict, detail, _ = _evaluate(_save(), _reopen(loaded_sha256="b" * 64))
    assert verdict == "fail" and "bytes_sha256_equal" in detail


def test_evaluate_counts_mismatch_is_fail():
    verdict, detail, _ = _evaluate(
        _save(), _reopen(counts={"events": 6, "gates": 2, "eventNodes": 7,
                                 "connections": 9}))
    assert verdict == "fail" and "counts_equal" in detail


def test_evaluate_phaseA_error_is_error():
    verdict, detail, checks = _evaluate(
        {"status": "save_error", "error": "boom"}, _reopen())
    assert verdict == "error" and "阶段A" in detail
    assert checks["phaseA_ok"] is False


def test_evaluate_phaseB_error_is_error():
    verdict, detail, _ = _evaluate(_save(), {"status": "no_output", "exit_code": 3})
    assert verdict == "error" and "阶段B" in detail


# --------------------------------------------------- run_reopen_check(synthetic)
def test_reopen_check_dry_run_is_blocked_with_artifacts(tmp_path):
    res = run_reopen_check(FIXTURE, tmp_path / "runs", case="unit_case", execute=False)
    assert res.verdict == "blocked"
    assert res.mode == "dry-run"
    assert "execute=False" in res.verdict_detail
    ev = Path(res.evidence_dir)
    assert (ev / "manifest.json").exists()
    assert (ev / "inputs" / "unit_case_faulttreeplus.xml").exists()
    assert json.loads((ev / "manifest.json").read_text(encoding="utf-8"))["job_state"] == "blocked"


def test_reopen_check_blocks_when_license_down(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "medini_automation.application.persistence.license_service_running",
        lambda: False)
    res = run_reopen_check(FIXTURE, tmp_path / "runs", case="unit_case")
    assert res.verdict == "blocked"
    assert "1055" in res.verdict_detail or "许可" in res.verdict_detail


def test_reopen_check_blocks_when_workcopy_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "medini_automation.application.persistence.license_service_running",
        lambda: True)
    res = run_reopen_check(
        FIXTURE, tmp_path / "runs", case="unit_case",
        workcopy_project=tmp_path / "nope")
    assert res.verdict == "blocked"
    assert "工作副本工程不存在" in res.verdict_detail


def test_reopen_check_bad_contract_is_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "bad", "events": {}, "gates": {},
                               "top": "MISSING"}), encoding="utf-8")
    res = run_reopen_check(bad, tmp_path / "runs", case="bad")
    assert res.verdict == "error" and res.contract_valid is False
