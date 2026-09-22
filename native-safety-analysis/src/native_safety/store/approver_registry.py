"""Approver registry: an out-of-band, human-maintained identity boundary.

R3 root cause: a name string is not an identity. The old check rejected a
blacklist of agent-ish names and accepted every other string, so any caller
with the same file/system access could approve its own proposal by picking an
unlisted name. A longer blacklist is not a fix.

Design (what this file actually claims):

  * The registry is a JSON file maintained BY HAND by the local operator.
    This code NEVER writes it, so a compromised process cannot add itself
    approvers through any API offered here.
  * Each entry stores a salted SHA-256 of the approver name, not the name.
    Stealing the file does not reveal who the approvers are; a leaked name
    cannot be verified against another registry by offline brute force of
    the file alone (each registry draws its own salt).
  * Registration is the act of authorization. A name not in the registry has
    no approval authority, regardless of how human it looks.
  * The proposer of a change may not decide or apply it (four-eyes), even
    when the proposer is a registered approver for other changes.

What this is NOT (kept explicit so nobody upgrades the claim by accident):

  * Not network authentication, not Kerberos/SSO, not hardware tokens.
  * Binding to the host is file-system ACLs on the registry path and on the
    store database. Whoever can rewrite BOTH can defeat this boundary; that
    is the stated trust assumption of a single-workstation tool.
  * Registry compromise is detectable (file mtime/hash change) but not
    preventable here.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path

from .errors import StoreError
from . import errors


def _entry_hash(name: str, salt: str) -> str:
    # One iteration is deliberate: this is an identity lookup, not password
    # storage. The secret is the registry file's location and ACL, and the
    # name itself is not high-entropy material worth stretching.
    return hashlib.sha256((salt + ":" + name.strip()).encode("utf-8")).hexdigest()


def make_registry_entry(name: str, salt: str | None = None) -> dict:
    """Build one registry entry. Helper for the OPERATOR, not for hot paths.

    Exposed so the operator does not have to reimplement the hashing; the
    registry file itself is still assembled and placed by hand.
    """
    if not isinstance(name, str) or not name.strip():
        raise StoreError(errors.APPROVAL_AUTHORITY, "approver name must be a non-empty string")
    salt = salt or secrets.token_hex(16)
    return {"salt": salt, "name_hash": _entry_hash(name, salt)}


def load_registry(path: str | Path) -> dict:
    """Parse and sanity-check a registry file. Raises StoreError on bad shape."""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise StoreError(
            errors.APPROVAL_AUTHORITY,
            f"approver registry not found at {p}. Approvals are disabled until the "
            "local operator creates one; refusing is the fail-closed default.",
        ) from None
    except OSError as exc:
        raise StoreError(
            errors.APPROVAL_AUTHORITY, f"approver registry unreadable at {p}: {exc}"
        ) from None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise StoreError(
            errors.APPROVAL_AUTHORITY, f"approver registry is not valid JSON: {exc}"
        ) from None
    if not isinstance(data, dict) or not isinstance(data.get("approvers"), list):
        raise StoreError(
            errors.APPROVAL_AUTHORITY,
            'approver registry must be an object like {"salt": "...", "approvers": [...]}',
        )
    for i, entry in enumerate(data["approvers"]):
        if (not isinstance(entry, dict) or not isinstance(entry.get("salt"), str)
                or not isinstance(entry.get("name_hash"), str)):
            raise StoreError(
                errors.APPROVAL_AUTHORITY,
                f"approver registry entry #{i} must be {{salt, name_hash}}",
            )
    return data


def is_registered(data: dict, name: str) -> bool:
    """Constant-shape lookup: name -> bool. No exception, no partial match."""
    probe = (name or "").strip()
    if not probe:
        return False
    return any(
        _entry_hash(probe, entry["salt"]) == entry["name_hash"]
        for entry in data.get("approvers", [])
    )
