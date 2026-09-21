"""Object-level patch language: edit instructions over a canonical model form.

The forward twin of `review revert`: instead of supplying a whole new model
file, an author supplies a small list of edit operations. The patch applies to
the CURRENT baseline's canonical form (read from the store), produces a NEW
canonical form, and that form enters the ordinary propose -> decide -> apply
loop — so every existing guard (optimistic concurrency, re-hash anchoring,
agent-cannot-approve, verify) covers patches for free.

Why canonical-level validation instead of rebuilding a model file:
  the canonical form renders probabilities as exact decimals OR exact n/d
  rationals ("1/3"), which is WIDER than the model-input lexical pattern.
  A rebuilt model file could not legally express a 1/3 probability, so the
  result of a patch must be validated where it lives: on the canonical form.
  The checks are the same in substance — unique ids, resolvable references,
  acyclicity (whole graph, not just reachable parts), probability range,
  K_OF_N bounds, and the two-way probability-semantics guard.

Deliberate v1 boundaries:
  * operations apply IN ORDER; each sees the previous one's result;
  * `set_event_rate` edits an EXISTING rate event's record through the full
    conversion gate (parse -> q -> provenance); `set_event_probability`
    refuses rate events (their p is derived), and adding a NEW rate event is
    a separate, unimplemented operation — it must carry the whole rate gate;
  * `add_event` adds a plain-probability event only;
  * nothing here decides acceptability — approval stays with a named human.
"""
from __future__ import annotations

from fractions import Fraction

from . import errors
from .errors import ModelError
from .model import GATE_KINDS, ID_PATTERN
from .model_diff import change_summary, diff_canonical_forms
from .rate_model import (
    DEFAULT_PRECISION_DIGITS,
    mission_probability,
    parse_rate_spec,
    rate_provenance,
)
from .ratnum import exact_decimal_or_fraction

PATCH_VERSION = "model-patch-v1"

OPERATIONS = (
    "set_event_probability",
    "set_event_rate",
    "add_event",
    "remove_event",
    "set_gate",
    "add_gate",
    "remove_gate",
    "set_top_event",
    "set_condition",
)

_ASSUMPTION_KEYS = ("basic_events_independent", "probability_semantics", "condition")
_PROBABILITY_SEMANTICS = {
    "fixed_conditioned_probability",
    "fixed_mission_probability_from_constant_rate",
}


def apply_patch(canonical: dict, ops: list) -> dict:
    """Apply an ordered list of edit operations to a canonical model form.

    Pure: returns the NEW canonical form; the input is never mutated. Raises
    ModelError(PATCH_OP) on the first invalid operation, and validates the
    final form as a whole (references, cycles, semantics) before returning.
    """
    if not isinstance(ops, list) or not ops:
        raise ModelError(errors.PATCH_OP, "a patch must be a non-empty array of operations")
    if canonical is None or not isinstance(canonical, dict):
        raise ModelError(errors.PATCH_OP, "a patch needs a base canonical form to edit")

    form = _deep_copy_form(canonical)

    for index, op in enumerate(ops):
        tag = f"op[{index}]"
        if not isinstance(op, dict):
            raise ModelError(errors.PATCH_OP, f"{tag}: an operation must be an object")
        kind = op.get("op")
        if kind not in OPERATIONS:
            raise ModelError(
                errors.PATCH_OP,
                f"{tag}: unknown operation {kind!r} (supported: {list(OPERATIONS)})",
            )
        handler = _HANDLERS[kind]
        handler(form, op, tag)

    _validate_final_form(form)
    return form


def patch_preview(canonical: dict, ops: list) -> dict:
    """Apply a patch read-only and return {form, diff, summary} — no store."""
    new_form = apply_patch(canonical, ops)
    diff = diff_canonical_forms(canonical, new_form)
    return {"form": new_form, "diff": diff, "summary": change_summary(diff)}


# --------------------------------------------------------------------------
# individual operations
# --------------------------------------------------------------------------


