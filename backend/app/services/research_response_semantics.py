"""Finite scalar interpreter. Response text is data, never an instruction/oracle."""
import hashlib
from app.schemas import research_intent as codec
from app.schemas.research_verification import Expectation

DEFINITION = {
    'protocol': 'ra-response-scalar/1', 'version': 1,
    'max_bytes': 16384, 'max_depth': 4, 'max_nodes': 256,
    'object_status': 200, 'denial_status': 403,
    'authenticated': True, 'denial_error': 'access_denied',
    'unknown_fields': 'inconclusive', 'malformed': 'inconclusive',
    'media_types':['application/json','application/json;charset=utf-8','application/json;charset="utf-8"'],
    'purposes':['business','health'],
}
DEFINITION_DIGEST = codec.digest('ra-response-definition/1', DEFINITION)


def _bounded(value, depth=0, nodes=None):
    nodes = [0] if nodes is None else nodes
    nodes[0] += 1
    if depth > 4 or nodes[0] > 256:
        raise ValueError('structure')
    if type(value) is dict:
        for key, item in value.items():
            _bounded(key, depth+1, nodes)
            _bounded(item, depth+1, nodes)
    elif type(value) is list:
        for item in value:
            _bounded(item, depth+1, nodes)


def interpret(*, status, body, expectation, auth_type, purpose, content_type, content_encoding):
    """Only minimized fixed results survive; not even unknown keys are retained."""
    result = {'interpreter': DEFINITION_DIGEST, 'outcome': 'inconclusive',
              'reason': 'response_unavailable', 'response_sha256': None, 'response_bytes': 0}
    if type(body) is not bytes:
        return result
    if purpose not in ('business','health'):
        return {**result,'reason':'purpose_unsupported'}
    result.update(response_sha256=hashlib.sha256(body).hexdigest(), response_bytes=len(body))
    if len(body) > DEFINITION['max_bytes']:
        return {**result, 'reason': 'response_limit'}
    media=content_type.lower().replace(' ','') if type(content_type) is str else None
    if (media not in ('application/json','application/json;charset=utf-8','application/json;charset="utf-8"')
            or content_encoding not in (None, '', 'identity')):
        return {**result, 'reason': 'response_representation'}
    try:
        exp = codec.validate(Expectation, expectation)
        value = codec.parse(body)
        _bounded(value)
    except (codec.IntentError, ValueError):
        return {**result, 'reason': 'response_malformed'}
    if type(value) is not dict:
        return {**result, 'reason': 'object_missing'}
    if auth_type not in ('anonymous', 'bearer') or (auth_type == 'bearer') != (exp.identity_value is not None):
        return {**result, 'reason': 'identity_unqualified'}
    obj = {exp.object_key}
    identity = {exp.identity_key, 'authenticated'}
    allowed = obj | (identity if auth_type == 'bearer' else set())
    if 'error' in value:
        allowed.add('error')
    if not set(value) <= allowed:
        return {**result, 'reason': 'response_unknown_fields'}
    if type(value.get(exp.object_key)) is not str or value[exp.object_key] != exp.object_value:
        return {**result, 'reason': 'object_mismatch'}
    needs_identity = auth_type == 'bearer' and (purpose == 'health' or 'error' in value or bool(identity & set(value)))
    if needs_identity and (value.get('authenticated') is not True
            or type(value.get(exp.identity_key)) is not str or value[exp.identity_key] != exp.identity_value):
        return {**result, 'reason': 'identity_mismatch'}
    if purpose == 'health':
        if auth_type == 'bearer' and status == 200 and set(value) == obj | identity:
            return {**result, 'outcome': 'healthy', 'reason': 'identity_and_health_object_verified'}
        return {**result, 'reason': 'health_unqualified'}
    if status == 200 and 'error' not in value:
        return {**result, 'outcome': 'object_read', 'reason': 'exact_object_verified'}
    if status == 403 and value.get('error') == 'access_denied':
        return {**result, 'outcome': 'business_denied', 'reason': 'explicit_business_denial'}
    return {**result, 'reason': 'authentication_or_transport_uncertain'}


def pair_outcome(expected, baseline, probe):
    if expected not in ('allowed', 'denied'):
        return 'inconclusive', 'access_facts_unqualified'
    if baseline != 'object_read':
        return 'inconclusive', 'baseline_unqualified'
    if probe == 'object_read':
        return ('allowed', 'expected_access_observed') if expected == 'allowed' else ('suspected_violation', 'denied_object_read')
    if probe == 'business_denied':
        return ('expected_denial', 'qualified_business_denial') if expected == 'denied' else ('inconclusive', 'allowed_access_not_observed')
    return 'inconclusive', 'probe_unqualified'
