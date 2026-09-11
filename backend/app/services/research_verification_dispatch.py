"""Dedicated exact dispatcher using M8; no flags that open legacy entry points."""
from contextlib import contextmanager
from datetime import timedelta
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.db.models.execution_plan import ExecutionPlan
from app.db.models.execution_plan_cancellation import ExecutionPlanCancellation
from app.db.models.research_verification import VerificationAttempt as Attempt,VerificationWitness as Witness
from app.schemas import research_intent as c, research_verification as s
from app.services import research_intent as intent,research_verification as service
from app.services import research_verification_clock as vc,research_response_semantics as semantics
from app.services.execution_plan_approval import _latest_exact_decision
from app.services.execution_authorization import load_execution_authorization
from app.executors.http import ExecutionBlockedError


@contextmanager
def context_permit(bind, context_id):
    # Session advisory permit, autocommit: no transaction spans rate/network wait.
    if type(context_id) is not int or not 1<=context_id<=2147483647:
        raise c.IntentError('intent_invalid',422)
    connection=bind.connect().execution_options(isolation_level='AUTOCOMMIT')
    acquired=False
    try:
        acquired=connection.scalar(text('SELECT pg_try_advisory_lock(73104, :context)'),{'context':context_id})
        if not acquired:raise c.IntentError('verification_dispatch_busy')
        yield
    finally:
        try:
            if acquired:connection.execute(text('SELECT pg_advisory_unlock(73104, :context)'),{'context':context_id})
        except Exception:connection.invalidate()
        finally:connection.close()