def _op_set_event_rate(form: dict, op: dict, tag: str) -> None:
    """Edit the rate record of an EXISTING rate event; p re-derives from it.

    The full conversion gate re-runs: lexical/unit/range checks via
    parse_rate_spec, then q = 1-exp(-lambda*t) at the event's recorded
    precision, then a fresh provenance record. The patched form therefore
    carries the same guarantees as a rate event that arrived through model
    validation — there is no shortcut around the transcendental conversion.
    """
    event_id = op.get("event")
    _require_id(event_id, f"{tag}.event")
    raw = op.get("rate")
    if not isinstance(raw, dict):
        raise ModelError(errors.PATCH_OP, f"{tag}: rate must be an object (a failure_rate spec)")
    for event in form["basic_events"]:
        if event["id"] == event_id:
            if "rate" not in event:
                raise ModelError(
                    errors.PATCH_OP,
                    f"{tag}: event {event_id!r} is a plain-probability event; use "
                    "set_event_probability (converting a plain event to a rate event is not "
                    "offered — add_event with a rate is a separate, unimplemented operation)",
                )
            spec = parse_rate_spec(raw, event_id)
            precision = int(event["rate"].get("precision_digits") or DEFAULT_PRECISION_DIGITS)
            q = mission_probability(spec, precision)
            event["rate"] = rate_provenance(spec, q, precision)
            event["p"] = exact_decimal_or_fraction(q)
            return
    raise ModelError(errors.PATCH_OP, f"{tag}: no basic event {event_id!r}")


def _op_set_event_probability(form: dict, op: dict, tag: str) -> None:
    event_id = op.get("event")
    _require_id(event_id, f"{tag}.event")
    text = op.get("probability")
    if not isinstance(text, str) or not text.strip():
        raise ModelError(errors.PATCH_OP, f"{tag}: probability must be a non-empty decimal string")
    try:
        value = Fraction(text.strip())
    except (ValueError, ZeroDivisionError) as exc:
        raise ModelError(
            errors.PATCH_OP, f"{tag}: probability {text!r} is not an exact number"
        ) from exc
    if not 0 <= value <= 1:
        raise ModelError(errors.PATCH_OP, f"{tag}: probability must lie in [0,1], got {text!r}")
    for event in form["basic_events"]:
        if event["id"] == event_id:
            if "rate" in event:
                raise ModelError(
                    errors.PATCH_OP,
                    f"{tag}: event {event_id!r} is rate-derived — its p comes from lambda*t and "
                    "cannot be set directly; editing a rate is a separate, unimplemented operation",
                )
            event["p"] = exact_decimal_or_fraction(value)
            return
    raise ModelError(errors.PATCH_OP, f"{tag}: no basic event {event_id!r}")


def _op_add_event(form: dict, op: dict, tag: str) -> None:
    spec = op.get("event")
    if not isinstance(spec, dict):
        raise ModelError(errors.PATCH_OP, f"{tag}: event must be an object")
    event_id = spec.get("id")
    _require_id(event_id, f"{tag}.event.id")
    text = spec.get("p")
    if not isinstance(text, str) or not text.strip():
        raise ModelError(errors.PATCH_OP, f"{tag}: event.p must be a non-empty decimal string")
    try:
        value = Fraction(text.strip())
    except (ValueError, ZeroDivisionError) as exc:
        raise ModelError(
            errors.PATCH_OP, f"{tag}: event.p {text!r} is not an exact number"
        ) from exc
    if not 0 <= value <= 1:
        raise ModelError(errors.PATCH_OP, f"{tag}: event.p must lie in [0,1], got {text!r}")
    if any(event["id"] == event_id for event in form["basic_events"]):
        raise ModelError(errors.PATCH_OP, f"{tag}: basic event {event_id!r} already exists")
    if any(gate["id"] == event_id for gate in form["gates"]):
        raise ModelError(errors.PATCH_OP, f"{tag}: id {event_id!r} is already a gate id")
    # keep the events sorted by id (canonical invariant)
    events = form["basic_events"] + [{"id": event_id, "p": exact_decimal_or_fraction(value)}]
    events.sort(key=lambda e: e["id"])
    form["basic_events"] = events


