"""Explicit W2 confirmation, exact platform health selection and pair evidence.

All local metadata writes retain caller transaction ownership. Network dispatch
is separate; this module never imports observations as execution evidence.
"""
from datetime import timedelta
from sqlalchemy import select, func
from app.db.models.research_verification import (VerificationContract as Contract,
    VerificationHealthSelection as HealthSelection, VerificationAttempt as Attempt,
    VerificationWitness as Witness, VerificationPair as Pair, VerificationAudit as Audit)
from app.db.models.research_intent import IntentVersion, IntentManifest, IntentPlanMember
from app.db.models.test_run import TestRun
from app.db.models.execution_plan_progress import ExecutionPlanProgress
from app.db.models.execution_plan_cancellation import ExecutionPlanCancellation
from app.schemas import research_intent as c, research_verification as s
from app.services import research_intent as intent
from app.services import research_response_semantics as semantics
from app.services import research_verification_clock as vc

PROTOCOLS = {Contract:'ra-verification-contract/1', HealthSelection:'ra-health-selection/1',
             Attempt:'ra-dispatch-attempt/1', Witness:'ra-execution-witness/1', Pair:'ra-pair-evidence/1'}


def ref(row):
    return {'id':row.id,'digest':row.digest}


def cap(db, model, context_id, limit=1024):
    if db.scalar(select(func.count()).select_from(model).where(model.context_id==context_id)) >= limit:
        raise c.IntentError('verification_storage_limit')


def audit(db, context_id, code, clock):
    cap(db,Audit,context_id,4096)
    row=Audit(context_id=context_id,code=code,recorded_at=clock())
    db.add(row);db.flush()
    return row.id


def integrity(row, model, context_id):
    if (row is None or row.context_id!=context_id or row.body.get('protocol')!=PROTOCOLS[model]
            or row.body.get('recorded_at')!=c.stamp(row.recorded_at)
            or c.digest(PROTOCOLS[model],row.body)!=row.digest):
        raise c.IntentError('verification_evidence_unavailable')
    return row


def exact(db, model, context, reference):
    reference=c.validate(s.EvidenceRef,reference)
    row=db.scalar(select(model).where(model.context_id==context.id,model.id==reference.id,
        model.digest==reference.digest).execution_options(populate_existing=True))
    return integrity(row,model,context.id)


def final_boundary(value, clock):
    clock=vc.current(clock)
    vc.check_mark(value['body']['clock'],clock,end=c.timestamp(value['eligibility_until']))


def output(value):
    value=s.Receipt.model_validate(value).model_dump()
    raw=c.canonical(value)
    if len(raw)>c.MAX_OUTPUT:raise c.IntentError('verification_response_limit',500)
    return raw


def receipt(db, context, row, kind, deadline, clock, *, code='read'):
    value={'protocol':'ra-verification-receipt/1','kind':kind,'reference':ref(row),
           'body':row.body,'eligibility_until':c.stamp(deadline),
           'audit_id':audit(db,context.id,code,clock),'execution_authorized':False,'finding_confirmed':False}
    output(value);final_boundary(value,clock)
    return value


def _contract(db,context,manifest,snapshot,clock):
    row=db.scalar(select(Contract).where(Contract.context_id==context.id,Contract.manifest_id==manifest.id)
        .execution_options(populate_existing=True))
    if row is None:raise c.IntentError('intent_w2_evidence_unavailable')
    integrity(row,Contract,context.id)
    intent._exact(db,Contract,context,c.Reference.model_validate(intent._ref(row)),clock)
    if (row.body['manifest']!=intent._ref(manifest) or row.body['interpreter']!=semantics.DEFINITION_DIGEST
            or row.context_version!=manifest.context_version or row.target_id!=manifest.target_id):
        raise c.IntentError('verification_contract_changed')
    _expectations(row.body['expectations'],snapshot)
    vc.check_mark(row.body['clock'],clock,end=row.valid_until)
    return row


