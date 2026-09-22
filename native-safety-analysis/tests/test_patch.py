"""Object-level patch language (model-patch-v1).

The forward twin of revert: instead of a whole new model file, an author
supplies edit operations. The patch applies to the CURRENT baseline's
canonical form — read from the store — and the patched form enters the
ordinary propose -> decide -> apply loop, so every existing guard covers
patches for free.

Canonical-level validation is the point: the canonical form renders
probabilities as exact n/d rationals when needed ("1/3"), which the model
input lexical pattern cannot express, so the result of a patch must be
validated where it lives.
"""
from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from native_safety.adapters.json_io import load_model  # noqa: E402
from native_safety.cli.main import main  # noqa: E402
from native_safety.domain import errors as err  # noqa: E402
from native_safety.domain.errors import ModelError  # noqa: E402
from native_safety.domain.patch import OPERATIONS, apply_patch, patch_preview  # noqa: E402
from native_safety.domain.semantic_hash import (  # noqa: E402
    canonical_model_form,
    hash_canonical_form,
    semantic_model_hash,
)
from native_safety.kernel.solve import solve_model  # noqa: E402
from native_safety.store import SqliteRepository, StoreError  # noqa: E402
from native_safety.store import errors as store_err  # noqa: E402

EXAMPLES = ROOT / "reference" / "03_contracts" / "examples"
HUMAN = "J. Engineer"


@pytest.fixture()
def form():
    model = load_model(EXAMPLES / "M03_repeated_event.json")
    return canonical_model_form(model)


@pytest.fixture()
def seeded(tmp_path):
    model = load_model(EXAMPLES / "M03_repeated_event.json")
    solution = solve_model(model)
    with SqliteRepository(str(tmp_path / "store.sqlite")) as repo:
        repo.record_run(
            run_id="r1", model=model, solution=solution,
            payload={
                "schema_version": "0.1.0", "model_schema_version": model.schema_version,
                "run_id": "r1", "model_id": model.model_id, "engine_version": "0.1.0",
                "semantic_model_hash": semantic_model_hash(model), "status": "succeeded",
                "top_probability": str(solution.top_probability), "cut_sets_complete": True,
                "importance_status": "not_requested",
                "engine_stats": {"top_probability_exact": str(solution.top_probability)},
            },
            actor="local-cli",
        )
        yield repo, model


# --------------------------------------------------------------------------
# the pure patch function
# --------------------------------------------------------------------------


def test_a_patch_is_pure_the_input_form_is_never_mutated(form):
    before = json.dumps(form, sort_keys=True)
    apply_patch(form, [{"op": "set_event_probability", "event": "A", "probability": "0.9"}])
    assert json.dumps(form, sort_keys=True) == before


def test_operations_apply_in_order_and_see_each_others_results(form):
    patched = apply_patch(form, [
        {"op": "add_event", "event": {"id": "D", "p": "0.05"}},
        {"op": "set_gate", "gate": {"id": "G2", "kind": "AND", "inputs": ["A", "D"]}},
    ])
    gate = [g for g in patched["gates"] if g["id"] == "G2"][0]
    assert gate["inputs"] == ["A", "D"]  # D existed by the time G2 was rewired


def test_a_probability_change_renders_through_the_shared_renderer(form):
    patched = apply_patch(form, [{"op": "set_event_probability", "event": "A", "probability": "0.25"}])
    assert [e for e in patched["basic_events"] if e["id"] == "A"][0]["p"] == "0.25"
    # "0.250" is the same rational: the canonical render normalises it
    again = apply_patch(form, [{"op": "set_event_probability", "event": "A", "probability": "0.250"}])
    assert hash_canonical_form(again) == hash_canonical_form(patched)


def test_a_rational_probability_is_accepted_and_kept_exact(form):
    patched = apply_patch(form, [{"op": "set_event_probability", "event": "A", "probability": "1/3"}])
    assert [e for e in patched["basic_events"] if e["id"] == "A"][0]["p"] == "1/3"
    # ...and a second patch can read it back and set something else
    final = apply_patch(patched, [{"op": "set_event_probability", "event": "A", "probability": "1/7"}])
    assert [e for e in final["basic_events"] if e["id"] == "A"][0]["p"] == "1/7"


