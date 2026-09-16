"""Local, serializable pre-execution task records; no executor or network entry.

Context/catalog locks precede task and plan locks. Every public operation owns a
savepoint, including output encoding, and leaves commit ownership to its caller.
W1 holds one GET per exact plan forever; only W2 can define reconciliation/release.
"""
from functools import wraps

from sqlalchemy import func, select

from app.ai.w2.lifecycle import writer
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.execution_plan import ExecutionPlan
from app.db.models.execution_plan_cancellation import ExecutionPlanCancellation
from app.db.models.execution_plan_claim import ExecutionPlanClaim
from app.db.models.execution_plan_progress import ExecutionPlanProgress
from app.db.models.research_task import (
    ResearchTask as Task, ResearchTaskVersion as Version, ResearchTaskMember as Member,
    ResearchTaskEvent as Event, ResearchTaskAllocation as Allocation,
)
from app.db.models.research_verification import VerificationAttempt, VerificationWitness
from app.db.models.test_run import TestRun
from app.schemas import research_intent as c, research_task as s
from app.services import research_intent as intent, research_verification as verify
from app.services import research_verification_clock as vc
from app.services.execution_plan_approval import _latest_exact_decision, validate_persisted_plan_integrity


def operation(fn):
    guarded = writer(fn)

    @wraps(fn)
    def bounded(*args, **kwargs):
        try:
            return guarded(*args, **kwargs)
        except s.TaskError:
            raise
        except c.IntentError as exc:
            raise s.TaskError('task_invalid' if exc.status == 422 else 'task_unavailable', exc.status) from None
        except Exception:
            # Never expose SQL, credentials, dependency bodies or error chains.
            raise s.TaskError('task_unavailable') from None
    return bounded


class Checks:
    def __init__(self, now):
        self.clock = vc.current(now)
        self.children = []

    def child(self):
        if len(self.children) >= s.MAX_REQUESTS:
            raise s.TaskError('task_limit')
        # Each RA-04 intent retains its existing <=4-consumer / 64-window limits.
        # The parent observes UTC ordering across the entire bounded task read.
        clock = vc.Clock(self.sample)
        self.children.append(clock)
        return clock

    def sample(self):
        try:
            return self.clock()
        except c.IntentError:
            # A parent clock failure must not interrupt child sampling before
            # RA-04 can retain its independent, sticky intent invalidation.
            for clock in self.children:
                clock.invalidate()
            raise

    def finish(self):
        for clock in self.children:
            clock()
        self.sample()


def _ref(task, version):
    return dict(number=task.number, version=version.version, digest=version.digest)


def _task(db, context, number):
    c.validate(c.Reference, dict(number=number, version=1, digest='0' * 64))
    return db.scalar(select(Task).where(Task.context_id == context.id, Task.number == number)
                     .with_for_update().execution_options(populate_existing=True))


def _events(db, task):
    rows = list(db.scalars(select(Event).where(Event.task_id == task.id)
                          .order_by(Event.sequence).limit(s.MAX_EVENTS + 1)))
    if len(rows) > s.MAX_EVENTS or [r.sequence for r in rows] != list(range(1, len(rows) + 1)):
        raise s.TaskError()
    for row in rows:
        body = s.Event.model_validate(row.body)
        if (body.sequence != row.sequence or body.kind != row.kind
                or body.recorded_at != c.stamp(row.recorded_at)):
            raise s.TaskError()
    return rows


def _latest(db, task):
    return db.scalar(select(Version).where(Version.task_id == task.id)
                     .order_by(Version.version.desc()).limit(1).execution_options(populate_existing=True))


