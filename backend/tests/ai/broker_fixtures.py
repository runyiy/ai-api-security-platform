"""Independently authored metadata/TLS fixtures, never live authorization."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4
import json
import struct

from app.ai.proposals.adapter import request_body
from app.ai.proposals.bindings import MODEL, PROFILE, RATE_CARD, USAGE_MAPPING
from app.ai.proposals.codec import canonical
from app.ai.w2.preparation import PROMPT_DIGEST, SCHEMA_DIGEST
from app.ai.w2.records import make, stamp
from app.ai.w3.broker.protocol import digest


def metadata():
    uid = uuid4().hex
    now = datetime.now(timezone.utc)
    start, end = stamp(now - timedelta(seconds=1)), stamp(now + timedelta(seconds=30))
    scope = make('Scope', task_id='task_' + uid, case_id='case', project_ref='project', context_ref='context',
        context_version=1, context_generation=1, account_ref='account', deployment_ref='deployment_' + uid,
        policy_id='policy', policy_version=1)
    call = 'q_' + uid
    payload = dict(protocol='ra-ai-proposal-input/1', request_ref=call, task='prioritize_review', candidates=[
        dict(ref='c_' + '1'*32, shape='single_resource_path_get_json_object', actor_mode='anonymous'),
        dict(ref='c_' + '2'*32, shape='single_resource_path_get_json_object', actor_mode='bearer')], rules=[], gaps=[])
    body = request_body(payload)
    core = make('BindingCore', scope=scope, question_id='Q4', question_digest='1'*64, call_ref=call, attempt=1,
        w1_binding_digest='2'*64, payload_digest=sha256(canonical(payload)).hexdigest(), body_digest=sha256(body).hexdigest(),
        registry_digest='3'*64, configuration_digest='4'*64, config_revision='config', secret_ref='fixture_ref',
        secret_version='fixture_version', necessity_version='ra-w2-necessity/1', retrieval_version='ra-w2-retrieval/1',
        projections=[], candidates=[], prompt_digest=PROMPT_DIGEST, schema_digest=SCHEMA_DIGEST, model=MODEL,
        profile=PROFILE, destination='https://api.openai.com:443/v1/responses', method='POST', reasoning='low',
        input_limit=4096, output_limit=1024, reserved_tokens=5120, reserved_microusd=22528, rate_card=RATE_CARD,
        usage_mapping=USAGE_MAPPING, currency='USD', token_bound_evidence_ref='synthetic_fixture_only',
        measurement_kind='synthetic', valid_from=start, expires_at=end)
    key = make('ReservationKey', scope=scope, call_ref=call, attempt=1, core_digest=core.fingerprint())
    run = make('RunContext', scope=scope, call_ref=call, attempt=1, owner_id='controller', owner_generation=1,
        process_epoch='test', started_at=stamp(now), deadline_at=end, mono_start_ns=1, mono_deadline_ns=30_000_000_001,
        cancellation_id='cancel')
    reservation = make('Reservation', key=key, reservation_id='reserved', owner_id='controller', owner_generation=1,
        input_limit=4096, output_limit=1024, reserved_tokens=5120, reserved_microusd=22528, currency='USD',
        rate_card=RATE_CARD, usage_mapping=USAGE_MAPPING, expires_at=end, measurement_kind='synthetic')
    admission = make('Admission', key=key, admission_id='admitted', owner_generation=1, admitted_at=stamp(now), expires_at=end)
    binding = dict(key=key.document(), core=core.document(), run=run.document(), reservation=reservation.document(), manifest_digest='5'*64)
    return SimpleNamespace(**locals())


def response(*, terminal='completed', usage=True, refusal=False):
    value = dict(object='response', id='resp_owned_synthetic', model=MODEL, status=terminal,
        output=[dict(type='message', role='assistant', status='completed', id='msg_synthetic',
                     content=[dict(type='refusal', refusal='synthetic refusal')] if refusal else [])])
    if usage:
        value['usage'] = dict(input_tokens=2048, output_tokens=384, total_tokens=2432,
            input_tokens_details=dict(cached_tokens=0, cache_write_tokens=0), output_tokens_details=dict(reasoning_tokens=128))
    raw = canonical(value)
    return b'HTTP/1.1 200 Synthetic\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(raw)).encode() + b'\r\n\r\n' + raw


def arm(client, data):
    client.call('bootstrap', dict(deployment=data.scope.deployment_ref, sql_empty=digest([])))
    client.call('bind', data.binding)
    client.call('prepare', dict(body=data.body.decode()))
    ticket = client.begin_acceptance_v1(data.run, data.admission, data.core.body_digest)
    return client.issue(data.run, data.admission, ticket, 'committed_synthetic_marker', 'g_session')


def invalidation(data):
    context = make('InvalidationContext', deployment_ref=data.scope.deployment_ref, writer_id='synthetic_writer',
        process_epoch='test', deadline_at=data.end, mono_deadline_ns=30_000_000_001, cancellation_id='cancel')
    request = make('InvalidationRequest', operation_id=uuid4().hex, operation_digest='6'*64,
        deployment_ref=data.scope.deployment_ref, writer_id='synthetic_writer', action='INVALIDATE_DEPLOYMENT',
        reason='LIFECYCLE', deadline_at=data.end)
    return context, request


def frames(path):
    raw = path.read_bytes()
    result, offset = [], 0
    while offset < len(raw):
        length = struct.unpack('!I', raw[offset:offset+4])[0]
        result.append(json.loads(raw[offset+4:offset+4+length])); offset += 4 + length
    assert offset == len(raw)
    return result
