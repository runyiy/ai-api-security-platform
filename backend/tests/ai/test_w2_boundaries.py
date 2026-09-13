"""Strict bounds, local-only missing inputs and completed-call qualification."""
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json

import pytest
from sqlalchemy import select,text
from app.db.session import SessionLocal,engine
from app.ai.proposals.adapter import OpenAIProposalAdapter
from app.ai.proposals.codec import canonical,bounded_json,ProposalRejected
from app.ai.proposals.transport import MemoryWire,MemoryConnector,MemoryResolver,MemorySecret,ProviderTransport
from app.ai.w2.records import make,decode,PortError,MAX_N
from app.ai.w2.registry import ref_id
from app.ai.w2.w1_bridge import MemoryPermitPort,TerminalObservationPort
from tests.ai.test_w2_execution import response,balances
from tests.ai.w2_fixtures import w2,rule_pair,knowledge_pair,subject_pair,two_intake_targets,zero_capabilities,KNOWN  # noqa: F401


@pytest.mark.parametrize('name,limit',[('Scope',4096),('ReadContext',32768)])
def test_t2_exact_record_byte_limit_and_plus_one(w2,name,limit):
    value=w2.scope if name=='Scope' else w2.read()
    raw=value.encode();padded=raw+b' '*(limit-len(raw))
    assert decode(name,padded)==value
    with pytest.raises(PortError):decode(name,padded+b' ')
    assert not w2.witness.accepted


@pytest.mark.parametrize('field,maximum',[('context_version',10000),('context_generation',2147483647),('policy_version',10000)])
def test_t2_exact_versions_generations_and_plus_one(w2,field,maximum):
    value=w2.scope.document();value[field]=maximum
    assert decode('Scope',canonical(value))[field]==maximum
    value[field]+=1
    with pytest.raises(PortError):decode('Scope',canonical(value))


def test_t2_selected_32_33_fixed_top_k_and_utf8_bounds(w2):
    sources=[make('ExactSource',scope='reusable_synthetic',knowledge_id='knowledge-'+str(i),version=1,digest='a'*64) for i in range(1,34)]
    values=dict(question_digest=w2.questions['Q4'].fingerprint(),purpose='offline_context_explanation',keywords=['sharing'],tags=['sharing'],selected=sources[:32],top_k=4)
    assert len(make('RetrievalRequest',**values).selected)==32
    for change in ({'selected':sources},{'selected':sources[:1]*2},{'top_k':5},{'keywords':['x'*33]},{'keywords':['sharíng']}):
        with pytest.raises(PortError):make('RetrievalRequest',**{**values,**change})
    for size in (64,65):
        doc={**w2.scope.document(),'task_id':'x'*size}
        if size==64:assert decode('Scope',canonical(doc)).task_id=='x'*size
        else:
            with pytest.raises(PortError):decode('Scope',canonical(doc))


@pytest.mark.parametrize('kind',['model_permission','token_certificate','projection_role'])
def test_t12_missing_decision_or_full_render_certificate_zero_model_calls(w2,kind):
    entry={'model_permission':ref_id('permission',w2.scope),'token_certificate':w2.certificate.evidence_id,
           'projection_role':w2.binding.decision_refs[-1]}[kind]
    record={'model_permission':w2.permission,'token_certificate':w2.certificate,'projection_role':
        w2.registry.get(entry,'Decision',w2.deadline()) if kind=='projection_role' else None}[kind]
    w2.registry.revoke(entry,record.fingerprint(),w2.deadline())
    if kind=='projection_role':
        with pytest.raises(PortError):w2.prepare()
    else:assert w2.prepare()[0].state=='AWAITING_APPROVAL'
    assert not w2.authority.calls and not w2.witness.accepted


