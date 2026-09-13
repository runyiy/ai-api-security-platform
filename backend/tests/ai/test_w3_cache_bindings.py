"""Exact cache bindings, bounded decoding and independently sealed analysis."""
from copy import copy
from dataclasses import asdict, replace
from datetime import timedelta
from hashlib import sha256
import json

import pytest

from app.ai.proposals.adapter import request_body
from app.ai.proposals.codec import canonical
from app.ai.proposals.contract import input_document
from app.ai.w2.preparation import w1_registry_document
from app.ai.w2.records import decode, make, PortError, stamp, usage_view, utc
from app.ai.w3.cache import AnalysisCache, MAX_ANALYSIS_BYTES, decode_analysis
from tests.ai.test_w2_execution import response
from tests.ai.test_w3_cache import completed, read, remembered, sql_state, independent_state
from tests.ai.test_w3_cache_security import cache_only_capabilities  # noqa: F401
from tests.ai.w2_fixtures import (w2, rule_pair, knowledge_pair, subject_pair,
    two_intake_targets, zero_capabilities, KNOWN)  # noqa: F401


def _record(runtime):
    """Untrusted synthetic bytes for decoder tests; never a stored completion."""
    envelope = json.loads(response(runtime).partition(b'\r\n\r\n')[2])
    return dict(format='ra-w3-analysis/1', key_digest=AnalysisCache.key_digest(runtime),
        completion_id='synthetic_decoder_completion', settlement_id='synthetic_decoder_settlement',
        usage=usage_view(KNOWN).document(),
        proposal=json.loads(envelope['output'][0]['content'][0]['text']))


def _variant(runtime, *, core=None, configuration=None, registry=None,
             payload=None, certificate=None, manifest_digest=None):
    """Rebind negative copies of a real preparation; this grants no approval."""
    original = runtime.prepared
    config = replace(original.config, **(configuration or {}))
    reg = replace(original.prepared.registry, **(registry or {}))
    prepared = replace(original.prepared, registry=reg,
                       payload=original.prepared.payload if payload is None else payload)
    body = request_body(input_document(prepared.payload))
    bindings = dict(original.core.document())
    bindings.update(w1_binding_digest=prepared.digest(config),
        payload_digest=sha256(prepared.payload).hexdigest(), body_digest=sha256(body).hexdigest(),
        registry_digest=sha256(canonical(w1_registry_document(reg))).hexdigest(),
        configuration_digest=sha256(canonical(asdict(config))).hexdigest(),
        config_revision=config.revision, secret_ref=config.secret_ref,
        secret_version=config.secret_version, rate_card=config.rate_card,
        usage_mapping=config.usage_mapping)
    bindings.update(core or {})
    revised = replace(original, core=make('BindingCore', **bindings), config=config,
        prepared=prepared, body=body,
        certificate=original.certificate if certificate is None else certificate,
        manifest_digest=original.manifest_digest if manifest_digest is None else manifest_digest)
    result = copy(runtime)
    result.prepared, result.key = revised, revised.key
    result.run = make('RunContext', **{**runtime.run.document(), 'scope': revised.core.scope})
    return result