@pytest.mark.parametrize(
    "ops, why",
    [
        ([], "empty"),
        ({"op": "set_event_probability"}, "not a list"),
        ([{"op": "bogus"}], "unknown operation"),
        ([{"op": "set_event_probability", "event": "NOPE", "probability": "0.1"}], "unknown event"),
        ([{"op": "set_event_probability", "event": "A", "probability": "1.5"}], "out of range"),
        ([{"op": "set_event_probability", "event": "A", "probability": "abc"}], "not a number"),
        ([{"op": "add_event", "event": {"id": "A", "p": "0.1"}}], "duplicate event id"),
        ([{"op": "add_event", "event": {"id": "G1", "p": "0.1"}}], "id clashes with a gate"),
        ([{"op": "remove_event", "event": "A"}], "event still referenced"),
        ([{"op": "remove_event", "event": "NOPE"}], "removing an unknown event"),
        ([{"op": "remove_gate", "gate": "G1"}], "gate still referenced"),
        ([{"op": "set_gate", "gate": {"id": "NOPE", "kind": "OR", "inputs": ["A"]}}], "unknown gate"),
        ([{"op": "add_gate", "gate": {"id": "G1", "kind": "OR", "inputs": ["A"]}}], "duplicate gate"),
        ([{"op": "set_gate", "gate": {"id": "G1", "kind": "XOR", "inputs": ["A"]}}], "bad kind"),
        ([{"op": "set_gate", "gate": {"id": "G1", "kind": "AND", "inputs": []}}], "empty inputs"),
        ([{"op": "set_gate", "gate": {"id": "G1", "kind": "K_OF_N", "inputs": ["A", "B"]}}], "k missing"),
        ([{"op": "set_gate", "gate": {"id": "G1", "kind": "K_OF_N", "inputs": ["A", "B"], "k": 3}}], "k too large"),
        ([{"op": "set_gate", "gate": {"id": "G1", "kind": "K_OF_N", "inputs": ["A", "A"], "k": 1}}], "kofn dup"),
        ([{"op": "set_gate", "gate": {"id": "G1", "kind": "AND", "inputs": ["A"], "k": 1}}], "k on AND"),
        ([{"op": "set_top_event", "gate": "NOPE"}], "top not defined"),
        ([{"op": "set_condition", "condition": "  "}], "blank condition"),
        ([{"op": "add_gate", "gate": {"id": "GX", "kind": "OR", "inputs": ["GX"]}}], "self-cycle"),
    ],
)
def test_invalid_operations_are_rejected_with_patch_op(form, ops, why):
    with pytest.raises(ModelError) as caught:
        apply_patch(form, ops)
    assert caught.value.code == err.PATCH_OP


def test_a_cycle_created_across_two_operations_is_rejected(form):
    with pytest.raises(ModelError) as caught:
        apply_patch(form, [
            {"op": "add_gate", "gate": {"id": "GX", "kind": "OR", "inputs": ["G1"]}},
            {"op": "set_gate", "gate": {"id": "G1", "kind": "AND", "inputs": ["A", "GX"]}},
        ])
    assert caught.value.code == err.PATCH_OP


def test_an_unreachable_cycle_is_rejected_too(form):
    with pytest.raises(ModelError) as caught:
        apply_patch(form, [
            {"op": "add_gate", "gate": {"id": "GA", "kind": "OR", "inputs": ["GB"]}},
            {"op": "add_gate", "gate": {"id": "GB", "kind": "OR", "inputs": ["GA"]}},
        ])
    assert caught.value.code == err.PATCH_OP  # whole graph must stay a DAG


def test_rate_events_refuse_direct_probability_edits():
    model = load_model(ROOT / "examples" / "R01_rate_and.json")
    form = canonical_model_form(model)
    rate_event = next(e["id"] for e in form["basic_events"] if "rate" in e)
    with pytest.raises(ModelError) as caught:
        apply_patch(form, [{"op": "set_event_probability", "event": rate_event, "probability": "0.5"}])
    assert caught.value.code == err.PATCH_OP
    assert "rate-derived" in caught.value.message


# --------------------------------------------------------------------------
# set_event_rate: editing a rate record through the full conversion gate
# --------------------------------------------------------------------------

RATE_FORM = None  # populated lazily; keeps the import-time cost out of collection


def _rate_form():
    global RATE_FORM
    if RATE_FORM is None:
        model = load_model(ROOT / "examples" / "R01_rate_and.json")
        RATE_FORM = canonical_model_form(model)
    return json.loads(json.dumps(RATE_FORM, ensure_ascii=False))