@pytest.mark.parametrize('w2',[{}, {'source_window_seconds':1}],indirect=True,ids=['call_window','one_second_source_window'])
@pytest.mark.parametrize('stage',['qualified_final_return','qualified_complete'])
@pytest.mark.parametrize('change',['within','equal','cancelled','wall_rollback','mono_rollback'])
def test_x4_completed_final_lookup_preserves_known_usage(w2,stage,change):
    w2.reopen();rt=w2.runtime()
    original=rt.hook
    def hook(actual):
        original(actual)
        if actual==stage:
            if change=='within':w2.clock.advance(.5)
            elif change=='equal':
                w2.clock.advance((__import__('app.ai.w2.records',fromlist=['utc']).utc(rt.prepared.core.expires_at)-w2.clock.utcnow()).total_seconds())
            elif change=='cancelled':rt.deadline.cancelled=lambda _:True
            elif change=='wall_rollback':w2.clock.wall-=timedelta(microseconds=1)
            else:w2.clock.ns-=1
    rt.hook=hook
    wire=MemoryWire(response(rt),'8.8.8.8',w2_bound=True,
        permit_port=MemoryPermitPort(rt.prepared.core.body_digest,rt.consume,rt.close,rt.deadline.remaining))
    secret=MemorySecret(rt.prepared.config.secret_ref,rt.prepared.config.secret_version,b'synthetic-provider-key-only')
    adapter=OpenAIProposalAdapter(transport=ProviderTransport(resolver=MemoryResolver(('8.8.8.8',)),
        connector=MemoryConnector(wire),secret=secret,execution_kind='synthetic'),authority=rt,coordination=rt,clock=rt.clock,
        terminal_observer=TerminalObservationPort(rt.observe_terminal))
    result=adapter.propose_once(prepared=rt.prepared.prepared,config=rt.prepared.config,receipt=rt.receipt)
    completion,result=rt.complete_v1(result)
    assert w2.witness.accepted[rt.key.fingerprint()]==len(wire.writes)==secret.calls==1
    assert result.usage==KNOWN
    assert bool(result.display)==(change=='within')
    assert completion.display_state==('ELIGIBLE_NOW' if change=='within' else 'SUPPRESSED')
    assert all((b['settled_tokens'],b['settled_microusd'],b['held_tokens'])==(2432,8704,0) for b in balances(rt.key))


def test_t3_test_only_publication_is_filtered_before_card_load(w2,monkeypatch):
    from app.db.models.research_knowledge import KnowledgeVersion as KV,KnowledgeEvent as KE
    from app.schemas import research_knowledge as schema
    from app.services import research_knowledge as service
    with w2.writer.mutation('LIFECYCLE','synthetic_negative',sha256(b'test-only publication').hexdigest(),w2.deadline()) as operation:
        with SessionLocal() as db,db.begin():
            operation.bind(db)
            original=db.scalar(select(KV).where(KV.digest==w2.source.digest))
            content={**original.content,'knowledge_id':'knowledge-999'}
            digest=schema.digest(content)
            version=KV(context_id=original.context_id,context_version=original.context_version,target_id=original.target_id,
                scope='reusable_synthetic',knowledge_id='knowledge-999',version=1,content=content,digest=digest,
                review=original.review,recorded_at=original.recorded_at)
            db.add(version);db.flush()
            ids=[]
            for index,action in enumerate(('review','reuse','publish'),1):
                body=schema.EventBody(digest=digest,actor='synthetic_test_reviewer',evidence='synthetic_test_only',review=original.review,
                    context_version=1,valid_from=w2.start,valid_until=w2.end,review_event_id=ids[0] if ids else None,
                    reuse_event_id=ids[1] if len(ids)>1 else None,validation_ref='synthetic_test_only')
                event=KE(version_id=version.id,sequence=index,action=action,body=body.model_dump(),recorded_at=w2.clock.utcnow())
                db.add(event);db.flush();ids.append(event.id)
        operation.committed()
    def forbidden(*args):raise AssertionError('excluded card loaded or ranked')
    monkeypatch.setattr(service,'_exact',forbidden);monkeypatch.setattr(service,'_rank',forbidden)
    request=make('RetrievalRequest',question_digest=w2.questions['Q4'].fingerprint(),purpose='offline_context_explanation',
        keywords=['sharing'],tags=['sharing'],selected=[make('ExactSource',scope='reusable_synthetic',knowledge_id='knowledge-999',version=1,digest=digest)],top_k=4)
    read=w2.read()
    with pytest.raises(PortError):w2.retrieval.retrieve_projected_v1(read,request,w2.deadline(read))
    assert not w2.witness.accepted


def test_t2_t11_real_journal_pending_capacity_is_64_and_stops_closed(w2):
    w2.reopen();d=w2.deadline()
    ctx=make('InvalidationContext',deployment_ref=w2.scope.deployment_ref,writer_id='capacity_writer',
        process_epoch=w2.process_epoch,deadline_at=d.context.deadline_at,mono_deadline_ns=d.context.mono_deadline_ns,
        cancellation_id=d.context.cancellation_id)
    with w2.guard.read(d):
        for i in range(64):
            request=make('InvalidationRequest',operation_id='pending_'+str(i),operation_digest=sha256(str(i).encode()).hexdigest(),
                deployment_ref=w2.scope.deployment_ref,writer_id=ctx.writer_id,action='INVALIDATE_DEPLOYMENT',reason='LIFECYCLE',deadline_at=ctx.deadline_at)
            ack=w2.authority.invalidate_v1(ctx,request)
            assert ack.disposition=='PENDING'
            assert w2.authority.lookup_invalidation_v1(ctx,request.operation_id,request.operation_digest)==ack
        extra=make('InvalidationRequest',**{**request.document(),'operation_id':'one_too_many'})
        with pytest.raises(PortError,match='LIMIT_EXCEEDED'):w2.authority.invalidate_v1(ctx,extra)
    assert sum(x.disposition=='PENDING' for x in w2.authority.operations.values())==64
    assert w2.authority.state=='RECOVERY_REQUIRED' and w2.authority.coverage()
    assert not w2.witness.accepted


