"""Independently authored synthetic W2 questions, decisions and fake endpoints.

W3 validation and publication use the real existing services in the owned TEST
database. Registry approvals are separate, explicit synthetic operator fixtures.
"""
from dataclasses import asdict
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import json
import shutil
import tempfile

import pytest
from sqlalchemy import select, text
from app.db.session import SessionLocal, engine
from app.ai.proposals.adapter import Usage
from app.ai.proposals.codec import canonical
from app.ai.w2 import schema as tables
from app.ai.w2.records import make, stamp, utc, require
from app.ai.w2.registry import Registry, ref_id, ROLES
from app.ai.w2.retrieval import ProjectedRetrieval, PROJECTION_DIGEST, TEMPLATE_DIGEST
from app.ai.w2.preparation import PreparationService, PROMPT_DIGEST, SCHEMA_DIGEST, TEMPLATES
from app.ai.w2.fake_authority import FakeAuthority, Journal, EndpointWitness
from app.ai.w2.guard import Guard, WriterController
from app.ai.w2.accounting import BudgetStore
from app.ai.w2.recovery import Recovery
from app.ai.w2.time import Deadline
from app.ai.w2.w1_bridge import CallRuntime
from app.ai.w2 import lifecycle
from tests.research_rule_fixtures import (rule_pair, knowledge_pair, subject_pair, two_intake_targets,
    rule, validate, publish, call, NOW, REF, zero_capabilities)  # noqa: F401

KNOWN = Usage('known', 2048, 384, 0, 0, 128, 2432)


class Clock:
    def __init__(self):
        self.wall, self.ns = NOW, 100_000_000_000

    def utcnow(self):
        return self.wall

    def monotonic_ns(self):
        return self.ns

    def advance(self, seconds):
        self.wall += timedelta(seconds=seconds)
        self.ns += int(seconds*1e9)


