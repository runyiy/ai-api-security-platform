"""The adopted v1 input/output shapes and deterministic, authority-free display."""
import re
from dataclasses import dataclass

from .codec import array, bounded_json, canonical, choice, fields, require, unique_strings

INPUT_VERSION = 'ra-ai-proposal-input/1'
OUTPUT_VERSION = 'ra-ai-proposal-output/1'
SHAPE = 'single_resource_path_get_json_object'
GAPS = ('FACTS_MISSING', 'UNSUPPORTED_SHAPE', 'NO_APPLICABLE_RULE')
UNCERTAINTY = ('NOT_EXECUTED', *GAPS)
TYPES = ('REVIEW_CANDIDATE', 'REQUEST_INPUT', 'EXPLAIN_RULE')
REASONS = ('CANDIDATE_REVIEW_ONLY', 'INPUT_REQUIRED', 'GENERAL_RULE_ONLY')
REFUSALS = ('INSUFFICIENT_CONTEXT', 'NO_APPLICABLE_RULE', 'UNSUPPORTED_SHAPE', 'SAFETY_REFUSAL')


def reference(value, kind):
    require(type(value) is str and re.fullmatch(kind + r'_[0-9a-f]{32}', value) is not None,
            'REFERENCE_UNAVAILABLE')


def input_document(raw: bytes):
    doc = bounded_json(raw, maximum=16384, depth=5, nodes=1024, limit_code='INPUT_LIMIT')
    fields(doc, 'protocol request_ref task candidates rules gaps')
    choice(doc['protocol'], (INPUT_VERSION,), 'VERSION_UNSUPPORTED')
    reference(doc['request_ref'], 'q')
    choice(doc['task'], ('prioritize_review',))
    for name, kind, cap in [('candidates', 'c', 8), ('rules', 'k', 4), ('gaps', 'g', 8)]:
        items = doc[name]
        array(items, cap)
        seen = set()
        for item in items:
            fields(item, {'c': 'ref shape actor_mode', 'k': 'ref claim applicability_codes', 'g': 'ref code'}[kind])
            reference(item['ref'], kind)
            require(item['ref'] not in seen, 'REFERENCE_UNAVAILABLE')
            seen.add(item['ref'])
            if kind == 'c':
                choice(item['shape'], (SHAPE, 'unsupported_shape'))
                choice(item['actor_mode'], ('anonymous', 'bearer', 'unknown'))
            elif kind == 'k':
                require(type(item['claim']) is str and 1 <= len(item['claim'].encode()) <= 512)
                unique_strings(item['applicability_codes'], 4, 1)
                for code in item['applicability_codes']:
                    choice(code, (SHAPE, 'anonymous', 'bearer', 'independent_facts_required'))
            else:
                choice(item['code'], GAPS)
    require(any(doc[name] for name in ('candidates', 'rules', 'gaps')), 'DATA_INELIGIBLE')
    return doc


@dataclass(frozen=True)
class Display:
    suggestion_ref: str
    text: str
    references: tuple[str, ...]
    uncertainty_codes: tuple[str, ...]


