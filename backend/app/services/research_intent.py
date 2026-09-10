"""Bounded offline W1 conversion. New-purpose execution is deliberately closed."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import re
from sqlalchemy import select, func, text
from sqlalchemy.orm import load_only
from app.db.models.research_intent import (IntentMapping, IntentManifest, IntentBudgetDecision,
    IntentVersion, IntentPlanMember, IntentAudit)
from app.db.models.research_subject import ResearchSubjectVersion
from app.db.models.resource import Resource
from app.db.models.endpoint import Endpoint
from app.db.models.target import Target
from app.db.models.endpoint_resource_binding import EndpointResourceBinding
from app.db.models.resource_access_assertion import ResourceAccessAssertion
from app.db.models.research_observation import ObservationRecord, ObservationPreparation
from app.db.models.research_knowledge import KnowledgeVersion, KnowledgeEvent
from app.db.models.test_case import TestCase
from app.schemas import research_intent as s, research_knowledge as ks
from app.schemas.research_context import ResearchIntakeInput
from app.schemas.research_subject import SubjectInput
from app.services import research_context as intake, research_observation as observation
from app.services import research_subject as subject, research_knowledge as knowledge
from app.services.bola_binding_selection import select_bola_binding
from app.services.bola_binding_matrix_preview import preview_bola_binding_matrix, BOLAResourceSlotAssignment
from app.services.test_execution import build_test_case_url
from app.services.test_case_planning import _build_scope_context
from app.services.execution_plan import PlanActionInput, _create_execution_plan
from app.services.execution_plan_approval import validate_persisted_plan_integrity

TEST_TYPE = 'ra_intent_get_v1'
PROTOCOLS = {IntentMapping: 'ra-mapping/1', IntentManifest: 'ra-manifest/1'}


def _time(clock=None):
    value = clock() if callable(clock) else clock
    value = value if value is not None else datetime.now(timezone.utc)
    return s.timestamp(s.stamp(value))


class _Clock:
    """Request-local UTC samples must never move backward, including encoding."""
    def __init__(self, source):
        self.source, self.last = source, None

    def __call__(self):
        at = _time(self.source)
        if self.last is not None and at < self.last:
            raise s.IntentError('intent_clock_invalid')
        self.last = at
        return at


def _clock(source):
    return source if isinstance(source, _Clock) else _Clock(source)


def _json(value):
    if isinstance(value, datetime): return s.stamp(value)
    if isinstance(value, dict): return {k: _json(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)): return [_json(v) for v in value]
    return value


def _ref(row):
    return dict(number=row.number, version=row.version, digest=row.digest)


def _locked(db, project, context_id):
    context = knowledge._locked(db, project, context_id)
    # Same order as W2; prevent inserts/reviews/Scope/Endpoint phantoms.
    db.execute(text('LOCK TABLE resource_access_assertions, authorization_revisions, scopes, endpoints, endpoint_resource_bindings IN SHARE MODE'))
    return context


def _base(db, context, version, target_id, clock):
    observation._eligible_context(db, context, version)
    observation._target(db, context, target_id)
    ready = intake.read_context(db, context.project_number, context.id, version=version, now=_time(clock))
    if ready['latest_version'] != version or ({'permission_missing','data_ineligible'}.intersection(ready['missing_inputs']) or ready['budget_rate_exceeded']):
        raise s.IntentError('intent_permission_missing')
    data = ResearchIntakeInput.model_validate(ready['intake'])
    item = next((t for t in data.targets if t.target_id == target_id), None)
    if item is None: raise s.IntentError()
    t, r, scopes, snapshot_digest = intake._authorization_metadata(db, context, item)
    if not r or len(scopes)>256: raise s.IntentError('intent_permission_missing')
    at = _time(clock)
    if r['valid_from'] and at < r['valid_from'] or r['valid_until'] and at >= r['valid_until']:
        raise s.IntentError('intent_permission_missing')
    until = r['valid_until'] or datetime.max.replace(tzinfo=timezone.utc)
    return {'context_id':context.id, 'project_number':context.project_number,
        'context_version':version, 'target_id':target_id, 'authorization_revision_id':r['id'],
        'permission_digest':snapshot_digest, 'permission_source':item.permission_source.model_dump(),
        'data_policy':'synthetic-only/1'}, [until], data


def _mapping_snapshot(db, context, p, clock):
    base, deadlines, _ = _base(db, context, p.context_version, p.target_id, clock)
    endpoint = db.scalar(select(Endpoint).options(load_only(Endpoint.id, Endpoint.target_id, Endpoint.path, Endpoint.method, Endpoint.parameters, raiseload=True)).where(Endpoint.id==p.endpoint_id, Endpoint.target_id==p.target_id)
        .with_for_update(read=True).execution_options(populate_existing=True))
    resource = db.scalar(select(Resource).where(Resource.id==p.resource_id, Resource.target_id==p.target_id)
        .with_for_update(read=True).execution_options(populate_existing=True))
    if endpoint is None or resource is None: raise s.IntentError()
    slot = select_bola_binding(db, endpoint_id=endpoint.id, binding_id=p.binding_id)
    # Only independently confirmed synthetic metadata; no arbitrary private strings.
    if (slot.location != 'path' or endpoint.method != 'GET' or len(re.findall(r'\{[^{}]+\}',endpoint.path))!=1
        or resource.resource_type not in {'folder','project','task','record'}
        or not re.fullmatch(r'[0-9]{1,16}',resource.external_id)):
        raise s.IntentError('intent_unsupported_shape')
    target = db.get(Target,p.target_id)
    url = build_test_case_url(target=target,endpoint=endpoint,resource=resource)
    if '?' in url or len(url)>2048: raise s.IntentError('intent_unsupported_shape')
    scope = _build_scope_context(db=db,target_id=target.id,request_url=url)
    # Frozen selector must be the one the legacy builder actually substitutes.
    from app.generators.bola import detect_resource_binding
    if detect_resource_binding(endpoint).parameter_name != slot.selector: raise s.IntentError('intent_unsupported_shape')
    binding = db.get(EndpointResourceBinding,p.binding_id)
    base.update(endpoint_id=endpoint.id,template=endpoint.path,method='GET',resource_id=resource.id,
        resource_type=resource.resource_type,external_id=resource.external_id,binding_id=p.binding_id,
        selector=slot.selector,location='path',binding_review=binding.review_state,
        binding_provenance=binding.provenance,origin=target.base_url,network_mode=target.network_mode,
        url=url,scope=scope['matched_scope'],scope_context_version=scope['context_version'],builder='legacy-single-resource/1')
    return base, deadlines


def _latest(db, model, context, number):
    return db.scalar(select(model).where(model.context_id==context.id,model.number==number)
        .order_by(model.version.desc()).limit(1).execution_options(populate_existing=True))


def _capacity(db, model, context, limit=s.MAX_VERSIONS):
    if db.scalar(select(func.count()).select_from(model).where(model.context_id==context.id)) >= limit:
        raise s.IntentError('intent_storage_limit')


def _next(db, model, context, number, expected):
    if type(number) is not int or not 1<=number<=1024: raise s.IntentError('intent_invalid',422)
    old = _latest(db,model,context,number)
    if (old.version if old else 0)!=expected: raise s.IntentError('intent_version_conflict')
    _capacity(db,model,context)
    return old


def _exact(db, model, context, ref, clock, *, latest=True):
    row=db.scalar(select(model).where(model.context_id==context.id,model.number==ref.number,
        model.version==ref.version,model.digest==ref.digest).execution_options(populate_existing=True))
    if row is None: raise s.IntentError()
    protocol = PROTOCOLS.get(model, row.body.get('protocol'))
    if model is IntentVersion and protocol not in {'ra-intent/1','ra-health-intent/1'}: raise s.IntentError('intent_integrity')
    if s.digest(protocol,row.body)!=row.digest: raise s.IntentError('intent_integrity')
    if (row.body.get('recorded_at')!=s.stamp(row.recorded_at) or row.body.get('version')!=row.version
        or row.body.get('intent_id' if model is IntentVersion else 'number')!=row.number): raise s.IntentError('intent_integrity')
    if model is IntentVersion and row.body.get('expires_at')!=s.stamp(row.valid_until): raise s.IntentError('intent_integrity')
    if latest and _latest(db,model,context,ref.number).id!=row.id: raise s.IntentError('intent_dependency_changed')
    if not row.recorded_at <= _time(clock) < row.valid_until: raise s.IntentError('intent_expired')
    return row


def _audit(db, context, code, at):
    _capacity(db,IntentAudit,context,s.MAX_AUDIT)
    row=IntentAudit(context_id=context.id,code=code,recorded_at=at)
    db.add(row);db.flush()
    return row.id


def final_boundary(value, clock=None):
    at=_clock(clock)()
    if not s.timestamp(value['body']['recorded_at']) <= at < s.timestamp(value['eligibility_until']):
        raise s.IntentError('intent_expired')


def _receipt(db,context,row,kind,deadlines,clock):
    deadlines=[row.valid_until,*deadlines]
    value=dict(protocol='ra-w1-receipt/1',reference=_ref(row),kind=kind,body=row.body,
        eligibility_until=s.stamp(min(deadlines)),audit_id=_audit(db,context,kind if kind!='intent' else 'intent',_time(clock)),
        execution_authorized=False,execution_status='w2_dependency_closed')
    if kind=='intent': value['body']={**row.body,'link':row.link,'link_digest':row.link_digest}
    value=s.Receipt.model_validate(value).model_dump()
    s.output(value)
    final_boundary(value,clock)
    return value


def confirm_mapping(db,project,context_id,number,payload,*,now=None):
    now=_clock(now)
    p=s.validate(s.MappingInput,payload);context=_locked(db,project,context_id)
    with db.begin_nested():
        old=_next(db,IntentMapping,context,number,p.expected_version)
        snapshot,deadlines=_mapping_snapshot(db,context,p,now)
        if old and old.target_id!=p.target_id: raise s.IntentError()
        at=_time(now)
        if old and at<old.recorded_at: raise s.IntentError('intent_clock_invalid')
        body={'protocol':'ra-mapping/1','number':number,'version':p.expected_version+1,'command':p.model_dump(),'snapshot':snapshot,
            'supersedes':_ref(old) if old else None,'recorded_at':s.stamp(at),'actor':'local_operator'}
        if len(s.canonical(body))>s.MAX_CORE: raise s.IntentError('intent_core_limit')
        row=IntentMapping(context_id=context.id,context_version=p.context_version,target_id=p.target_id,
            number=number,version=p.expected_version+1,body=body,digest=s.digest('ra-mapping/1',body),recorded_at=at,valid_until=min(deadlines))
        db.add(row);db.flush()
        return _receipt(db,context,row,'mapping',deadlines,now)


def _mapping(db,context,reference,clock):
    row=_exact(db,IntentMapping,context,reference,clock)
    command=s.validate(s.MappingInput,row.body['command'])
    if command.decision!='confirm': raise s.IntentError('intent_mapping_unconfirmed')
    snapshot,deadlines=_mapping_snapshot(db,context,command,clock)
    if snapshot!=row.body['snapshot']: raise s.IntentError('intent_dependency_changed')
    return row,snapshot,[row.valid_until,*deadlines]


def _actor(db,context,action,mapping,clock):
    row=db.scalar(select(ResearchSubjectVersion).where(ResearchSubjectVersion.context_id==context.id,
        ResearchSubjectVersion.proposal_number==action.subject.number,ResearchSubjectVersion.version_number==action.subject.version))
    if row is None or subject._latest(db,context.id,action.subject.number).id!=row.id: raise s.IntentError()
    p=subject.validate(SubjectInput,row.proposal)
    if any(getattr(p,k)!=mapping[k] for k in ('context_version','target_id','endpoint_id','binding_id','resource_id')):
        raise s.IntentError('intent_subject_mismatch')
    # Keep the raiseload identity strongly referenced through the M14/M12 call;
    # SQLAlchemy's identity map is weak and a subsequent resolver db.get must
    # not reload the legacy credentials column after _qualify returns.
    identity=subject._identity(db,p.target_id,p.test_identity_id)
    if identity is None: raise s.IntentError('intent_identity_missing')
    at,credential_version,facts,gaps=subject._qualify(db,context,p,_time(clock))
    ignored={'budget_unapproved','intent_decision_pending','membership_unverified','owner_unknown','session_health_unverified','session_unknown','credential_update_needed'}
    if set(gaps)-ignored: raise s.IntentError('intent_facts_missing')
    if row.credential_version_id!=credential_version: raise s.IntentError('intent_credential_changed')
    if p.identity_choice=='bearer' and (p.credential_binding_id is None or credential_version is None):
        raise s.IntentError('intent_credential_missing')
    preview=preview_bola_binding_matrix(db,endpoint_id=p.endpoint_id,
        assignments=[BOLAResourceSlotAssignment(p.binding_id,p.resource_id)],test_identity_ids=[p.test_identity_id],evaluation_time=at)
    candidates=preview.slots[0].preview.candidates
    if len(candidates)!=1: raise s.IntentError('intent_facts_missing')
    candidate=_json(asdict(candidates[0]))
    if action.role in {'baseline','health_baseline','health_probe'} and facts['expected_access']!='allowed':
        raise s.IntentError('intent_baseline_missing')
    rows=list(db.execute(select(ResourceAccessAssertion.id,ResourceAccessAssertion.relationship,
        ResourceAccessAssertion.expected_access,ResourceAccessAssertion.provenance,ResourceAccessAssertion.verification_state,
        ResourceAccessAssertion.asserted_at,ResourceAccessAssertion.valid_from,ResourceAccessAssertion.valid_until,
        ResourceAccessAssertion.confidence).where(ResourceAccessAssertion.resource_id==p.resource_id,
        ResourceAccessAssertion.test_identity_id==p.test_identity_id,ResourceAccessAssertion.verification_state=='verified',
        (ResourceAccessAssertion.valid_until.is_(None)) | (ResourceAccessAssertion.valid_until>at))
        .order_by(ResourceAccessAssertion.id).limit(257)).mappings())
    if len(rows)>256: raise s.IntentError('intent_fact_limit')
    deadlines=[]
    for fact in rows:
        start=max(fact['asserted_at'],fact['valid_from'] or fact['asserted_at'])
        end=fact['valid_until']
        if end is not None and end<=start: continue
        if start>at: deadlines.append(start)
        elif end: deadlines.append(end)
    if p.sources:
        gaps=knowledge._shape_gaps(db,context,{'proposal':p.model_dump()},_time(clock))
        if {'preview_only_shape','object_shape_missing'} & gaps: raise s.IntentError('intent_source_shape_unavailable')
    sources=[]
    for source in sorted(p.sources,key=lambda x:(x.observation_id,x.source_entry_index)):
        record=db.scalar(select(ObservationRecord).where(ObservationRecord.context_id==context.id,ObservationRecord.id==source.observation_id))
        prep=db.get(ObservationPreparation,record.preparation_id)
        deadlines.extend([record.expires_at,observation.timestamp(prep.registry['valid_until'])])
        sources.append(source.model_dump())
    actor={'subject':action.subject.model_dump(),'subject_digest':s.digest('ra-subject-reference/1',p.model_dump()),
        'identity_id':p.test_identity_id,'auth_type':p.identity_choice,'credential_binding_id':p.credential_binding_id,
        'credential_version_id':credential_version,'facts':facts,'assertions':[_json(dict(r)) for r in rows],
        'sources':sources,'candidate':candidate}
    return actor,deadlines


def _manifest_snapshot(db,context,p,clock):
    base,deadlines,intake_data=_base(db,context,p.context_version,p.target_id,clock)
    b=intake_data.budget
    if (b.target_requests is None or len(p.actions)>b.target_requests or b.duration_seconds is None
        or p.duration_seconds>b.duration_seconds or b.rate_millirequests_per_second is None
        or p.rate_millirequests_per_second>b.rate_millirequests_per_second or b.concurrency!=1):
        raise s.IntentError('intent_budget_exceeded')
    actions=[]
    for action in p.actions:
        row,mapping,ends=_mapping(db,context,action.mapping,clock)
        if row.target_id!=p.target_id or row.context_version!=p.context_version: raise s.IntentError()
        actor,ends2=_actor(db,context,action,mapping,clock)
        actions.append({'role':action.role,'mapping':action.mapping.model_dump(),'request':mapping,'actor':actor})
        deadlines.extend([*ends,*ends2])
    a,b=actions[:2]
    if a['request']!=b['request'] or a['actor']['identity_id']==b['actor']['identity_id']:
        raise s.IntentError('intent_pair_mismatch')
    for action in actions[2:]:
        parent=actions[0 if action['role']=='health_baseline' else 1]
        if (action['actor']['identity_id']!=parent['actor']['identity_id']
            or action['actor']['credential_version_id']!=parent['actor']['credential_version_id']
            or action['actor']['auth_type']!='bearer'):
            raise s.IntentError('intent_health_mismatch')
    sources={(r['observation_id'],r['source_entry_index']) for a in actions for r in a['actor']['sources']}
    if len(sources)>8: raise s.IntentError('intent_source_limit')
    base['actions']=actions
    return base,deadlines


def record_manifest(db,project,context_id,number,payload,*,now=None):
    now=_clock(now)
    p=s.validate(s.ManifestInput,payload);context=_locked(db,project,context_id)
    with db.begin_nested():
        old=_next(db,IntentManifest,context,number,p.expected_version)
        snapshot,deadlines=_manifest_snapshot(db,context,p,now)
        if old and old.target_id!=p.target_id: raise s.IntentError()
        at=_time(now);deadlines.append(at+timedelta(seconds=p.duration_seconds))
        if old and at<old.recorded_at: raise s.IntentError('intent_clock_invalid')
        body={'protocol':'ra-manifest/1','number':number,'version':p.expected_version+1,'command':p.model_dump(),'snapshot':snapshot,
            'supersedes':_ref(old) if old else None,'recorded_at':s.stamp(at)}
        if len(s.canonical(body))>s.MAX_CORE: raise s.IntentError('intent_core_limit')
        row=IntentManifest(context_id=context.id,context_version=p.context_version,target_id=p.target_id,
            number=number,version=p.expected_version+1,body=body,digest=s.digest('ra-manifest/1',body),recorded_at=at,valid_until=min(deadlines))
        db.add(row);db.flush()
        return _receipt(db,context,row,'manifest',deadlines,now)


def _manifest(db,context,ref,clock):
    row=_exact(db,IntentManifest,context,ref,clock)
    p=s.validate(s.ManifestInput,row.body['command'])
    snapshot,deadlines=_manifest_snapshot(db,context,p,clock)
    if snapshot!=row.body['snapshot']: raise s.IntentError('intent_dependency_changed')
    return row,snapshot,[row.valid_until,*deadlines]


def decide_budget(db,project,context_id,payload,*,now=None):
    now=_clock(now)
    p=s.validate(s.BudgetDecision,payload);context=_locked(db,project,context_id)
    with db.begin_nested():
        row,_,deadlines=_manifest(db,context,p.manifest,now)
        events=list(db.scalars(select(IntentBudgetDecision).where(IntentBudgetDecision.manifest_id==row.id).order_by(IntentBudgetDecision.sequence).limit(17)))
        if len(events)!=p.expected_sequence or len(events)>=s.MAX_DECISIONS: raise s.IntentError('intent_budget_conflict')
        at=_time(now)
        if events and at<events[-1].recorded_at: raise s.IntentError('intent_clock_invalid')
        event=IntentBudgetDecision(manifest_id=row.id,sequence=len(events)+1,decision=p.decision,
            body={'manifest':_ref(row),'actor':'local_operator','evidence':p.evidence.model_dump(),'recorded_at':s.stamp(at)},recorded_at=at)
        db.add(event);db.flush()
        value=_receipt(db,context,row,'budget',deadlines,now)
        value['body']={**event.body,'decision_id':event.id,'sequence':event.sequence,'decision':event.decision}
        s.output(value)
        final_boundary(value,now)
        return value


def _budget(db,manifest,clock):
    event=db.scalar(select(IntentBudgetDecision).where(IntentBudgetDecision.manifest_id==manifest.id)
        .order_by(IntentBudgetDecision.sequence.desc()).limit(1))
    if event is None or event.decision!='approved' or event.body['manifest']!=_ref(manifest) or event.recorded_at>_time(clock):
        raise s.IntentError('intent_budget_unapproved')
    return {'decision_id':event.id,'sequence':event.sequence,'manifest':_ref(manifest)}


def _interpretation(db,context,snapshot,purpose,clock):
    # W2 owns production provenance verification. No environment flag, schema
    # override, registered provider, fixture ID or caller receipt can bypass this.
    raise s.IntentError('intent_w2_evidence_unavailable')


def _qualification(proof,snapshot,purpose,clock):
    """Check W1 temporal/pinning envelope of an independently supplied W2 result.

    The only production producer above refuses; tests monkeypatch it explicitly.
    This function neither interprets responses nor declares caller evidence true.
    """
    if type(proof) is not dict or set(proof)!={'interpreter','health'}:
        raise s.IntentError('intent_w2_evidence_unavailable')
    interpreter=proof['interpreter']
    if (type(interpreter) is not dict or set(interpreter)!={'id','version','digest'}
        or type(interpreter['id']) is not int or interpreter['id']<=0
        or type(interpreter['version']) is not int or interpreter['version']<=0
        or not re.fullmatch('[0-9a-f]{64}',interpreter['digest'])):
        raise s.IntentError('intent_w2_evidence_unavailable')
    health=proof['health']
    needed=[a['actor'] for a in snapshot['actions'][:2] if a['actor']['auth_type']=='bearer'] if purpose=='business' else []
    if type(health) is not list or len(health)!=len(needed): raise s.IntentError('intent_health_missing')
    deadlines=[];at=_time(clock)
    for actor,receipt in zip(needed,health):
        fields={'identity_id','credential_binding_id','credential_version_id','context_id','target_id','evidence_id','digest','send_at','complete_at','verified_at','valid_until'}
        if type(receipt) is not dict or set(receipt)!=fields: raise s.IntentError('intent_health_missing')
        if any(type(receipt[k]) is not int or not 1<=receipt[k]<=2147483647 for k in ('identity_id','credential_binding_id','credential_version_id','context_id','target_id','evidence_id')): raise s.IntentError('intent_health_missing')
        if any(receipt[k]!=actor[k] for k in ('identity_id','credential_binding_id','credential_version_id')) or receipt['context_id']!=snapshot['context_id'] or receipt['target_id']!=snapshot['target_id']:
            raise s.IntentError('intent_health_mismatch')
        if type(receipt['evidence_id']) is not int or receipt['evidence_id']<=0 or not re.fullmatch('[0-9a-f]{64}',receipt['digest']): raise s.IntentError('intent_health_missing')
        send,complete,verified,end=[s.timestamp(receipt[k]) for k in ('send_at','complete_at','verified_at','valid_until')]
        end=min(end,send+timedelta(seconds=120))
        if not send<=complete<=verified<=at<end: raise s.IntentError('intent_health_expired')
        deadlines.append(end)
    if len(s.canonical(proof))>4096: raise s.IntentError('intent_response_limit')
    return deadlines


def _knowledge(db,context,ref,snapshot,clock):
    if ref is None: return None,[]
    row=knowledge._exact(db,context,ref)
    content=ks.validate(ks.Content,row.content)
    at=_time(clock);pub=knowledge._publication(db,row,at)
    if (ks.digest(content.model_dump())!=row.digest or content.category!='rule' or pub is None
        or pub[1].evidence!='operator_recorded' or not isinstance(pub[1].validation_ref,ks.ValidationRef)
        or (row.scope=='project' and (row.context_id!=context.id or row.context_version!=snapshot['context_version'] or row.target_id!=snapshot['target_id']))
        or any(a['actor']['auth_type'] not in content.applicability.actors for a in snapshot['actions'][:2])):
        raise s.IntentError('intent_knowledge_unavailable')
    rows=list(db.scalars(select(KnowledgeVersion).where((KnowledgeVersion.context_id==context.id)|(KnowledgeVersion.scope=='reusable_synthetic')).order_by(KnowledgeVersion.id).limit(257)))
    if len(rows)>256: raise s.IntentError('intent_knowledge_limit')
    catalog={ks.canonical(knowledge._ref(r)):r for r in rows}
    disabled=set(db.scalars(select(KnowledgeEvent.version_id).where(KnowledgeEvent.version_id.in_([r.id for r in rows]),KnowledgeEvent.action=='disable').limit(4097)))
    if not knowledge._lineage_eligible(content,catalog,disabled): raise s.IntentError('intent_knowledge_unavailable')
    sources={(r['observation_id'],r['source_entry_index']) for a in snapshot['actions'] for r in a['actor']['sources']}
    sources.update((r.observation_id,r.source_entry_index) for r in content.source_refs if r.kind=='observation')
    if len(sources)>8: raise s.IntentError('intent_source_limit')
    deadlines=knowledge._sources(db,context,content,snapshot['target_id'],_time(clock))+[pub[2]]
    return {'reference':ref.model_dump(),'contract':content.contract.model_dump() if hasattr(content.contract,'model_dump') else content.contract,
        'validation_ref':pub[1].validation_ref.model_dump(),'review_event_id':pub[1].review_event_id,
        'reuse_event_id':pub[1].reuse_event_id,'publication_event_id':pub[0].id},deadlines


def _selected_snapshot(snapshot,purpose):
    if purpose=='business': return snapshot
    selected=[a for a in snapshot['actions'] if a['role']==purpose]
    if len(selected)!=1: raise s.IntentError('intent_health_missing')
    return {**snapshot,'actions':selected}


def convert(db,project,context_id,number,payload,*,now=None):
    now=_clock(now)
    p=s.validate(s.ConvertInput,payload);context=_locked(db,project,context_id)
    with db.begin_nested():
        old=_next(db,IntentVersion,context,number,p.expected_version)
        manifest,snapshot,deadlines=_manifest(db,context,p.manifest,now)
        budget=_budget(db,manifest,now)
        if p.purpose!='business' and p.knowledge is not None: raise s.IntentError('intent_invalid',422)
        rule,ends=_knowledge(db,context,p.knowledge,snapshot,now);deadlines.extend(ends)
        proof=_interpretation(db,context,snapshot,p.purpose,now)
        deadlines.extend(_qualification(proof,snapshot,p.purpose,now))
        actions=snapshot['actions'][:2] if p.purpose=='business' else [a for a in snapshot['actions'] if a['role']==p.purpose]
        if not actions: raise s.IntentError('intent_health_missing')
        if old and old.target_id!=manifest.target_id: raise s.IntentError()
        # A manifest slot can fund only one intent, irrespective of number/version.
        previous=list(db.scalars(select(IntentVersion).where(IntentVersion.context_id==context.id).order_by(IntentVersion.id).limit(1025)))
        if any(r.body['manifest']==p.manifest.model_dump() and r.body['purpose']==p.purpose for r in previous):
            raise s.IntentError('intent_manifest_consumed')
        at=_time(now);deadlines.append(at+timedelta(seconds=300));until=min(deadlines)
        if old and at<old.recorded_at: raise s.IntentError('intent_clock_invalid')
        protocol='ra-intent/1' if p.purpose=='business' else 'ra-health-intent/1'
        core={'protocol':protocol,'intent_id':number,'version':p.expected_version+1,'pair_id':f'{context.id}:{number}:{p.expected_version+1}',
            'supersedes':_ref(old) if old else None,'recorded_at':s.stamp(at),'created_at':s.stamp(at),'expires_at':s.stamp(until),
            'purpose':p.purpose,'manifest':p.manifest.model_dump(),'snapshot':_selected_snapshot(snapshot,p.purpose),'budget':budget,
            'interpretation':proof,'knowledge':rule,'review':p.evidence.model_dump(),'request_count':len(actions),
            'limits':{'health_seconds':120,'pair_seconds':30,'intent_seconds':300,'concurrency':1,
                'manifest_requests':len(snapshot['actions']),'rate_millirequests_per_second':manifest.body['command']['rate_millirequests_per_second']}}
        if len(s.canonical(core))>s.MAX_CORE: raise s.IntentError('intent_core_limit')
        core_digest=s.digest(protocol,core)
        members=[]
        for selected in actions:
            req,actor=selected['request'],selected['actor']
            case=db.scalar(select(TestCase).where(TestCase.endpoint_id==req['endpoint_id'],TestCase.actor_identity_id==actor['identity_id'],TestCase.resource_id==req['resource_id'],TestCase.test_type==TEST_TYPE).with_for_update())
            if case is None:
                case=TestCase(endpoint_id=req['endpoint_id'],actor_identity_id=actor['identity_id'],resource_id=req['resource_id'],test_type=TEST_TYPE,ownership_relation='unspecified',expected_statuses=[],status='pending')
                db.add(case);db.flush()
            if case.ownership_relation!='unspecified' or case.expected_statuses!=[]: raise s.IntentError('intent_integrity')
            role=selected['role'] if p.purpose=='business' else 'health'
            marker={'protocol':protocol,'intent_id':number,'version':p.expected_version+1,'intent_digest':core_digest,'pair_id':core['pair_id'],'role':role}
            plan=_create_execution_plan(db,target_id=manifest.target_id,authorization_revision_id=snapshot['authorization_revision_id'],actor_identity_id=actor['identity_id'],credential_binding_id=actor['credential_binding_id'],
                actions=[PlanActionInput('GET',req['url'],case.id,req['resource_id'])],policy_context={'context_version':req['scope_context_version'],'matched_scope':req['scope'],'research_intent':marker})
            from app.services.safety_audit import SafetyAuditService
            SafetyAuditService(db).append_plan_created(plan=plan,test_case_id=case.id)
            members.append({'role':role,'plan_id':plan.id,'plan_digest':plan.plan_digest,'action_id':plan.actions[0].id,'test_case_id':case.id})
        link_protocol='ra-plan-link/1' if p.purpose=='business' else 'ra-health-link/1'
        link={'protocol':link_protocol,'intent_digest':core_digest,'members':members}
        row=IntentVersion(context_id=context.id,context_version=manifest.context_version,target_id=manifest.target_id,number=number,version=p.expected_version+1,
            body=core,digest=core_digest,recorded_at=at,valid_until=until,link=link,link_digest=s.digest(link_protocol,link))
        db.add(row);db.flush()
        for member in members:
            db.add(IntentPlanMember(intent_id=row.id,role=member['role'],plan_id=member['plan_id'],action_id=member['action_id'],test_case_id=member['test_case_id']))
        db.flush()
        return _receipt(db,context,row,'intent',deadlines,now)


def read(db,project,context_id,kind,reference,*,now=None):
    now=_clock(now)
    ref=s.validate(s.Reference,reference);context=_locked(db,project,context_id)
    with db.begin_nested():
        if kind=='mapping': row,_,deadlines=_mapping(db,context,ref,now)
        elif kind=='manifest': row,_,deadlines=_manifest(db,context,ref,now)
        elif kind=='intent':
            row=_exact(db,IntentVersion,context,ref,now)
            manifest,snapshot,deadlines=_manifest(db,context,s.Reference.model_validate(row.body['manifest']),now)
            if _selected_snapshot(snapshot,row.body['purpose'])!=row.body['snapshot'] or _budget(db,manifest,now)!=row.body['budget']: raise s.IntentError('intent_dependency_changed')
            rule,ends=_knowledge(db,context,ks.ExactRef.model_validate(row.body['knowledge']['reference']) if row.body['knowledge'] else None,snapshot,now)
            if rule!=row.body['knowledge']: raise s.IntentError('intent_dependency_changed')
            deadlines.extend(ends)
            proof=_interpretation(db,context,snapshot,row.body['purpose'],now)
            if proof!=row.body['interpretation']: raise s.IntentError('intent_dependency_changed')
            deadlines.extend(_qualification(proof,snapshot,row.body['purpose'],now))
            if s.digest(row.link['protocol'],row.link)!=row.link_digest or row.link['intent_digest']!=row.digest: raise s.IntentError('intent_integrity')
            members=list(db.scalars(select(IntentPlanMember).where(IntentPlanMember.intent_id==row.id).order_by(IntentPlanMember.id).limit(3)))
            if len(members)!=row.body['request_count'] or len(members)!=len(row.link['members']): raise s.IntentError('intent_integrity')
            roles=['baseline','probe'] if row.body['purpose']=='business' else ['health']
            if [m.role for m in members]!=roles: raise s.IntentError('intent_integrity')
            selected=snapshot['actions'][:2] if row.body['purpose']=='business' else [a for a in snapshot['actions'] if a['role']==row.body['purpose']]
            for member,entry,chosen in zip(members,row.link['members'],selected):
                plan=validate_persisted_plan_integrity(db,member.plan_id)
                case=db.get(TestCase,member.test_case_id)
                if (case is None or case.test_type!=TEST_TYPE or case.ownership_relation!='unspecified' or case.expected_statuses!=[]
                    or case.endpoint_id!=chosen['request']['endpoint_id'] or case.resource_id!=chosen['request']['resource_id']
                    or case.actor_identity_id!=chosen['actor']['identity_id']): raise s.IntentError('intent_integrity')
                marker={'protocol':row.body['protocol'],'intent_id':row.number,'version':row.version,'intent_digest':row.digest,'pair_id':row.body['pair_id'],'role':member.role}
                if (plan.target_id!=row.target_id or plan.authorization_revision_id!=snapshot['authorization_revision_id']
                    or plan.actor_identity_id!=chosen['actor']['identity_id'] or plan.credential_binding_id!=chosen['actor']['credential_binding_id']
                    or plan.action_count!=1 or len(plan.actions)!=1 or plan.actions[0].id!=member.action_id
                    or plan.actions[0].method!='GET' or plan.actions[0].url!=chosen['request']['url']
                    or plan.actions[0].test_case_id!=case.id or plan.actions[0].resource_id!=case.resource_id
                    or plan.policy_context!={'context_version':chosen['request']['scope_context_version'],'matched_scope':chosen['request']['scope'],'research_intent':marker}): raise s.IntentError('intent_integrity')
                if (member.plan_id!=entry['plan_id'] or member.role!=entry['role'] or member.action_id!=entry['action_id']
                    or member.test_case_id!=entry['test_case_id'] or plan.plan_digest!=entry['plan_digest']
                    or plan.policy_context.get('research_intent',{}).get('intent_digest')!=row.digest): raise s.IntentError('intent_integrity')
        else: raise s.IntentError('intent_invalid',422)
        return _receipt(db,context,row,kind,deadlines,now)
