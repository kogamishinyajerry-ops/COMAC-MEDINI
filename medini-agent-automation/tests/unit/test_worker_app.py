"""worker/application 层单测：状态机、幂等、切片编排（synthetic）。"""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from medini_automation.domain.changeset import (
    ChangeOp, ChangeSet, ApprovalRef,
)
from medini_automation.worker.job import (
    InvalidTransition, Job, JobStore,
)
from medini_automation.application.slice import run_slice

FIX = Path(__file__).resolve().parents[1] / "fixtures"


class TestJobStateMachine:
    def test_happy_path(self):
        j = Job()
        for s in ("validated", "awaiting_approval", "queued",
                  "running", "readback", "verified"):
            j.transition(s)
        assert j.state == "verified"

    def test_no_backward(self):
        j = Job()
        j.transition("running")
        with pytest.raises(InvalidTransition, match="BACKWARD"):
            j.transition("validated")

    def test_branch_states(self):
        j = Job()
        j.transition("blocked", "许可停止")
        assert j.state == "blocked"

    def test_verified_terminal_no_branch(self):
        j = Job()
        for s in ("validated", "queued", "running", "readback", "verified"):
            j.transition(s)
        with pytest.raises(InvalidTransition, match="TERMINAL"):
            j.transition("failed")

    def test_unknown_state_rejected(self):
        j = Job()
        with pytest.raises(InvalidTransition, match="UNKNOWN_STATE"):
            j.transition("success")   # 禁止编造状态


class TestIdempotency:
    def test_same_key_same_payload_returns_existing(self, tmp_path):
        store = JobStore(tmp_path)
        j1 = Job(idempotency_key="K1", payload_digest="D1")
        store.save(j1)
        found = store.find_by_idempotency("K1", "D1")
        assert found is not None and found.job_id == j1.job_id

    def test_same_key_diff_payload_conflict(self, tmp_path):
        store = JobStore(tmp_path)
        store.save(Job(idempotency_key="K1", payload_digest="D1"))
        with pytest.raises(KeyError, match="IDEMPOTENCY_CONFLICT"):
            store.find_by_idempotency("K1", "D2")

    def test_no_key_returns_none(self, tmp_path):
        store = JobStore(tmp_path)
        assert store.find_by_idempotency("K9", "D9") is None


class TestChangeSetApproval:
    """审批红线：Agent 不能自批；批准范围必须绑定 change_id。"""

    def test_no_approval_blocks_apply(self):
        cs = ChangeSet(
            change_id="C1", project_id="P1", expected_baseline_hash="h0",
            ops=[ChangeOp("add_event", {"id": "A", "probability": "1/10"})])
        problems = cs.validate_for_apply()
        assert any("NO_APPROVAL" in p for p in problems)

    def test_scope_mismatch_rejected(self):
        cs = ChangeSet(
            change_id="C1", project_id="P1", expected_baseline_hash="h0",
            ops=[ChangeOp("add_event", {"id": "A", "probability": "1/10"})],
            approval=ApprovalRef(
                approver="zhang", approved_at="2026-09-21T10:00:00+08:00",
                credential_fingerprint="fp", scope="change:C2"))
        problems = cs.validate_for_apply()
        assert any("APPROVAL_SCOPE_MISMATCH" in p for p in problems)

    def test_approved_true_in_args_is_not_credential(self):
        """输入 approved=true 不是批准凭证（契约 §5）。"""
        cs = ChangeSet(
            change_id="C1", project_id="P1", expected_baseline_hash="h0",
            ops=[ChangeOp("add_event",
                          {"id": "A", "probability": "1/10",
                           "approved": True})])
        assert cs.approval is None
        assert any("NO_APPROVAL" in p for p in cs.validate_for_apply())

    def test_valid_approval_passes(self):
        cs = ChangeSet(
            change_id="C1", project_id="P1", expected_baseline_hash="h0",
            ops=[ChangeOp("add_gate", {"id": "G", "type": "OR",
                                       "inputs": ["A"]})],
            approval=ApprovalRef(
                approver="zhang", approved_at="2026-09-21T10:00:00+08:00",
                credential_fingerprint="fp", scope="change:C1"))
        assert cs.validate_for_apply() == []

    def test_patch_hash_deterministic(self):
        mk = lambda: ChangeSet(
            change_id="C1", project_id="P1", expected_baseline_hash="h0",
            ops=[ChangeOp("add_event", {"id": "A",
                                        "probability": Fraction(1, 10)})])
        assert mk().patch_hash() == mk().patch_hash()


class TestSliceDryRun:
    """切片 dry-run：全链路除实机外全部真实执行。"""

    def test_abc_dryrun_blocked_honest(self, tmp_path):
        """许可 STOPPED 时 verdict=blocked（不是 fail 也不是假 pass）。"""
        res = run_slice(FIX / "slice_abc.json", tmp_path,
                        case="abc", execute=True)
        assert res.contract_valid
        assert res.reference_q == "11/250"            # 0.044
        assert res.reference_mcs == [["A", "B"], ["A", "C"]]
        assert res.xml_path and Path(res.xml_path).exists()
        # 实机不可用 → blocked（或本机许可恰好恢复 → pass/fail，都不许是假数据）
        assert res.verdict in ("blocked", "pass", "fail")
        if res.verdict == "blocked":
            assert "许可" in res.verdict_detail or "STOPPED" in res.verdict_detail \
                or "不存在" in res.verdict_detail

    def test_manifest_contents(self, tmp_path):
        res = run_slice(FIX / "slice_or_save.json", tmp_path,
                        case="or_save", execute=False)
        ev = Path(res.evidence_dir)
        manifest = json.loads((ev / "manifest.json").read_text("utf-8"))
        for key in ("job_id", "case", "mode", "semantic_model_hash",
                    "native_raw_hash", "xml_artifact_hash", "verdict",
                    "adapter_version", "contracts_version"):
            assert key in manifest, key
        assert (ev / "inputs" / "contract.json").exists()
        assert (ev / "inputs" / "or_save_faulttreeplus.xml").exists()
        assert (ev / "verification" / "reference.json").exists()

    def test_illegal_contract_fails_clean(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({
            "name": "bad", "top_gate_id": "TOP",
            "events": [{"id": "A", "probability": "0.1"}],
            "gates": [{"id": "TOP", "type": "OR",
                       "inputs": ["A", "MISSING"]}]}), encoding="utf-8")
        res = run_slice(bad, tmp_path, case="bad", execute=False)
        assert res.verdict == "error"
        assert "UNKNOWN_REFERENCE" in res.verdict_detail
