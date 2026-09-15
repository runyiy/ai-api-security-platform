"""Synthetic task inputs through the real RA-04 interpreter, without any send."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, select, text

from app.db.session import SessionLocal, engine
from app.db.models import AuthorizationRevision, TestIdentity
from app.db.models.research_task import TABLES
from app.db.models.research_verification import VerificationContract, VerificationAudit
from app.db.models.execution_plan_approval_record import ExecutionPlanApprovalRecord
from app.db.models.execution_plan_progress import ExecutionPlanProgress
from app.db.models.execution_plan_claim import ExecutionPlanClaim
from app.db.models.execution_plan_cancellation import ExecutionPlanCancellation
from app.services import research_intent as intent, research_subject as subject, research_verification as verify
from app.services import research_task as tasks
from tests.research_intent_fixtures import intent_graph, subject_pair, call, approve, conversion, proposal, mapping, assertion
from tests.research_intake_fixtures import two_intake_targets as original_targets, NOW, REF
from tests.research_observation_fixtures import zero_capabilities


@pytest.fixture
def two_intake_targets(original_targets):
    with SessionLocal() as db:
        for g in original_targets:
            db.get(AuthorizationRevision, g['revision']).require_human_execution_approval = True
        db.commit()
    return original_targets


def confirm(g):
    expectations = [dict(role=a['role'], object_key='record_id',
        object_value='80814' if a['role'].startswith('health_') else '7001',
        identity_key='subject_id', identity_value='fixture_p' if a['role'] in g.get('bearer_roles', ()) else None)
        for a in g['manifest_input']['actions']]
    return call(verify.confirm, g, 1, dict(manifest=g['manifest'], expected_version=0,
        decision='confirm', expectations=expectations, evidence=REF))


@pytest.fixture
def task_graph(intent_graph, request, monkeypatch):
    g = intent_graph
    health = getattr(request, 'param', None) == 'health'
    if health:
        from app.db.models import Resource
        from app.db.models.resource_access_assertion import ResourceAccessAssertion
        with SessionLocal() as db:
            resource = Resource(target_id=g['target'], resource_type='project', external_id='80814', owner_identity_id=g['bearer'])
            db.add(resource)
            db.flush()
            rid = resource.id
            db.add(ResourceAccessAssertion(resource_id=rid, test_identity_id=g['bearer'], relationship='owner',
                expected_access='allowed', provenance='target_fixture', confidence=100,
                verification_state='verified', asserted_at=NOW))
            db.commit()
        g['extra_resources'] = [rid]
        p = proposal(g)
        p.update(resource_id=rid, identity_choice='bearer', test_identity_id=g['bearer'],
            credential_binding_id=g['credential'], session_state='unknown', credential_update='unknown')
        call(subject.record, g, 3, p)
        p = mapping(g)
        p['resource_id'] = rid
        health_mapping = call(intent.confirm_mapping, g, 2, p)['reference']
        p = deepcopy(g['manifest_input'])
        p['actions'].append(dict(role='health_probe', subject=dict(number=3, version=1), mapping=health_mapping))
        g['bearer_roles'] = {'probe', 'health_probe'}
    else:
        # Two independent anonymous actors need no invented health receipt.
        with SessionLocal() as db:
            db.get(TestIdentity, g['bearer']).auth_type = 'anonymous'
            db.commit()
        p = proposal(g)
        p['test_identity_id'] = g['bearer']
        call(subject.record, g, 2, dict(expected_version=1, correction_reference=REF, proposal=p), correction=True)
        p = deepcopy(g['manifest_input'])
        p['actions'][1]['subject']['version'] = 2
    p['expected_version'] = g['manifest']['version']
    g['manifest_input'] = p
    g['manifest'] = call(intent.record_manifest, g, 1, p)['reference']
    approve(g)
    confirm(g)
    value = call(intent.convert, g, 1, conversion(g, purpose='health_probe' if health else 'business'))
    g['intent'] = value
    g['selection'] = [dict(intent=value['reference'], plan_id=m['plan_id'], plan_digest=m['plan_digest'],
                           role='health_probe' if m['role'] == 'health' else m['role']) for m in value['body']['link']['members']]
    try:
        yield g
    finally:
        with SessionLocal() as db:
            # The entire task domain belongs exclusively to these serial TEST
            # fixtures. Production has no delete, truncate or release operation.
            db.execute(text('TRUNCATE '+', '.join(m.__tablename__ for m in reversed(TABLES))))
            plans = [m['plan_id'] for m in g['selection']]
            for model in (ExecutionPlanCancellation, ExecutionPlanProgress, ExecutionPlanClaim, ExecutionPlanApprovalRecord):
                db.execute(delete(model).where(model.execution_plan_id.in_(plans)))
            for model in (VerificationContract, VerificationAudit):
                db.execute(delete(model).where(model.context_id == g['ctx']))
            db.commit()


def creation(g, **changes):
    value = dict(previous=None, expected_sequence=0, context_version=1, target_id=g['target'],
        authorization_revision_id=g['revision'], members=deepcopy(g['selection']), evidence=REF,
        limits=dict(target_requests=10, duration_seconds=60, concurrency=1,
                    rate_millirequests_per_second=500, ai_calls=0, ai_tokens=0, ai_cost_microusd=0))
    value.update(changes)
    return value


def decision(view, kind='approved'):
    return dict(task=view['reference'], expected_sequence=view['sequence'], decision=kind, evidence=REF)


def transition(view, action):
    return dict(task=view['reference'], expected_sequence=view['sequence'], action=action, evidence=REF)


def create(g, **changes):
    return call(tasks.create_version, g, 1, creation(g, **changes))
