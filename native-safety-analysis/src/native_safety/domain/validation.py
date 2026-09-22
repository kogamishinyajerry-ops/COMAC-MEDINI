"""Semantic validation of static FTA models.

Enforces the seed contract semantics: unique IDs, probability range,
reference existence, DAG-ness, K_OF_N bounds, and the explicit
independence / fixed-probability assumptions. Unsupported constructs are
REJECTED with explicit codes — never silently coerced to AND/OR.

Schema versions:
  * 0.1.0 — every basic event declares `probability` (fixed conditioned).
  * 0.2.0 — every basic event declares EXACTLY ONE of `probability` or
            `failure_rate` (constant rate + explicit units + mission time,
            non-repairable only). Rate events are converted to a fixed
            mission probability at load time.
The two versions are additive: v0.1.0 models keep their exact meaning.
"""
from __future__ import annotations

from fractions import Fraction

from . import errors
from .errors import ModelError
from .model import GATE_KINDS, ID_PATTERN, PROBABILITY_PATTERN, Assumptions, BasicEvent, Gate, StaticFtaModel
from .rate_model import DEFAULT_PRECISION_DIGITS, mission_probability, parse_rate_spec, rate_provenance
from .ratnum import exact_decimal_or_fraction

CONTRACT_VERSION = "0.1.0"  # result-envelope version (unchanged)
MODEL_SCHEMA_VERSIONS = ("0.1.0", "0.2.0")

MAX_BASIC_EVENTS = 20_000
MAX_GATES = 100_000


