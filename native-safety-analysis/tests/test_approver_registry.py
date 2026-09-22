"""R3: the approver registry as an identity boundary.

The thesis under test: a name string proves nothing. Approval authority comes
from out-of-band registration maintained by the local operator, and the
proposer of a change can never decide or apply it.

Negative matrix (each row must be refused with APPROVAL_AUTHORITY):
  - a plausible human name that is simply NOT REGISTERED
  - the proposer trying to approve their own proposal
  - the proposer trying to apply their own approved proposal
  - an agent-ish name (the legacy blacklist, still enforced)

Positive paths:
  - a registered non-proposer approves and applies
  - registry=None keeps the legacy trusted-operator mode working (documented
    boundary, existing tests depend on it)

Registry file robustness:
  - missing file  -> fail closed
  - invalid JSON  -> fail closed
  - wrong shape   -> fail closed
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from native_safety.adapters.json_io import load_model  # noqa: E402
from native_safety.store import (  # noqa: E402
    SqliteRepository,
    StoreError,
    is_registered,
    load_registry,
    make_registry_entry,
)
from native_safety.store import errors as store_err  # noqa: E402

EXAMPLES = ROOT / "reference" / "03_contracts" / "examples"

APPROVER = "YuDongjie"          # registered approver, never proposes here
PROPOSER = "SomeEngineer"       # proposes, is NOT registered


def _registry_payload(names: list[str]) -> dict:
    return {"approvers": [make_registry_entry(n) for n in names]}


def _write_registry(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "approvers.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


@pytest.fixture()
def env(tmp_path: Path):
    """Store + one proposed review authored by PROPOSER."""
    model = load_model(EXAMPLES / "M01_and.json")
    with SqliteRepository(str(tmp_path / "store.db")) as repo:
        repo.register_model_baseline(model, actor=PROPOSER)
        repo.propose_review(
            review_id="REV-R3",
            model_id=model.model_id,
            expected_baseline_hash=repo.current_baseline_hash(model.model_id),
            proposed_model=model,
            proposed_by=PROPOSER,
            note="seed proposal",
        )
        yield repo, "REV-R3"


# ------------------------------------------------------------------ registry itself

def test_entry_hash_is_salted_per_registry():
    a = make_registry_entry("Alice")
    b = make_registry_entry("Alice")
    assert a["name_hash"] != b["name_hash"], "per-registry salt must differ"


def test_is_registered_roundtrip():
    data = _registry_payload([APPROVER, "AnotherApprover"])
    assert is_registered(data, APPROVER)
    assert is_registered(data, "  " + APPROVER + " ") is True or is_registered(data, APPROVER)
    assert not is_registered(data, PROPOSER)
    assert not is_registered(data, "")
    assert not is_registered(data, None)  # type: ignore[arg-type]


def test_registry_missing_file_fails_closed(tmp_path: Path):
    with pytest.raises(StoreError) as excinfo:
        load_registry(tmp_path / "nope.json")
    assert excinfo.value.code == store_err.APPROVAL_AUTHORITY
    assert "fail-closed" in excinfo.value.message or "refusing" in excinfo.value.message


def test_registry_bad_json_fails_closed(tmp_path: Path):
    p = tmp_path / "approvers.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreError) as excinfo:
        load_registry(p)
    assert excinfo.value.code == store_err.APPROVAL_AUTHORITY


def test_registry_wrong_shape_fails_closed(tmp_path: Path):
    p = tmp_path / "approvers.json"
    p.write_text('{"approvers": [{"salt": 1}]}', encoding="utf-8")
    with pytest.raises(StoreError) as excinfo:
        load_registry(p)
    assert excinfo.value.code == store_err.APPROVAL_AUTHORITY


# ------------------------------------------------------------------ review loop under the registry

def test_unregistered_human_looking_name_cannot_approve(env):
    repo, review_id = env
    reg = _registry_payload([APPROVER])
    with pytest.raises(StoreError) as excinfo:
        repo.decide_review(review_id, approve=True, reviewer=PROPOSER,
                           approver_registry=reg)
    assert excinfo.value.code == store_err.APPROVAL_AUTHORITY
    assert "not in the approver registry" in excinfo.value.message


def test_registered_approver_can_decide_and_apply(env):
    repo, review_id = env
    reg = _registry_payload([APPROVER])
    decided = repo.decide_review(review_id, approve=True, reviewer=APPROVER,
                                 approver_registry=reg, note="checked")
    assert decided["state"] == "approved"
    assert decided["decided_by"] == APPROVER
    applied, _stale = repo.apply_review(review_id, reviewer=APPROVER,
                                        approver_registry=reg)
    assert applied["state"] == "applied"


def test_agent_name_still_refused_with_registry(env):
    repo, review_id = env
    reg = _registry_payload([APPROVER])
    with pytest.raises(StoreError) as excinfo:
        repo.decide_review(review_id, approve=True, reviewer="agent",
                           approver_registry=reg)
    assert excinfo.value.code == store_err.APPROVAL_AUTHORITY


def test_legacy_mode_without_registry_still_works(env):
    """registry=None = documented trusted-operator mode (existing behaviour)."""
    repo, review_id = env
    decided = repo.decide_review(review_id, approve=True, reviewer="AnyHuman")
    assert decided["state"] == "approved"


# ------------------------------------------------------------------ four-eyes

def test_proposer_cannot_decide_own_proposal_even_when_registered(env):
    """The killer case: proposer IS a registered approver for other work."""
    repo, review_id = env
    reg = _registry_payload([APPROVER, PROPOSER])  # both registered
    with pytest.raises(StoreError) as excinfo:
        repo.decide_review(review_id, approve=True, reviewer=PROPOSER,
                           approver_registry=reg)
    assert excinfo.value.code == store_err.APPROVAL_AUTHORITY
    assert "proposed" in excinfo.value.message


def test_proposer_cannot_apply_own_approved_proposal(env):
    repo, review_id = env
    reg = _registry_payload([APPROVER, PROPOSER])
    repo.decide_review(review_id, approve=True, reviewer=APPROVER,
                       approver_registry=reg)
    with pytest.raises(StoreError) as excinfo:
        repo.apply_review(review_id, reviewer=PROPOSER, approver_registry=reg)
    assert excinfo.value.code == store_err.APPROVAL_AUTHORITY


def test_four_eyes_also_enforced_in_legacy_mode(env):
    """Even without a registry, self-approval is refused."""
    repo, review_id = env
    with pytest.raises(StoreError) as excinfo:
        repo.decide_review(review_id, approve=True, reviewer=PROPOSER)
    assert excinfo.value.code == store_err.APPROVAL_AUTHORITY


# ------------------------------------------------------------------ registry loaded from disk

def test_registry_from_disk_end_to_end(env, tmp_path: Path):
    repo, review_id = env
    p = _write_registry(tmp_path, _registry_payload([APPROVER]))
    reg = load_registry(p)
    repo.decide_review(review_id, approve=True, reviewer=APPROVER,
                       approver_registry=reg)
    applied, _ = repo.apply_review(review_id, reviewer=APPROVER,
                                   approver_registry=reg)
    assert applied["state"] == "applied"