def _op_remove_event(form: dict, op: dict, tag: str) -> None:
    event_id = op.get("event")
    _require_id(event_id, f"{tag}.event")
    if not any(event["id"] == event_id for event in form["basic_events"]):
        raise ModelError(errors.PATCH_OP, f"{tag}: no basic event {event_id!r}")
    referenced = _referenced_ids(form)
    if event_id in referenced:
        raise ModelError(
            errors.PATCH_OP,
            f"{tag}: basic event {event_id!r} is still referenced (by a gate input or the top "
            "event); remove or rewire the referencing gate first",
        )
    form["basic_events"] = [e for e in form["basic_events"] if e["id"] != event_id]


def _op_set_gate(form: dict, op: dict, tag: str) -> None:
    spec = op.get("gate")
    if not isinstance(spec, dict):
        raise ModelError(errors.PATCH_OP, f"{tag}: gate must be an object")
    gate_id = spec.get("id")
    _require_id(gate_id, f"{tag}.gate.id")
    if not any(gate["id"] == gate_id for gate in form["gates"]):
        raise ModelError(errors.PATCH_OP, f"{tag}: no gate {gate_id!r}")
    replacement = _gate_entry(spec, tag)
    for index, gate in enumerate(form["gates"]):
        if gate["id"] == gate_id:
            form["gates"][index] = replacement
            return


def _op_add_gate(form: dict, op: dict, tag: str) -> None:
    spec = op.get("gate")
    if not isinstance(spec, dict):
        raise ModelError(errors.PATCH_OP, f"{tag}: gate must be an object")
    gate_id = spec.get("id")
    _require_id(gate_id, f"{tag}.gate.id")
    if any(gate["id"] == gate_id for gate in form["gates"]):
        raise ModelError(errors.PATCH_OP, f"{tag}: gate {gate_id!r} already exists")
    if any(event["id"] == gate_id for event in form["basic_events"]):
        raise ModelError(errors.PATCH_OP, f"{tag}: id {gate_id!r} is already a basic event id")
    entry = _gate_entry(spec, tag)
    gates = form["gates"] + [entry]
    gates.sort(key=lambda g: g["id"])
    form["gates"] = gates


def _op_remove_gate(form: dict, op: dict, tag: str) -> None:
    gate_id = op.get("gate")
    _require_id(gate_id, f"{tag}.gate")
    if not any(gate["id"] == gate_id for gate in form["gates"]):
        raise ModelError(errors.PATCH_OP, f"{tag}: no gate {gate_id!r}")
    referenced = _referenced_ids(form)
    if gate_id in referenced:
        raise ModelError(
            errors.PATCH_OP,
            f"{tag}: gate {gate_id!r} is still referenced (by another gate input or the top "
            "event); remove or rewire the referencing gate first",
        )
    form["gates"] = [g for g in form["gates"] if g["id"] != gate_id]


def _op_set_top_event(form: dict, op: dict, tag: str) -> None:
    gate_id = op.get("gate")
    _require_id(gate_id, f"{tag}.gate")
    form["top_event"] = gate_id  # existence is checked by the final validation


def _op_set_condition(form: dict, op: dict, tag: str) -> None:
    condition = op.get("condition")
    if not isinstance(condition, str) or not condition.strip():
        raise ModelError(errors.PATCH_OP, f"{tag}: condition must be a non-empty statement")
    form["assumptions"]["condition"] = condition


_HANDLERS = {
    "set_event_probability": _op_set_event_probability,
    "set_event_rate": _op_set_event_rate,
    "add_event": _op_add_event,
    "remove_event": _op_remove_event,
    "set_gate": _op_set_gate,
    "add_gate": _op_add_gate,
    "remove_gate": _op_remove_gate,
    "set_top_event": _op_set_top_event,
    "set_condition": _op_set_condition,
}


def _gate_entry(spec: dict, tag: str) -> dict:
    kind = spec.get("kind")
    if kind not in GATE_KINDS:
        raise ModelError(errors.PATCH_OP, f"{tag}: gate.kind must be one of {sorted(GATE_KINDS)}")
    inputs = spec.get("inputs")
    if not isinstance(inputs, list) or not inputs or not all(isinstance(x, str) and x for x in inputs):
        raise ModelError(errors.PATCH_OP, f"{tag}: gate.inputs must be a non-empty array of ids")
    k = spec.get("k")
    if kind == "K_OF_N":
        if type(k) is not int or not 1 <= k <= len(inputs):
            raise ModelError(
                errors.PATCH_OP, f"{tag}: K_OF_N needs an integer k in [1, {len(inputs)}]"
            )
        if len(set(inputs)) != len(inputs):
            raise ModelError(errors.PATCH_OP, f"{tag}: K_OF_N inputs must be distinct references")
        return {"id": spec["id"], "kind": kind, "inputs": list(inputs), "k": k}
    if "k" in spec and k is not None:
        raise ModelError(errors.PATCH_OP, f"{tag}: k is only valid on K_OF_N")
    return {"id": spec["id"], "kind": kind, "inputs": list(inputs)}