def validate_model(data: dict) -> StaticFtaModel:
    """Parse raw dict into a validated StaticFtaModel or raise ModelError.

    Structural garbage must surface as ModelError, never as AttributeError /
    KeyError / TypeError leaking through the API boundary (R5).
    """
    if not isinstance(data, dict):
        raise ModelError(
            errors.INPUTS,
            f"model must be a JSON object, got {type(data).__name__}",
        )
    schema_version = data.get("schema_version")
    if schema_version not in MODEL_SCHEMA_VERSIONS:
        raise ModelError(
            errors.VERSION,
            f"expected model schema in {MODEL_SCHEMA_VERSIONS}, got {schema_version!r}",
        )
    supports_rates = schema_version == "0.2.0"
    precision = DEFAULT_PRECISION_DIGITS
    raw_precision = data.get("rate_precision_digits")
    if raw_precision is not None:
        if not supports_rates:
            raise ModelError(errors.VERSION, "rate_precision_digits requires model schema 0.2.0")
        if type(raw_precision) is not int or not 15 <= raw_precision <= 200:
            raise ModelError(errors.RATE_VALUE, "rate_precision_digits must be an integer in [15, 200]")
        precision = raw_precision

    raw_assumptions = data.get("assumptions")
    if not isinstance(raw_assumptions, dict):
        raise ModelError(errors.ASSUMPTIONS, "assumptions object is required")
    assumptions = Assumptions(
        basic_events_independent=raw_assumptions.get("basic_events_independent"),
        probability_semantics=raw_assumptions.get("probability_semantics"),
        condition=raw_assumptions.get("condition") or "",
    )
    if assumptions.basic_events_independent is not True:
        raise ModelError(errors.ASSUMPTIONS, "basic_events_independent must be true in this schema")
    if assumptions.probability_semantics not in (
        "fixed_conditioned_probability",
        "fixed_mission_probability_from_constant_rate",
    ):
        raise ModelError(
            errors.ASSUMPTIONS,
            "probability_semantics must be 'fixed_conditioned_probability' or "
            "'fixed_mission_probability_from_constant_rate'",
        )
    if not assumptions.condition:
        raise ModelError(errors.ASSUMPTIONS, "condition must be a non-empty statement of applicability")

    raw_events = data.get("basic_events")
    if not isinstance(raw_events, list) or not raw_events:
        raise ModelError(errors.INPUTS, "basic_events must be a non-empty array")
    if len(raw_events) > MAX_BASIC_EVENTS:
        raise ModelError(errors.SIZE_LIMIT, f"more than {MAX_BASIC_EVENTS} basic events")

    events: list[BasicEvent] = []
    probabilities: dict[str, Fraction] = {}
    rates: dict[str, dict] = {}
    any_rate = False
    for raw in raw_events:
        if not isinstance(raw, dict):
            raise ModelError(
                errors.INPUTS,
                f"basic_events entries must be objects, got {type(raw).__name__}",
            )
        eid = raw.get("id")
        if not isinstance(eid, str) or not ID_PATTERN.fullmatch(eid):
            raise ModelError(errors.ID, f"invalid basic event id {eid!r}")
        if eid in probabilities:
            raise ModelError(errors.DUPLICATE_ID, f"duplicate basic event id {eid}")
        label = raw.get("label")
        if not isinstance(label, str) or not label:
            raise ModelError(errors.INPUTS, f"basic event {eid} requires a label")
        source = raw.get("source")
        if not isinstance(source, str) or not source:
            raise ModelError(errors.SOURCE, f"basic event {eid} requires a source")

        has_probability = "probability" in raw
        has_rate = "failure_rate" in raw
        if supports_rates:
            if has_probability == has_rate:
                raise ModelError(
                    errors.RATE_VALUE,
                    f"event {eid}: exactly one of 'probability' or 'failure_rate' is required",
                )
        elif has_rate:
            raise ModelError(errors.VERSION, f"event {eid}: failure_rate requires model schema 0.2.0")

        if has_rate:
            spec = parse_rate_spec(raw["failure_rate"], eid)
            fraction = mission_probability(spec, precision)
            text = exact_decimal_or_fraction(fraction)
            derivation = "rate_converted"
            any_rate = True
            rates[eid] = rate_provenance(spec, fraction, precision)
        else:
            if "probability" not in raw:
                raise ModelError(
                    errors.PROBABILITY,
                    f"event {eid}: probability is required in schema 0.1.0",
                )
            if not isinstance(raw["probability"], str) or not PROBABILITY_PATTERN.fullmatch(raw["probability"]):
                raise ModelError(errors.PROBABILITY, f"invalid probability for {eid}: {raw.get('probability')!r}")
            fraction = Fraction(raw["probability"])
            text = raw["probability"]
            derivation = "declared"

        if not 0 <= fraction <= 1:
            raise ModelError(errors.PROBABILITY, f"probability out of [0,1] for {eid}")
        events.append(
            BasicEvent(
                id=eid, label=label, probability=fraction, probability_text=text,
                source=source, derivation=derivation,
            )
        )
        probabilities[eid] = fraction

    if any_rate and assumptions.probability_semantics != "fixed_mission_probability_from_constant_rate":
        raise ModelError(
            errors.ASSUMPTIONS,
            "models containing failure_rate must declare probability_semantics "
            "'fixed_mission_probability_from_constant_rate'",
        )
    if not any_rate and assumptions.probability_semantics == "fixed_mission_probability_from_constant_rate":
        raise ModelError(
            errors.ASSUMPTIONS,
            "probability_semantics 'fixed_mission_probability_from_constant_rate' requires at least one failure_rate event",
        )

    raw_gates = data.get("gates")
    if not isinstance(raw_gates, list):
        raise ModelError(errors.INPUTS, "gates must be an array")
    if len(raw_gates) > MAX_GATES:
        raise ModelError(errors.SIZE_LIMIT, f"more than {MAX_GATES} gates")

    gates: list[Gate] = []
    gate_by_id: dict[str, Gate] = {}
    for raw in raw_gates:
        if not isinstance(raw, dict):
            raise ModelError(
                errors.INPUTS,
                f"gates entries must be objects, got {type(raw).__name__}",
            )
        gid = raw.get("id")
        if not isinstance(gid, str) or not ID_PATTERN.fullmatch(gid):
            raise ModelError(errors.ID, f"invalid gate id {gid!r}")
        if gid in gate_by_id or gid in probabilities:
            raise ModelError(errors.DUPLICATE_ID, f"duplicate gate id {gid}")
        kind = raw.get("kind")
        if not isinstance(kind, str) or kind not in GATE_KINDS:
            raise ModelError(errors.UNSUPPORTED_GATE, f"gate {gid} has unsupported kind {kind!r}")
        inputs = raw.get("inputs")
        if not isinstance(inputs, list) or not inputs or not all(isinstance(x, str) for x in inputs):
            raise ModelError(errors.INPUTS, f"gate {gid} needs a non-empty inputs array")
        k = raw.get("k")
        if kind == "K_OF_N":
            if type(k) is not int or not 1 <= k <= len(inputs):
                raise ModelError(errors.K_OF_N, f"gate {gid} needs integer k in [1, {len(inputs)}]")
            if len(set(inputs)) != len(inputs):
                raise ModelError(errors.K_OF_N, f"gate {gid} K_OF_N inputs must be distinct references")
        elif "k" in raw:
            raise ModelError(errors.K_OF_N, f"gate {gid} k is only valid on K_OF_N")
        gate = Gate(id=gid, kind=kind, inputs=tuple(inputs), k=k)
        gates.append(gate)
        gate_by_id[gid] = gate

    all_ids = frozenset(probabilities) | frozenset(gate_by_id)
    top = data.get("top_event")
    if not isinstance(top, str):
        raise ModelError(errors.TOP_EVENT, "top_event is required")
    if top not in all_ids:
        raise ModelError(errors.UNKNOWN_REFERENCE, f"top_event {top!r} is not defined")
    for gate in gates:
        for ref in gate.inputs:
            if ref not in all_ids:
                raise ModelError(errors.UNKNOWN_REFERENCE, f"gate {gate.id} references unknown id {ref!r}")

    _assert_acyclic(top, gate_by_id)
    _assert_all_gates_acyclic(top, gate_by_id)

    return StaticFtaModel(
        schema_version=schema_version,
        model_id=data.get("model_id") or "",
        baseline_id=data.get("baseline_id") or "",
        assumptions=assumptions,
        basic_events=tuple(events),
        gates=tuple(gates),
        top_event=top,
        probabilities=probabilities,
        rates=rates,
        rate_precision_digits=precision,
    )