def _expectations(values,snapshot):
    parsed=[c.validate(s.Expectation,value) for value in values]
    if [p.role for p in parsed]!=[a['role'] for a in snapshot['actions']]:
        raise c.IntentError('verification_expectations_missing')
    for exp, action in zip(parsed,snapshot['actions']):
        bearer=action['actor']['auth_type']=='bearer'
        if (exp.object_value!=action['request']['external_id'] or bearer!=(exp.identity_value is not None)
                or (exp.role.startswith('health_') and not bearer)):
            raise c.IntentError('verification_expectation_mismatch')
    return parsed


def confirm(db,project,context_id,number,payload,*,now=None):
    clock=vc.current(now);p=c.validate(s.ConfirmInput,payload);context=intent._locked(db,project,context_id)
    with db.begin_nested():
        old=intent._next(db,Contract,context,number,p.expected_version)
        manifest,snapshot,ends=intent._manifest(db,context,p.manifest,clock)
        _expectations([e.model_dump() for e in p.expectations],snapshot)
        if db.scalar(select(Contract.id).where(Contract.manifest_id==manifest.id)) is not None:
            raise c.IntentError('verification_contract_already_confirmed')
        if old and (old.target_id!=manifest.target_id or old.recorded_at>clock()):
            raise c.IntentError('verification_contract_changed')
        mark=clock.mark()
        body={'protocol':PROTOCOLS[Contract],'number':number,'version':p.expected_version+1,
              'manifest':p.manifest.model_dump(),'interpreter':semantics.DEFINITION_DIGEST,
              'expectations':[e.model_dump() for e in p.expectations],
              'decision':'confirmed','actor':'local_operator','evidence':p.evidence.model_dump(),
              'supersedes':intent._ref(old) if old else None,'recorded_at':mark['at'],'clock':mark}
        if len(c.canonical(body))>32768:raise c.IntentError('verification_record_limit')
        row=Contract(context_id=context.id,context_version=manifest.context_version,target_id=manifest.target_id,
            manifest_id=manifest.id,number=number,version=p.expected_version+1,body=body,
            digest=c.digest(PROTOCOLS[Contract],body),recorded_at=c.timestamp(mark['at']),valid_until=min(ends))
        db.add(row);db.flush()
        return receipt(db,context,row,'contract',min(ends),clock,code='confirm')