# --------------------------------------------------------------------------
# whole-form validation (canonical level, mirrors validate_model in substance)
# --------------------------------------------------------------------------


def _validate_final_form(form: dict) -> None:
    events = form.get("basic_events", [])
    gates = form.get("gates", [])
    ids = [e["id"] for e in events] + [g["id"] for g in gates]
    if len(ids) != len(set(ids)):
        raise ModelError(errors.PATCH_OP, "the patched form has duplicate ids")

    known = set(ids)
    top = form.get("top_event")
    if top not in known:
        raise ModelError(errors.PATCH_OP, f"top_event {top!r} is not defined by the patched form")

    gate_by_id = {g["id"]: g for g in gates}
    for gate in gates:
        for ref in gate["inputs"]:
            if ref not in known:
                raise ModelError(
                    errors.PATCH_OP,
                    f"gate {gate['id']!r} references unknown id {ref!r} after the patch",
                )

    _assert_acyclic_canonical(top, gate_by_id)

    assumptions = form.get("assumptions", {})
    for key in _ASSUMPTION_KEYS:
        if key not in assumptions:
            raise ModelError(errors.PATCH_OP, f"the patched form lost assumptions.{key}")
    if assumptions.get("basic_events_independent") is not True:
        raise ModelError(errors.PATCH_OP, "basic_events_independent must remain true")
    has_rate = any("rate" in e for e in events)
    semantics = assumptions.get("probability_semantics")
    if semantics not in _PROBABILITY_SEMANTICS:
        raise ModelError(errors.PATCH_OP, f"unknown probability_semantics {semantics!r}")
    if has_rate and semantics != "fixed_mission_probability_from_constant_rate":
        raise ModelError(
            errors.PATCH_OP,
            "the patched form still contains rate events but does not declare "
            "'fixed_mission_probability_from_constant_rate'",
        )
    if not has_rate and semantics == "fixed_mission_probability_from_constant_rate":
        raise ModelError(
            errors.PATCH_OP,
            "probability_semantics 'fixed_mission_probability_from_constant_rate' requires at "
            "least one rate event; the patched form has none",
        )


def _assert_acyclic_canonical(top: str, gate_by_id: dict) -> None:
    """Any cycle anywhere in the gate table is rejected, reachable or not."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(gate_by_id, WHITE)

    def visit(start: str) -> None:
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            node, index = stack.pop()
            if index == 0:
                if color.get(node, WHITE) != WHITE:
                    continue
                color[node] = GRAY
            gate = gate_by_id.get(node)
            if gate is None:
                color[node] = BLACK
                continue
            if index < len(gate["inputs"]):
                stack.append((node, index + 1))
                child = gate["inputs"][index]
                if color.get(child, WHITE) == GRAY:
                    raise ModelError(
                        errors.PATCH_OP,
                        f"the patched form has a cycle through {child!r}",
                    )
                if color.get(child, WHITE) == WHITE:
                    stack.append((child, 0))
            else:
                color[node] = BLACK

    visit(top)
    for gate_id in gate_by_id:
        if color[gate_id] == WHITE:
            visit(gate_id)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _referenced_ids(form: dict) -> set[str]:
    referenced: set[str] = set()
    for gate in form.get("gates", []):
        referenced.update(gate.get("inputs", []))
    referenced.add(form.get("top_event"))
    referenced.discard(None)
    return referenced


def _require_id(value, field: str) -> None:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ModelError(errors.PATCH_OP, f"{field} must be a stable identifier, got {value!r}")


def _deep_copy_form(form: dict) -> dict:
    import json

    return json.loads(json.dumps(form, ensure_ascii=False))