def _assert_acyclic(top: str, gate_by_id: dict[str, Gate]) -> None:
    """Iterative DFS from the top event; any back edge is a cycle."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {top: WHITE}
    stack: list[tuple[str, int]] = [(top, 0)]
    while stack:
        node, index = stack.pop()
        if index == 0:
            if color.get(node, WHITE) == BLACK:
                continue
            if color.get(node, WHITE) == GRAY:
                continue
            color[node] = GRAY
        gate = gate_by_id.get(node)
        if gate is None:  # basic event — leaf
            color[node] = BLACK
            continue
        if index < len(gate.inputs):
            stack.append((node, index + 1))
            child = gate.inputs[index]
            state = color.get(child, WHITE)
            if state == GRAY:
                raise ModelError(errors.CYCLE, f"cycle detected through {child!r}")
            if state == WHITE:
                color.setdefault(child, WHITE)
                stack.append((child, 0))
        else:
            color[node] = BLACK


def _assert_all_gates_acyclic(top: str, gate_by_id: dict[str, Gate]) -> None:
    """Cycles anywhere in the gate table are rejected — even in parts not
    reachable from the declared top event (contract: whole model is a DAG)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(gate_by_id, WHITE)

    for start in gate_by_id:
        if color[start] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            node, index = stack.pop()
            if index == 0:
                if color[node] != WHITE:
                    continue
                color[node] = GRAY
            gate = gate_by_id[node]
            if index < len(gate.inputs):
                stack.append((node, index + 1))
                child = gate.inputs[index]
                if child in gate_by_id:
                    state = color[child]
                    if state == GRAY:
                        raise ModelError(errors.CYCLE, f"cycle detected through {child!r}")
                    if state == WHITE:
                        stack.append((child, 0))
            else:
                color[node] = BLACK