def output_document(raw: bytes, inp: dict, allowed_pairs: frozenset[tuple[str, str]]):
    doc = bounded_json(raw, maximum=8192, depth=5, nodes=1024)
    fields(doc, 'protocol request_ref status suggestions refusal_code')
    choice(doc['protocol'], (OUTPUT_VERSION,), 'VERSION_UNSUPPORTED')
    require(doc['request_ref'] == inp['request_ref'], 'REQUEST_MISMATCH')
    choice(doc['status'], ('suggestions', 'refusal'))
    array(doc['suggestions'], 4)
    gaps = {g['ref']: g['code'] for g in inp['gaps']}
    candidates = {c['ref']: c for c in inp['candidates']}
    rules = {r['ref']: r for r in inp['rules']}
    if doc['status'] == 'refusal':
        require(not doc['suggestions'])
        choice(doc['refusal_code'], REFUSALS)
        required = dict(zip(REFUSALS[:3], ('FACTS_MISSING', 'NO_APPLICABLE_RULE', 'UNSUPPORTED_SHAPE')))
        require(doc['refusal_code'] == 'SAFETY_REFUSAL' or required[doc['refusal_code']] in gaps.values())
        return doc, ()
    require(doc['refusal_code'] is None and bool(doc['suggestions']))
    seen, display = set(), []
    for i, suggestion in enumerate(doc['suggestions'], 1):
        fields(suggestion, 'suggestion_ref type candidate_ref rule_refs gap_refs reason_code uncertainty_codes')
        require(len(canonical(suggestion)) <= 1024, 'OUTPUT_LIMIT')
        require(suggestion['suggestion_ref'] == f's_{i}')
        kind, candidate = suggestion['type'], suggestion['candidate_ref']
        choice(kind, TYPES, 'SUGGESTION_UNSUPPORTED')
        choice(suggestion['reason_code'], REASONS)
        for name, registry in [('rule_refs', rules), ('gap_refs', gaps)]:
            unique_strings(suggestion[name], 2)
            for ref in suggestion[name]:
                require(ref in registry, 'REFERENCE_UNAVAILABLE')
        if candidate is not None:
            require(type(candidate) is str and candidate in candidates, 'REFERENCE_UNAVAILABLE')
        unique_strings(suggestion['uncertainty_codes'], 4, 1)
        for code in suggestion['uncertainty_codes']:
            choice(code, UNCERTAINTY)
        rr, gg = suggestion['rule_refs'], suggestion['gap_refs']
        key = (kind, candidate, frozenset(rr), frozenset(gg))
        require(key not in seen)
        seen.add(key)
        codes = ['NOT_EXECUTED']
        if kind == 'REVIEW_CANDIDATE':
            require(candidate is not None and 1 <= len(rr) <= 2 and not gg, 'SUGGESTION_UNSUPPORTED')
            c = candidates[candidate]
            require(c['shape'] == SHAPE and c['actor_mode'] in ('anonymous', 'bearer'), 'SUGGESTION_UNSUPPORTED')
            require(all((candidate, r) in allowed_pairs for r in rr), 'SUGGESTION_UNSUPPORTED')
            reason, text = REASONS[0], 'Suggested for human review. Not executed.'
        elif kind == 'REQUEST_INPUT':
            require(candidate is None and not rr and 1 <= len(gg) <= 2, 'SUGGESTION_UNSUPPORTED')
            codes += [code for code in GAPS if code in {gaps[g] for g in gg}]
            reason, text = REASONS[1], 'Additional approved input is required. Not executed.'
        else:
            require(candidate is None and len(rr) == 1 and not gg, 'SUGGESTION_UNSUPPORTED')
            reason, text = REASONS[2], 'Review the referenced general rule. No project conclusion.'
        require(suggestion['reason_code'] == reason and suggestion['uncertainty_codes'] == codes,
                'SUGGESTION_UNSUPPORTED')
        refs = ((candidate,) if candidate is not None else ()) + tuple(rr) + tuple(gg)
        display.append(Display(f's_{i}', text, refs, tuple(codes)))
    return doc, tuple(display)


def output_schema():
    """Fresh strict schema; callers cannot mutate a shared prompt/schema constant."""
    def enum(values, nullable=False):
        return {'type': ['string', 'null'] if nullable else 'string', 'enum': [*values, None] if nullable else list(values)}
    def refs(kind, nullable=False):
        return {'type': ['string', 'null'] if nullable else 'string', 'pattern': kind + '_[0-9a-f]{32}', 'minLength': 34, 'maxLength': 34}
    def obj(properties):
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    suggestion = obj({'suggestion_ref': enum(('s_1', 's_2', 's_3', 's_4')), 'type': enum(TYPES),
                      'candidate_ref': refs('c', True),
                      'rule_refs': {'type': 'array', 'items': refs('k'), 'maxItems': 2},
                      'gap_refs': {'type': 'array', 'items': refs('g'), 'maxItems': 2},
                      'reason_code': enum(REASONS),
                      'uncertainty_codes': {'type': 'array', 'items': enum(UNCERTAINTY), 'minItems': 1, 'maxItems': 4}})
    return obj({'protocol': enum((OUTPUT_VERSION,)), 'request_ref': refs('q'),
                'status': enum(('suggestions', 'refusal')),
                'suggestions': {'type': 'array', 'items': suggestion, 'maxItems': 4},
                'refusal_code': enum(REFUSALS, True)})