def _exact(db, task, reference):
    row = db.scalar(select(Version).where(Version.task_id == task.id,
        Version.version == reference.version, Version.digest == reference.digest)
        .execution_options(populate_existing=True))
    if row is None or reference.number != task.number:
        raise s.TaskError()
    core = s.Core.model_validate(row.body)
    if (c.digest('ra-local-task/1', row.body) != row.digest
            or (core.number, core.version, core.context_id, core.target_id, core.context_version,
                core.authorization_revision_id, core.recorded_at) !=
               (task.number, row.version, task.context_id, task.target_id, row.context_version,
                row.authorization_revision_id, c.stamp(row.recorded_at))):
        raise s.TaskError()
    members = list(db.scalars(select(Member).where(Member.version_id == row.id).order_by(Member.ordinal)))
    if [(m.ordinal, m.plan_id, m.intent_id, m.manifest_id) for m in members] != [
            (m.ordinal, m.plan_id, m.intent_id, m.manifest_id) for m in core.members]:
        raise s.TaskError()
    return row, core


def _current(db, context, reference, expected_sequence):
    task = _task(db, context, reference.number)
    if task is None:
        raise s.TaskError()
    row, core = _exact(db, task, reference)
    events = _events(db, task)
    if _latest(db, task).id != row.id or len(events) != expected_sequence:
        raise s.TaskError('task_stale')
    if any(e.kind == 'cancelled' for e in events):
        raise s.TaskError('task_cancelled')
    return task, row, core, events


def _append(db, task, version, events, kind, evidence, clock):
    if len(events) >= s.MAX_EVENTS:
        raise s.TaskError('task_limit')
    at = clock()
    if at < version.recorded_at or (events and at < events[-1].recorded_at):
        raise s.TaskError('task_clock_invalid')
    body = s.Event(sequence=len(events) + 1, version=version.version, kind=kind,
                   actor='local_operator', evidence=evidence, recorded_at=c.stamp(at)).model_dump()
    row = Event(task_id=task.id, version_id=version.id, sequence=len(events) + 1,
                kind=kind, body=body, recorded_at=at)
    db.add(row)
    db.flush()
    return [*events, row]


def _limits(limits, intake):
    for name in ('target_requests', 'duration_seconds', 'concurrency', 'rate_millirequests_per_second'):
        cap = getattr(intake.budget, name)
        if cap is None or getattr(limits, name) > cap:
            raise s.TaskError('task_budget_limit')
    # Intake already checks its rate against the current revision. Task rate is
    # <=1/s; the existing platform limiter is 2/s and still applies in W2.


def _snapshot(db, context, payload, checks, task_id):
    metadata, ends, intake = intent._base(db, context, payload.context_version, payload.target_id, checks.clock)
    if metadata['authorization_revision_id'] != payload.authorization_revision_id:
        raise s.TaskError()
    _limits(payload.limits, intake)
    if len(payload.members) > payload.limits.target_requests:
        raise s.TaskError('task_budget_limit')
    all_ids = [m.plan_id for m in payload.members]
    if len(set(all_ids)) != len(all_ids):
        raise s.TaskError('task_invalid', 422)
    result, known, seen = [], {}, set()
    for ordinal, selected in enumerate(payload.members, 1):
        key = (selected.intent.number, selected.intent.version, selected.intent.digest)
        if key not in known:
            known[key] = verify.current_intent(db, context.project_number, context.id, selected.intent, checks.child())
        _, core, manifest, _, end = known[key]
        if (core.context_version, core.target_id, core.body['snapshot']['authorization_revision_id']) != (
                payload.context_version, payload.target_id, payload.authorization_revision_id):
            raise s.TaskError()
        link = next((m for m in core.link['members'] if m['plan_id'] == selected.plan_id), None)
        if link is None:
            raise s.TaskError()
        role = core.body['purpose'] if link['role'] == 'health' else link['role']
        # Refresh after RA-04 qualification even when a caller reused a Session.
        db.scalar(select(ExecutionPlan).where(ExecutionPlan.id == selected.plan_id)
                  .with_for_update().execution_options(populate_existing=True))
        plan = validate_persisted_plan_integrity(db, selected.plan_id)
        if (role != selected.role or plan.digest_version != 'v1'
                or plan.plan_digest != selected.plan_digest or plan.plan_digest != link['plan_digest']
                or plan.target_id != payload.target_id or plan.authorization_revision_id != payload.authorization_revision_id
                or plan.action_count != 1):
            raise s.TaskError()
        if db.get(ExecutionPlanCancellation, plan.id, populate_existing=True) is not None:
            raise s.TaskError('task_plan_cancelled')
        other = select(Member.id).join(Version, Member.version_id == Version.id).where(Member.plan_id == plan.id)
        if task_id is not None:
            other = other.where(Version.task_id != task_id)
        if db.scalar(other.limit(1)) is not None:
            raise s.TaskError('task_plan_already_bound')
        dependencies = []
        for health in core.body['interpretation']['health']:
            witness = db.scalar(select(VerificationWitness.plan_id).where(
                VerificationWitness.context_id == context.id, VerificationWitness.id == health['evidence_id'],
                VerificationWitness.digest == health['digest']))
            if witness is None:
                raise s.TaskError()
            dependencies.append(witness)
        if role == 'probe':
            dependencies.append(next(m['plan_id'] for m in core.link['members'] if m['role'] == 'baseline'))
        dependencies = list(dict.fromkeys(dependencies))
        if any(pid not in seen for pid in dependencies):
            raise s.TaskError('task_dependency_missing')
        dependencies.sort(key=all_ids.index)
        selection = selected.model_dump(include=set(s.Selection.model_fields))
        result.append(s.Member(**selection, ordinal=ordinal, intent_id=core.id,
            manifest=intent._ref(manifest), manifest_id=manifest.id, digest_version=plan.digest_version,
            action_id=link['action_id'], depends_on=dependencies).model_dump())
        seen.add(plan.id)
        ends.append(end)
    return metadata, result, min(ends)


