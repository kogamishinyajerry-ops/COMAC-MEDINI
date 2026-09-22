"""Structural diff between two canonical model forms.

The store keeps the canonical form of every baseline it has ever held
(immutable by §203), so "what changed between these two versions of the
model" is answerable from the store alone — no model files needed. This
module is the pure function that answers it.

Deliberate properties:
  * input and output are plain JSON dicts — the diff is serializable,
    storable, and comparable in tests without rebuilding domain objects;
  * comparison is by CANONICAL content, so lexical noise is already gone
    before we look (two models that differ only in "0.10" vs "0.1" have
    identical canonical forms and therefore an empty diff);
  * a gate whose inputs are merely REORDERED is reported as changed
    (canonical-v1 keeps declared order, so the hash really does change)
    but flagged ``order_only`` — a Boolean-equivalent edit that still
    re-anchors the baseline is exactly the kind of change a reviewer
    wants told apart from a real logic change;
  * ``semantically_equal`` mirrors the hash question: true iff the two
    canonical forms are identical dicts.

Nothing here decides whether a change is ACCEPTABLE. The diff states
facts; approval authority stays with the named human (§181).
"""
from __future__ import annotations

_DIFF_VERSION = "model-diff-v1"

_ASSUMPTION_KEYS = ("basic_events_independent", "probability_semantics", "condition")


def diff_canonical_forms(old: dict, new: dict) -> dict:
    """Diff two canonical model forms. Pure, total, deterministic.

    ``old`` may be None (first baseline of a model: everything is "added");
    ``new`` may be None (everything "removed") — callers keep those states
    out of the UI, but the function stays total for verification purposes.
    """
    return {
        "diff_version": _DIFF_VERSION,
        "old": _index(old),
        "new": _index(new),
        "assumptions_changed": _assumption_changes(old, new),
        "schema_version_changed": _scalar_change(old, new, "schema_version"),
        "model_id_changed": _scalar_change(old, new, "model_id"),
        "top_event_changed": _scalar_change(old, new, "top_event"),
        "events": _entity_diff(old, new, "basic_events", _event_fingerprint),
        "gates": _entity_diff(old, new, "gates", _gate_fingerprint),
        "semantically_equal": old == new,
    }


def change_summary(diff: dict) -> dict:
    """Human-facing counts derived from a diff (no hidden re-computation)."""
    events, gates = diff["events"], diff["gates"]
    return {
        "semantically_equal": diff["semantically_equal"],
        "events_added": len(events["added"]),
        "events_removed": len(events["removed"]),
        "events_changed": len(events["changed"]),
        "gates_added": len(gates["added"]),
        "gates_removed": len(gates["removed"]),
        "gates_changed": len(gates["changed"]),
        "gates_changed_order_only": sum(1 for g in gates["changed"] if g.get("order_only")),
        "assumptions_changed": len(diff["assumptions_changed"]),
        "top_event_changed": diff["top_event_changed"] is not None,
        "schema_version_changed": diff["schema_version_changed"] is not None,
    }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _index(form: dict | None) -> dict | None:
    if form is None:
        return None
    return {"schema_version": form.get("schema_version"), "hash_inputs": "canonical"}


def _entities(form: dict | None, key: str) -> dict[str, dict]:
    if form is None:
        return {}
    return {entity["id"]: entity for entity in form.get(key, [])}


def _event_fingerprint(entity: dict) -> dict:
    out = {"p": entity.get("p")}
    if "rate" in entity:
        out["rate"] = entity["rate"]
    return out


def _gate_fingerprint(entity: dict) -> dict:
    out = {"kind": entity.get("kind"), "inputs": list(entity.get("inputs", []))}
    if "k" in entity:
        out["k"] = entity["k"]
    return out


def _entity_diff(old: dict | None, new: dict | None, key: str, fingerprint) -> dict:
    old_map, new_map = _entities(old, key), _entities(new, key)
    added = [new_map[eid] for eid in sorted(set(new_map) - set(old_map))]
    removed = [old_map[eid] for eid in sorted(set(old_map) - set(new_map))]
    changed: list[dict] = []
    unchanged = 0
    for eid in sorted(set(old_map) & set(new_map)):
        old_fp, new_fp = fingerprint(old_map[eid]), fingerprint(new_map[eid])
        if old_fp == new_fp:
            unchanged += 1
            continue
        entry: dict = {"id": eid, "old": old_fp, "new": new_fp}
        if key == "gates":
            old_inputs, new_inputs = old_fp.get("inputs", []), new_fp.get("inputs", [])
            entry["order_only"] = (
                old_inputs != new_inputs and sorted(old_inputs) == sorted(new_inputs)
                and old_fp.get("kind") == new_fp.get("kind")
                and old_fp.get("k") == new_fp.get("k")
            )
        changed.append(entry)
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
    }


def _assumption_changes(old: dict | None, new: dict | None) -> list[dict]:
    out: list[dict] = []
    for key in _ASSUMPTION_KEYS:
        old_value = (old or {}).get("assumptions", {}).get(key)
        new_value = (new or {}).get("assumptions", {}).get(key)
        if old_value != new_value:
            out.append({"field": key, "old": old_value, "new": new_value})
    return out


def _scalar_change(old: dict | None, new: dict | None, key: str) -> dict | None:
    old_value = (old or {}).get(key)
    new_value = (new or {}).get(key)
    if old_value == new_value:
        return None
    return {"old": old_value, "new": new_value}
