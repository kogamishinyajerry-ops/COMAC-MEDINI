"""P2/P3 单元测试：application/agent_api 的九个受控操作。

全部 synthetic：真实 ``.fta``（tests/fixtures/abc.fta，medini 落盘产物）+
临时工程 / 临时状态根；不依赖 medini 进程与许可。

覆盖重点是**拒绝路径**——本层的价值就在「该拒的必须拒，且拒得可解释」：
白名单、只读工程、基线绑定、审批门禁（**签名 / 有效期 / 权限 / 内容绑定 /
防重放**）、patch_hash 防篡改、幂等、单写者锁、证据完整性。

P3 之后审批凭证必须**签名**：本文件的 `approve()` 用审批面板的私钥真签发，
不再手写四个字段 —— 那正是 P2 的漏洞（只比 scope 字符串，等于允许 Agent 自批）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from medini_automation.application import agent_api as A
from medini_automation.domain import approval as AP
from medini_automation.domain import ed25519 as ED
from medini_automation.worker.job import Job, JobStore

FIX = Path(__file__).resolve().parents[1] / "fixtures"
ABC_FTA = FIX / "abc.fta"
ABC_CONTRACT = FIX / "slice_abc.json"

ECLIPSE_PROJECT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<projectDescription>
\t<name>PROJ</name>
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


def make_project(tmp_path: Path, name: str = "WC") -> Path:
    pdir = tmp_path / name
    (pdir / "fta").mkdir(parents=True)
    (pdir / ".project").write_text(ECLIPSE_PROJECT_XML, encoding="utf-8")
    (pdir / ".project.medini").write_text(MIN_PROJECT, encoding="utf-8")
    (pdir / "fta" / "abc.fta").write_bytes(ABC_FTA.read_bytes())
    return pdir


#: 审批面板的私钥（测试固定值 —— 只为复现，不是任何真实身份的凭据）
APPROVER = "E12345"
APPROVER_SEED = bytes.fromhex("11" * 32)
#: 另一个身份：无 model_write 权限（用来测权限不足）
LOOKER = "R99999"
LOOKER_SEED = bytes.fromhex("22" * 32)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离环境：临时工程 + 临时状态根 + 临时信任根。"""
    wc = make_project(tmp_path, "WC")
    ro = make_project(tmp_path, "RO")
    monkeypatch.setattr(A, "PROJECTS", {
        "WC": A.ProjectEntry("WC", wc, True, "workcopy", "test"),
        "RO": A.ProjectEntry("RO", ro, False, "source-project", "test"),
    })
    monkeypatch.setattr(A, "STATE_ROOT", tmp_path / "state")
    # 信任根放仓库外（测试里就是 tmp）—— 谁签得了批准由这个文件决定
    home = tmp_path / "approval"
    home.mkdir()
    (home / "trust.json").write_text(json.dumps({
        "max_validity_seconds": 3600,
        "approvers": [
            {"identity": APPROVER, "public_key": ED.public_key(APPROVER_SEED).hex(),
             "permissions": ["model_write", "analysis_run"], "note": "结构组"},
            {"identity": LOOKER, "public_key": ED.public_key(LOOKER_SEED).hex(),
             "permissions": ["evidence_publish"], "note": "只看不发"},
        ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("MEDINI_APPROVAL_HOME", str(home))
    runs = tmp_path / "runs"
    runs.mkdir()
    return {"wc": wc, "ro": ro, "state": tmp_path / "state", "runs": runs,
            "tmp": tmp_path, "approval_home": home}


def bootstrap(env, ops=None, case: str = "abc") -> dict:
    """建立 v1 基线（并把候选变更做成 awaiting_approval 的提案）。"""
    return A.prepare_change(
        "WC", case, ops or [{"op": "set_probability",
                             "args": {"id": "A", "probability": "1/4"}}],
        expected_baseline_hash=None, contract_path=str(ABC_CONTRACT),
        reason="test")


def approve(change_id: str, *, seed: bytes = APPROVER_SEED,
            approver: str = APPROVER, **kw) -> dict:
    """用受信任审批面板的私钥**真签发**一份凭证。

    上下文从变更单本身读 —— 这样测试改的不是"四个字段"，而是"签谁、签什么"。
    """
    blob = json.loads(A._change_path(change_id).read_text(encoding="utf-8"))
    ctx = AP.ApprovalContext(
        change_id=change_id, project_id=blob["project_id"],
        patch_hash=blob["patch_hash"],
        expected_baseline_hash=blob["expected_baseline_hash"],
        required_permission=blob.get("required_permission", "model_write"))
    return AP.issue_attestation(
        seed=seed, approver=approver, context=ctx,
        credential_fingerprint="sha256:deadbeef", **kw)


# ============================================================ get_capabilities
def test_capabilities_shape():
    r = A.get_capabilities()
    assert r["status"] in ("ok", "blocked")
    assert r["capabilities"] and all(
        c["status"] in ("verified", "partial", "unsupported", "unverified")
        for c in r["capabilities"])
    assert r["worker_count"] == 1
    assert "license_port_1055" in r["worker_env"]


def test_capabilities_worker_id_and_version_match():
    r = A.get_capabilities(worker_id="W1", required_version="medini-analyze-2023R2")
    assert r["worker_id"] == "W1"
    assert r["required_version"] == "medini-analyze-2023R2"
    assert all("version_match" in c for c in r["capabilities"])
    assert isinstance(r["version_satisfied"], bool)


def test_capabilities_unknown_version_never_satisfied():
    r = A.get_capabilities(required_version="medini-analyze-9999")
    assert r["version_satisfied"] is False


def test_capabilities_lists_projects(env):
    r = A.get_capabilities()
    ids = {p["project_id"] for p in r["worker_env"]["projects"]}
    assert ids == {"WC", "RO"}


# =============================================================== read_project
def test_read_project_rejects_unknown_id(env):
    r = A.read_project("NOPE")
    assert r["status"] == "blocked" and r["code"] == "PROJECT_NOT_ALLOWED"
    assert r["hint"] and r["recovery"]


def test_read_project_lists_cases(env):
    r = A.read_project("WC")
    assert r["status"] == "ok"
    assert r["cases"] == ["abc"]
    assert r["orphan_cases"] == ["abc"]        # 还没发布图 → 孤儿
    assert r["registered_diagrams"] == []


def test_read_project_case_not_found(env):
    r = A.read_project("WC", case="nope")
    assert r["status"] == "blocked" and r["code"] == "CASE_NOT_FOUND"
    assert "abc" in r["hint"]


def test_read_project_missing_project_dir(env, monkeypatch):
    monkeypatch.setattr(A, "PROJECTS", {
        "GONE": A.ProjectEntry("GONE", env["tmp"] / "gone", True, "workcopy")})
    r = A.read_project("GONE")
    assert r["status"] == "blocked" and r["code"] == "PROJECT_MISSING"


def test_read_project_native_snapshot(env):
    r = A.read_project("WC", case="abc")
    assert r["status"] == "ok"
    n = r["native"]
    assert n["counts"] == {"connection": 9, "eventNode": 7, "gate": 3}
    assert n["model_xmi_id"]
    assert len(n["events"]) == 6      # 含门输出被建模成的 Event
    assert len(n["gates"]) == 3
    assert len(n["file_sha256"]) == 64


def test_read_project_without_baseline_points_to_prepare(env):
    r = A.read_project("WC", case="abc")
    assert r["baseline"] is None and r["mapping"] is None
    assert r["native_drift"] is None
    assert "medini_prepare_change" in r["next_action"]


def test_read_project_mapping_and_mapping_loss(env):
    bootstrap(env)
    r = A.read_project("WC", case="abc")
    m = r["mapping"]
    assert [e["contract_id"] for e in m["events"]] == ["A", "B", "C"]
    assert all(e["native_xmi_id"] for e in m["events"])
    assert all(g["native_xmi_id"] for g in m["gates"])
    assert m["unmatched_contract_events"] == []
    assert m["unmatched_contract_gates"] == []
    # 映射损失是实测观察，必须非空且覆盖关键差异
    joined = " ".join(r["mapping_loss"])
    assert "事件计数语义不同" in joined
    assert "省略默认 kind" in joined
    assert "xmi:id" in joined


def test_native_drift_detects_stale_native_file(env):
    """基线推进（A→1/4）后原生 .fta 仍是 0.1 → 必须报漂移。

    ``prepare_change`` 只提案；基线要等 ``apply_change`` 才推进，所以漂移在
    实施之后才出现——这正是「分析/变更已推进但原生文件还没跟上」的真实形态。
    """
    cid = bootstrap(env)["change_id"]
    assert A.read_project("WC", case="abc")["native_drift"] == []
    assert A.apply_change(cid, approval=approve(cid))["status"] == "ok"
    r = A.read_project("WC", case="abc")
    assert r["native_drift"] == [{"contract_id": "A", "native": "0.1",
                                  "baseline": "1/4"}]
    assert "medini_reopen_check" in r["next_action"]


def test_native_drift_empty_when_consistent(env):
    """基线概率与原生一致时不报漂移。"""
    A.prepare_change("WC", "abc", [], expected_baseline_hash=None,
                     contract_path=str(ABC_CONTRACT))
    r = A.read_project("WC", case="abc")
    assert r["native_drift"] == []
    assert "next_action" not in r


# ============================================================= prepare_change
def test_prepare_rejects_read_only_project(env):
    r = A.prepare_change("RO", "abc", [], expected_baseline_hash=None,
                         contract_path=str(ABC_CONTRACT))
    assert r["status"] == "blocked" and r["code"] == "PROJECT_READ_ONLY"


def test_prepare_bootstrap_establishes_baseline(env):
    r = bootstrap(env)
    assert r["status"] == "ok" and r["bootstrapped_baseline"] is True
    assert r["baseline_version"] == 1 and r["next_version"] == 2
    assert r["baseline_hash"] != r["candidate_hash"]
    assert len(r["patch_hash"]) == 64
    assert r["requires_approval"] is True
    assert r["diff"][0]["target"] == "A"
    # 基线已落盘
    assert (env["state"] / "projects" / "WC" / "abc.baseline.json").exists()
    assert (env["state"] / "projects" / "WC" / "abc.contract.json").exists()


def test_prepare_bootstrap_requires_contract(env):
    r = A.prepare_change("WC", "abc", [], expected_baseline_hash=None)
    assert r["code"] == "CONTRACT_REQUIRED"


def test_prepare_bootstrap_contract_missing(env):
    r = A.prepare_change("WC", "abc", [], expected_baseline_hash=None,
                         contract_path=str(env["tmp"] / "nope.json"))
    assert r["code"] == "CONTRACT_NOT_FOUND"


def test_prepare_invalid_contract_rejected(env):
    bad = env["tmp"] / "bad.json"
    bad.write_text(json.dumps({"schema": "static-fta", "name": "x",
                               "top_gate_id": "TOP", "events": [],
                               "gates": []}), encoding="utf-8")
    r = A.prepare_change("WC", "abc", [], expected_baseline_hash=None,
                         contract_path=str(bad))
    assert r["status"] == "blocked" and r["code"] == "CONTRACT_INVALID"


def test_prepare_hash_required_once_baseline_exists(env):
    bootstrap(env)
    r = A.prepare_change("WC", "abc", [], expected_baseline_hash=None)
    assert r["code"] == "BASELINE_HASH_REQUIRED"
    assert r["hint"].startswith("当前基线哈希 = ")


def test_prepare_baseline_mismatch(env):
    bootstrap(env)
    r = A.prepare_change("WC", "abc", [], expected_baseline_hash="deadbeef")
    assert r["code"] == "BASELINE_MISMATCH"


def test_prepare_unbound_hash_on_fresh_project(env):
    r = A.prepare_change("WC", "abc", [], expected_baseline_hash="deadbeef")
    assert r["code"] == "BASELINE_UNKNOWN"


@pytest.mark.parametrize("op,args,code", [
    ({"op": "add_event", "args": {"id": "A", "probability": "1/2"}}, None, "OP_REJECTED"),
    ({"op": "add_event", "args": {"id": "D"}}, None, "OP_REJECTED"),
    ({"op": "add_event", "args": {"id": "D", "probability": "zzz"}}, None, "OP_REJECTED"),
    ({"op": "add_gate", "args": {"id": "G", "type": "AND", "inputs": ["XX"]}}, None, "OP_REJECTED"),
    ({"op": "add_gate", "args": {"id": "G", "type": "AND", "inputs": []}}, None, "OP_REJECTED"),
    ({"op": "set_probability", "args": {"id": "ZZ", "probability": "1/2"}}, None, "OP_REJECTED"),
    ({"op": "delete_everything", "args": {}}, None, "OP_UNSUPPORTED"),
])
def test_prepare_rejected_ops(env, op, args, code):
    bootstrap(env)
    cur = A.read_project("WC", case="abc")["baseline"]["semantic_hash"]
    r = A.prepare_change("WC", "abc", [op], expected_baseline_hash=cur)
    assert r["status"] == "blocked" and r["code"] == code


def test_prepare_add_event_and_gate_accepted(env):
    bootstrap(env)
    cur = A.read_project("WC", case="abc")["baseline"]["semantic_hash"]
    r = A.prepare_change("WC", "abc", [
        {"op": "add_event", "args": {"id": "D", "probability": "1/10",
                                     "description": "新事件"}},
        {"op": "add_gate", "args": {"id": "G_NEW", "type": "AND",
                                    "inputs": ["C", "D"]}},
    ], expected_baseline_hash=cur)
    assert r["status"] == "ok"
    assert [d["op"] for d in r["diff"]] == ["add_event", "add_gate"]


def test_prepare_rejected_op_leaves_no_baseline(env):
    """被拒绝的提案不得留下半成品基线。"""
    r = A.prepare_change("WC", "abc",
                         [{"op": "set_probability",
                           "args": {"id": "ZZ", "probability": "1/2"}}],
                         expected_baseline_hash=None,
                         contract_path=str(ABC_CONTRACT))
    assert r["status"] == "blocked"
    assert not (env["state"] / "projects" / "WC" / "abc.baseline.json").exists()


def test_prepare_op_changes_do_not_touch_project_file(env):
    """提案是纯内存操作：不得改写工作副本里的 .fta。"""
    before = (env["wc"] / "fta" / "abc.fta").read_bytes()
    bootstrap(env)
    assert (env["wc"] / "fta" / "abc.fta").read_bytes() == before


# =============================================================== apply_change
def test_apply_change_not_found(env):
    r = A.apply_change("nope")
    assert r["code"] == "CHANGE_NOT_FOUND"


def test_apply_without_approval_refused(env):
    r = A.apply_change(bootstrap(env)["change_id"])
    assert r["status"] == "blocked" and r["code"] == "NO_APPROVAL"
    assert "受信任" in r["hint"] and "approved=true" in r["hint"]


def test_apply_unsigned_fields_are_not_a_credential(env):
    """P2 的漏洞回归：手写四个字段（无签名）必须被拒，而不是通过。

    上一版只比对 scope 字符串，等于 Agent 自己拼 JSON 就能自批。
    """
    cid = bootstrap(env)["change_id"]
    hand_written = {"approver": APPROVER, "approved_at": "2026-09-21T20:00:00+08:00",
                    "credential_fingerprint": "sha256:deadbeef",
                    "scope": f"change:{cid}"}
    r = A.apply_change(cid, approval=hand_written)
    assert r["status"] == "blocked" and r["code"] == "APPROVAL_UNSIGNED"
    assert "签名" in r["error"]


@pytest.mark.parametrize("drop", ["approver", "approved_at", "scope",
                                  "nonce", "patch_hash", "expires_at"])
def test_apply_approval_incomplete(env, drop):
    cid = bootstrap(env)["change_id"]
    appr = approve(cid)
    appr[drop] = ""
    r = A.apply_change(cid, approval=appr)
    assert r["code"] == "APPROVAL_INCOMPLETE"
    assert drop in r["error"]


def test_apply_blanked_audit_field_breaks_signature(env):
    """``credential_fingerprint`` 允许为空，但**改它一样验不过** —— 签名覆盖全部字段。"""
    cid = bootstrap(env)["change_id"]
    appr = approve(cid)
    appr["credential_fingerprint"] = ""
    r = A.apply_change(cid, approval=appr)
    assert r["code"] == "APPROVAL_SIGNATURE_INVALID"


def test_apply_approval_scope_mismatch(env):
    cid = bootstrap(env)["change_id"]
    appr = approve(cid)
    appr["scope"] = "change:OTHER"
    r = A.apply_change(cid, approval=appr)
    assert r["code"] == "APPROVAL_SCOPE_MISMATCH"
    assert f"change:{cid}" in r["hint"]


def test_apply_approval_bound_to_content_not_change_id(env):
    """批准绑定的是**内容摘要**，不是 change_id。

    把凭证原样拿去批另一个变更（哪怕 scope 相同）→ patch_hash 不匹配。
    """
    c1 = bootstrap(env)["change_id"]
    appr = approve(c1)
    v = A.read_project("WC", case="abc")["baseline"]["semantic_hash"]
    c2 = A.prepare_change("WC", "abc", [{"op": "set_probability",
                                         "args": {"id": "B", "probability": "1/3"}}],
                          expected_baseline_hash=v)
    r = A.apply_change(c2["change_id"], approval=appr)
    assert r["status"] == "blocked"
    assert r["code"] in ("APPROVAL_SCOPE_MISMATCH", "APPROVAL_PATCH_MISMATCH")


def test_apply_approval_perm_denied(env):
    cid = bootstrap(env)["change_id"]
    r = A.apply_change(cid, approval=approve(cid, seed=LOOKER_SEED, approver=LOOKER))
    assert r["code"] == "APPROVAL_PERMISSION_DENIED"


def test_apply_approval_expired(env):
    cid = bootstrap(env)["change_id"]
    appr = approve(cid, validity_seconds=1)
    import time as _t
    _t.sleep(2)
    r = A.apply_change(cid, approval=appr)
    assert r["code"] == "APPROVAL_EXPIRED"


def test_apply_approval_forged_by_other_key(env):
    """用别人的私钥签 APPROVER 的名字 → 验签失败。"""
    cid = bootstrap(env)["change_id"]
    r = A.apply_change(cid, approval=approve(cid, seed=LOOKER_SEED))
    assert r["code"] == "APPROVAL_SIGNATURE_INVALID"


def test_apply_fail_closed_when_trust_root_missing(env, monkeypatch):
    """信任根不可用 → 拒绝一切审批，而不是"没配置就放行"。"""
    cid = bootstrap(env)["change_id"]
    appr = approve(cid)
    monkeypatch.setenv("MEDINI_APPROVAL_HOME", str(env["tmp"] / "nope"))
    r = A.apply_change(cid, approval=appr)
    assert r["status"] == "blocked" and r["code"] == "TRUST_ROOT_MISSING"


def test_apply_happy_path_bumps_baseline(env):
    cid = bootstrap(env)["change_id"]
    before = A.read_project("WC", case="abc")["baseline"]["semantic_hash"]
    r = A.apply_change(cid, approval=approve(cid))
    assert r["status"] == "ok"
    assert r["baseline"]["version"] == 2
    assert r["baseline"]["prev_semantic_hash"] == before
    assert r["baseline"]["semantic_hash"] != before
    assert r["baseline"]["approved_by"] == "E12345"
    assert r["baseline"]["change_id"] == cid
    assert r["ops_applied"] == 1
    # 基线内容确实变了
    cur = A.read_project("WC", case="abc")["normalized"]
    assert next(e for e in cur["events"] if e["id"] == "A")["probability"] == "1/4"


def test_apply_replay_same_key_returns_first_result(env):
    """同一幂等键 + 同一载荷 → 重放首次结果，**不再写一遍**（规划 L114）。

    重放不是错误：调用方在重试，正确反应是把第一次的结果原样还给它。
    """
    cid = bootstrap(env)["change_id"]
    first = A.apply_change(cid, approval=approve(cid))
    assert first["status"] == "ok" and first["replayed"] is False
    v_after_first = A.read_project("WC", case="abc")["baseline"]["version"]

    replay = A.apply_change(cid, approval=approve(cid))
    assert replay["status"] == "ok" and replay["replayed"] is True
    assert replay["baseline"]["version"] == first["baseline"]["version"]
    assert A.read_project("WC", case="abc")["baseline"]["version"] == v_after_first


def test_apply_conflicting_payload_same_key_refused(env):
    """同一幂等键配不同载荷 → 拒绝（这是"用错键"，不是重放）。"""
    cid = bootstrap(env)["change_id"]
    A.apply_change(cid, approval=approve(cid))
    p = A._change_path(cid)
    blob = json.loads(p.read_text(encoding="utf-8"))
    blob["patch_hash"] = "0" * 64          # 伪造一个新载荷
    p.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
    r = A.apply_change(cid, approval=approve(cid))
    assert r["status"] == "blocked" and r["code"] == "IDEMPOTENCY_CONFLICT"


def test_apply_second_call_with_other_key_refused(env):
    """换一个幂等键再提交同一变更 → 不走重放，按状态拒绝。"""
    cid = bootstrap(env)["change_id"]
    assert A.apply_change(cid, approval=approve(cid))["status"] == "ok"
    r = A.apply_change(cid, approval=approve(cid),
                       idempotency_key="idem-other")
    assert r["code"] == "CHANGE_STATE"


def test_approval_nonce_blocks_replayed_credential(env):
    """凭证一次性：把变更单回滚成 awaiting_approval 再重放同一份批准 → 拒。

    这是 nonce 存在的真正理由 —— 状态检查挡不住"有人把变更单改回去"。
    """
    cid = bootstrap(env)["change_id"]
    original = A._change_path(cid).read_text(encoding="utf-8")
    appr = approve(cid)
    assert A.apply_change(cid, approval=appr)["status"] == "ok"

    A._change_path(cid).write_text(original, encoding="utf-8")   # 回滚变更单
    r = A.apply_change(cid, approval=appr, idempotency_key="idem-replay")
    assert r["status"] == "blocked" and r["code"] == "APPROVAL_REPLAYED"
    assert "一次性" in r["hint"]


def test_idempotent_replay_still_verifies_signature(env):
    """端到端实测抓到的漏洞回归：篡改凭证不得从幂等重放路径拿到 ok。

    首次执行的完整校验链是过了的；重放不再查有效期/nonce（否则"重试"会因为
    凭证过期而失败，与"同一幂等键返回既有作业"冲突），但**必须验签** ——
    否则一份被篡改的凭证会得到 ok，给调用方「这份凭证有效」的错误信号。
    """
    cid = bootstrap(env)["change_id"]
    assert A.apply_change(cid, approval=approve(cid))["status"] == "ok"

    tampered = approve(cid)
    tampered["patch_hash"] = "0" + tampered["patch_hash"][1:]
    r = A.apply_change(cid, approval=tampered)
    assert r["status"] == "blocked" and r["code"] == "APPROVAL_SIGNATURE_INVALID"

    r2 = A.apply_change(cid, approval=approve(cid))
    assert r2["status"] == "ok" and r2["replayed"] is True
    assert r2["approval_reverified"] is True

    # 不带凭证：纯查询"那笔完成了没有"，合法用法
    r3 = A.apply_change(cid)
    assert r3["status"] == "ok" and r3["replayed"] is True
    assert r3["approval_reverified"] is False
    assert r3["baseline"]["version"] == 2


def test_apply_failure_writes_recovery_record(env, monkeypatch):
    """落盘中断必须留现场（规划 L80「执行日志、恢复记录」）。

    不留现场的话，"上次到底写到哪一步"只能靠猜。
    """
    cid = bootstrap(env)["change_id"]

    def boom(*a, **k):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(A, "_store_baseline", boom)
    with pytest.raises(OSError):
        A.apply_change(cid, approval=approve(cid))

    rec = A.recovery_log("WC")
    assert len(rec) == 1
    assert rec[0]["change_id"] == cid
    assert rec[0]["stage"] == "write_baseline"
    assert rec[0]["state"] == "recovery_required"
    assert rec[0]["nonce_consumed"] is True

    # read_project 把它暴露出来，并提示先核对再重新提案
    rp = A.read_project("WC", case="abc")
    assert len(rp["pending_recovery"]) == 1
    assert "恢复记录" in rp["next_action"]

    # 人工核对后可清除
    assert A.clear_recovery("WC", cid) == 1
    assert A.read_project("WC", case="abc")["pending_recovery"] == []
    assert A.clear_recovery("WC", "nonexistent") == 0


def test_idempotency_records_are_auditable(env):
    cid = bootstrap(env)["change_id"]
    A.apply_change(cid, approval=approve(cid))
    rows = A.idempotency_record("WC", "abc")
    assert len(rows) == 1
    assert rows[0]["key"] == f"auto-{A.read_project('WC', case='abc')['baseline']['patch_hash'][:16]}"
    assert rows[0]["actor"] == APPROVER
    assert "result" not in rows[0]        # 审计视图不复制结果载荷


def test_apply_blocked_when_writer_lock_held(env):
    """单写者：锁被别的写者持有时报 WRITER_BUSY 并回持有者信息。"""
    from medini_automation.application.writer_lock import writer_lock
    cid = bootstrap(env)["change_id"]
    appr = approve(cid)
    with writer_lock("WC/abc", root=A.STATE_ROOT, actor="other-engineer"):
        r = A.apply_change(cid, approval=appr)
    assert r["status"] == "blocked" and r["code"] == "WRITER_BUSY"
    assert r["lock_holder"]["actor"] == "other-engineer"
    # 锁释放后可正常实施
    assert A.apply_change(cid, approval=approve(cid))["status"] == "ok"


def test_apply_detects_tampered_change_file(env):
    cid = bootstrap(env)["change_id"]
    p = A._change_path(cid)
    blob = json.loads(p.read_text(encoding="utf-8"))
    blob["ops"].append({"op": "set_probability",
                        "args": {"id": "B", "probability": "9/10"}})
    p.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
    r = A.apply_change(cid, approval=approve(cid))
    assert r["code"] == "PATCH_HASH_MISMATCH"


def test_apply_refused_when_baseline_moved_after_approval(env):
    """批准之后基线被另一个变更推进 → 必须拒绝（不允许盲写）。

    真实时序：change2 批准在 v2 基线上，随后 change3 把基线推到 v3；
    此时再实施 change2 必须失败，因为它绑定的 v2 已经不是当前基线。
    """
    c1 = bootstrap(env)                              # A→1/4
    assert A.apply_change(c1["change_id"],
                          approval=approve(c1["change_id"]))["status"] == "ok"
    v2 = A.read_project("WC", case="abc")["baseline"]["semantic_hash"]

    c2 = A.prepare_change(                            # B→1/3，绑 v2
        "WC", "abc", [{"op": "set_probability",
                       "args": {"id": "B", "probability": "1/3"}}],
        expected_baseline_hash=v2)
    assert c2["status"] == "ok"

    c3 = A.prepare_change(                            # A→1/6，也绑 v2
        "WC", "abc", [{"op": "set_probability",
                       "args": {"id": "A", "probability": "1/6"}}],
        expected_baseline_hash=v2)
    assert c3["status"] == "ok"
    assert A.apply_change(c3["change_id"],
                          approval=approve(c3["change_id"]))["status"] == "ok"

    r = A.apply_change(c2["change_id"], approval=approve(c2["change_id"]))
    assert r["status"] == "blocked" and r["code"] == "BASELINE_MISMATCH"
    assert "已被推进" in r["error"]


def test_apply_writes_changelog(env):
    cid = bootstrap(env)["change_id"]
    A.apply_change(cid, approval=approve(cid))
    log = A.change_log("WC", "abc")
    assert len(log) == 1
    assert log[0]["change_id"] == cid and log[0]["approver"] == "E12345"


def test_apply_never_touches_project_dir(env):
    before = sorted(p.name for p in (env["wc"] / "fta").iterdir())
    cid = bootstrap(env)["change_id"]
    A.apply_change(cid, approval=approve(cid))
    assert sorted(p.name for p in (env["wc"] / "fta").iterdir()) == before


# ================================================================ run_analysis
def test_run_analysis_without_baseline(env):
    r = A.run_analysis("WC", "abc", model_hash="x")
    assert r["code"] == "NO_BASELINE"


def test_run_analysis_model_hash_mismatch(env):
    bootstrap(env)
    r = A.run_analysis("WC", "abc", model_hash="deadbeef")
    assert r["code"] == "MODEL_HASH_MISMATCH"


def test_run_analysis_rejects_unknown_project(env):
    r = A.run_analysis("NOPE", "abc", model_hash="x")
    assert r["code"] == "PROJECT_NOT_ALLOWED"


def test_run_analysis_blocked_without_license(env, monkeypatch):
    bootstrap(env)
    cur = A.read_project("WC", case="abc")["baseline"]["semantic_hash"]
    monkeypatch.setattr(A, "MEDINI_EXE_DEFAULT", env["tmp"] / "nope.exe")
    r = A.run_analysis("WC", "abc", model_hash=cur)
    assert r["code"] == "MEDINI_MISSING"


# ==================================================================== get_job
def test_get_job_not_found(env):
    r = A.get_job("nope", out_root=env["runs"])
    assert r["code"] == "JOB_NOT_FOUND"


def test_get_job_verified(env):
    store = JobStore(env["runs"] / "jobs")
    job = Job(kind="run_analysis", state="verified")
    store.save(job)
    r = A.get_job(job.job_id, out_root=env["runs"])
    assert r["status"] == "ok" and r["terminal"] is True
    assert r["recovery"] == "medini_readback"
    assert isinstance(r["duration_s"], float)


def test_get_job_failed_gives_errors(env):
    store = JobStore(env["runs"] / "jobs")
    job = Job(kind="run_analysis")
    job.transition("validated", "ok")
    job.transition("failed", "medini 退出码 1，无新鲜结果文件")
    store.save(job)
    r = A.get_job(job.job_id, out_root=env["runs"])
    assert r["errors"] and r["recovery"] == "medini_readback"


def test_get_job_running_notes_no_cancel(env):
    store = JobStore(env["runs"] / "jobs")
    job = Job(kind="run_analysis")
    job.transition("validated", "ok")
    job.transition("queued", "排队")
    job.transition("running", "执行中")
    store.save(job)
    r = A.get_job(job.job_id, out_root=env["runs"])
    assert r["terminal"] is False
    assert "不支持取消" in r["hint"]


# ================================================================== readback
def _fake_evidence(runs: Path, job_id: str, *, with_medini=True,
                   verdict="pass", model_hash="H1") -> Path:
    ev = runs / f"run_abc_{job_id}"
    (ev / "verification").mkdir(parents=True)
    if with_medini:
        (ev / "results").mkdir(parents=True)
        (ev / "results" / "medini-actual.json").write_text(
            json.dumps({"Q_top": 0.11}), encoding="utf-8")
    (ev / "verification" / "reference.json").write_text(
        json.dumps({"Q_top_exact": "11/100"}), encoding="utf-8")
    (ev / "manifest.json").write_text(json.dumps({
        "job_id": job_id, "case": "abc", "job_state": "verified",
        "semantic_model_hash": model_hash, "verdict": verdict,
        "medini_target": "x.exe",
    }), encoding="utf-8")
    return ev


def test_readback_job_not_found(env):
    assert A.readback("nope", out_root=env["runs"])["code"] == "JOB_NOT_FOUND"


def test_readback_evidence_missing(env):
    store = JobStore(env["runs"] / "jobs")
    job = Job(state="verified")
    store.save(job)
    r = A.readback(job.job_id, out_root=env["runs"])
    assert r["code"] == "EVIDENCE_NOT_FOUND"


def test_readback_all_checks_pass(env):
    store = JobStore(env["runs"] / "jobs")
    job = Job(state="verified")
    store.save(job)
    _fake_evidence(env["runs"], job.job_id, model_hash="H1")
    r = A.readback(job.job_id, expected_model_hash="H1", out_root=env["runs"])
    assert r["status"] == "ok" and r["failed_checks"] == []
    assert r["verdict"] == "pass"
    assert r["recovery"] == "medini_export_evidence"


def test_readback_flags_missing_native_result(env):
    store = JobStore(env["runs"] / "jobs")
    job = Job(state="verified")
    store.save(job)
    _fake_evidence(env["runs"], job.job_id, with_medini=False)
    r = A.readback(job.job_id, out_root=env["runs"])
    assert r["status"] == "error"
    assert "native_result_present" in r["failed_checks"]


def test_readback_flags_model_hash_mismatch(env):
    store = JobStore(env["runs"] / "jobs")
    job = Job(state="verified")
    store.save(job)
    _fake_evidence(env["runs"], job.job_id, model_hash="H1")
    r = A.readback(job.job_id, expected_model_hash="H2", out_root=env["runs"])
    assert "model_hash_matches_manifest" in r["failed_checks"]


# ============================================================ export_evidence
def test_export_requires_verified_job(env):
    store = JobStore(env["runs"] / "jobs")
    job = Job(state="running")
    store.save(job)
    r = A.export_evidence(job.job_id, out_root=env["runs"])
    assert r["code"] == "JOB_NOT_VERIFIED"


def test_export_bundle_lists_files_with_hashes(env):
    store = JobStore(env["runs"] / "jobs")
    job = Job(state="verified")
    store.save(job)
    _fake_evidence(env["runs"], job.job_id, model_hash="H1")
    r = A.export_evidence(job.job_id, out_root=env["runs"])
    assert r["status"] == "ok"
    assert r["file_count"] >= 3
    assert all(len(f["sha256"]) == 64 for f in r["files"])
    assert r["approval"] is None
    assert "不构成适航" in r["disclaimer"]
    bundle = Path(r["bundle_root"]) / "bundle.json"
    assert bundle.exists()
    blob = json.loads(bundle.read_text(encoding="utf-8"))
    assert blob["job_id"] == job.job_id and blob["file_count"] == r["file_count"]


def test_export_is_idempotent(env):
    store = JobStore(env["runs"] / "jobs")
    job = Job(state="verified")
    store.save(job)
    _fake_evidence(env["runs"], job.job_id)
    a = A.export_evidence(job.job_id, out_root=env["runs"])
    b = A.export_evidence(job.job_id, out_root=env["runs"])
    assert a["file_count"] == b["file_count"]
    assert [f["sha256"] for f in a["files"]] == [f["sha256"] for f in b["files"]]


# =============================================================== reopen_check
def test_reopen_check_without_model(env):
    r = A.reopen_check("WC", "abc", out_root=env["runs"])
    assert r["code"] == "NO_MODEL"


def test_reopen_check_unknown_project(env):
    r = A.reopen_check("NOPE", "abc", contract_path=str(ABC_CONTRACT))
    assert r["code"] == "PROJECT_NOT_ALLOWED"


def test_reopen_check_uses_baseline_contract(env, monkeypatch):
    """有基线时用基线契约；medini 缺失时诚实 blocked 而非报错。"""
    bootstrap(env)
    monkeypatch.setattr(A, "MEDINI_EXE_DEFAULT", env["tmp"] / "nope.exe")
    r = A.reopen_check("WC", "abc", out_root=env["runs"])
    assert r["code"] == "MEDINI_MISSING"


# ============================================================== 纯函数单元
def test_native_snapshot_parses_real_fta():
    snap = A._native_snapshot(ABC_FTA)
    assert snap["model_xmi_id"].startswith("_")
    assert len(snap["events"]) == 6
    a = next(e for e in snap["events"] if e["contract_id"] == "A")
    assert a["name"] == "基本事件A" and a["raw_probability"] == "0.1"
    assert len(a["node_ids"]) == 2          # 共享基本事件：一条 events 挂两个 node
    top = next(g for g in snap["gates"] if g["name"] == "TOP")
    assert top["kind"] == "OR"
    assert next(g for g in snap["gates"] if g["name"] == "G_AB")["kind"] is None


def test_native_drift_exact_fraction_comparison():
    """精确有理数比对，不能因二进制浮点把 0.3 误判为漂移。"""
    native = {"events": [{"contract_id": "C", "raw_probability": "0.3"}]}
    contract = {"events": [{"id": "C", "probability": "3/10"}]}
    assert A._native_drift(native, contract) == []


def test_apply_ops_reports_dangling_input():
    contract = {"events": [{"id": "A", "probability": "1/2"}], "gates": []}
    _, _, problems = A._apply_ops(contract, [
        A.ChangeOp(op="add_gate",
                   args={"id": "G", "type": "AND", "inputs": ["NOPE"]})])
    assert problems and "不存在" in problems[0]


def test_guard_returns_structured_refusal():
    """公开操作绝不因策略原因抛异常。"""
    r = A.read_project("DEFINITELY-NOT-ALLOWED")
    assert r["status"] == "blocked" and r["code"] == "PROJECT_NOT_ALLOWED"