def witness(db,context,reference,*,current_definition=True):
    row=exact(db,Witness,context,reference);body=row.body
    attempt=integrity(db.get(Attempt,row.attempt_id,populate_existing=True),Attempt,context.id)
    core=db.get(IntentVersion,row.intent_id,populate_existing=True)
    run=db.get(TestRun,row.run_id,populate_existing=True)
    progress=db.get(ExecutionPlanProgress,row.plan_id,populate_existing=True)
    if core is None or core.context_id!=context.id:
        raise c.IntentError('verification_evidence_unavailable')
    member=next((m for m in core.link['members'] if m['plan_id']==row.plan_id),None)
    if (member is None or attempt.intent_id!=row.intent_id or attempt.plan_id!=row.plan_id
            or body['intent']!=intent._ref(core) or body['link_digest']!=core.link_digest
            or attempt.body['intent']!=intent._ref(core) or attempt.body['link_digest']!=core.link_digest
            or attempt.body['manifest']!=core.body['manifest'] or attempt.manifest_id!=db.scalar(select(IntentManifest.id).where(IntentManifest.context_id==context.id,IntentManifest.number==core.body['manifest']['number'],IntentManifest.version==core.body['manifest']['version']))
            or attempt.body['plan_id']!=row.plan_id or attempt.body['action_id']!=member['action_id']
            or attempt.body['role']!=member['role'] or attempt.body['plan_digest']!=member['plan_digest']
            or attempt.slot!=(core.body['purpose'] if member['role']=='health' else member['role'])
            or body['attempt']!=ref(attempt) or body['plan_id']!=row.plan_id or body['run_id']!=row.run_id
            or body['action_id']!=member['action_id'] or body['role']!=member['role']
            or body['send']['clock_domain']!=attempt.body['prepared']['clock_domain']
            or body['send']['monotonic_ns']<attempt.body['prepared']['monotonic_ns']
            or c.timestamp(body['send']['at'])<c.timestamp(attempt.body['prepared']['at'])
            or body['credential_version_id']!=attempt.body['credential_version_id']
            or (current_definition and body['interpreter']!=semantics.DEFINITION_DIGEST)
            or run is None or run.execution_plan_id!=row.plan_id or run.test_case_id!=member['test_case_id']
            or run.authorization_revision_id!=core.body['snapshot']['authorization_revision_id']
            or run.response_body is not None or run.response_status!=body['response_status']
            or c.digest('ra-request-snapshot/1',run.request_data)!=body['request_digest']
            or progress is None or progress.phase!='network_started'
            or progress.fencing_generation!=attempt.body['fencing_generation']):
        raise c.IntentError('verification_provenance_mismatch')
    if body['outcome'] not in ('healthy','object_read','business_denied','inconclusive') or body['temporal_status'] not in ('qualified','dependency_unavailable','network_incomplete','clock_or_deadline_invalid'):
        raise c.IntentError('verification_provenance_mismatch')
    if body['temporal_status']=='qualified':
        marks=[body[key] for key in ('send','complete','clock')]
        if (len({m['clock_domain'] for m in marks})!=1
                or not c.timestamp(marks[0]['at'])<=c.timestamp(marks[1]['at'])<=c.timestamp(marks[2]['at'])
                or not marks[0]['monotonic_ns']<=marks[1]['monotonic_ns']<=marks[2]['monotonic_ns']):
            raise c.IntentError('verification_clock_anomaly')
        if body['outcome']!='inconclusive' and (run.error_message is not None or body['response_status']!=(403 if body['outcome']=='business_denied' else 200)):
            raise c.IntentError('verification_provenance_mismatch')
    from app.db.models.authorization_revision import AuthorizationRevision
    from app.db.models.execution_plan_approval_record import ExecutionPlanApprovalRecord
    revision=db.get(AuthorizationRevision,run.authorization_revision_id)
    approval_id=attempt.body['approval_id']
    approved=db.get(ExecutionPlanApprovalRecord,approval_id) if approval_id is not None else None
    if revision is None or (revision.require_human_execution_approval and approved is None):
        raise c.IntentError('verification_approval_provenance_missing')
    if approval_id is not None and (approved is None or approved.execution_plan_id!=row.plan_id
            or approved.decision!='approved' or approved.plan_digest!=member['plan_digest']):
        raise c.IntentError('verification_approval_provenance_missing')
    return row,core


def _current_approval(db,core,plan_id):
    from app.db.models.authorization_revision import AuthorizationRevision
    from app.db.models.execution_plan import ExecutionPlan
    from app.services.execution_plan_approval import _latest_exact_decision
    revision=db.get(AuthorizationRevision,core.body['snapshot']['authorization_revision_id'])
    # Cancellation and exact approval writers coordinate on the plan row.
    # Hold it through this consumption transaction, including the final send.
    plan=db.scalar(select(ExecutionPlan).where(ExecutionPlan.id==plan_id)
        .with_for_update().execution_options(populate_existing=True))
    if plan is None:raise c.IntentError('verification_plan_mismatch')
    decision=_latest_exact_decision(db,plan)
    if revision is None or (revision.require_human_execution_approval and (decision is None or decision.decision!='approved')):
        raise c.IntentError('verification_approval_required')
    if db.get(ExecutionPlanCancellation,plan_id) is not None:
        raise c.IntentError('verification_cancelled')


def _healthy(db,context,reference,actor,expectation,clock):
    with vc.current(clock).dependency():
        return _healthy_dependency(db,context,reference,actor,expectation,clock)