@pytest.fixture
def w2(rule_pair, tmp_path, request):
    g = rule_pair[0]
    source = rule(g, scope='reusable_synthetic')
    proof = validate(g, source)['validation_ref']
    publication = publish(g, source, proof)
    uid = uuid4().hex
    scope = make('Scope', task_id='task_'+uid, case_id='case_1', project_ref='project_'+uid,
        context_ref='context_'+uid, context_version=1, context_generation=1, account_ref='account_'+uid,
        deployment_ref='deployment_'+uid, policy_id='policy_1', policy_version=1)
    clock = Clock()
    process_epoch = 'process_'+uid
    def read(scope=scope):
        return make('ReadContext', scope=scope, process_epoch=process_epoch,
            deadline_at=stamp(clock.utcnow()+timedelta(seconds=30)), mono_deadline_ns=clock.monotonic_ns()+30_000_000_000,
            cancellation_id='cancel_'+uid)
    def deadline(context=None):
        return Deadline(context or read(), clock, lambda _: False, process_epoch)
    guard = Guard(engine.url)
    journal = Journal(tmp_path/'journal')
    witness = EndpointWitness(tmp_path/'witness')
    authority = FakeAuthority(scope.deployment_ref, journal, witness, clock)
    writer = WriterController(scope.deployment_ref, guard, authority, SessionLocal)
    registry = Registry(SessionLocal, writer)
    store = BudgetStore(SessionLocal, writer)
    recovery = Recovery(store, authority, writer)
    retrieval = ProjectedRetrieval(SessionLocal, registry, guard)
    preparation = PreparationService(registry, retrieval)
    start, end = stamp(NOW-timedelta(seconds=1)), stamp(NOW+timedelta(minutes=30))
    if 'source_window_seconds' in getattr(request,'param',{}):
        end=stamp(NOW+timedelta(seconds=request.param['source_window_seconds']))
    initial_records=[]
    def install(entry_id, value):
        initial_records.append((entry_id,1,value))
    def decisions(source_id, version, digest, projection):
        refs = []
        for role in ROLES:
            ref = role+'_'+uuid4().hex
            install(ref, make('Decision', decision_id=ref, role=role, source_id=source_id, source_version=version,
                source_digest=digest, projection_digest=projection, actor='local_operator', evidence='independent_synthetic_reviewed',
                valid_from=start, expires_at=end))
            refs.append(ref)
        return refs
    try:
        writer.register_deployment(deadline())
        lifecycle.attach(engine, writer, deadline)
        source = make('ExactSource', **source)
        install(ref_id('scope', scope), make('ScopeMapping', scope=scope, project_number=g['project'], context_id=g['ctx'],
                                            valid_from=start, expires_at=end))
        refs = decisions(source.knowledge_id, source.version, source.digest, PROJECTION_DIGEST)
        from app.db.models.research_knowledge import KnowledgeEvent, KnowledgeVersion
        with SessionLocal() as db:
            body = db.get(KnowledgeEvent, publication['event_id']).body
            content = db.scalar(select(KnowledgeVersion.content).where(KnowledgeVersion.digest == source.digest))
        binding = make('ProjectionBinding', source=source, projection_digest=PROJECTION_DIGEST,
            template_digest=TEMPLATE_DIGEST, template='sharing-complete/1', decision_refs=refs,
            validation_id=proof['validation_id'], validation_digest=proof['digest'], review_event_id=body['review_event_id'],
            reuse_event_id=body['reuse_event_id'], publication_event_id=publication['event_id'], valid_from=start, expires_at=end)
        install(ref_id('projection', source), binding)
        install(ref_id('review', source), make('ProjectionReview', binding=binding, content_digest=source.digest,
            examples_digest=sha256(canonical(content['example_refs'])).hexdigest(),
            counterexamples_digest=sha256(canonical(content['counterexample_refs'])).hexdigest(), reviewer_decision=refs[3],
            valid_from=start, expires_at=end))
        candidates, candidate_reviews = [], []
        for index, actor in enumerate(('anonymous', 'bearer'), 1):
            sid = 'synthetic_candidate_'+str(index)
            scenario = make('SyntheticScenario', source_id=sid, source_version=index,
                shape='single_resource_path_get_json_object', actor_mode=actor, relationship='non_owner',
                expected_access='allowed', facts_ref='independent_fact_'+str(index), expectation_ref='independent_expectation_'+str(index),
                authorship='independent_synthetic', valid_from=start, expires_at=end)
            install(ref_id('scenario', [sid,index]), scenario)
            projected = sha256(canonical(dict(shape=scenario.shape, actor_mode=actor))).hexdigest()
            decision_refs = decisions(sid,index,scenario.fingerprint(),projected)
            reference = make('CandidateRef', source_id=sid, source_version=index, source_digest=scenario.fingerprint())
            cert = make('SyntheticCandidateCertificate', source_id=sid, source_version=index, source_digest=scenario.fingerprint(),
                shape=scenario.shape, actor_mode=actor, scenario_manifest_digest=scenario.fingerprint(), missing=[],
                decision_refs=decision_refs, valid_from=start, expires_at=end)
            install(ref_id('candidate', reference), cert)
            candidates.append(reference)
            candidate_reviews.append(decision_refs[3])
        local = make('LocalPolicy', policy_ref='access_policy', policy_version='ra-w2-necessity/1',
            rule='explicit_access_facts', template='access-basis/1', review_ref='reviewed_access_policy', valid_from=start, expires_at=end)
        install(local.policy_ref, local)
        questions = {}
        for label, count in (('Q1',0),('Q2',0),('Q3',1),('Q4',2),('Q4b',2)):
            entry = 'Q4' if label=='Q4b' else label
            q = make('QuestionManifest', scope=scope, question_id='question_'+label, entry=entry,
                policy_version='ra-w2-necessity/1', template=TEMPLATES[entry][0], initial_rule=entry != 'Q2',
                local_policy_ref=local.policy_ref if entry=='Q1' else None, rule=None if entry=='Q1' else source,
                projection=None if entry=='Q1' else binding, candidate_refs=candidates[:count],
                synthetic_review_refs=candidate_reviews[:count], missing=[], valid_from=start, expires_at=end)
            install(ref_id('question', [scope.task_id,scope.case_id,q.question_id]),q)
            questions[label]=q
        manifest = make('TaskManifest', scope=scope, questions=list(questions.values()), review_ref='independent_task_review',
                        valid_from=start, expires_at=end)
        install(ref_id('manifest',scope.task_id),manifest)
        certificate = make('TokenCertificate', evidence_id='synthetic_token_bound', config_revision='config_1',
            model='gpt-5.6-terra', profile='ra-openai-responses/1', prompt_digest=PROMPT_DIGEST, schema_digest=SCHEMA_DIGEST,
            projection_digest=PROJECTION_DIGEST, counting_version='synthetic-fixture/1', input_limit=4096, output_limit=1024,
            measurement_kind='synthetic', valid_from=start, expires_at=end)
        install(certificate.evidence_id,certificate)
        permission = make('ModelPermission', permission_id='permission_1', scope=scope, config_revision='config_1', account_ref=scope.account_ref,
            secret_ref='synthetic_provider_key', secret_version='version_1', enabled=True,
            permissions=['protocol','transport','model','data','retention','account','budget'],
            token_bound_evidence_ref=certificate.evidence_id, valid_from=start, expires_at=end, measurement_kind='synthetic')
        install(ref_id('permission',scope),permission)
        registry.install_many(initial_records,deadline())
        for kind in ('account','task'):
            caps=getattr(request,'param',{}).get('caps',(10240,45056,2,60_000_000_000))
            store.install_policy(scope, kind, caps, manifest.fingerprint(),
                                 'budget_'+kind, start,end,deadline())
        def reopen():
            d=deadline()
            ctx=make('InvalidationContext', deployment_ref=scope.deployment_ref, writer_id='recovery', process_epoch=process_epoch,
                deadline_at=d.context.deadline_at,mono_deadline_ns=d.context.mono_deadline_ns,cancellation_id=d.context.cancellation_id)
            def qualification(token, operation):
                mapping = registry.scope_mapping(scope,operation)
                with guard.read(operation,token), SessionLocal() as db, db.begin():
                    retrieval.context(db,operation.context,operation)
                    retrieval._qualify(db, __import__('app.services.research_context',fromlist=['_context'])._context(db,g['project'],g['ctx']),source,operation)
                registry.model_permission(scope,operation)
                return sha256(canonical([mapping.fingerprint(),permission.fingerprint(),binding.fingerprint()])).hexdigest()
            return recovery.reopen(ctx,'reopen_'+uuid4().hex,d,qualification)
        def prepare(entry='Q4'):
            r=read();d=deadline(r)
            return preparation.prepare_v1(r,questions[entry],d),d
        def runtime(entry='Q4', reserve=True):
            outcome,d=prepare(entry)
            require(outcome.state=='PRIORITY_UNRESOLVED','RESERVATION_UNAVAILABLE')
            prepared=preparation.prepared[outcome.prepared_ref]
            until=min(utc(d.context.deadline_at),utc(prepared.core.expires_at))
            run=make('RunContext',scope=scope,call_ref=prepared.key.call_ref,attempt=1,owner_id='owner_1',owner_generation=1,
                process_epoch=process_epoch,started_at=stamp(clock.utcnow()),deadline_at=stamp(until),
                mono_start_ns=clock.monotonic_ns(),mono_deadline_ns=min(d.context.mono_deadline_ns,
                    clock.monotonic_ns()+int((until-clock.utcnow()).total_seconds()*1e9)),cancellation_id=d.context.cancellation_id)
            rt=CallRuntime(prepared,run,deadline(run),preparation,store,authority,guard)
            if reserve:
                rt.reserve_v1()
            rt.hook=lambda stage: authority.script_final(rt.key,KNOWN) if stage=='admitted' else None
            return rt
        yield SimpleNamespace(**locals())
    finally:
        lifecycle.detach(engine)
        guard.close()
        journal.close()
        witness.close()
        # All tables belong solely to this explicitly owned TEST fixture run.
        with engine.begin() as db:
            db.execute(text('TRUNCATE '+', '.join(t.name for t in reversed(tables.TABLES))))