def test_full_version_key_preserves_real_qualified_binding_and_separates_changed_versions(w2):
    w2.reopen()
    runtime = w2.runtime(reserve=False)
    original_key = AnalysisCache.key_digest(runtime)
    material = json.loads(AnalysisCache._key(runtime))
    assert material['core'] == runtime.prepared.core.document()
    assert material['run'] == runtime.run.document()
    assert material['data_class'] == 'synthetic_authored'
    assert material['privacy_class'] == 'reusable_synthetic'
    assert material['provider'] == 'openai'
    assert (material['input_protocol'], material['output_protocol']) == (
        'ra-ai-proposal-input/1', 'ra-ai-proposal-output/1')
    assert material['counting_version'] == runtime.prepared.certificate.counting_version
    assert runtime.prepared.prepared.digest(runtime.prepared.config) == runtime.prepared.core.w1_binding_digest
    before = sql_state(), independent_state(w2)

    inp = input_document(runtime.prepared.prepared.payload)
    inp['candidates'].reverse()
    source = runtime.prepared.prepared.registry.sources[0]
    sources = (replace(source, version=source.version + 1),
               *runtime.prepared.prepared.registry.sources[1:])
    scope = make('Scope', **{**runtime.key.scope.document(), 'context_generation': 2})
    variants = {
        'qualified_payload_order': _variant(runtime, payload=canonical(inp)),
        'source_version': _variant(runtime, registry={'sources': sources}),
        'context_generation': _variant(runtime, core={'scope': scope}),
        'prompt_digest': _variant(runtime, core={'prompt_digest': '1' * 64}),
        'schema_digest': _variant(runtime, core={'schema_digest': '2' * 64}),
        'configuration_version': _variant(runtime, configuration={'revision': 'config_2'}),
        'secret_version': _variant(runtime, configuration={'secret_version': 'version_2'}),
        'manifest_digest': _variant(runtime, manifest_digest='3' * 64),
    }
    keys = {name: AnalysisCache.key_digest(value) for name, value in variants.items()}
    assert original_key not in keys.values() and len(set(keys.values())) == len(keys)
    # Fixed protocol choices are rejected, not treated as qualified future versions.
    for record, field, value in (
        (runtime.prepared.core, 'model', 'unapproved-model'),
        (runtime.prepared.core, 'profile', 'ra-openai-responses/2'),
        (runtime.prepared.core, 'usage_mapping', 'synthetic-usage/2'),
        (runtime.prepared.core, 'rate_card', 'synthetic-rate/2'),
        (runtime.prepared.certificate, 'counting_version', 'synthetic-fixture/2'),
    ):
        with pytest.raises(PortError):
            decode(record._name, canonical({**record.document(), field: value}))
    assert (sql_state(), independent_state(w2)) == before
    assert not w2.authority.calls and not w2.witness.accepted


def test_transplanted_result_and_seal_cannot_bind_a_fresh_qualified_call(w2, tmp_path, cache_only_capabilities):
    cache, runtime, _, outcome = remembered(w2, tmp_path)
    other = w2.runtime(reserve=False)
    cache_only_capabilities()
    original_key, other_key = AnalysisCache.key_digest(runtime), AnalysisCache.key_digest(other)
    assert original_key != other_key and runtime.key.call_ref != other.key.call_ref
    cache._entries[other_key] = cache._entries[original_key]
    cache._seals[other_key] = cache._seals[original_key]
    before = sql_state(), independent_state(w2)
    with pytest.raises(PortError):
        read(cache, w2, other)
    assert other_key not in cache._entries and other_key not in cache._seals
    assert read(cache, w2, runtime).display == outcome.display
    assert (sql_state(), independent_state(w2)) == before
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1
    assert w2.witness.accepted[other.key.fingerprint()] == 0


def test_foreign_project_read_and_private_source_relabel_cannot_expose_cached_analysis(
        w2, tmp_path, cache_only_capabilities):
    cache, runtime, _, outcome = remembered(w2, tmp_path)
    cache_only_capabilities()
    foreign_scope = make('Scope', **{**runtime.key.scope.document(),
        'project_ref': 'foreign_project', 'context_ref': 'foreign_context'})
    context = make('ReadContext', **{**w2.read().document(), 'scope': foreign_scope})
    before = sql_state(), independent_state(w2)
    with pytest.raises(PortError):
        cache.read_v1(runtime, context, w2.deadline(context))
    foreign = _variant(runtime, core={'scope': foreign_scope}, registry={
        'project_ref': foreign_scope.project_ref, 'context_ref': foreign_scope.context_ref})
    foreign_key, original_key = AnalysisCache.key_digest(foreign), AnalysisCache.key_digest(runtime)
    assert foreign_key != original_key
    cache._entries[foreign_key] = cache._entries[original_key]
    cache._seals[foreign_key] = cache._seals[original_key]
    with pytest.raises(PortError):
        cache.read_v1(foreign, context, w2.deadline(context))
    assert foreign_key not in cache._entries and foreign_key not in cache._seals
    for data_class, scope in (('project_private', 'reusable_synthetic'), ('synthetic_authored', 'project')):
        original = runtime.prepared.prepared.registry
        sources = (replace(original.sources[0], data_class=data_class, scope=scope), *original.sources[1:])
        altered = _variant(runtime, registry={'sources': sources})
        with pytest.raises(PortError):
            AnalysisCache.key_digest(altered)
        with pytest.raises(PortError):
            read(cache, w2, altered)
    assert read(cache, w2, runtime).display == outcome.display
    assert (sql_state(), independent_state(w2)) == before