class Dispatch:
    def __init__(self,*,bind,project,context_id,reference,plan_id,executor,clock=None):
        self.bind,self.project,self.context_id=bind,project,context_id
        self.reference=c.validate(c.Reference,reference);self.plan_id=plan_id;self.executor=executor
        self.clock=vc.current(clock);self.before_network=None;self.claim_handle=None;self.claim_service=None
        self.attempt_id=None;self.send_mark=None;self.completion=None;self.semantic=None;self.status=None
        self.temporal_status='qualified';self.credential_version_id=None

    def validate(self,db,*,require_baseline=True,require_approval=True):
        context,core,manifest,contract,end=service.current_intent(db,self.project,self.context_id,self.reference,self.clock)
        member=service._member(db,core,self.plan_id)
        selected=next(a for a in core.body['snapshot']['actions'] if a['role']==(core.body['purpose'] if member.role=='health' else member.role))
        plan=db.get(ExecutionPlan,self.plan_id,populate_existing=True)
        target,revision,scopes=load_execution_authorization(db,core.target_id)
        if target.network_mode!='private_local':
            raise c.IntentError('verification_network_mode_unsupported')
        if revision is None or revision.id!=core.body['snapshot']['authorization_revision_id']:
            raise c.IntentError('verification_authorization_changed')
        decision=_latest_exact_decision(db,plan)
        if require_approval and revision.require_human_execution_approval and (decision is None or decision.decision!='approved'):
            raise c.IntentError('verification_approval_required')
        if db.get(ExecutionPlanCancellation,self.plan_id) is not None:
            raise c.IntentError('verification_cancelled')
        if member.role=='probe' and require_baseline:
            baseline_plan=next(m['plan_id'] for m in core.link['members'] if m['role']=='baseline')
            baseline=db.scalar(select(Witness).where(Witness.context_id==context.id,Witness.plan_id==baseline_plan))
            if baseline is None:raise c.IntentError('verification_baseline_missing')
            baseline,bcore=service.witness(db,context,service.ref(baseline))
            if bcore.id!=core.id or baseline.body['outcome']!='object_read' or baseline.body['temporal_status']!='qualified':
                raise c.IntentError('verification_baseline_unqualified')
            service._current_approval(db,core,baseline.plan_id)
            end=min(end,c.timestamp(baseline.body['complete']['at'])+timedelta(seconds=30))
            vc.check_mark(baseline.body['complete'],self.clock,seconds=30,end=end)
        self.core,self.manifest,self.contract,self.member=core,manifest,contract,member
        self.selected,self.deadline=selected,end
        if selected['actor']['auth_type'] not in ('anonymous','bearer'):
            raise c.IntentError('verification_actor_unsupported')
        self.expectation=next(e for e in contract.body['expectations'] if e['role']==selected['role'])
        self.credential_version_id=selected['actor']['credential_version_id']
        self.rate=manifest.body['command']['rate_millirequests_per_second']/1000
        self.approval_id=decision.id if decision and decision.decision=='approved' else None
        if self.clock()>=end:raise c.IntentError('verification_expired')
        return target,revision,scopes

    @contextmanager
    def sending(self):
        # Independently durable M8 marker comes first. Any later failure cannot
        # erase possible transmission or allow an automatic resend.
        self.before_network()
        with Session(bind=self.bind,expire_on_commit=False) as db:
            with db.begin():
                db.execute(text("SET LOCAL lock_timeout = '1000ms'"))
                db.execute(text("SET LOCAL statement_timeout = '2000ms'"))
                target,revision,scopes=self.validate(db)
                # Coordinate exact cancellation and fencing through the request
                # write, after all domain/context/identity/binding locks.
                db.scalar(select(ExecutionPlan).where(ExecutionPlan.id==self.plan_id).with_for_update())
                self.claim_service.assert_current(self.claim_handle,db=db)
                if db.get(ExecutionPlanCancellation,self.plan_id,populate_existing=True) is not None:
                    raise c.IntentError('verification_cancelled')
                decision=self.executor.policy_engine.evaluate(target=target,authorization_revision=revision,
                    scopes=scopes,request_url=self.selected['request']['url'],method='GET')
                if not decision.allowed:raise ExecutionBlockedError(code=decision.code,reason=decision.reason)
                # Bound by the reviewed finite manifest. Each exact role can
                # consume once, regardless of requests to retry the local API.
                attempts=list(db.scalars(select(Attempt).where(Attempt.manifest_id==self.manifest.id).limit(5)))
                slot=self.core.body['purpose'] if self.member.role=='health' else self.member.role
                if len(attempts)>=len(self.manifest.body['snapshot']['actions']) or any(a.slot==slot or a.plan_id==self.plan_id for a in attempts):
                    raise c.IntentError('verification_budget_consumed')
                service.cap(db,Attempt,self.context_id,4096)
                send=self.clock.mark()
                body={'protocol':service.PROTOCOLS[Attempt],'intent':self.reference.model_dump(),
                    'link_digest':self.core.link_digest,'manifest':intent._ref(self.manifest),
                    'plan_id':self.plan_id,'action_id':self.member.action_id,'role':self.member.role,
                    'plan_digest':self.core.link['members'][0]['plan_digest'] if self.member.role=='health' else next(m['plan_digest'] for m in self.core.link['members'] if m['plan_id']==self.plan_id),
                    'credential_version_id':self.credential_version_id,'approval_id':self.approval_id,
                    'fencing_generation':self.claim_handle.fencing_generation,
                    'prepared':send,'eligibility_until':c.stamp(self.deadline),'recorded_at':send['at'],'clock':send}
                row=Attempt(context_id=self.context_id,manifest_id=self.manifest.id,intent_id=self.core.id,
                    plan_id=self.plan_id,slot=slot,body=body,digest=c.digest(service.PROTOCOLS[Attempt],body),recorded_at=c.timestamp(send['at']))
                db.add(row);db.flush();service.audit(db,self.context_id,'dispatch',self.clock)
                if len(c.canonical(body))>4096:raise c.IntentError('verification_record_limit')
                # Sample after audit/flush waits, immediately before handing the
                # fully prepared GET bytes to the connected stream.
                self.executor.network_gateway.controller.check_enabled(self.core.target_id)
                self.send_mark=self.clock.mark()
                at=c.timestamp(self.send_mark['at'])
                if at>=self.deadline:raise c.IntentError('verification_expired')
                self.attempt_id=row.id
                yield (self.deadline-at).total_seconds()
                self.clock()
        # Only the short request write is in the transaction; response I/O is not.

    def completed(self,status,body,content_type,content_encoding):
        self.status=status
        try:self.completion=self.clock.mark()
        except c.IntentError:
            self.temporal_status='clock_or_deadline_invalid'
            self.completion=vc.Clock(self.clock.wall).mark()
        if (self.send_mark is None or self.completion['monotonic_ns']-self.send_mark['monotonic_ns']>=5_000_000_000):
            self.temporal_status='network_incomplete'
        self.semantic=semantics.interpret(status=status,body=body,expectation=self.expectation,
            auth_type=self.selected['actor']['auth_type'],purpose='health' if self.member.role=='health' else 'business',
            content_type=content_type,content_encoding=content_encoding)

    def persist_result(self,db,run):
        attempt=service.integrity(db.get(Attempt,self.attempt_id),Attempt,self.context_id) if self.attempt_id else None
        if attempt is None:raise c.IntentError('verification_platform_provenance_missing')
        if self.completion is None:
            self.completion=vc.Clock(self.clock.wall).mark()
            self.temporal_status='network_incomplete'
        semantic=self.semantic or semantics.interpret(status=None,body=None,expectation=self.expectation,
            auth_type=self.selected['actor']['auth_type'],purpose='business',content_type=None,content_encoding=None)
        # Canonical network truth survives an expired/changed dependency. Such a
        # witness can never qualify health, baseline dispatch, or pair success.
        try:
            with Session(bind=self.bind) as check_db:
                self.validate(check_db)
        except Exception:
            self.temporal_status='dependency_unavailable'
        mark=vc.Clock(self.clock.wall).mark()
        send,complete=self.send_mark,self.completion
        if not (c.timestamp(send['at'])<=c.timestamp(complete['at'])<=c.timestamp(mark['at'])
                and send['monotonic_ns']<=complete['monotonic_ns']<=mark['monotonic_ns']):
            self.temporal_status='clock_or_deadline_invalid'
        end=min(c.timestamp(attempt.body['eligibility_until']),self.deadline)
        if self.member.role=='health':end=min(end,c.timestamp(send['at'])+timedelta(seconds=120))
        elif self.member.role=='baseline':end=min(end,c.timestamp(complete['at'])+timedelta(seconds=30))
        try:vc.check_mark(send,vc.Clock(self.clock.wall),end=end)
        except c.IntentError:self.temporal_status='clock_or_deadline_invalid'
        if self.temporal_status!='qualified':semantic={**semantic,'outcome':'inconclusive','reason':self.temporal_status}
        service.cap(db,Witness,self.context_id,4096)
        body={'protocol':service.PROTOCOLS[Witness],'version':1,'intent':self.reference.model_dump(),
            'link_digest':attempt.body['link_digest'],'attempt':service.ref(attempt),'plan_id':self.plan_id,
            'action_id':self.member.action_id,'run_id':run.id,'role':self.member.role,
            'credential_version_id':attempt.body['credential_version_id'],'send':send,'complete':complete,
            'response_status':run.response_status,'request_digest':c.digest('ra-request-snapshot/1',run.request_data),
            **semantic,'temporal_status':self.temporal_status,'eligibility_until':c.stamp(end),
            'recorded_at':mark['at'],'clock':mark}
        if len(c.canonical(body))>4096:raise c.IntentError('verification_record_limit')
        row=Witness(context_id=self.context_id,intent_id=self.core.id,plan_id=self.plan_id,run_id=run.id,
            attempt_id=attempt.id,body=body,digest=c.digest(service.PROTOCOLS[Witness],body),recorded_at=c.timestamp(mark['at']))
        db.add(row);db.flush();service.audit(db,self.context_id,'witness',vc.Clock(self.clock.wall))
        if self.temporal_status=='qualified':vc.check_mark(send,self.clock,end=end)


def dispatch(db,project,context_id,payload,*,executor,now=None,encode=None):
    from app.services.plan_execution import PlanExecutionService
    p=c.validate(s.PlanInput,payload);clock=vc.current(now)
    with context_permit(db.get_bind(),context_id):
        gate=Dispatch(bind=db.get_bind(),project=project,context_id=context_id,reference=p.intent,
                      plan_id=p.plan_id,executor=executor,clock=clock)
        run=PlanExecutionService(db=db,executor=executor)._execute_research(gate)
        # M8 canonical result has its own commit. Only this bounded response's
        # audit/encoding transaction may roll back after actual network traffic.
        db.rollback()
        with db.begin():
            row=db.scalar(select(Witness).where(Witness.context_id==context_id,Witness.run_id==run.id))
            if row is None:raise c.IntentError('verification_platform_provenance_missing')
            value=service.read_execution(db,project,context_id,service.ref(row),now=clock)
            return encode(value,clock) if encode else value