def _healthy_dependency(db,context,reference,actor,expectation,clock):
    row,core=witness(db,context,reference);body=row.body
    bind_clock(db,context,intent._ref(core),clock)
    if core.body['purpose'] not in ('health_baseline','health_probe') or body['outcome']!='healthy' or body['temporal_status']!='qualified':
        raise c.IntentError('verification_health_unqualified')
    # Current original health intent dependencies are required; never substitute a
    # new source/mapping or a newer credential after source availability recovers.
    current=intent.read(db,context.project_number,context.id,'intent',intent._ref(core),now=clock)
    _current_approval(db,core,row.plan_id)
    original=core.body['snapshot']['actions'][0]
    if any(original['actor'][key]!=actor[key] for key in ('identity_id','auth_type','credential_binding_id','credential_version_id')):
        raise c.IntentError('verification_health_mismatch')
    original_contract=integrity(db.get(Contract,core.body['interpretation']['interpreter']['id']),Contract,context.id)
    exp=next(e for e in original_contract.body['expectations'] if e['role']==core.body['purpose'])
    if exp['identity_value']!=expectation['identity_value'] or exp['identity_key']!=expectation['identity_key']:
        raise c.IntentError('verification_health_mismatch')
    end=min(c.timestamp(current['eligibility_until']),c.timestamp(body['eligibility_until']),c.timestamp(body['send']['at'])+timedelta(seconds=120))
    for mark in ('send','complete','clock'):vc.check_mark(body[mark],clock)
    if not body['send']['monotonic_ns']<=body['complete']['monotonic_ns']<=body['clock']['monotonic_ns']:
        raise c.IntentError('verification_clock_anomaly')
    if not c.timestamp(body['send']['at'])<=c.timestamp(body['complete']['at'])<=c.timestamp(body['clock']['at']):
        raise c.IntentError('verification_clock_anomaly')
    vc.check_mark(body['send'],clock,seconds=120,end=end)
    proof={k:actor[k] for k in ('identity_id','credential_binding_id','credential_version_id')}
    proof.update(context_id=context.id,target_id=core.target_id,evidence_id=row.id,digest=row.digest,
        send_at=body['send']['at'],complete_at=body['complete']['at'],verified_at=body['clock']['at'],valid_until=c.stamp(end))
    return proof,end


def select_health(db,project,context_id,payload,*,now=None):
    clock=vc.current(now);p=c.validate(s.HealthSelectionInput,payload);context=intent._locked(db,project,context_id)
    with db.begin_nested():
        manifest,snapshot,ends=intent._manifest(db,context,p.manifest,clock)
        contract=_contract(db,context,manifest,snapshot,clock)
        needed=[a for a in snapshot['actions'][:2] if a['actor']['auth_type']=='bearer']
        if [a['role'] for a in needed]!=[h.role for h in p.health]:raise c.IntentError('verification_health_missing')
        for action,choice in zip(needed,p.health):
            exp=next(e for e in contract.body['expectations'] if e['role']==action['role'])
            _,end=_healthy(db,context,choice.evidence,action['actor'],exp,clock);ends.append(end)
        cap(db,HealthSelection,context.id)
        if db.scalar(select(HealthSelection.id).where(HealthSelection.manifest_id==manifest.id)) is not None:
            raise c.IntentError('verification_health_already_selected')
        mark=clock.mark();body={'protocol':PROTOCOLS[HealthSelection],'manifest':p.manifest.model_dump(),
            'contract':ref(contract),'health':[h.model_dump() for h in p.health],
            'actor':'local_operator','evidence':p.evidence.model_dump(),'recorded_at':mark['at'],'clock':mark}
        row=HealthSelection(context_id=context.id,manifest_id=manifest.id,contract_id=contract.id,
            body=body,digest=c.digest(PROTOCOLS[HealthSelection],body),recorded_at=c.timestamp(mark['at']))
        db.add(row);db.flush()
        return receipt(db,context,row,'health_selection',min(ends),clock,code='select_health')


