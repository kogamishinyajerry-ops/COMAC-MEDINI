#!/usr/bin/env python3
"""Small synthetic FTA oracle; stdlib only; not a production safety analyzer.

Exact Fraction probabilities and direct Boolean evaluation are intentionally used
instead of a BDD. Max 20 basic events. No external software or network calls.
"""
from __future__ import annotations
import json
import re
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

class ModelError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")

def validate(model: dict[str, Any]) -> tuple[dict[str, Fraction], dict[str, dict[str, Any]]]:
    """Validate only the documented seed semantics, not arbitrary JSON Schema."""
    if model.get('schema_version') != '0.1.0':
        raise ModelError('VERSION', 'Expected seed schema 0.1.0')
    assumptions = model.get('assumptions', {})
    if (assumptions.get('basic_events_independent') is not True or
        assumptions.get('probability_semantics') != 'fixed_conditioned_probability' or
        not assumptions.get('condition')):
        raise ModelError('ASSUMPTIONS', 'Explicit fixed-probability independence is required')
    events = model.get('basic_events', [])
    if not 1 <= len(events) <= 20:
        raise ModelError('SIZE_LIMIT', 'Reference supports 1..20 basic events only')
    probs: dict[str, Fraction] = {}
    nodes: dict[str, dict[str, Any]] = {}
    for event in events:
        eid = event.get('id')
        if not isinstance(eid, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,127}', eid):
            raise ModelError('ID', 'Invalid event ID')
        if eid in probs:
            raise ModelError('DUPLICATE_ID', eid)
        text = event.get('probability')
        if not isinstance(text, str) or not re.fullmatch(r'(0(\.[0-9]+)?|1(\.0+)?)', text):
            raise ModelError('PROBABILITY', f'Invalid probability for {eid}: {text!r}')
        probs[eid] = Fraction(text)
        if not event.get('source'):
            raise ModelError('SOURCE', eid)
    for gate in model.get('gates', []):
        gid = gate.get('id')
        if not isinstance(gid, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,127}', gid):
            raise ModelError('ID', 'Invalid gate ID')
        if gid in probs or gid in nodes:
            raise ModelError('DUPLICATE_ID', gid)
        kind = gate.get('kind')
        if kind not in {'AND', 'OR', 'K_OF_N'}:
            raise ModelError('UNSUPPORTED_GATE', str(kind))
        refs = gate.get('inputs')
        if not isinstance(refs, list) or not refs or not all(isinstance(x, str) for x in refs):
            raise ModelError('INPUTS', gid)
        if kind == 'K_OF_N':
            k = gate.get('k')
            if type(k) is not int or not 1 <= k <= len(refs) or len(set(refs)) != len(refs):
                raise ModelError('K_OF_N', gid)
        elif 'k' in gate:
            raise ModelError('K_OF_N', 'k is only valid on K_OF_N')
        nodes[gid] = gate
    all_ids = set(probs) | set(nodes)
    if model.get('top_event') not in all_ids:
        raise ModelError('UNKNOWN_REFERENCE', 'top_event')
    for gate in nodes.values():
        for ref in gate['inputs']:
            if ref not in all_ids:
                raise ModelError('UNKNOWN_REFERENCE', ref)
    active: set[str] = set()
    done: set[str] = set()
    def visit(nid: str) -> None:
        if nid in active:
            raise ModelError('CYCLE', nid)
        if nid in done or nid in probs:
            return
        active.add(nid)
        for ref in nodes[nid]['inputs']:
            visit(ref)
        active.remove(nid)
        done.add(nid)
    for node_id in nodes:
        visit(node_id)
    return probs, nodes

def solve_reference(model: dict[str, Any]) -> tuple[Fraction, list[list[str]]]:
    probs, gates = validate(model)
    ids = sorted(probs)
    def evaluate(nid: str, state: dict[str, bool]) -> bool:
        if nid in state:
            return state[nid]
        gate = gates[nid]
        values = [evaluate(child, state) for child in gate['inputs']]
        if gate['kind'] == 'AND':
            return all(values)
        if gate['kind'] == 'OR':
            return any(values)
        return sum(values) >= gate['k']
    probability = Fraction(0)
    true_masks: list[int] = []
    for mask in range(1 << len(ids)):
        state = {eid: bool(mask & (1 << i)) for i, eid in enumerate(ids)}
        if not evaluate(model['top_event'], state):
            continue
        weight = Fraction(1)
        for eid in ids:
            weight *= probs[eid] if state[eid] else (1 - probs[eid])
        probability += weight
        true_masks.append(mask)
    minimal_masks: list[int] = []
    for mask in sorted(true_masks, key=lambda m: (m.bit_count(), m)):
        if not any((candidate & mask) == candidate for candidate in minimal_masks):
            minimal_masks.append(mask)
    mcs = [[eid for i, eid in enumerate(ids) if mask & (1 << i)] for mask in minimal_masks]
    return probability, sorted(mcs)

def main() -> int:
    catalog = json.loads((ROOT / 'cases.json').read_text(encoding='utf-8'))
    passed = 0
    for case in catalog['cases']:
        path = (ROOT / case['file']).resolve()
        if ROOT not in path.parents:
            raise ValueError('Fixture path escaped the contract directory')
        model = json.loads(path.read_text(encoding='utf-8'))
        try:
            probability, cut_sets = solve_reference(model)
            ok = case['expected_valid'] and probability == Fraction(case['expected_probability'])
            ok = ok and cut_sets == sorted([sorted(x) for x in case['expected_minimal_cut_sets']])
            detail = f'p={probability}; MCS={cut_sets}'
        except ModelError as exc:
            ok = not case['expected_valid'] and exc.code == case['expected_error']
            detail = str(exc)
        except Exception as exc:
            ok = False
            detail = f'Unexpected failure: {type(exc).__name__}: {exc}'
        print(f"{'PASS' if ok else 'FAIL'} {case['case_id']}: {detail}")
        passed += int(ok)
    print(f"\n{passed}/{len(catalog['cases'])} synthetic seed checks passed.")
    print('This is not a Medini test, B-line application test, or aviation acceptance.')
    return 0 if passed == len(catalog['cases']) else 1

if __name__ == '__main__':
    sys.exit(main())