def _allocations(db, task):
    rows = list(db.scalars(select(Allocation).where(Allocation.task_id == task.id)
                          .order_by(Allocation.slot).limit(s.MAX_REQUESTS + 1)))
    if len(rows) > s.MAX_REQUESTS:
        raise s.TaskError('task_budget_limit')
    return rows


def _allocate(db, task, row, core, clock):
    held = _allocations(db, task)
    previous = {r.plan_id for r in held}
    new = [m for m in core.members if m.plan_id not in previous]
    if len(held) + len(new) > core.limits.target_requests:
        raise s.TaskError('task_budget_limit')
    for slot, member in enumerate(new, len(held) + 1):
        db.add(Allocation(plan_id=member.plan_id, task_id=task.id, first_version_id=row.id,
            slot=slot, requests=1, state='held_unreconciled', recorded_at=clock()))
    db.flush()  # Unique plan/slot constraints arbitrate competing reservations.


def _observations(db, context, task, core, clock):
    accessible = True
    try:
        with db.begin_nested():
            latest = intent.intake._latest(db, context.id)
            intent._base(db, context, latest.version_number, task.target_id, clock)
    except Exception:
        accessible = False
    result = []
    for member in core.members:
        value = dict(plan_id=member.plan_id, phase='unavailable', claim_present=None,
            canonical_run_id=None, verification_attempt_id=None, cancelled=None, exact_approval='unavailable')
        if accessible:
            # Only minimal, currently owned plan metadata. Never load a TestRun body.
            phase = db.scalar(select(ExecutionPlanProgress.phase).where(ExecutionPlanProgress.execution_plan_id == member.plan_id))
            value.update(phase=phase or 'no_record',
                claim_present=db.get(ExecutionPlanClaim, member.plan_id, populate_existing=True) is not None,
                canonical_run_id=db.scalar(select(TestRun.id).where(TestRun.execution_plan_id == member.plan_id)),
                verification_attempt_id=db.scalar(select(VerificationAttempt.id).where(VerificationAttempt.plan_id == member.plan_id)),
                cancelled=db.get(ExecutionPlanCancellation, member.plan_id, populate_existing=True) is not None)
            plan = db.get(ExecutionPlan, member.plan_id, populate_existing=True)
            revision = db.get(AuthorizationRevision, core.authorization_revision_id, populate_existing=True)
            if plan is not None and plan.plan_digest == member.plan_digest and revision is not None:
                decision = _latest_exact_decision(db, plan)
                value['exact_approval'] = (decision.decision if decision else
                    'missing' if revision.require_human_execution_approval else 'not_required')
        result.append(value)
    return result