def _new_rate(lambda_text, mission_text, *, source="derating per vendor rev B"):
    return {
        "model": "constant_failure_rate", "lambda": lambda_text, "lambda_unit": "1/h",
        "mission_time": mission_text, "mission_time_unit": "h",
        "repairable": False, "source": source,
    }


def test_set_event_rate_rederives_p_and_provenance():
    patched = apply_patch(_rate_form(), [
        {"op": "set_event_rate", "event": "P1", "rate": _new_rate("5e-6", "2000")},
    ])
    event = [e for e in patched["basic_events"] if e["id"] == "P1"][0]
    assert event["rate"]["lambda"] == "5e-6"
    assert event["rate"]["mission_time"] == "2000"
    assert event["rate"]["lambda_t"] == "0.01"  # exact lambda*t
    # provenance fields that ride along (the spec's own source string is not
    # part of the canonical provenance record; precision and units are)
    assert event["rate"]["precision_digits"] == 40  # inherited from the event's record
    assert event["p"].startswith("0.0099501662")  # 1-exp(-0.01)


def test_set_event_rate_matches_the_independent_oracle():
    """q must agree with the pure-rational series oracle within the declared
    1e-30 tolerance — the same contract run_rate_cross_check enforces."""
    sys.path.insert(0, str(ROOT / "verification"))
    from run_rate_cross_check import oracle_q

    for lam, t in [("5e-6", "2000"), ("2e-5", "10000"), ("1e-4", "50")]:
        patched = apply_patch(_rate_form(), [
            {"op": "set_event_rate", "event": "P1", "rate": _new_rate(lam, t)},
        ])
        event = [e for e in patched["basic_events"] if e["id"] == "P1"][0]
        reference = oracle_q(Fraction(lam), Fraction(t))
        assert abs(Fraction(event["p"]) - reference) < Fraction(1, 10**30)


def test_set_event_rate_reruns_the_whole_conversion_gate():
    # the gate re-runs with its OWN error taxonomy: a bad rate spec surfaces
    # the underlying rate codes (RATE_VALUE / RATE_UNITS / RATE_UNSUPPORTED /
    # SOURCE), which classify the reason instead of flattening it to PATCH_OP
    for bad_rate, why, expected_code in [
        (_new_rate("-1e-6", "1"), "negative lambda", "RATE_VALUE"),
        (_new_rate("1e-6", "-1"), "negative mission time", "RATE_VALUE"),
        (dict(_new_rate("1e-6", "1"), lambda_unit="1/s"), "bad lambda unit", "RATE_UNITS"),
        (dict(_new_rate("1e-6", "1"), mission_time_unit="s"), "bad time unit", "RATE_UNITS"),
        (dict(_new_rate("1e-6", "1"), repairable=True), "repairable", "RATE_UNSUPPORTED"),
        (dict(_new_rate("1e-6", "1"), model="weibull"), "unsupported model", "RATE_UNSUPPORTED"),
        (dict(_new_rate("1e-6", "1"), inspection_interval="100"), "forbidden field", "RATE_UNSUPPORTED"),
        ({**_new_rate("1e-6", "1"), "source": ""}, "blank source", "SOURCE"),
    ]:
        with pytest.raises(ModelError) as caught:
            apply_patch(_rate_form(), [{"op": "set_event_rate", "event": "P1", "rate": bad_rate}])
        assert caught.value.code == expected_code, f"{why}: {caught.value.code}"


def test_set_event_rate_refuses_a_plain_event():
    model = load_model(EXAMPLES / "M03_repeated_event.json")
    form = canonical_model_form(model)
    with pytest.raises(ModelError) as caught:
        apply_patch(form, [{"op": "set_event_rate", "event": "A", "rate": _new_rate("1e-6", "1")}])
    assert caught.value.code == err.PATCH_OP
    assert "plain-probability" in caught.value.message


def test_set_event_rate_on_an_unknown_event_is_rejected():
    with pytest.raises(ModelError) as caught:
        apply_patch(_rate_form(), [{"op": "set_event_rate", "event": "NOPE", "rate": _new_rate("1e-6", "1")}])
    assert caught.value.code == err.PATCH_OP