def interpretation(db,context,snapshot,purpose,clock,*,manifest):
    clock=vc.current(clock);contract=_contract(db,context,manifest,snapshot,clock)
    result={'interpreter':{'id':contract.id,'version':contract.version,'digest':contract.digest},'health':[]}
    if purpose!='business':return result
    needed=[a for a in snapshot['actions'][:2] if a['actor']['auth_type']=='bearer']
    if not needed:return result
    selected=db.scalar(select(HealthSelection).where(HealthSelection.manifest_id==manifest.id))
    if selected is None:raise c.IntentError('verification_health_missing')
    integrity(selected,HealthSelection,context.id)
    if selected.contract_id!=contract.id or selected.body['contract']!=ref(contract) or selected.body['manifest']!=intent._ref(manifest):
        raise c.IntentError('verification_health_mismatch')
    if [a['role'] for a in needed]!=[h['role'] for h in selected.body['health']]:raise c.IntentError('verification_health_missing')
    for action,choice in zip(needed,selected.body['health']):
        exp=next(e for e in contract.body['expectations'] if e['role']==action['role'])
        proof,_=_healthy(db,context,choice['evidence'],action['actor'],exp,clock)
        result['health'].append(proof)
    return result


def business_boundary(db,context,core,clock):
    """A recorded baseline narrows every current intent/approval/evidence view."""
    if core.body['purpose']!='business':return []
    pid=next(m['plan_id'] for m in core.link['members'] if m['role']=='baseline')
    row=db.scalar(select(Witness).where(Witness.context_id==context.id,Witness.plan_id==pid))
    if row is None:return []
    row,original=witness(db,context,ref(row))
    if original.id!=core.id:raise c.IntentError('verification_pair_mismatch')
    end=c.timestamp(row.body['complete']['at'])+timedelta(seconds=30)
    vc.check_mark(row.body['complete'],clock,seconds=30,end=end)
    return [end]


def bind_clock(db,context,reference,clock):
    from app.services.research_verification_fault import check
    reference=c.validate(c.Reference,reference).model_dump()
    vc.current(clock).bind_intent(db.get_bind(),context.project_number,context.id,reference)
    check(db,context.id,reference)


def current_intent(db,project,context_id,reference,clock):
    context=intent._locked(db,project,context_id)
    bind_clock(db,context,reference,clock)
    value=intent.read(db,project,context_id,'intent',reference,now=clock)
    row=intent._exact(db,IntentVersion,context,c.validate(c.Reference,reference),clock)
    # A W1 test-only proof envelope is never accepted by the production dispatcher.
    manifest,snapshot,ends=intent._manifest(db,context,c.Reference.model_validate(row.body['manifest']),clock)
    proof=interpretation(db,context,snapshot,row.body['purpose'],clock,manifest=manifest)
    if proof!=row.body['interpretation']:raise c.IntentError('verification_provenance_mismatch')
    ends.append(c.timestamp(value['eligibility_until']))
    contract=_contract(db,context,manifest,snapshot,clock)
    return context,row,manifest,contract,min(ends)