@pytest.mark.parametrize('malformed', ['null', 'duplicate_key', 'byte_limit', 'depth_limit', 'bool_usage'])
def test_analysis_decoder_rejects_malformed_or_unbounded_bytes(w2, malformed):
    w2.reopen()
    runtime = w2.runtime(reserve=False)
    doc = _record(runtime)
    raw = canonical(doc)
    assert len(raw) < MAX_ANALYSIS_BYTES
    assert decode_analysis(raw + b' ' * (MAX_ANALYSIS_BYTES - len(raw)), runtime, doc['key_digest'])[0] == doc
    if malformed == 'null':
        bad = b'null'
    elif malformed == 'duplicate_key':
        bad = raw[:-1] + b',"format":"ra-w3-analysis/1"}'
    elif malformed == 'byte_limit':
        bad = raw + b' ' * (MAX_ANALYSIS_BYTES + 1 - len(raw))
    elif malformed == 'depth_limit':
        nested = None
        for _ in range(10):
            nested = [nested]
        doc['proposal'] = nested
        bad = canonical(doc)
    else:
        doc['usage']['input_tokens'] = True
        bad = canonical(doc)
    before = sql_state(), independent_state(w2)
    with pytest.raises(PortError):
        decode_analysis(bad, runtime, doc['key_digest'])
    assert (sql_state(), independent_state(w2)) == before
    assert not w2.authority.calls and not w2.witness.accepted


def test_valid_alternate_w1_suggestion_cannot_replace_sealed_result(w2, tmp_path, cache_only_capabilities):
    cache, runtime, _, outcome = remembered(w2, tmp_path)
    cache_only_capabilities()
    key = AnalysisCache.key_digest(runtime)
    doc = json.loads(cache._entries[key])
    inp = input_document(runtime.prepared.prepared.payload)
    candidate = inp['candidates'][1]['ref']
    assert candidate != doc['proposal']['suggestions'][0]['candidate_ref']
    doc['proposal']['suggestions'][0]['candidate_ref'] = candidate
    altered = canonical(doc)
    _, valid_display, _ = decode_analysis(altered, runtime, key)
    assert valid_display and valid_display != outcome.display
    before = sql_state(), independent_state(w2)
    cache._entries[key] = altered
    with pytest.raises(PortError):
        read(cache, w2, runtime)
    assert key not in cache._entries and key not in cache._seals
    assert (sql_state(), independent_state(w2)) == before
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1


def test_first_seal_requires_exact_parent_observed_outcome_completion_and_receipt(
        w2, tmp_path, cache_only_capabilities):
    runtime, completion, outcome = completed(w2, tmp_path)
    assert completion.display_state == 'ELIGIBLE_NOW' and outcome.code is None
    cache_only_capabilities()
    cache = AnalysisCache()
    key = AnalysisCache.key_digest(runtime)
    doc = _record(runtime)
    doc['proposal']['suggestions'][0]['candidate_ref'] = input_document(
        runtime.prepared.prepared.payload)['candidates'][1]['ref']
    _, alternate_display, _ = decode_analysis(canonical(doc), runtime, key)
    assert alternate_display and alternate_display != outcome.display
    forged_outcome = replace(outcome, display=alternate_display)
    forged_completion = make('Completion', **{**completion.document(),
        'evaluated_at': stamp(utc(completion.evaluated_at) + timedelta(microseconds=1))})
    before = sql_state(), independent_state(w2)
    for supplied_completion, supplied_outcome in (
        (completion, forged_outcome), (forged_completion, outcome),
    ):
        with pytest.raises(PortError):
            cache.remember_v1(runtime, supplied_completion, supplied_outcome)
        assert not cache._entries and not cache._seals
        assert (sql_state(), independent_state(w2)) == before
    original_receipt = runtime._analysis_receipt
    assert original_receipt is not None
    runtime._analysis_receipt = None
    try:
        with pytest.raises(PortError):
            cache.remember_v1(runtime, completion, outcome)
        assert not cache._entries and not cache._seals
        assert (sql_state(), independent_state(w2)) == before
    finally:
        runtime._analysis_receipt = original_receipt
    assert cache.remember_v1(runtime, completion, outcome) == key
    assert read(cache, w2, runtime).display == outcome.display
    assert (sql_state(), independent_state(w2)) == before
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1