def test_a_rate_edit_is_visible_in_the_diff_and_reversible():
    form = _rate_form()
    ops = [{"op": "set_event_rate", "event": "P1", "rate": _new_rate("5e-6", "2000")}]
    preview = patch_preview(form, ops)
    assert preview["summary"]["events_changed"] == 1
    changed = preview["diff"]["events"]["changed"][0]
    assert changed["id"] == "P1"
    assert "rate" in changed["new"]  # the diff fingerprints the rate record too
    # applying the same edit twice is idempotent at the form level (same hash)
    once = apply_patch(form, ops)
    twice = apply_patch(once, ops)
    assert hash_canonical_form(once) == hash_canonical_form(twice)


def test_a_rate_edit_converges_with_a_full_rate_model_change():
    """The same λ/t change via patch and via a rebuilt rate model file must
    produce the same canonical hash — the convergence contract extended to
    rate edits."""
    form = _rate_form()
    patched = apply_patch(form, [
        {"op": "set_event_rate", "event": "P1", "rate": _new_rate("5e-6", "2000")},
    ])
    raw = json.loads((ROOT / "examples" / "R01_rate_and.json").read_text(encoding="utf-8"))
    raw["basic_events"][0]["failure_rate"]["lambda"] = "5e-6"
    raw["basic_events"][0]["failure_rate"]["mission_time"] = "2000"
    via_model = canonical_model_form(load_model_from_dict(raw))
    assert hash_canonical_form(patched) == hash_canonical_form(via_model)


# --------------------------------------------------------------------------
# add_event with a rate spec + set_probability_semantics
# --------------------------------------------------------------------------


def _plain_form():
    model = load_model(EXAMPLES / "M03_repeated_event.json")
    return canonical_model_form(model)


_RATE_SPEC = {
    "model": "constant_failure_rate", "lambda": "2e-6", "lambda_unit": "1/h",
    "mission_time": "500", "mission_time_unit": "h",
    "repairable": False, "source": "new sensor channel",
}


def test_add_event_accepts_a_rate_spec_through_the_gate():
    patched = apply_patch(_plain_form(), [
        {"op": "add_event", "event": {"id": "D", "rate": _RATE_SPEC}},
        {"op": "set_probability_semantics",
         "probability_semantics": "fixed_mission_probability_from_constant_rate"},
    ])
    assert patched["schema_version"] == "0.2.0"  # auto-upgraded with the first rate event
    event = [e for e in patched["basic_events"] if e["id"] == "D"][0]
    assert event["rate"]["lambda_t"] == "0.001"  # exact 2e-6 * 500
    assert event["p"].startswith("0.0009995001666250083319")  # 1-exp(-0.001)
    assert event["rate"]["precision_digits"] == 40  # model default


def test_add_event_rate_oracle_and_convergence():
    sys.path.insert(0, str(ROOT / "verification"))
    from run_rate_cross_check import oracle_q

    ops = [
        {"op": "add_event", "event": {"id": "D", "rate": _RATE_SPEC}},
        {"op": "set_probability_semantics",
         "probability_semantics": "fixed_mission_probability_from_constant_rate"},
    ]
    patched = apply_patch(_plain_form(), ops)
    event = [e for e in patched["basic_events"] if e["id"] == "D"][0]
    reference = oracle_q(Fraction("2e-6"), Fraction("500"))
    assert abs(Fraction(event["p"]) - reference) < Fraction(1, 10**30)

    raw = json.loads((EXAMPLES / "M03_repeated_event.json").read_text(encoding="utf-8"))
    raw["schema_version"] = "0.2.0"
    raw["assumptions"]["probability_semantics"] = "fixed_mission_probability_from_constant_rate"
    raw["basic_events"].append({"id": "D", "label": "D", "source": "new sensor channel",
                                "failure_rate": _RATE_SPEC})
    via_model = canonical_model_form(load_model_from_dict(raw))
    assert hash_canonical_form(patched) == hash_canonical_form(via_model)


def test_add_event_rate_refuses_when_the_semantics_is_not_flipped():
    with pytest.raises(ModelError) as caught:
        apply_patch(_plain_form(), [{"op": "add_event", "event": {"id": "D", "rate": _RATE_SPEC}}])
    assert caught.value.code == err.PATCH_OP
    assert "fixed_mission_probability_from_constant_rate" in caught.value.message


