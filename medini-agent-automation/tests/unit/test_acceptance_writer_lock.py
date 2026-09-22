"""Regression tests for the acceptance audit; no Medini installation used."""
import hashlib
import json
import os
import subprocess
import sys

import pytest

from medini_automation.application.writer_lock import (
    WriterBusy, read_lock_holder, writer_lock,
)


def lock_path(root, scope):
    name = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:40]
    return root / "locks" / f"{name}.lock"


def test_live_writer_cannot_be_preempted_by_age(tmp_path):
    with writer_lock("case", root=tmp_path, actor="first") as owner:
        path = lock_path(tmp_path, "case")
        before = path.read_bytes()
        os.utime(path, (1, 1))
        with pytest.raises(WriterBusy):
            with writer_lock("case", root=tmp_path, actor="second", stale_after_s=0):
                pytest.fail("two live writers entered the same scope")
        assert path.read_bytes() == before
        assert read_lock_holder(tmp_path, "case").holder["lock_id"] == owner["lock_id"]
    assert not path.exists()


def test_normal_release_and_scope_isolation(tmp_path):
    with writer_lock("one", root=tmp_path) as first:
        with writer_lock("two", root=tmp_path) as second:
            assert first["lock_id"] != second["lock_id"]
        assert read_lock_holder(tmp_path, "one").held
    assert not read_lock_holder(tmp_path, "one").held


@pytest.mark.parametrize("content", ["{broken", "null", "[]", "true", '"text"'])
def test_damaged_lock_fails_closed(tmp_path, content):
    path = lock_path(tmp_path, "case")
    path.parent.mkdir()
    path.write_text(content, encoding="utf-8")
    os.utime(path, (1, 1))
    with pytest.raises(WriterBusy):
        with writer_lock("case", root=tmp_path, stale_after_s=0):
            pytest.fail("damaged lock must not be preempted")
    assert path.read_text(encoding="utf-8") == content


def test_does_not_delete_foreign_token_on_exit(tmp_path):
    with writer_lock("case", root=tmp_path) as owner:
        path = lock_path(tmp_path, "case")
        replacement = dict(owner, lock_id="different-owner-token")
        path.write_text(json.dumps(replacement), encoding="utf-8")
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["lock_id"] == "different-owner-token"


def test_release_after_body_exception(tmp_path):
    with pytest.raises(RuntimeError, match="operation failed"):
        with writer_lock("case", root=tmp_path):
            raise RuntimeError("operation failed")
    assert not read_lock_holder(tmp_path, "case").held


def test_another_process_is_denied_while_owner_is_live(tmp_path):
    code = """
import sys
from pathlib import Path
from medini_automation.application.writer_lock import WriterBusy, writer_lock
try:
    with writer_lock('case', root=Path(sys.argv[1]), stale_after_s=0):
        sys.exit(4)
except WriterBusy:
    sys.exit(0)
"""
    with writer_lock("case", root=tmp_path):
        path = lock_path(tmp_path, "case")
        os.utime(path, (1, 1))
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path)],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_reject_unbounded_waits(tmp_path, value):
    with pytest.raises(ValueError):
        with writer_lock("case", root=tmp_path, wait_s=value):
            pytest.fail("invalid wait must fail before acquisition")