def _qualify(db, context, task, row, core, checks):
    # RA-04 must observe expiry itself before a task deadline rejects the read;
    # otherwise correcting UTC could resurrect its old intent after this read.
    metadata, members, end = _snapshot(db, context, core, checks, task.id)
    if metadata['permission_digest'] != core.permission_digest or members != [m.model_dump() for m in core.members]:
        raise s.TaskError()
    checks.clock.watch_window(row.recorded_at, min(end, c.timestamp(core.eligibility_until)))
    checks.finish()


def _view(db, context, task, row, core, events, now, *, qualified=None, encode=None):
    checks = qualified
    if checks is None:
        candidate = Checks(now)
        try:
            with db.begin_nested():
                _qualify(db, context, task, row, core, candidate)
            checks = candidate
        except Exception:
            # Immutable references remain inspectable after expiry/revocation or
            # restart. A failed dependency read produces no reusable permission.
            checks = None
    clock = vc.current(now)
    latest = _latest(db, task)
    relevant = [e for e in events if e.version_id == row.id]
    decisions = [e for e in relevant if e.kind in ('approved', 'revoked')]
    state = 'awaiting_budget_approval'
    if decisions:
        state = 'budget_approved' if decisions[-1].kind == 'approved' else 'budget_revoked'
    if any(e.kind == 'paused' for e in relevant):
        state = 'paused'
    if latest.id != row.id:
        state = 'superseded'
    if any(e.kind == 'cancelled' for e in events):
        state = 'cancelled'
    held = _allocations(db, task)
    versions = dict(db.execute(select(Version.id, Version.version).where(Version.task_id == task.id)).all())
    allocations = [dict(plan_id=r.plan_id, first_version=versions[r.first_version_id],
                        requests=r.requests, state=r.state) for r in held]
    current_budget = (state == 'budget_approved' and checks is not None
        and len(held) <= core.limits.target_requests
        and {m.plan_id for m in core.members} <= {r.plan_id for r in held})
    observations = _observations(db, context, task, core, clock)
    blockers = ['execution_disabled', 'reconciliation_required']
    if not current_budget:
        blockers.append('budget_approval_missing')
    if checks is None:
        blockers.append('dependencies_unavailable')
    if any(p['exact_approval'] not in ('approved', 'not_required') for p in observations):
        blockers.append('exact_plan_approval_missing')
    if any(p['cancelled'] for p in observations):
        blockers.append('plan_cancelled')
    if state in ('paused', 'cancelled', 'superseded'):
        blockers.append('task_inactive')
    value = s.View(protocol='ra-local-task-view/1', reference=_ref(task, row),
        current_version=latest.version, sequence=len(events), state=state, core=core,
        events=[e.body for e in events], allocations=allocations, held_requests=len(held),
        selected_requests=len(core.members), observations=observations,
        dependencies_current=checks is not None, budget_current=current_budget,
        blockers=blockers, observed_at=c.stamp(clock()), execution_authorized=False,
        runtime_enforcement='w2_not_implemented').model_dump()
    raw = c.canonical(value)
    if len(raw) > s.MAX_OUTPUT:
        raise s.TaskError('task_response_limit', 500)
    result = encode(raw) if encode else value
    if checks is not None:
        checks.finish()
    clock()
    return result


