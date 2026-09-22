"""P3 域层测试：审批校验链 + 防重复机制。

分三块：
- `TestApprovalChain` —— 签发 / 校验 / **每一条拒绝路径各有精确 code**
- `TestSignatureOnly` —— 幂等重放用的"只验签"路径
- `TestReplayGuard` / `TestWriterLock` —— nonce 一次性、幂等键、跨进程写锁

全部 synthetic，不依赖 medini 与网络。信任根指向 tmp_path
（`MEDINI_APPROVAL_HOME`），不碰用户的 `~/.medini-approval`。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from medini_automation.application.replay_guard import (
    IdempotencyStore,
    NonceStore,
    new_idempotency_key,
)
from medini_automation.application.writer_lock import (
    WriterBusy,
    read_lock_holder,
    writer_lock,
)
from medini_automation.domain import approval as AP
from medini_automation.domain import ed25519 as ED

SEED_A = bytes.fromhex("11" * 32)          # 有 model_write + analysis_run
SEED_B = bytes.fromhex("22" * 32)          # 只有 evidence_publish
SEED_X = bytes.fromhex("33" * 32)          # **不在信任根里**（攻击者密钥）


@pytest.fixture()
def trust_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "approval"
    home.mkdir()
    (home / "trust.json").write_text(json.dumps({
        "max_validity_seconds": 3600,
        "approvers": [
            {"identity": "alice", "public_key": ED.public_key(SEED_A).hex(),
             "permissions": ["model_write", "analysis_run"], "note": "结构组"},
            {"identity": "bob", "public_key": ED.public_key(SEED_B).hex(),
             "permissions": ["evidence_publish"], "note": "只看不发"},
        ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("MEDINI_APPROVAL_HOME", str(home))
    return home


CTX = AP.ApprovalContext(
    change_id="chg-1", project_id="AUTO-WC",
    patch_hash="a" * 64, expected_baseline_hash="b" * 64,
    required_permission="model_write")


def issue(**kw) -> dict:
    kw.setdefault("seed", SEED_A)
    kw.setdefault("approver", "alice")
    kw.setdefault("context", CTX)
    kw.setdefault("credential_fingerprint", "sha256:deadbeef")
    return AP.issue_attestation(**kw)


# ==================================================== 校验链
class TestApprovalChain:
    def test_happy_path(self, trust_home: Path):
        ref = AP.verify_attestation(issue(), context=CTX)
        assert ref["approver"] == "alice" and ref["signed"] is True
        assert ref["permission"] == "model_write"
        assert ref["trust_source"] == str(trust_home / "trust.json")
        assert ref["patch_hash"] == CTX.patch_hash

    def test_signed_payload_covers_every_field(self, trust_home: Path):
        """任何字段被改都验不过 —— 逐字段验证（不是抽查）。"""
        att = issue()
        for field in AP.SIGNED_FIELDS:
            if field in ("v", "permission"):        # 这两个改完走更早的分支
                continue
            bent = {**att, field: ("zzz" if field != "scope" else "change:other")}
            with pytest.raises(AP.ApprovalError):
                AP.verify_attestation(bent, context=CTX)

    def test_missing_signature_is_unsigned_not_incomplete(self, trust_home: Path):
        """没签名 ≠ 缺字段。给出更准确的 code。"""
        att = {k: v for k, v in issue().items() if k != "signature"}
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(att, context=CTX)
        assert ei.value.code == "APPROVAL_UNSIGNED"

    def test_empty_or_none_attestation(self, trust_home: Path):
        for bad in ({}, None, "text"):
            with pytest.raises(AP.ApprovalError) as ei:
                AP.verify_attestation(bad, context=CTX)  # type: ignore[arg-type]
            assert ei.value.code == "NO_APPROVAL"

    @pytest.mark.parametrize(
        "drop", [f for f in AP.SIGNED_FIELDS if f != "credential_fingerprint"])
    def test_missing_required_field(self, trust_home: Path, drop: str):
        att = issue()
        att[drop] = ""
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(att, context=CTX)
        assert ei.value.code == "APPROVAL_INCOMPLETE"
        assert drop in ei.value.message

    def test_blank_audit_field_breaks_signature_not_completeness(
            self, trust_home: Path):
        """`credential_fingerprint` 允许为空，但**改它一样验不过**。

        它不是必需字段（审计信息），但仍是签名覆盖范围 —— 所以被改会走
        验签失败而不是"缺字段"。
        """
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation({**issue(), "credential_fingerprint": ""},
                                  context=CTX)
        assert ei.value.code == "APPROVAL_SIGNATURE_INVALID"

    def test_unsupported_version(self, trust_home: Path):
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation({**issue(), "v": 99}, context=CTX)
        assert ei.value.code == "APPROVAL_VERSION"

    def test_unknown_permission_value(self, trust_home: Path):
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation({**issue(), "permission": "root"}, context=CTX)
        assert ei.value.code == "APPROVAL_PERMISSION_UNKNOWN"

    # -------------------------------------------------- 时间
    def test_expired(self, trust_home: Path):
        att = issue(validity_seconds=60)
        later = datetime.now(timezone.utc) + timedelta(seconds=61)
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(att, context=CTX, now=later)
        assert ei.value.code == "APPROVAL_EXPIRED"

    def test_not_yet_valid(self, trust_home: Path):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        att = issue(now=future)
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(att, context=CTX)
        assert ei.value.code == "APPROVAL_NOT_YET_VALID"

    def test_timestamp_without_timezone_rejected(self, trust_home: Path):
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(
                {**issue(), "approved_at": "2026-09-21T20:00:00"}, context=CTX)
        assert ei.value.code == "APPROVAL_TIMESTAMP"

    def test_expires_not_after_approved(self, trust_home: Path):
        att = issue()
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation({**att, "expires_at": att["approved_at"]},
                                  context=CTX)
        assert ei.value.code == "APPROVAL_TIMESTAMP"

    def test_validity_span_capped_by_trust_root(self, trust_home: Path):
        """签发侧不设上限，上限由信任根统一强制（避免两处规则漂移）。"""
        att = issue(validity_seconds=7200)          # 信任根上限 3600
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(att, context=CTX)
        assert ei.value.code == "APPROVAL_VALIDITY_TOO_LONG"

    # -------------------------------------------------- 内容绑定
    def test_scope_mismatch(self, trust_home: Path):
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation({**issue(), "scope": "change:other"}, context=CTX)
        assert ei.value.code == "APPROVAL_SCOPE_MISMATCH"

    def test_project_mismatch(self, trust_home: Path):
        other = AP.ApprovalContext("chg-1", "OTHER", "a" * 64, "b" * 64)
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(issue(), context=other)
        assert ei.value.code == "APPROVAL_PROJECT_MISMATCH"

    def test_patch_hash_is_bound(self, trust_home: Path):
        """凭证绑内容摘要，不只绑 change_id —— 换内容即失效。"""
        other = AP.ApprovalContext("chg-1", "AUTO-WC", "Z" * 64, "b" * 64)
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(issue(), context=other)
        assert ei.value.code == "APPROVAL_PATCH_MISMATCH"

    def test_baseline_hash_is_bound(self, trust_home: Path):
        other = AP.ApprovalContext("chg-1", "AUTO-WC", "a" * 64, "Z" * 64)
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(issue(), context=other)
        assert ei.value.code == "APPROVAL_BASELINE_MISMATCH"

    # -------------------------------------------------- 身份与权限
    def test_unknown_approver(self, trust_home: Path):
        att = issue(seed=SEED_X, approver="mallory")
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(att, context=CTX)
        assert ei.value.code == "APPROVAL_UNKNOWN_APPROVER"

    def test_forged_by_other_key(self, trust_home: Path):
        """用不在信任根的密钥冒充 alice → 只能靠密码学挡住，字段全对。"""
        att = issue(seed=SEED_X, approver="alice")
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(att, context=CTX)
        assert ei.value.code == "APPROVAL_SIGNATURE_INVALID"

    def test_permission_denied(self, trust_home: Path):
        """bob 只有 evidence_publish，签不出 model_write 的批准。"""
        att = issue(seed=SEED_B, approver="bob")
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(att, context=CTX)
        assert ei.value.code == "APPROVAL_PERMISSION_DENIED"

    def test_admin_implies_all_permissions(self, tmp_path, monkeypatch):
        home = tmp_path / "adm"
        home.mkdir()
        (home / "trust.json").write_text(json.dumps({
            "approvers": [{"identity": "root", "public_key": ED.public_key(SEED_A).hex(),
                           "permissions": ["admin"]}]}), encoding="utf-8")
        monkeypatch.setenv("MEDINI_APPROVAL_HOME", str(home))
        ref = AP.verify_attestation(issue(approver="root"), context=CTX)
        assert ref["approver"] == "root"

    # -------------------------------------------------- fail-closed
    def test_fail_closed_when_trust_root_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEDINI_APPROVAL_HOME", str(tmp_path / "nope"))
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(issue(), context=CTX)
        assert ei.value.code == "TRUST_ROOT_MISSING"

    def test_fail_closed_when_trust_root_corrupt(self, tmp_path, monkeypatch):
        home = tmp_path / "bad"
        home.mkdir()
        (home / "trust.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("MEDINI_APPROVAL_HOME", str(home))
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(issue(), context=CTX)
        assert ei.value.code == "TRUST_ROOT_MISSING"
        # 并且能在 source 里看到"为什么"（cli trust 会显示它）
        assert "解析失败" in AP.load_trust_root().source

    def test_trust_root_never_raises(self, tmp_path: Path):
        """load_trust_root 对任何畸形输入都退化为空 TrustRoot，不抛。"""
        for content in ("[]", "{}", '{"approvers": "nope"}',
                        '{"approvers": [{"identity": "x"}]}'):
            p = tmp_path / "t.json"
            p.write_text(content, encoding="utf-8")
            assert AP.load_trust_root(p).entries == {}

    def test_bad_public_key_in_trust_root(self, tmp_path, monkeypatch):
        home = tmp_path / "badkey"
        home.mkdir()
        (home / "trust.json").write_text(json.dumps({
            "approvers": [{"identity": "alice", "public_key": "zz",
                           "permissions": ["model_write"]}]}), encoding="utf-8")
        monkeypatch.setenv("MEDINI_APPROVAL_HOME", str(home))
        with pytest.raises(AP.ApprovalError) as ei:
            AP.verify_attestation(issue(), context=CTX)
        assert ei.value.code == "TRUST_ROOT_INVALID_KEY"

    # -------------------------------------------------- 签发侧守卫
    def test_issue_rejects_bad_arguments(self):
        with pytest.raises(ValueError):
            issue(validity_seconds=0)
        with pytest.raises(ValueError):
            issue(approver="")
        with pytest.raises(ValueError):
            issue(permission="root")

    def test_issue_uses_fresh_nonce_by_default(self):
        assert issue()["nonce"] != issue()["nonce"]

    def test_payload_requires_all_fields(self):
        with pytest.raises(ValueError):
            AP.approval_payload({"v": 1})


# ==================================================== 只验签
class TestSignatureOnly:
    def test_ok_for_valid_signature(self, trust_home: Path):
        assert AP.verify_signature_only(issue()) == (True, "")

    def test_rejects_tampered(self, trust_home: Path):
        att = {**issue(), "patch_hash": "c" * 64}
        ok, why = AP.verify_signature_only(att)
        assert ok is False and "APPROVAL_SIGNATURE_INVALID" in why

    def test_ignores_expiry(self, trust_home: Path):
        """重放针对已完成的作业 —— 凭证此刻过期不该影响重放判定。"""
        att = issue(validity_seconds=60)
        time.sleep(0.01)
        assert AP.verify_signature_only(att)[0] is True

    def test_rejects_no_signature(self, trust_home: Path):
        ok, why = AP.verify_signature_only({"approver": "alice"})
        assert ok is False and "APPROVAL_UNSIGNED" in why

    def test_fail_closed_without_trust_root(self, tmp_path, monkeypatch):
        att_before = None
        monkeypatch.setenv("MEDINI_APPROVAL_HOME", str(tmp_path / "a"))
        Path(tmp_path / "a").mkdir()
        (Path(tmp_path / "a") / "trust.json").write_text(json.dumps({
            "approvers": [{"identity": "alice",
                           "public_key": ED.public_key(SEED_A).hex(),
                           "permissions": ["model_write"]}]}), encoding="utf-8")
        att_before = issue()
        monkeypatch.setenv("MEDINI_APPROVAL_HOME", str(tmp_path / "gone"))
        ok, why = AP.verify_signature_only(att_before)
        assert ok is False and "TRUST_ROOT_MISSING" in why


# ==================================================== 防重复
class TestReplayGuard:
    def test_nonce_consume_is_single_use(self, tmp_path: Path):
        ns = NonceStore(tmp_path)
        first = ns.consume("n1", actor="alice", scope="change:c1")
        assert first.state == "fresh"
        second = ns.consume("n1", actor="mallory")
        assert second.is_replay
        assert second.first_used_by == "alice"
        assert second.first_used_at

    def test_nonce_peek_has_no_side_effect(self, tmp_path: Path):
        ns = NonceStore(tmp_path)
        assert ns.peek("n2").state == "fresh"
        assert ns.peek("n2").state == "fresh"       # 仍然 fresh
        ns.consume("n2")
        assert ns.peek("n2").is_replay

    def test_nonce_isolation_between_values(self, tmp_path: Path):
        ns = NonceStore(tmp_path)
        ns.consume("a")
        assert ns.peek("b").state == "fresh"

    def test_nonce_path_is_not_traversable(self, tmp_path: Path):
        """nonce 来自外部输入 → 不能拼出仓库外的路径。"""
        ns = NonceStore(tmp_path)
        ns.consume("../../../etc/passwd")
        written = list(ns.dir.glob("*.json"))
        assert len(written) == 1
        assert ".." not in str(written[0])

    def test_idempotency_new_then_replay(self, tmp_path: Path):
        st = IdempotencyStore(tmp_path)
        assert st.lookup("k1", "h1").state == "new"
        st.record("k1", "h1", "apply_change", {"status": "ok"})
        v = st.lookup("k1", "h1")
        assert v.is_replay and v.record is not None
        assert v.record["result"] == {"status": "ok"}

    def test_idempotency_conflict_on_different_payload(self, tmp_path: Path):
        st = IdempotencyStore(tmp_path)
        st.record("k1", "h1", "apply_change", {"status": "ok"})
        v = st.lookup("k1", "h2")
        assert v.state == "conflict"
        assert "h1"[:16] in v.detail and "h2"[:16] in v.detail

    def test_idempotency_keys_are_independent(self, tmp_path: Path):
        st = IdempotencyStore(tmp_path)
        st.record("k1", "h1", "op", {"n": 1})
        assert st.lookup("k2", "h1").state == "new"

    def test_new_key_is_unique(self):
        assert new_idempotency_key() != new_idempotency_key()


# ==================================================== 写锁
class TestWriterLock:
    def test_second_writer_is_blocked(self, tmp_path: Path):
        with writer_lock("WC/abc", root=tmp_path, actor="w1"):
            with pytest.raises(WriterBusy) as ei:
                with writer_lock("WC/abc", root=tmp_path, actor="w2"):
                    pass
            assert "w1" in str(ei.value)

    def test_released_after_exit(self, tmp_path: Path):
        with writer_lock("WC/abc", root=tmp_path, actor="w1"):
            assert read_lock_holder(tmp_path, "WC/abc").held is True
        assert read_lock_holder(tmp_path, "WC/abc").held is False

    def test_released_even_on_exception(self, tmp_path: Path):
        with pytest.raises(RuntimeError):
            with writer_lock("WC/abc", root=tmp_path, actor="w1"):
                raise RuntimeError("boom")
        assert read_lock_holder(tmp_path, "WC/abc").held is False

    def test_scopes_are_independent(self, tmp_path: Path):
        with writer_lock("WC/abc", root=tmp_path, actor="w1"):
            with writer_lock("WC/or", root=tmp_path, actor="w1"):
                assert read_lock_holder(tmp_path, "WC/or").held is True

    def test_old_lock_is_refused_not_preempted(self, tmp_path: Path):
        """锁龄不能证明写者已停止：老旧锁一律拒绝，绝不自动抢占。

        旧契约按 stale_after_s 抢占「看起来太旧」的锁 —— 长作业期间那会
        放进第二个写者。新契约 fail closed：恢复必须先停写、回读、归档，
        再由受控管理员解除。本测试固定这条语义，不因绿灯而放宽。
        """
        import hashlib
        lockdir = tmp_path / "locks"
        with writer_lock("WC/abc", root=tmp_path, actor="w1"):
            pass
        assert list(lockdir.glob("*.lock")) == [], "正常退出应删掉锁"

        slug = hashlib.sha256(b"WC/abc").hexdigest()[:40]
        lp = lockdir / f"{slug}.lock"
        lp.write_text('{"actor":"dead","pid":1,"at":"2020-01-01T00:00:00"}',
                      encoding="utf-8")
        old = time.time() - 9999
        os.utime(lp, (old, old))

        # 锁龄 9999s，远超任何一个阈值：显式 stale_after_s、极小阈值、
        # 以及默认值三种情况都必须拒绝，而不是抢占。
        for kwargs in ({"stale_after_s": 600}, {"stale_after_s": 1}, {}):
            with pytest.raises(WriterBusy) as ei:
                with writer_lock("WC/abc", root=tmp_path, actor="rescuer", **kwargs):
                    pass
            assert "dead" in str(ei.value)

        # 拒绝之后原始记录必须原样保留：不删除、不改写、不留抢占审计。
        assert lp.exists(), "拒绝写入时不得删除既有锁记录"
        assert json.loads(lp.read_text(encoding="utf-8"))["actor"] == "dead"
        assert read_lock_holder(tmp_path, "WC/abc").holder["actor"] == "dead"
        assert list(lockdir.glob("*.stale.jsonl")) == [], "不得再产生抢占审计"

    def test_lock_wait_times_out(self, tmp_path: Path):
        with writer_lock("WC/abc", root=tmp_path, actor="w1"):
            t0 = time.monotonic()
            with pytest.raises(WriterBusy):
                with writer_lock("WC/abc", root=tmp_path, actor="w2", wait_s=0.2):
                    pass
            assert time.monotonic() - t0 >= 0.15
