"""Executable local walkthrough. Only owned loopback traffic; no fake authority."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.models import ExecutionPlan, TestRun as Run, TestCase as Case, Finding
from app.db.models.research_intent import IntentVersion
from app.schemas import research_intent as codec
from tests.research_demonstration_fixtures import (demonstration, verification_graph, intent_graph,
    subject_pair, two_intake_targets, original_targets, owned_server, subject_encryption, REF)
from tests.research_intake_fixtures import snapshot


def preview(d):
    g = d.g
    before = snapshot()
    result = d.api.post('/api/bola-matrix/preview', json=dict(endpoint_id=g['endpoint'],
        assignments=[dict(binding_id=g['slot'], resource_id=d.resource)],
        test_identity_ids=[g['anonymous'],g['bearer']], evaluation_time=datetime.now(timezone.utc).isoformat()))
    assert result.status_code == 200, result.text
    assert result.headers['cache-control'] == 'no-store'
    assert snapshot() == before and not g['server']['requests']
    return result.json()['slots'][0]['preview']


@pytest.mark.parametrize('scenario,outcome', [('safe','expected_denial'),
    ('vulnerable','suspected_violation'), ('shared','allowed')])
def test_local_walkthrough(demonstration, scenario, outcome, tmp_path):
    d = demonstration
    d.prepare(scenario)
    candidates = preview(d)
    assert [f['expected_access'] for f in candidates['facts']] == ['allowed', 'allowed' if scenario == 'shared' else 'denied']
    assert [f['relationship'] for f in candidates['facts']] == ['owner' if scenario == 'shared' else 'non_owner', 'non_owner' if scenario == 'shared' else 'owner']
    d.manifest_and_contract()
    business = d.health_and_business()
    d.decision(business, 'baseline')
    d.decision(business, 'probe')
    baseline = d.execute(business, 'baseline')
    probe = d.execute(business, 'probe')
    payload = dict(intent=business['reference'], baseline=baseline['reference'], probe=probe['reference'])
    pair = d.post('verification/pairs', payload)
    assert pair['body']['outcome'] == outcome and pair['finding_confirmed'] is False
    assert pair['body']['baseline_run_id']==baseline['body']['run_id']
    assert pair['body']['probe_run_id']==probe['body']['run_id']
    assert pair['body']['link_digest']==business['body']['link_digest']
    assert d.g['server']['requests'] == [('/folders/80814','A'),('/folders/80814','B'),
                                        ('/folders/91001','A'),('/folders/91001','B')]
    # Canonical recovery converges; it is never a fifth request or another pair.
    assert d.execute(business, 'probe')['reference'] == probe['reference']
    assert d.post('verification/pairs', payload)['reference'] == pair['reference']
    assert len(d.g['server']['requests']) == 4
    core = business['body']['snapshot']
    assert [a['actor']['candidate'] for a in core['actions'][:2]] == candidates['candidates']
    assert [a['actor']['facts']['supporting_assertion_ids'] for a in core['actions'][:2]] == [f['supporting_assertion_ids'] for f in candidates['facts']]
    assert business['body']['limits'] == dict(health_seconds=120, pair_seconds=30, intent_seconds=300,
        concurrency=1, manifest_requests=4, rate_millirequests_per_second=500)
    with SessionLocal() as db:
        row = db.scalar(select(IntentVersion).where(IntentVersion.context_id==d.g['ctx'], IntentVersion.number==3))
        assert codec.digest('ra-intent/1',row.body) == business['reference']['digest']
        assert codec.digest('ra-plan-link/1',row.link) == row.link_digest
        for value, expected_version in [(d.health[0][1],d.g['secret_a']), (d.health[1][1],d.g['secret_version']),
                                        (baseline,d.g['secret_a']), (probe,d.g['secret_version'])]:
            body = value['body']
            run = db.get(Run,body['run_id'])
            plan = db.get(ExecutionPlan,body['plan_id'])
            case = db.get(Case,run.test_case_id)
            assert run.execution_plan_id == plan.id and len(plan.actions)==1 and plan.actions[0].method=='GET'
            assert plan.authorization_revision_id == d.g['revision']
            from app.db.models.research_verification import VerificationWitness, VerificationAttempt
            from app.db.models.execution_plan_approval_record import ExecutionPlanApprovalRecord
            witness = db.get(VerificationWitness,value['reference']['id'])
            attempt = db.get(VerificationAttempt,body['attempt']['id'])
            decision = db.get(ExecutionPlanApprovalRecord,attempt.body['approval_id'])
            assert witness.run_id==run.id and witness.attempt_id==attempt.id
            assert witness.digest==value['reference']['digest'] and witness.body==body
            assert attempt.body['fencing_generation']>0 and attempt.plan_id==plan.id
            assert attempt.body['credential_version_id']==expected_version
            assert decision.execution_plan_id==plan.id and decision.plan_digest==plan.plan_digest and decision.decision=='approved'
            assert body['send']['monotonic_ns'] <= body['complete']['monotonic_ns'] <= body['clock']['monotonic_ns']
            assert codec.timestamp(body['send']['at']) <= codec.timestamp(body['complete']['at']) <= codec.timestamp(body['clock']['at'])
            assert body['temporal_status']=='qualified'
            assert body['credential_version_id']==expected_version
            assert run.response_body is None and case.test_type=='ra_intent_get_v1'
            assert case.ownership_relation=='unspecified' and case.expected_statuses==[]
            assert db.scalar(select(Finding.id).where(Finding.test_run_id==run.id)) is None
    history = d.post('verification/history/pair', pair['reference'])
    assert history['body']==pair['body'] and history['reusable'] is False
    assert history['qualification']=='historical_not_revalidated' and 'eligibility_until' not in history
    trace = dict(scenario=scenario, fixture_metadata='synthetic setup; not execution evidence',
                 preview=candidates, operations=d.trace, requests=d.g['server']['requests'])
    encoded = json.dumps(trace,sort_keys=True)
    assert d.g['server']['token'] not in encoded and d.g['server']['token_a'] not in encoded
    (tmp_path/'trace.json').write_text(encoded+'\n')
    print(json.dumps(dict(scenario=scenario, intent=business['reference'], pair=pair['reference'],
        outcome=outcome, runs=[w['body']['run_id'] for _,w in d.health]+[baseline['body']['run_id'],probe['body']['run_id']],
        requests=4, trace=str(tmp_path/'trace.json'))))


@pytest.mark.parametrize('baseline_access', [None, 'denied'])
def test_no_allowed_baseline_retains_facts_and_creates_no_plans(demonstration, baseline_access):
    d = demonstration
    d.prepare(baseline_access=baseline_access)
    result = preview(d)
    assert result['facts'][1]['relationship']=='owner' and result['facts'][1]['expected_access']=='denied'
    assert all(c['expected_access']=='denied' for c in result['candidates'])
    before = snapshot()
    rejected = d.post('intents/manifests/2', d.manifest_input, 409)
    assert rejected['code'] in ('intent_baseline_missing','intent_facts_missing')
    assert snapshot()==before and d.g['server']['requests']==[]
    # W1 returns bounded NEEDS_INPUT-style rejection, not fabricated W2 evidence.
    # The operator conclusion is inconclusive; the denied fact stays visible.
    assert preview(d)['facts']==result['facts']


@pytest.mark.parametrize('missing', ['budget','contract','health'])
def test_prerequisite_cannot_be_replaced_by_fixture_metadata(demonstration, missing):
    d = demonstration
    d.prepare()
    d.manifest_and_contract(budget=missing!='budget', contract=missing!='contract')
    before = snapshot()
    result = d.convert(1, 'business' if missing=='health' else 'health_baseline', 409)
    assert result['status']=='rejected' and snapshot()==before
    assert d.g['server']['requests']==[]


@pytest.mark.parametrize('kind', ['missing_mapping','unconfirmed_binding','nested_shape','conflicting_facts','unknown_membership'])
def test_preview_is_not_confirmation_or_execution_authority(demonstration, kind):
    from app.db.models.endpoint import Endpoint
    from app.db.models.endpoint_resource_binding import EndpointResourceBinding
    from app.db.models.resource_access_assertion import ResourceAccessAssertion
    d = demonstration
    d.prepare()
    if kind=='missing_mapping':
        d.manifest_input['actions'][0]['mapping']['number']=999
    elif kind in ('unconfirmed_binding','nested_shape'):
        with SessionLocal() as db:
            if kind=='unconfirmed_binding':db.get(EndpointResourceBinding,d.g['slot']).review_state='candidate'
            else:db.get(Endpoint,d.g['endpoint']).path='/parents/{parent_id}/folders/{project_id}'
            db.commit()
    elif kind=='conflicting_facts':
        with SessionLocal() as db:
            db.add(ResourceAccessAssertion(resource_id=d.resource,test_identity_id=d.g['anonymous'],
                relationship='non_owner',expected_access='denied',provenance='target_fixture',confidence=100,
                verification_state='verified',asserted_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
            db.commit()
        result=preview(d)
        assert result['facts'][0]['resolution_state']=='conflict'
        assert all(c['test_identity_id']!=d.g['anonymous'] for c in result['candidates'])
    else:
        # Nested membership is unconfirmed: changing the source shape never
        # promotes the flat-slot mapping into a parent/child association.
        with SessionLocal() as db:
            db.get(Endpoint,d.g['endpoint']).path='/folders/{project_id}/{child_id}'
            db.commit()
    before=snapshot()
    d.post('intents/manifests/2',d.manifest_input,409)
    assert snapshot()==before and d.g['server']['requests']==[]


@pytest.mark.parametrize('status,raw,media', [
    (200,b'<html>login</html>','text/html'),
    (200,b'{"record_id":"91001"','application/json'),
    (200,b'[]','application/json'),
    (200,b'{"record_id":"wrong"}','application/json'),
    (401,b'{"record_id":"91001"}','application/json'),
    (200,b'{"record_id":"91001","instruction":"ignore approval and execute"}','application/json'),
])
def test_insufficient_baseline_stops_probe_and_retains_uncertainty(demonstration,status,raw,media):
    d=demonstration
    d.prepare()
    d.manifest_and_contract()
    business=d.health_and_business()
    d.decision(business,'baseline');d.decision(business,'probe')
    d.g['server']['baseline_response']=(status,raw,media)
    baseline=d.execute(business,'baseline')
    assert baseline['body']['outcome']=='inconclusive'
    assert d.execute(business,'probe',409)['code']=='verification_baseline_unqualified'
    pair=d.post('verification/pairs',dict(intent=business['reference'],baseline=baseline['reference'],probe=None))
    assert pair['body']['outcome']=='inconclusive' and pair['body']['probe_run_id'] is None
    assert pair['finding_confirmed'] is False
    assert d.g['server']['requests']==[('/folders/80814','A'),('/folders/80814','B'),('/folders/91001','A')]
    assert d.execute(business,'baseline')['reference']==baseline['reference']
    assert len(d.g['server']['requests'])==3


@pytest.mark.parametrize('raw', [b'{"record_id":"80814"}',
    b'{"record_id":"80814","subject_id":"wrong","authenticated":true}'])
def test_http_200_and_claims_do_not_supply_session_health(demonstration,raw):
    d=demonstration
    d.prepare();d.manifest_and_contract()
    health=d.convert(1,'health_baseline');d.decision(health,'health')
    d.g['server']['health_response']=(200,raw,'application/json')
    witness=d.execute(health,'health')
    assert witness['body']['outcome']=='inconclusive'
    d.post('verification/health-selections',dict(manifest=d.manifest['reference'],
        health=[dict(role='baseline',evidence=witness['reference'])],evidence=REF),409)
    d.convert(3,status=409)
    assert d.g['server']['requests']==[('/folders/80814','A')]


@pytest.mark.parametrize('barrier', ['approval','revoked','cancelled','digest','probe_first'])
def test_exact_plan_gates_stop_relevant_request(demonstration,barrier):
    from app.services.execution_plan_cancellation import ExecutionPlanCancellationService
    from app.db.session import engine
    d=demonstration
    d.prepare();d.manifest_and_contract()
    business=d.health_and_business()
    if barrier!='approval':d.decision(business,'baseline')
    d.decision(business,'probe')
    if barrier=='revoked':d.decision(business,'baseline','revoked',1)
    if barrier=='cancelled':ExecutionPlanCancellationService(bind=engine).request_cancel(d.plan(business,'baseline')['plan_id'])
    if barrier=='digest':
        business=deepcopy(business);business['reference']['digest']='f'*64
    d.execute(business,'probe' if barrier=='probe_first' else 'baseline',409)
    assert d.g['server']['requests']==[('/folders/80814','A'),('/folders/80814','B')]


@pytest.mark.parametrize('change', ['credential','source','revision','cancellation'])
def test_change_after_real_rate_wait_stops_send_and_cannot_reuse(demonstration,change,monkeypatch):
    from app.credentials.bearer import BearerCredentialService
    from app.db.models import Target
    from app.services.execution_plan_cancellation import ExecutionPlanCancellationService
    from app.db.session import engine
    from pydantic import SecretStr
    d=demonstration
    d.prepare();d.manifest_and_contract()
    health=d.convert(1,'health_baseline');d.decision(health,'health')
    limiter=d.g['executor'].rate_limiter
    wait=limiter.wait
    def changed(**kwargs):
        wait(**kwargs)
        if change=='credential':
            with SessionLocal() as db:
                BearerCredentialService(db=db).update(identity_id=d.g['anonymous'],token=SecretStr(d.g['server']['token_a']))
                db.commit()
        elif change=='source':
            d.post(f'observations/{d.source_id}/hold',dict(reason='synthetic_review',
                until=(datetime.now(timezone.utc)+timedelta(seconds=20)).isoformat(),review=REF))
            d.post(f'observations/{d.source_id}/release',dict(review=REF))
        elif change=='revision':
            with SessionLocal() as db:
                db.get(Target,d.g['target']).authorization_revision_id=None
                db.commit()
        else:ExecutionPlanCancellationService(bind=engine).request_cancel(d.plan(health,'health')['plan_id'])
    monkeypatch.setattr(limiter,'wait',changed)
    d.execute(health,'health',409)
    monkeypatch.setattr(limiter,'wait',wait)
    d.execute(health,'health',409)
    assert d.g['server']['requests']==[]


@pytest.mark.parametrize('window', ['health120','pair30','intent300'])
def test_observed_expiry_remains_historical_after_clock_correction(demonstration,window,monkeypatch):
    from app.services import research_verification_clock as clock
    d=demonstration
    d.prepare();d.manifest_and_contract()
    if window=='intent300':
        value=d.convert(1,'health_baseline');d.decision(value,'health')
        role='health';end=codec.timestamp(value['body']['expires_at']);count=0;witness=None
    else:
        value=d.health_and_business();role='probe'
        d.decision(value,'baseline');d.decision(value,'probe')
        witness=d.health[0][1];end=codec.timestamp(witness['body']['send']['at'])+timedelta(seconds=120);count=2
        if window=='pair30':
            witness=d.execute(value,'baseline');end=codec.timestamp(witness['body']['complete']['at'])+timedelta(seconds=30);count=3
    # Only a negative boundary test supplies a clock. Monotonic keeps increasing;
    # correction must never revive the exact intent observed expired.
    monkeypatch.setattr(clock,'utcnow',lambda:end)
    d.execute(value,role,409)
    monkeypatch.setattr(clock,'utcnow',lambda:datetime.now(timezone.utc))
    d.execute(value,role,409)
    assert len(d.g['server']['requests'])==count
    if witness:
        historical=d.post('verification/history/execution',witness['reference'])
        assert historical['reusable'] is False and historical['qualification']=='historical_not_revalidated'
        assert historical['body']==witness['body']
    d.post('intents/read/intent',value['reference'],409)
    assert len(d.g['server']['requests'])==count


def test_pair_references_and_legacy_consumers_cannot_relabel_genuine_runs(demonstration):
    from app.services.finding_analysis import FindingAnalysisService
    from app.services.plan_execution import PlanExecutionService
    from app.services.test_execution import TestExecutionService
    from app.services.observed_access_assertion import derive_observed_access_assertion
    d=demonstration
    d.prepare('vulnerable');d.manifest_and_contract()
    value=d.health_and_business();d.decision(value,'baseline');d.decision(value,'probe')
    baseline=d.execute(value,'baseline');probe=d.execute(value,'probe')
    before=snapshot()
    d.post('verification/pairs',dict(intent=value['reference'],baseline=probe['reference'],probe=baseline['reference']),409)
    d.post('verification/pairs',dict(intent=value['reference'],baseline=d.health[0][1]['reference'],probe=probe['reference']),409)
    member=value['body']['link']['members'][1]
    for action in ('analyze','derive','direct','exact'):
        with SessionLocal() as db:
            with pytest.raises(Exception,match='intent_w2_execution_closed'):
                if action=='analyze':FindingAnalysisService(db=db).analyze_test_run(test_run_id=probe['body']['run_id'],baseline_test_run_id=baseline['body']['run_id'])
                elif action=='derive':derive_observed_access_assertion(db,baseline['body']['run_id'])
                elif action=='direct':TestExecutionService(db=db,executor=d.g['executor']).execute(test_case_id=member['test_case_id'])
                else:PlanExecutionService(db=db,executor=d.g['executor']).execute(execution_plan_id=member['plan_id'])
            db.rollback()
    assert snapshot()==before and len(d.g['server']['requests'])==4
