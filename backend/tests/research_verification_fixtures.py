"""Independent synthetic W2 server; real gateway, encrypted ephemeral token and M8."""
from copy import deepcopy
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import json
import secrets
import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select
from app.db.session import SessionLocal, engine, network_coordination_engine
from app.db.models import Target, Resource, AuthorizationRevision, TestRun
from app.db.models.credential_secret_version import CredentialSecretVersion
from app.db.models.research_intent import IntentPlanMember, IntentVersion
from app.db.models.research_verification import (VerificationContract, VerificationHealthSelection,
    VerificationAttempt, VerificationWitness, VerificationPair, VerificationAudit, VerificationClockFault)
from app.credentials.stored_secret import StoredSecretCipher
from app.core.config import settings
from app.services import research_intent as intent, research_subject as subject, research_verification as verify
from app.services import research_verification_clock as vc
from app.services.research_verification_dispatch import dispatch
from app.executors.http import PolicyEnforcedHTTPExecutor
from app.executors.rate_limit import PostgresRateLimiter
from app.network_safety.gateway import NetworkGateway
from app.network_safety.postgres_controller import PostgresNetworkExecutionController
from app.policies.scope_policy import ScopePolicyEngine
from tests.research_intent_fixtures import (intent_graph, subject_pair, call, mapping, conversion, approve,
    proposal, assertion, NOW, REF)
from tests.research_intake_fixtures import two_intake_targets as original_targets
from tests.research_subject_fixtures import subject_encryption


@pytest.fixture
def owned_server():
    state={'requests':[], 'response':None, 'before_response':None, 'token':secrets.token_urlsafe(24)}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            # Do not log or persist Authorization; record only equality to our ephemeral token.
            bearer=self.headers.get('Authorization')=='Bearer '+state['token']
            state['requests'].append((self.path,bearer))
            if state['before_response']:state['before_response']()
            if state['response'] is not None:status,body,content_type=state['response']
            else:
                value={'record_id':self.path.rsplit('/',1)[1]}
                if bearer:value.update(subject_id='fixture_actor_p',authenticated=True)
                status,body,content_type=200,json.dumps(value).encode(),'application/json'
            self.send_response(status);self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(body)));self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    state['port']=server.server_port
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield state
    finally:server.shutdown();server.server_close();thread.join(3)


@pytest.fixture
def two_intake_targets(original_targets,owned_server,monkeypatch):
    from tests import research_subject_fixtures as fixtures
    old=fixtures.observation
    monkeypatch.setattr(fixtures,'observation',lambda project,port:old(project,owned_server['port'] if project==1 else port))
    original=fixtures.intake
    def intake(ids):
        value=original(ids);value['budget']['duration_seconds']=300;return value
    monkeypatch.setattr(fixtures,'intake',intake)
    with SessionLocal() as db:
        db.get(Target,original_targets[0]['target']).base_url=f"http://127.0.0.1:{owned_server['port']}"
        db.get(AuthorizationRevision,original_targets[0]['revision']).require_human_execution_approval=True
        db.commit()
    return original_targets


def confirm(g):
    expectations=[]
    for action in g['manifest_input']['actions']:
        bearer=action['role'] in g.get('bearer_roles',{'probe','health_probe'})
        expectations.append(dict(role=action['role'],object_key='record_id',object_value='80814' if action['role'].startswith('health_') else g.get('business_object','7001'),
            identity_key='subject_id',identity_value='fixture_actor_p' if bearer else None))
    return call(verify.confirm,g,1,dict(manifest=g['manifest'],expected_version=0,decision='confirm',expectations=expectations,evidence=REF))


def plan_approve(g,value,role):
    member=next(m for m in value['body']['link']['members'] if m['role']==role)
    call(verify.approve,g,dict(intent=value['reference'],plan_id=member['plan_id'],expected_sequence=0,decision='approved',evidence=REF))
    return member


def send(g,value,role,**kwargs):
    member=next(m for m in value['body']['link']['members'] if m['role']==role)
    with SessionLocal() as db:
        return dispatch(db,g['project'],g['ctx'],dict(intent=value['reference'],plan_id=member['plan_id']),
            executor=g['executor'],now=kwargs.pop('now',NOW),**kwargs)