def test_t2_t11_normal_event_capacity_preserves_stop_slots(w2):
    w2.reopen();rt=w2.runtime();rt.admit(rt.receipt,rt.prepared.core.w1_binding_digest,30)
    rt.begin_send();rt.consume(rt.prepared.body,3);rt.end_send()
    while len(w2.authority.events(rt.key))<56:w2.authority.late_usage(rt.key,KNOWN)
    with pytest.raises(PortError,match='LIMIT_EXCEEDED'):w2.authority.late_usage(rt.key,KNOWN)
    rt.close()
    events=w2.authority.events(rt.key)
    assert len(events)==57 and events[-1].kind=='STREAM_CLOSED'
    assert w2.authority.coverage() and w2.witness.accepted[rt.key.fingerprint()]==1
    result=rt.reconcile()
    assert result.settled_tokens==2432


@pytest.mark.parametrize('size',[16384,16385])
def test_t12_entire_rendered_fake_body_counter_exact_and_plus_one(w2,monkeypatch,size):
    from app.ai.w2 import preparation
    original=preparation.request_body
    def padded(document):
        body=original(document)
        assert len(body)<size
        return body+b' '*(size-len(body))
    monkeypatch.setattr(preparation,'request_body',padded)
    if size==16384:
        result,_=w2.prepare()
        prepared=w2.preparation.prepared[result.prepared_ref]
        assert preparation.synthetic_input_units(prepared.body)==4096
    else:
        with pytest.raises(PortError,match='LIMIT_EXCEEDED'):w2.prepare()
    assert not w2.witness.accepted and not w2.authority.calls


@pytest.mark.parametrize('maximum,depth,nodes',[(4096,6,512),(32768,8,4096)])
def test_t2_w2_decoder_node_depth_exact_and_plus_one(maximum,depth,nodes):
    # Same preallocation decoder and parameters used by W2 records. Individual
    # record schemas can impose stricter cardinality before reaching this cap.
    value={'items':[0]*(nodes-2)}
    assert bounded_json(canonical(value),maximum=maximum,depth=depth,nodes=nodes)==value
    value['items'].append(0)
    with pytest.raises(ProposalRejected):bounded_json(canonical(value),maximum=maximum,depth=depth,nodes=nodes)
    value=0
    for _ in range(depth):value={'child':value}
    assert bounded_json(canonical(value),maximum=maximum,depth=depth,nodes=nodes)==value
    with pytest.raises(ProposalRejected):bounded_json(canonical({'child':value}),maximum=maximum,depth=depth,nodes=nodes)


def test_t2_t3_catalog_scan_256_257_filters_without_loading_unselected_cards(w2,monkeypatch):
    from app.db.models.research_knowledge import KnowledgeVersion as KV
    from app.services import research_knowledge as service
    with w2.writer.mutation('CONFIGURATION','scan_fixture',sha256(b'bounded synthetic metadata').hexdigest(),w2.deadline()) as op:
        with SessionLocal() as db,db.begin():
            op.bind(db)
            original=db.scalar(select(KV).where(KV.digest==w2.source.digest))
            # No fake publication authority is created for these rows. Only
            # metadata may be scanned; the selected real W3 rule is unchanged.
            values=dict(context_id=original.context_id,context_version=original.context_version,target_id=original.target_id,
                scope='reusable_synthetic',version=1,review=original.review,recorded_at=original.recorded_at)
            for n in range(2,257):
                content={**original.content,'knowledge_id':'knowledge-'+str(n),'claim':'UNSELECTED_PRIVATE_CANARY'}
                db.add(KV(**values,knowledge_id=content['knowledge_id'],content=content,digest=sha256(canonical(content)).hexdigest()))
        op.committed()
    loaded=[];original_exact=service._exact
    def exact(db,context,reference,**kwargs):
        loaded.append(reference.knowledge_id)
        assert reference.knowledge_id==w2.source.knowledge_id
        return original_exact(db,context,reference,**kwargs)
    monkeypatch.setattr(service,'_exact',exact)
    result,_=w2.prepare()
    assert result.state=='PRIORITY_UNRESOLVED' and set(loaded)=={w2.source.knowledge_id}
    with w2.writer.mutation('CONFIGURATION','scan_overflow',sha256(b'257 metadata rows').hexdigest(),w2.deadline()) as op:
        with SessionLocal() as db,db.begin():
            op.bind(db)
            content={**content,'knowledge_id':'knowledge-257'}
            db.add(KV(**values,knowledge_id='knowledge-257',content=content,digest=sha256(canonical(content)).hexdigest()))
        op.committed()
    loaded.clear()
    with pytest.raises(PortError,match='LIMIT_EXCEEDED'):w2.prepare()
    assert not loaded and not w2.witness.accepted