def test_add_event_rate_keeps_the_underlying_error_codes():
    for bad, code in [
        (dict(_RATE_SPEC, lambda_unit="1/s"), "RATE_UNITS"),
        (dict(_RATE_SPEC, repairable=True), "RATE_UNSUPPORTED"),
        (dict(_RATE_SPEC, mission_time="-5"), "RATE_VALUE"),
    ]:
        with pytest.raises(ModelError) as caught:
            apply_patch(_plain_form(), [{"op": "add_event", "event": {"id": "D", "rate": bad}}])
        assert caught.value.code == code


def test_add_event_rate_in_a_mixed_model_inherits_sibling_precision():
    form = canonical_model_form(load_model(ROOT / "examples" / "R02_rate_mixed.json"))
    patched = apply_patch(form, [
        {"op": "add_event", "event": {"id": "E", "rate": dict(_RATE_SPEC, source="extra")}},
    ])
    event = [e for e in patched["basic_events"] if e["id"] == "E"][0]
    assert event["rate"]["precision_digits"] == 40  # from the existing rate sibling
    assert patched["schema_version"] == "0.2.0"  # no change needed


def test_schema_never_downgrades_by_removing_the_last_rate_event():
    """Removing the last rate event leaves 0.2.0 + rate semantics in place;
    the final check refuses — a downgrade narrows declared semantics and
    must be an explicit decision, not an edit side effect."""
    form = canonical_model_form(load_model(ROOT / "examples" / "R01_rate_and.json"))
    plain = {"id": "PLAIN", "p": "0.1"}
    ops = [
        {"op": "add_event", "event": plain},
        {"op": "set_gate", "gate": {"id": "TOP", "kind": "AND", "inputs": ["PLAIN"]}},
    ] + [{"op": "remove_event", "event": e["id"]} for e in form["basic_events"] if "rate" in e]
    with pytest.raises(ModelError) as caught:
        apply_patch(form, ops)
    assert caught.value.code == err.PATCH_OP
    assert "fixed_mission_probability_from_constant_rate" in caught.value.message


def test_set_probability_semantics_is_an_explicit_declaration():
    # a lone flip on a plain model is refused by the two-way guard
    with pytest.raises(ModelError) as caught:
        apply_patch(_plain_form(), [
            {"op": "set_probability_semantics",
             "probability_semantics": "fixed_mission_probability_from_constant_rate"},
        ])
    assert caught.value.code == err.PATCH_OP
    # unknown vocabulary is rejected
    with pytest.raises(ModelError) as caught:
        apply_patch(_plain_form(), [
            {"op": "set_probability_semantics", "probability_semantics": "weibull"},
        ])
    assert caught.value.code == err.PATCH_OP
    # flipping BACK after removing rate events is equally refused
    form = _rate_form()
    ops = [
        {"op": "add_event", "event": {"id": "PLAIN", "p": "0.1"}},
        {"op": "set_gate", "gate": {"id": "TOP", "kind": "AND", "inputs": ["PLAIN"]}},
        {"op": "remove_event", "event": "P1"},
        {"op": "remove_event", "event": "V1"},
    ]
    with pytest.raises(ModelError) as caught:
        apply_patch(form, ops)
    assert caught.value.code == err.PATCH_OP


def test_add_event_still_accepts_plain_probabilities(form):
    patched = apply_patch(form, [{"op": "add_event", "event": {"id": "Z", "p": "1/9"}}])
    assert [e for e in patched["basic_events"] if e["id"] == "Z"][0]["p"] == "1/9"


def load_model_from_dict(data):
    from native_safety.domain.validation import validate_model
    return validate_model(data)


def test_removing_the_last_rate_event_breaks_the_semantics_guard():
    model = load_model(ROOT / "examples" / "R01_rate_and.json")
    form = canonical_model_form(model)
    # R01 is all rate events: add a plain event, rewire every gate input to it,
    # then remove the rate events — the two-way semantics guard must fire
    ops = [{"op": "add_event", "event": {"id": "PLAIN", "p": "0.1"}}]
    for gate in form["gates"]:
        ops.append({
            "op": "set_gate",
            "gate": {"id": gate["id"], "kind": "AND",
                     "inputs": ["PLAIN"]},
        })
    for event in form["basic_events"]:
        if "rate" in event:
            ops.append({"op": "remove_event", "event": event["id"]})
    with pytest.raises(ModelError) as caught:
        apply_patch(form, ops)
    assert caught.value.code == err.PATCH_OP
    assert "fixed_mission_probability_from_constant_rate" in caught.value.message