def bootstrap(g):
    confirm(g)
    health=call(intent.convert,g,1,conversion(g,purpose=g.get('health_purpose','health_probe')))
    plan_approve(g,health,'health')
    receipt=send(g,health,'health')
    assert receipt['body']['outcome']=='healthy',receipt
    call(verify.select_health,g,dict(manifest=g['manifest'],health=[dict(role=g.get('health_role','probe'),evidence=receipt['reference'])],evidence=REF))
    business=call(intent.convert,g,2,conversion(g))
    return health,receipt,business


@pytest.fixture
def verification_graph(intent_graph,subject_encryption,owned_server,monkeypatch):
    g=intent_graph
    # Replace only the owned fixture's deliberately unusable envelope, before W2
    # confirmation/dispatch. No real credential or producer-envelope monkeypatch.
    cipher=StoredSecretCipher.from_settings(settings)
    with SessionLocal() as db:
        version=db.get(CredentialSecretVersion,g['secret_version'])
        version.encrypted_envelope=cipher.encrypt(SecretStr(owned_server['token']),credential_binding_id=g['credential'])
        version.key_version=cipher.key_version
        resource=Resource(target_id=g['target'],resource_type='project',external_id='80814',owner_identity_id=g['bearer'])
        db.add(resource);db.flush();rid=resource.id;g['extra_resources']=[rid]
        from app.db.models.resource_access_assertion import ResourceAccessAssertion
        db.add(ResourceAccessAssertion(resource_id=rid,test_identity_id=g['bearer'],relationship='owner',expected_access='allowed',
            provenance='target_fixture',confidence=80,verification_state='verified',asserted_at=NOW-timedelta(seconds=1)))
        db.commit()
    p=proposal(g);p.update(identity_choice='bearer',test_identity_id=g['bearer'],credential_binding_id=g['credential'],resource_id=rid,
        session_state='unknown',credential_update='unknown')
    call(subject.record,g,3,p)
    p=mapping(g);p['resource_id']=rid
    health_mapping=call(intent.confirm_mapping,g,2,p)['reference']
    p=deepcopy(g['manifest_input']);p.update(expected_version=1,duration_seconds=300)
    p['actions'].append(dict(role='health_probe',subject=dict(number=3,version=1),mapping=health_mapping))
    g['manifest_input']=p;g['manifest']=call(intent.record_manifest,g,1,p)['reference'];approve(g)
    monkeypatch.setattr(vc,'utcnow',lambda:NOW)
    g['executor']=PolicyEnforcedHTTPExecutor(policy_engine=ScopePolicyEngine(platform_allowed_hosts={'127.0.0.1'}),
        rate_limiter=PostgresRateLimiter(2,bind=engine),
        network_gateway=NetworkGateway(controller=PostgresNetworkExecutionController(bind=network_coordination_engine)))
    g['server']=owned_server
    try:yield g
    finally:
        with SessionLocal() as db:
            plans=list(db.scalars(select(IntentPlanMember.plan_id).join(IntentVersion).where(IntentVersion.context_id==g['ctx'])))
            for model in (VerificationClockFault,VerificationPair,VerificationWitness,VerificationAttempt,VerificationHealthSelection,VerificationContract,VerificationAudit):
                db.execute(delete(model).where(model.context_id==g['ctx']))
            from app.db.models.execution_plan_approval_record import ExecutionPlanApprovalRecord
            from app.db.models.execution_plan_progress import ExecutionPlanProgress
            from app.db.models.execution_plan_claim import ExecutionPlanClaim
            from app.db.models.execution_plan_cancellation import ExecutionPlanCancellation
            from app.db.models.safety_decision_record import SafetyDecisionRecord
            for model in (SafetyDecisionRecord,ExecutionPlanApprovalRecord,ExecutionPlanProgress,ExecutionPlanClaim,ExecutionPlanCancellation,TestRun):
                db.execute(delete(model).where(model.execution_plan_id.in_(plans)))
            db.commit()