@operation
def create_version(db, project, context_id, number, payload, *, now=None, encode=None):
    p = c.validate(s.CreateVersion, payload)
    context = intent._locked(db, project, context_id)
    with db.begin_nested():
        checks = Checks(now)
        task = _task(db, context, number)
        events, previous = [], None
        if task is None:
            if p.previous is not None or p.expected_sequence != 0:
                raise s.TaskError('task_stale')
            if db.scalar(select(func.count()).select_from(Task).where(Task.context_id == context.id)) >= s.MAX_TASKS:
                raise s.TaskError('task_limit')
        else:
            if p.previous is None or p.previous.number != number or task.target_id != p.target_id:
                raise s.TaskError('task_stale')
            task, previous, _, events = _current(db, context, p.previous, p.expected_sequence)
            if previous.version >= s.MAX_VERSIONS:
                raise s.TaskError('task_limit')
        metadata, members, end = _snapshot(db, context, p, checks, task.id if task else None)
        at = checks.clock()
        if events and at < events[-1].recorded_at:
            raise s.TaskError('task_clock_invalid')
        if task is None:
            task = Task(context_id=context.id, target_id=p.target_id, number=number, recorded_at=at)
            db.add(task)
            db.flush()
        # Existing holds survive every version; even a new draft cannot hide or
        # promise a smaller limit than its retained historical liability.
        held = _allocations(db, task)
        if len({r.plan_id for r in held} | {m['plan_id'] for m in members}) > p.limits.target_requests:
            raise s.TaskError('task_budget_limit')
        core = s.Core(protocol='ra-local-task/1', number=number,
            version=previous.version + 1 if previous else 1, context_id=context.id,
            context_version=p.context_version, target_id=p.target_id,
            authorization_revision_id=p.authorization_revision_id,
            permission_digest=metadata['permission_digest'], previous=p.previous,
            limits=p.limits, members=members, evidence=p.evidence,
            recorded_at=c.stamp(at), eligibility_until=c.stamp(end))
        body = core.model_dump()
        if len(c.canonical(body)) > s.MAX_CORE:
            raise s.TaskError('task_input_limit', 413)
        row = Version(task_id=task.id, context_id=context.id, context_version=p.context_version,
            target_id=p.target_id, authorization_revision_id=p.authorization_revision_id,
            version=core.version, body=body, digest=c.digest('ra-local-task/1', body), recorded_at=at)
        db.add(row)
        db.flush()
        for member in core.members:
            db.add(Member(version_id=row.id, ordinal=member.ordinal, plan_id=member.plan_id,
                          intent_id=member.intent_id, manifest_id=member.manifest_id))
        db.flush()
        events = _append(db, task, row, events, 'version_created', p.evidence, checks.clock)
        return _view(db, context, task, row, core, events, now, qualified=checks, encode=encode)


@operation
def inspect_task(db, project, context_id, reference, *, now=None, encode=None):
    p = c.validate(s.TaskRef, reference)
    context = intent._locked(db, project, context_id)
    with db.begin_nested():
        task = _task(db, context, p.number)
        if task is None:
            raise s.TaskError()
        row, core = _exact(db, task, p)
        return _view(db, context, task, row, core, _events(db, task), now, encode=encode)


@operation
def decide_budget(db, project, context_id, payload, *, now=None, encode=None):
    p = c.validate(s.Decision, payload)
    context = intent._locked(db, project, context_id)
    with db.begin_nested():
        task, row, core, events = _current(db, context, p.task, p.expected_sequence)
        checks = None
        if p.decision == 'approved':
            if any(e.kind in ('approved', 'revoked', 'paused') for e in events if e.version_id == row.id):
                # Re-approval after any withdrawal requires a reviewable version.
                raise s.TaskError('task_new_version_required')
            checks = Checks(now)
            _qualify(db, context, task, row, core, checks)
            _allocate(db, task, row, core, checks.clock)
        elif not any(e.kind == 'approved' for e in events if e.version_id == row.id):
            raise s.TaskError('task_budget_approval_missing')
        events = _append(db, task, row, events, p.decision, p.evidence, vc.current(now))
        return _view(db, context, task, row, core, events, now, qualified=checks, encode=encode)


@operation
def transition(db, project, context_id, payload, *, now=None, encode=None):
    p = c.validate(s.Transition, payload)
    context = intent._locked(db, project, context_id)
    with db.begin_nested():
        task, row, core, events = _current(db, context, p.task, p.expected_sequence)
        if p.action in ('start', 'resume', 'dispatch'):
            raise s.TaskError('task_execution_disabled')
        events = _append(db, task, row, events, 'paused' if p.action == 'pause' else 'cancelled',
                         p.evidence, vc.current(now))
        return _view(db, context, task, row, core, events, now, encode=encode)