def test_preview_reports_the_same_diff_the_impact_command_would(form):
    ops = [{"op": "set_event_probability", "event": "A", "probability": "0.4"}]
    preview = patch_preview(form, ops)
    assert preview["summary"]["events_changed"] == 1
    assert preview["diff"]["semantically_equal"] is False


# --------------------------------------------------------------------------
# the store integration
# --------------------------------------------------------------------------


def test_patch_review_applies_to_the_stored_baseline(seeded):
    repo, model = seeded
    current = repo.current_baseline_hash("M03_repeated_event")
    record = repo.patch_review(
        review_id="P1", model_id="M03_repeated_event",
        expected_baseline_hash=current,
        ops=[{"op": "set_event_probability", "event": "A", "probability": "0.3"}],
        proposed_by="agent",
    )
    assert record["state"] == "proposed"
    assert record["proposed_hash"] is not None
    # baseline untouched by a proposal
    assert repo.current_baseline_hash("M03_repeated_event") == current


def test_a_patch_walks_the_ordinary_loop(seeded):
    repo, model = seeded
    current = repo.current_baseline_hash("M03_repeated_event")
    repo.patch_review(
        review_id="P1", model_id="M03_repeated_event", expected_baseline_hash=current,
        ops=[
            {"op": "set_event_probability", "event": "A", "probability": "0.3"},
            {"op": "add_event", "event": {"id": "D", "p": "0.05"}},
            {"op": "set_gate", "gate": {"id": "G2", "kind": "AND", "inputs": ["A", "D"]}},
        ],
        proposed_by="agent",
    )
    with pytest.raises(StoreError) as caught:
        repo.decide_review("P1", approve=True, reviewer="agent")
    assert caught.value.code == store_err.APPROVAL_AUTHORITY

    repo.decide_review("P1", approve=True, reviewer=HUMAN)
    record, stale = repo.apply_review("P1", reviewer=HUMAN)
    assert record["applied_baseline_hash"] == record["proposed_hash"]
    assert repo.current_baseline_hash("M03_repeated_event") == record["applied_baseline_hash"]
    assert list(stale) == ["r1"]
    assert repo.verify() == []


def test_an_invalid_patch_is_recorded_not_dropped(seeded):
    repo, model = seeded
    record = repo.patch_review(
        review_id="P1", model_id="M03_repeated_event",
        expected_baseline_hash=repo.current_baseline_hash("M03_repeated_event"),
        ops=[{"op": "remove_event", "event": "A"}],
        proposed_by="agent",
    )
    assert record["state"] == "invalid"
    assert record["validation_code"] == err.PATCH_OP
    with pytest.raises(StoreError) as caught:
        repo.decide_review("P1", approve=True, reviewer=HUMAN)
    assert caught.value.code == store_err.REVIEW_STATE


def test_a_patch_with_a_stale_expectation_is_refused(seeded):
    repo, model = seeded
    with pytest.raises(StoreError) as caught:
        repo.patch_review(
            review_id="P1", model_id="M03_repeated_event",
            expected_baseline_hash="0" * 64,
            ops=[{"op": "set_event_probability", "event": "A", "probability": "0.3"}],
            proposed_by="agent",
        )
    assert caught.value.code == store_err.BASELINE_CONFLICT


def test_impact_works_on_a_patch_proposal(seeded):
    repo, model = seeded
    repo.patch_review(
        review_id="P1", model_id="M03_repeated_event",
        expected_baseline_hash=repo.current_baseline_hash("M03_repeated_event"),
        ops=[{"op": "set_event_probability", "event": "A", "probability": "0.3"}],
        proposed_by="agent",
    )
    impact = repo.review_impact("P1")
    assert impact["summary"]["events_changed"] == 1
    changed = impact["diff"]["events"]["changed"]
    assert changed[0]["id"] == "A" and changed[0]["new"]["p"] == "0.3"


