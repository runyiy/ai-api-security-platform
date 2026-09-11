"""Owned W3 lab metadata and HTTP application; never a witness/proof producer."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from threading import Thread

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.main import app
from app.api.routes import research_verification as route
from app.credentials.bearer import BearerCredentialService
from app.db.session import SessionLocal
from app.db.models import Resource, TestIdentity
from app.db.models.credential_secret_version import CredentialSecretVersion
from app.db.models.resource_access_assertion import ResourceAccessAssertion
from app.services import research_verification_clock as clock
from tests.research_verification_fixtures import (verification_graph, intent_graph, subject_pair,
    two_intake_targets, original_targets, subject_encryption)
from tests.research_subject_fixtures import proposal
from tests.research_intake_fixtures import REF


@pytest.fixture
def owned_server(monkeypatch):
    # Date fixture metadata at actual UTC. Production clocks/time windows are not
    # frozen or extended; execution records actual wall and monotonic samples.
    from tests import (research_intake_fixtures, research_observation_fixtures,
        research_subject_fixtures, research_intent_fixtures, research_verification_fixtures)
    at = datetime.now(timezone.utc)
    for module in (research_intake_fixtures, research_observation_fixtures,
                   research_subject_fixtures, research_intent_fixtures, research_verification_fixtures):
        monkeypatch.setattr(module, 'NOW', at)
    original_observation = research_subject_fixtures.observation
    def dated_observation(*args, **kwargs):
        value = original_observation(*args, **kwargs)
        value['prepared_at'] = at.isoformat()
        value['entries'][0]['observed_at'] = (at-timedelta(seconds=1)).isoformat()
        return value
    monkeypatch.setattr(research_subject_fixtures, 'observation', dated_observation)
    original_call = research_subject_fixtures.call
    def fixture_call(*args, **kwargs):
        kwargs.setdefault('now', at)
        return original_call(*args, **kwargs)
    monkeypatch.setattr(research_subject_fixtures, 'call', fixture_call)
    state = dict(token=secrets.token_urlsafe(24), token_a=secrets.token_urlsafe(24),
                 requests=[], mode='safe', baseline_response=None, health_response=None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            auth = self.headers.get('Authorization')
            actor = 'A' if auth == 'Bearer '+state['token_a'] else 'B' if auth == 'Bearer '+state['token'] else None
            state['requests'].append((self.path, actor))
            value = self.path.rsplit('/', 1)[-1]
            body = dict(record_id=value, subject_id='demo_'+str(actor).lower(), authenticated=actor is not None)
            status, media = 200, 'application/json'
            if actor is None or self.path not in ('/folders/80814', '/folders/91001'):
                status, body = 401, {'error': 'unauthenticated'}
            elif value == '91001' and actor == 'B' and state['mode'] == 'safe':
                status, body = 403, {**body, 'error': 'access_denied'}
            # The deliberately vulnerable variant omits the B authorization
            # check. Shared mode separately grants B access in fixture facts.
            override = state['health_response'] if value == '80814' else state['baseline_response'] if actor == 'A' else None
            raw = json.dumps(body).encode()
            if override is not None:
                status, raw, media = override
            self.send_response(status)
            self.send_header('Content-Type', media)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    state['port'] = server.server_port
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


class Demonstration:
    def __init__(self, g, api):
        self.g, self.api, self.trace = g, api, []
        self.root = f"/api/research-projects/{g['project']}/contexts/{g['ctx']}"

    def post(self, path, payload, status=200):
        response = self.api.post(self.root+'/'+path, json=payload)
        assert response.status_code == status, (path, response.status_code, response.text)
        assert response.headers['cache-control'] == 'no-store'
        value = response.json()
        self.trace.append(dict(operation=path, input=deepcopy(payload), output=value))
        return value

    def convert(self, number, purpose='business', status=200):
        return self.post(f'intents/versions/{number}', dict(manifest=self.manifest['reference'],
            expected_version=0, purpose=purpose, knowledge=None, evidence=REF), status)

    def decision(self, value, role, decision='approved', sequence=0):
        return self.post('verification/plan-decisions', {**self.plan(value, role),
            'expected_sequence': sequence, 'decision': decision, 'evidence': REF})

    @staticmethod
    def plan(value, role):
        member = next(m for m in value['body']['link']['members'] if m['role'] == role)
        return dict(intent=value['reference'], plan_id=member['plan_id'])

    def execute(self, value, role, status=200):
        return self.post('verification/execute', self.plan(value, role), status)

    def prepare(self, scenario='safe', *, baseline_access='allowed', probe_access=None, source=None):
        """Seed business facts, then use actual local APIs for all W1/W2 decisions."""
        g = self.g
        if source is None:
            source = self.source()
        self.source_id = source
        g['server']['mode'] = scenario
        probe_access = probe_access or ('allowed' if scenario == 'shared' else 'denied')
        with SessionLocal() as db:
            resource = Resource(target_id=g['target'], resource_type='project', external_id='91001',
                                owner_identity_id=g['anonymous'] if scenario == 'shared' else g['bearer'])
            db.add(resource)
            db.flush()
            self.resource = resource.id
            g['extra_resources'].append(resource.id)
            for actor, relationship, access in ((g['anonymous'], 'owner' if scenario == 'shared' else 'non_owner', baseline_access),
                (g['bearer'], 'non_owner' if scenario == 'shared' else 'owner', probe_access)):
                if access is not None:
                    db.add(ResourceAccessAssertion(resource_id=resource.id, test_identity_id=actor,
                        relationship=relationship, expected_access=access, provenance='target_fixture',
                        confidence=100, verification_state='verified', asserted_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
            db.commit()
        self.actions = []
        self.subjects = []
        for number, role, actor, binding, rid in (
            (4, 'baseline', g['anonymous'], g['credential_a'], self.resource),
            (5, 'probe', g['bearer'], g['credential'], self.resource),
            (6, 'health_baseline', g['anonymous'], g['credential_a'], self.health_resource),
            (7, 'health_probe', g['bearer'], g['credential'], self.health_resource)):
            payload = proposal(g)
            payload.update(identity_choice='bearer', test_identity_id=actor, credential_binding_id=binding,
                           resource_id=rid, session_state='unknown', credential_update='unknown')
            if source and role == 'baseline':
                payload['sources'] = [dict(observation_id=source, source_entry_index=0)]
            self.subjects.append(self.post(f'subjects/{number}', payload))
            self.actions.append(dict(role=role, subject=dict(number=number, version=1)))
        for number, rid in ((3, self.resource), (4, self.health_resource)):
            mapping = self.post(f'intents/mappings/{number}', dict(context_version=1, target_id=g['target'],
                endpoint_id=g['endpoint'], binding_id=g['slot'], resource_id=rid, expected_version=0,
                decision='confirm', evidence=REF))
            for action in self.actions[:2] if number == 3 else self.actions[2:]:
                action['mapping'] = mapping['reference']
        self.manifest_input = dict(context_version=1, target_id=g['target'], expected_version=0,
            actions=self.actions, duration_seconds=300, rate_millirequests_per_second=500,
            concurrency=1, evidence=REF)

    def source(self):
        from tests.research_observation_fixtures import preparation, observation
        value = preparation(self.g)
        value.update(preparation_ref='preparation_2', path_templates=['/folders/{project_id}'])
        self.post('observations/preparations', value)
        raw = observation(port=self.g['server']['port'])
        raw.update(preparation_ref='preparation_2', batch_ref='batch_2', prepared_at=datetime.now(timezone.utc).isoformat())
        raw['entries'][0].update(entry_ref='entry_2', path_template='/folders/{project_id}',
            observed_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())
        return self.post('observations/preparations/preparation_2/batches', raw)['observation_id']

    def manifest_and_contract(self, *, budget=True, contract=True):
        self.manifest = self.post('intents/manifests/2', self.manifest_input)
        if budget:
            self.budget = self.post('intents/budget-decisions', dict(manifest=self.manifest['reference'],
                expected_sequence=0, decision='approved', evidence=REF))
        if contract:
            expectations = [dict(role=a['role'], object_key='record_id',
                object_value='80814' if a['role'].startswith('health_') else '91001',
                identity_key='subject_id', identity_value='demo_a' if a['role'] in ('baseline','health_baseline') else 'demo_b')
                for a in self.actions]
            self.contract = self.post('verification/contracts/2', dict(manifest=self.manifest['reference'],
                expected_version=0, decision='confirm', expectations=expectations, evidence=REF))

    def health_and_business(self):
        self.health = []
        for number, role in ((1, 'baseline'), (2, 'probe')):
            value = self.convert(number, 'health_'+role)
            self.decision(value, 'health')
            witness = self.execute(value, 'health')
            assert witness['body']['outcome'] == 'healthy'
            self.health.append((value, witness))
        self.selection = self.post('verification/health-selections', dict(manifest=self.manifest['reference'],
            health=[dict(role=role, evidence=witness['reference']) for role, (_, witness) in zip(('baseline','probe'), self.health)], evidence=REF))
        self.business = self.convert(3)
        return self.business


@pytest.fixture
def demonstration(verification_graph, monkeypatch):
    g = verification_graph
    # Restore the real production clock after the older reusable fixture's fixed
    # clock setup. No qualification function, envelope or M8 producer is patched.
    monkeypatch.setattr(clock, 'utcnow', lambda: datetime.now(timezone.utc))
    with SessionLocal() as db:
        actor = db.get(TestIdentity, g['anonymous'])
        actor.auth_type = 'bearer'
        binding = BearerCredentialService(db=db).provision(identity=actor, token=SecretStr(g['server']['token_a']))
        g['credential_a'] = binding.id
        g['secret_a'] = db.scalar(select(CredentialSecretVersion.id).where(CredentialSecretVersion.credential_binding_id == binding.id))
        health = db.scalar(select(Resource).where(Resource.target_id == g['target'], Resource.external_id == '80814'))
        db.add(ResourceAccessAssertion(resource_id=health.id, test_identity_id=actor.id, relationship='non_owner',
            expected_access='allowed', provenance='target_fixture', confidence=100, verification_state='verified',
            asserted_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
        health_id = health.id
        db.commit()
    monkeypatch.setattr(route, 'executor', g['executor'])  # Explicit owned-loopback platform policy; real executor.
    with TestClient(app) as api:
        demo = Demonstration(g, api)
        demo.health_resource = health_id
        yield demo
