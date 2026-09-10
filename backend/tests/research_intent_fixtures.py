"""Independent synthetic W1 graphs; future W2 proof exists only in monkeypatched tests."""
from datetime import timedelta
from copy import deepcopy
import pytest
from sqlalchemy import select, delete
from app.db.session import SessionLocal
from app.db.models import TestCase, PlanAction, ExecutionPlan
from app.db.models.safety_decision_record import SafetyDecisionRecord
from app.db.models.credential_binding import CredentialBinding
from app.db.models.credential_secret_version import CredentialSecretVersion
from app.db.models.research_intent import (IntentMapping,IntentManifest,IntentBudgetDecision,IntentVersion,IntentPlanMember,IntentAudit)
from app.services import research_intent as service, research_subject as subject
from app.schemas import research_intent as s
from tests.research_subject_fixtures import subject_pair,two_intake_targets,proposal,assertion,NOW,REF  # noqa: F401


def call(fn,g,*args,**kwargs):
    with SessionLocal() as db:
        result=fn(db,g['project'],g['ctx'],*args,now=kwargs.pop('now',NOW),**kwargs)
        db.commit();return result


def mapping(g,**changes):
    return dict(context_version=1,target_id=g['target'],endpoint_id=g['endpoint'],binding_id=g['slot'],resource_id=g['resource'],expected_version=0,decision='confirm',evidence=REF,**changes)


def conversion(g,**changes):
    value=dict(manifest=g['manifest'],expected_version=0,purpose='business',knowledge=None,evidence=REF)
    value.update(changes);return value


def future_proof(db,context,snapshot,purpose,clock):
    # No runtime producer/registration. Explicit future-qualified test envelope;
    # never evidence obtained from a deployment or a caller-provided passed flag.
    health=[]
    if purpose=='business':
        for a in snapshot['actions'][:2]:
            actor=a['actor']
            if actor['auth_type']!='bearer':continue
            health.append({k:actor[k] for k in ('identity_id','credential_binding_id','credential_version_id')}|dict(
                context_id=context.id,target_id=snapshot['target_id'],evidence_id=3107,digest='e'*64,
                send_at=s.stamp(NOW),complete_at=s.stamp(NOW),verified_at=s.stamp(NOW),valid_until=s.stamp(NOW+timedelta(seconds=120))))
    return dict(interpreter=dict(id=3101,version=1,digest='d'*64),health=health)


@pytest.fixture
def qualified_future(monkeypatch):
    monkeypatch.setattr(service,'_interpretation',future_proof)


@pytest.fixture
def intent_graph(subject_pair):
    g=subject_pair[0]
    with SessionLocal() as db:
        from app.db.models.endpoint import Endpoint
        from app.db.models.endpoint_resource_binding import EndpointResourceBinding
        from app.db.models.resource import Resource
        db.get(Resource,g['resource']).resource_type='project'
        db.get(Endpoint,g['endpoint']).path='/folders/{project_id}'
        db.get(EndpointResourceBinding,g['slot']).selector='project_id'
        b=CredentialBinding(test_identity_id=g['bearer'],auth_type='bearer',source_type='stored_secret',is_active=True)
        db.add(b);db.flush()
        v=CredentialSecretVersion(credential_binding_id=b.id,encrypted_envelope='NOT_A_CREDENTIAL',envelope_version=1,key_version='metadata-only-test')
        db.add(v);db.commit();g['credential']=b.id;g['secret_version']=v.id
    assertion(g,relationship='non_owner',access='allowed')
    assertion(g,relationship='owner',access='denied',identity=g['bearer'])
    p=proposal(g);call(subject.record,g,1,p)
    p.update(test_identity_id=g['bearer'],identity_choice='bearer',credential_binding_id=g['credential'],session_state='unknown',credential_update='unknown')
    call(subject.record,g,2,p)
    try:
        g['mapping']=call(service.confirm_mapping,g,1,mapping(g))['reference']
        manifest=dict(context_version=1,target_id=g['target'],expected_version=0,
            actions=[dict(role=role,subject=dict(number=i,version=1),mapping=g['mapping']) for i,role in ((1,'baseline'),(2,'probe'))],
            duration_seconds=60,rate_millirequests_per_second=500,concurrency=1,evidence=REF)
        g['manifest_input']=deepcopy(manifest)
        g['manifest']=call(service.record_manifest,g,1,manifest)['reference']
        yield g
    finally:
        with SessionLocal() as db:
            ids=list(db.scalars(select(IntentVersion.id).where(IntentVersion.context_id==g['ctx'])))
            members=list(db.scalars(select(IntentPlanMember).where(IntentPlanMember.intent_id.in_(ids))))
            plans=[r.plan_id for r in members];cases=[r.test_case_id for r in members]
            db.execute(delete(IntentPlanMember).where(IntentPlanMember.intent_id.in_(ids)))
            db.execute(delete(IntentVersion).where(IntentVersion.context_id==g['ctx']))
            db.execute(delete(SafetyDecisionRecord).where(SafetyDecisionRecord.execution_plan_id.in_(plans)))
            db.execute(delete(PlanAction).where(PlanAction.execution_plan_id.in_(plans)))
            db.execute(delete(ExecutionPlan).where(ExecutionPlan.id.in_(plans)))
            db.execute(delete(TestCase).where(TestCase.id.in_(cases)))
            from app.db.models import Resource
            from app.db.models.resource_access_assertion import ResourceAccessAssertion
            extra=g.get('extra_resources',[])
            db.execute(delete(ResourceAccessAssertion).where(ResourceAccessAssertion.resource_id.in_(extra)))
            db.execute(delete(Resource).where(Resource.id.in_(extra)))
            mids=list(db.scalars(select(IntentManifest.id).where(IntentManifest.context_id==g['ctx'])))
            db.execute(delete(IntentBudgetDecision).where(IntentBudgetDecision.manifest_id.in_(mids)))
            db.execute(delete(IntentManifest).where(IntentManifest.context_id==g['ctx']))
            db.execute(delete(IntentMapping).where(IntentMapping.context_id==g['ctx']))
            db.execute(delete(IntentAudit).where(IntentAudit.context_id==g['ctx']))
            db.commit()


def approve(g):
    return call(service.decide_budget,g,dict(manifest=g['manifest'],expected_sequence=0,decision='approved',evidence=REF))