def verify_pair(db,project,context_id,payload,*,now=None):
    clock=vc.current(now);p=c.validate(s.PairInput,payload)
    context=intent._locked(db,project,context_id)
    with db.begin_nested():
        _,core,_,_,end=current_intent(db,project,context_id,p.intent,clock)
        if core.body['purpose']!='business':raise c.IntentError('verification_pair_mismatch')
        baseline,bcore=witness(db,context,p.baseline)
        probe,pcore=witness(db,context,p.probe) if p.probe else (None,None)
        if bcore.id!=core.id or baseline.body['role']!='baseline' or (probe and (pcore.id!=core.id or probe.body['role']!='probe')):
            raise c.IntentError('verification_pair_mismatch')
        for item in (baseline,probe):
            if item is not None:_current_approval(db,core,item.plan_id)
        if baseline.body['temporal_status']!='qualified' or (probe and probe.body['temporal_status']!='qualified'):
            raise c.IntentError('verification_expired')
        end=min(end,c.timestamp(baseline.body['complete']['at'])+timedelta(seconds=30))
        vc.check_mark(baseline.body['clock'],clock)
        vc.check_mark(baseline.body['complete'],clock,seconds=30,end=end)
        if probe:
            vc.check_mark(probe.body['clock'],clock)
            if baseline.body['outcome']!='object_read':raise c.IntentError('verification_baseline_unqualified')
            if (c.timestamp(probe.body['send']['at'])<c.timestamp(baseline.body['complete']['at'])
                    or probe.body['send']['monotonic_ns']<baseline.body['complete']['monotonic_ns']):
                raise c.IntentError('verification_clock_anomaly')
            end=min(end,c.timestamp(probe.body['eligibility_until']))
        elif baseline.body['outcome']=='object_read':
            raise c.IntentError('verification_probe_missing')
        expected=core.body['snapshot']['actions'][1]['actor']['facts']['expected_access']
        outcome,reason=semantics.pair_outcome(expected,baseline.body['outcome'],probe.body['outcome'] if probe else None)
        previous=db.scalar(select(Pair).where(Pair.intent_id==core.id))
        if previous:
            integrity(previous,Pair,context.id)
            if previous.body['baseline']!=ref(baseline) or previous.body['probe']!=(ref(probe) if probe else None):
                raise c.IntentError('verification_pair_conflict')
            return receipt(db,context,previous,'pair',end,clock)
        cap(db,Pair,context.id)
        mark=clock.mark();body={'protocol':PROTOCOLS[Pair],'version':1,'intent':p.intent.model_dump(),
            'link_digest':core.link_digest,'baseline':ref(baseline),'probe':ref(probe) if probe else None,
            'baseline_run_id':baseline.run_id,'probe_run_id':probe.run_id if probe else None,
            'outcome':outcome,'reason':reason,'expected_access':expected,
            'interpreter':core.body['interpretation']['interpreter'],'recorded_at':mark['at'],'clock':mark}
        row=Pair(context_id=context.id,intent_id=core.id,baseline_id=baseline.id,probe_id=probe.id if probe else None,
            body=body,digest=c.digest(PROTOCOLS[Pair],body),recorded_at=c.timestamp(mark['at']))
        db.add(row);db.flush()
        return receipt(db,context,row,'pair',end,clock,code='pair')


def _member(db,core,plan_id):
    member=db.scalar(select(IntentPlanMember).where(IntentPlanMember.intent_id==core.id,IntentPlanMember.plan_id==plan_id))
    if member is None:raise c.IntentError('verification_plan_mismatch')
    return member


def approve(db,project,context_id,payload,*,now=None):
    from app.db.models.execution_plan import ExecutionPlan
    from app.db.models.execution_plan_approval_record import ExecutionPlanApprovalRecord
    from app.services.execution_plan_approval import validate_persisted_plan_integrity,_append_exact_decision
    clock=vc.current(now);p=c.validate(s.ApprovalInput,payload)
    context=intent._locked(db,project,context_id)
    with db.begin_nested():
        _,core,_,_,end=current_intent(db,project,context_id,p.intent,clock)
        member=_member(db,core,p.plan_id)
        db.scalar(select(ExecutionPlan).where(ExecutionPlan.id==p.plan_id).with_for_update())
        plan=validate_persisted_plan_integrity(db,p.plan_id)
        count=db.scalar(select(func.count()).select_from(ExecutionPlanApprovalRecord).where(ExecutionPlanApprovalRecord.execution_plan_id==plan.id))
        member_ids=[m['plan_id'] for m in core.link['members']]
        total=db.scalar(select(func.count()).select_from(ExecutionPlanApprovalRecord).where(ExecutionPlanApprovalRecord.execution_plan_id.in_(member_ids)))
        if count!=p.expected_sequence or count>=16 or total>=16:raise c.IntentError('verification_approval_conflict')
        event=_append_exact_decision(db,plan,p.decision)
        mark=clock.mark()
        # The existing immutable exact-plan decision remains authoritative; this
        # bounded receipt is an approval view, never an execution capability.
        body={'protocol':'ra-exact-approval-view/1','intent':p.intent.model_dump(),'link_digest':core.link_digest,
            'plan_id':plan.id,'plan_digest':plan.plan_digest,'action_id':member.action_id,
            'decision':event.decision,'decision_id':event.id,'sequence':count+1,'evidence':p.evidence.model_dump(),
            'recorded_at':mark['at'],'clock':mark}
        value={'protocol':'ra-verification-receipt/1','kind':'approval',
            'reference':{'id':event.id,'digest':c.digest('ra-exact-approval-view/1',body)},'body':body,
            'eligibility_until':c.stamp(end),'audit_id':audit(db,context.id,'approve',clock),
            'execution_authorized':False,'finding_confirmed':False}
        output(value);final_boundary(value,clock)
        return value