def test_a_revert_can_undo_a_patch(seeded):
    repo, model = seeded
    before = repo.current_baseline_hash("M03_repeated_event")
    repo.patch_review(
        review_id="P1", model_id="M03_repeated_event", expected_baseline_hash=before,
        ops=[{"op": "set_event_probability", "event": "A", "probability": "0.3"}],
        proposed_by="agent",
    )
    repo.decide_review("P1", approve=True, reviewer=HUMAN)
    repo.apply_review("P1", reviewer=HUMAN)
    assert repo.current_baseline_hash("M03_repeated_event") != before

    repo.propose_revert(
        review_id="RV", model_id="M03_repeated_event",
        target_baseline_hash=before,
        expected_baseline_hash=repo.current_baseline_hash("M03_repeated_event"),
        proposed_by="agent",
    )
    repo.decide_review("RV", approve=True, reviewer=HUMAN)
    repo.apply_review("RV", reviewer=HUMAN)
    assert repo.current_baseline_hash("M03_repeated_event") == before
    assert repo.verify() == []


def test_patch_and_full_model_proposal_produce_the_same_hash(seeded):
    """The two entry points to the same semantics must converge."""
    import dataclasses
    from fractions import Fraction

    repo, model = seeded
    events = tuple(
        dataclasses.replace(e, probability=Fraction(3, 10), probability_text="0.3")
        if e.id == "A" else e
        for e in model.basic_events
    )
    via_model = dataclasses.replace(model, basic_events=events)

    patch_record = repo.patch_review(
        review_id="P1", model_id="M03_repeated_event",
        expected_baseline_hash=repo.current_baseline_hash("M03_repeated_event"),
        ops=[{"op": "set_event_probability", "event": "A", "probability": "0.3"}],
        proposed_by="agent",
    )
    assert patch_record["proposed_hash"] == semantic_model_hash(via_model)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def cli(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if captured.out.strip() else None


def test_cli_patch_flow(seeded, capsys, tmp_path):
    repo, model = seeded
    db = repo.path
    current = repo.current_baseline_hash("M03_repeated_event")
    patch_path = tmp_path / "p.json"
    patch_path.write_text(json.dumps([
        {"op": "set_event_probability", "event": "A", "probability": "0.3"},
        {"op": "add_event", "event": {"id": "D", "p": "0.05"}},
        {"op": "set_gate", "gate": {"id": "G2", "kind": "AND", "inputs": ["A", "D"]}},
    ]), encoding="utf-8")

    code, payload = cli(
        capsys, "review", "patch-preview", db, "--model-id", "M03_repeated_event",
        "--patch", str(patch_path),
    )
    assert code == 0
    assert payload["summary"]["events_added"] == 1
    assert payload["summary"]["events_changed"] == 1
    assert payload["summary"]["gates_changed"] == 1

    code, payload = cli(
        capsys, "review", "patch", db, "--model-id", "M03_repeated_event",
        "--patch", str(patch_path), "--review-id", "P1",
        "--expected-baseline-hash", current,
    )
    assert code == 0 and payload["review"]["state"] == "proposed"

    code, payload = cli(capsys, "review", "impact", db, "P1")
    assert code == 0 and payload["summary"]["events_added"] == 1

    cli(capsys, "review", "decide", db, "P1", "--approve", "--reviewer", HUMAN)
    code, payload = cli(capsys, "review", "apply", db, "P1", "--reviewer", HUMAN)
    assert code == 0
    assert repo.current_baseline_hash("M03_repeated_event") == payload["review"]["applied_baseline_hash"]

    code, payload = cli(capsys, "store", "verify", db)
    assert code == 0 and payload["problems"] == []


def test_cli_patch_refusals(seeded, capsys, tmp_path):
    repo, model = seeded
    db = repo.path
    patch_path = tmp_path / "p.json"
    patch_path.write_text(json.dumps([{"op": "remove_event", "event": "A"}]), encoding="utf-8")

    code, payload = cli(
        capsys, "review", "patch", db, "--model-id", "M03_repeated_event",
        "--patch", str(patch_path), "--review-id", "P1",
        "--expected-baseline-hash", "0" * 64,
    )
    assert code == 2 and payload["error"]["code"] == store_err.BASELINE_CONFLICT

    bad_path = tmp_path / "not_an_array.json"
    bad_path.write_text(json.dumps({"op": "x"}), encoding="utf-8")
    code, payload = cli(
        capsys, "review", "patch-preview", db, "--model-id", "M03_repeated_event",
        "--patch", str(bad_path),
    )
    assert code == 2 and payload["error"]["code"] == err.PATCH_OP

    code, payload = cli(
        capsys, "review", "patch-preview", db, "--model-id", "NOPE",
        "--patch", str(patch_path),
    )
    assert code == 2 and payload["error"]["code"] == store_err.MODEL_NOT_FOUND