def read_execution(db,project,context_id,reference,*,now=None):
    clock=vc.current(now);context=intent._locked(db,project,context_id)
    with db.begin_nested():
        row,core=witness(db,context,reference)
        _,_,_,_,end=current_intent(db,project,context_id,intent._ref(core),clock)
        _current_approval(db,core,row.plan_id)
        end=min(end,c.timestamp(row.body['eligibility_until']))
        return receipt(db,context,row,'execution',end,clock)


def history_output(value):
    raw=c.canonical(s.HistoricalEvidence.model_validate(value).model_dump())
    if len(raw)>8192:raise c.IntentError('verification_response_limit',500)
    return raw


def history_for_plan(db,project,context_id,payload,*,now=None):
    """Recover canonical history when a failed response never delivered its ref."""
    clock=vc.current(now);p=c.validate(s.PlanInput,payload)
    context=intent._locked(db,project,context_id)
    core=db.scalar(select(IntentVersion).where(IntentVersion.context_id==context.id,
        IntentVersion.number==p.intent.number,IntentVersion.version==p.intent.version,
        IntentVersion.digest==p.intent.digest))
    if (core is None or c.digest(core.body['protocol'],core.body)!=core.digest
            or c.digest(core.link['protocol'],core.link)!=core.link_digest):
        raise c.IntentError('verification_evidence_unavailable')
    _member(db,core,p.plan_id)
    row=db.scalar(select(Witness).where(Witness.context_id==context.id,
        Witness.intent_id==core.id,Witness.plan_id==p.plan_id))
    if row is None:raise c.IntentError('verification_evidence_unavailable')
    return history(db,project,context_id,ref(row),now=clock,kind='execution')


def history(db,project,context_id,reference,*,now=None,kind="execution"):
    """Minimized immutable history; no health/access/dispatch eligibility claim."""
    clock=vc.current(now);context=intent._locked(db,project,context_id)
    with db.begin_nested():
        if kind=='execution':row,core=witness(db,context,reference,current_definition=False)
        elif kind=='pair':
            row=exact(db,Pair,context,reference);core=db.get(IntentVersion,row.intent_id)
            if core is None or core.context_id!=context.id or row.body['intent']!=intent._ref(core) or row.body['link_digest']!=core.link_digest:
                raise c.IntentError('verification_provenance_mismatch')
        else:raise c.IntentError('intent_invalid',422)
        # Require current project/data/permission access, without substituting its
        # version into the old evidence or reviving old source dependencies.
        _,ends,_=intent._base(db,context,intent.intake._latest(db,context.id).version_number,core.target_id,clock)
        value={'protocol':'ra-historical-'+kind+'/1','reference':ref(row),'body':row.body,
            'qualification':'historical_not_revalidated','reusable':False,
            'execution_authorized':False,'finding_confirmed':False,'audit_id':audit(db,context.id,'read',clock),
            'observed_at':c.stamp(clock())}
        history_output(value)
        if clock()>=min(ends):raise c.IntentError('intent_permission_missing')
        return value,min(ends)
