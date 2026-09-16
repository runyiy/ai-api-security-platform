from copy import deepcopy
from datetime import timedelta
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import delete, select, text, update

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.db.models import ExecutionPlan, PlanAction, AuthorizationRevision
from app.db.models.research_task import ResearchTaskAllocation, ResearchTaskEvent
from app.db.models.execution_plan_approval_record import ExecutionPlanApprovalRecord
from app.db.models.execution_plan_claim import ExecutionPlanClaim
from app.db.models.execution_plan_progress import ExecutionPlanProgress
from app.services import research_task as tasks, research_verification as verify, research_intent as intent
from app.schemas import research_task as s, research_intent as c
from tests.research_task_fixtures import (task_graph, intent_graph, subject_pair, two_intake_targets,
    original_targets, zero_capabilities, creation, decision, transition, create, call, NOW, REF)
from tests.research_intake_fixtures import snapshot


def test_persistent_explicit_budget_workflow_and_fresh_process(task_graph, tmp_path):
    g = task_graph
    before = snapshot()
    value = create(g)
    assert value['state'] == 'awaiting_budget_approval'
    assert value['dependencies_current'] and not value['budget_current']
    assert value['allocations'] == [] and value['selected_requests'] == 2
    assert [m['role'] for m in value['core']['members']] == ['baseline', 'probe']
    assert value['core']['members'][1]['depends_on'] == [g['selection'][0]['plan_id']]
    assert all(o['phase'] == 'no_record' and o['exact_approval'] == 'missing' for o in value['observations'])
    assert not any(o['canonical_run_id'] for o in value['observations'])
    # New interpreter, SQLAlchemy Session and empty process memory, with the same
    # independently qualified TEST URL. No operator .env or inherited secrets.
    program = """import json,sys
from app.db.session import SessionLocal
from app.services.research_task import inspect_task
from datetime import datetime
payload=json.loads(sys.stdin.read())
with SessionLocal() as db:
    value=inspect_task(db,payload['project'],payload['context'],payload['reference'],now=datetime.fromisoformat(payload['now']))
    db.commit()
    print(json.dumps(value))
"""
    result = subprocess.run([sys.executable, '-c', program], cwd=tmp_path,
        input=json.dumps(dict(project=g['project'], context=g['ctx'], reference=value['reference'], now=NOW.isoformat())),
        env={'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8', 'DATABASE_URL': settings.database_url,
             'PYTHONPATH': os.getcwd()}, text=True, capture_output=True, timeout=20, check=True)
    fresh = json.loads(result.stdout)
    assert fresh['core'] == value['core'] and fresh['events'] == value['events']
    assert fresh['execution_authorized'] is False and fresh['runtime_enforcement'] == 'w2_not_implemented'
    approved = call(tasks.decide_budget, g, decision(fresh))
    assert approved['state'] == 'budget_approved' and approved['budget_current']
    assert approved['held_requests'] == 2
    assert all(a['state'] == 'held_unreconciled' and a['requests'] == 1 for a in approved['allocations'])
    assert 'exact_plan_approval_missing' in approved['blockers']
    revoked = call(tasks.decide_budget, g, decision(approved, 'revoked'))
    assert revoked['state'] == 'budget_revoked' and not revoked['budget_current']
    assert revoked['allocations'] == approved['allocations']
    after = snapshot()
    for table in ('test_runs', 'execution_plan_approval_records', 'execution_plan_claims',
                  'execution_plan_progress', 'research_verification_attempts', 'research_verification_witnesses'):
        assert after[table] == before[table]


@pytest.mark.parametrize('change', ['project', 'context', 'context_version', 'revision', 'plan_digest', 'intent_digest', 'plan_id', 'duplicate', 'probe_first'])
def test_reference_and_membership_boundaries(task_graph, subject_pair, change):
    g = task_graph
    p = creation(g)
    project, context = g['project'], g['ctx']
    if change == 'project': project = 2
    if change == 'context': context = subject_pair[1]['ctx']
    if change == 'context_version': p['context_version'] = 2
    if change == 'revision': p['authorization_revision_id'] = subject_pair[1]['revision']
    if change == 'plan_digest': p['members'][0]['plan_digest'] = 'a' * 64
    if change == 'intent_digest': p['members'][0]['intent']['digest'] = 'a' * 64
    if change == 'plan_id': p['members'][0]['plan_id'] = 2147483647
    if change == 'duplicate': p['members'].append(deepcopy(p['members'][0]))
    if change == 'probe_first': p['members'].reverse()
    before = snapshot()
    with SessionLocal() as db:
        with pytest.raises(s.TaskError):
            tasks.create_version(db, project, context, 1, p, now=NOW)
        db.commit()
    assert snapshot() == before


@pytest.mark.parametrize('field,value', [('target_requests', 0), ('target_requests', 101),
    ('target_requests', 1), ('duration_seconds', 1801), ('duration_seconds', 61),
    ('rate_millirequests_per_second', 501), ('rate_millirequests_per_second', 0),
    ('concurrency', 2), ('concurrency', True), ('ai_calls', 1), ('ai_tokens', 1), ('ai_cost_microusd', 1)])
def test_hard_and_intake_limits(task_graph, field, value):
    g = task_graph
    p = creation(g)
    p['limits'][field] = value
    before = snapshot()
    with pytest.raises(s.TaskError):
        call(tasks.create_version, g, 1, p)
    assert snapshot() == before


@pytest.mark.parametrize('action', ['start', 'resume', 'dispatch'])
@pytest.mark.parametrize('approved', [False, True])
def test_all_execution_transitions_are_closed(task_graph, action, approved):
    g = task_graph
    view = create(g)
    if approved:
        view = call(tasks.decide_budget, g, decision(view))
    before = snapshot()
    with pytest.raises(s.TaskError, match='task_execution_disabled'):
        call(tasks.transition, g, transition(view, action))
    assert snapshot() == before


def test_revoke_requires_real_task_decision_and_stale_cas_rejects(task_graph):
    g = task_graph
    view = create(g)
    with pytest.raises(s.TaskError, match='task_budget_approval_missing'):
        call(tasks.decide_budget, g, decision(view, 'revoked'))
    approved = call(tasks.decide_budget, g, decision(view))
    with pytest.raises(s.TaskError, match='task_stale'):
        call(tasks.decide_budget, g, decision(view))
    revoked = call(tasks.decide_budget, g, decision(approved, 'revoked'))
    with pytest.raises(s.TaskError, match='task_new_version_required'):
        call(tasks.decide_budget, g, decision(revoked))


def test_new_reviewable_version_preserves_all_holds_and_requires_new_approval(task_graph):
    g = task_graph
    approved = call(tasks.decide_budget, g, decision(create(g)))
    p = creation(g, previous=approved['reference'], expected_sequence=approved['sequence'], members=[g['selection'][0]])
    p['limits']['target_requests'] = 1
    with pytest.raises(s.TaskError, match='task_budget_limit'):
        call(tasks.create_version, g, 1, p)
    p['limits']['target_requests'] = 2
    new = call(tasks.create_version, g, 1, p)
    assert new['reference']['version'] == 2 and not new['budget_current']
    assert new['held_requests'] == 2 and new['selected_requests'] == 1
    history = call(tasks.inspect_task, g, approved['reference'])
    assert history['state'] == 'superseded' and not history['budget_current']
    with pytest.raises(s.TaskError, match='task_stale'):
        call(tasks.decide_budget, g, decision(approved, 'revoked'))
    new = call(tasks.decide_budget, g, decision(new))
    assert new['held_requests'] == 2 and new['budget_current']
    with pytest.raises(s.TaskError, match='task_plan_already_bound'):
        call(tasks.create_version, g, 2, creation(g))


def test_pause_cancel_and_cancelled_members_cannot_revive(task_graph):
    g = task_graph
    view = call(tasks.decide_budget, g, decision(create(g)))
    paused = call(tasks.transition, g, transition(view, 'pause'))
    assert paused['state'] == 'paused' and not paused['budget_current']
    cancelled = call(tasks.transition, g, transition(paused, 'cancel'))
    assert cancelled['state'] == 'cancelled' and cancelled['held_requests'] == 2
    with pytest.raises(s.TaskError, match='task_cancelled'):
        call(tasks.create_version, g, 1, creation(g, previous=cancelled['reference'], expected_sequence=cancelled['sequence']))
    with pytest.raises(s.TaskError, match='task_plan_already_bound'):
        call(tasks.create_version, g, 2, creation(g))


def test_expired_references_inspectable_but_cannot_approve_and_revoke_still_works(task_graph):
    g = task_graph
    approved = call(tasks.decide_budget, g, decision(create(g)))
    at = NOW + timedelta(seconds=60)
    view = call(tasks.inspect_task, g, approved['reference'], now=at)
    assert not view['dependencies_current'] and not view['budget_current']
    assert view['held_requests'] == 2
    corrected = call(tasks.inspect_task, g, approved['reference'], now=NOW)
    assert not corrected['dependencies_current'] and not corrected['budget_current']
    revoked = call(tasks.decide_budget, g, decision(view, 'revoked'), now=at)
    assert revoked['state'] == 'budget_revoked' and revoked['held_requests'] == 2


def test_exact_plan_approval_remains_separate_and_revocation_visible(task_graph):
    g = task_graph
    view = call(tasks.decide_budget, g, decision(create(g)))
    for member in g['selection']:
        call(verify.approve, g, dict(intent=member['intent'], plan_id=member['plan_id'],
            expected_sequence=0, decision='approved', evidence=REF))
    view = call(tasks.inspect_task, g, view['reference'])
    assert all(o['exact_approval'] == 'approved' for o in view['observations'])
    member = g['selection'][0]
    call(verify.approve, g, dict(intent=member['intent'], plan_id=member['plan_id'],
        expected_sequence=1, decision='revoked', evidence=REF))
    view = call(tasks.inspect_task, g, view['reference'])
    assert view['observations'][0]['exact_approval'] == 'revoked'
    assert 'exact_plan_approval_missing' in view['blockers'] and not view['execution_authorized']


@pytest.mark.parametrize('change', ['context', 'revision', 'cancelled', 'plan_digest', 'action'])
def test_changes_after_creation_cannot_retain_budget_authority(task_graph, change):
    from app.services.research_context import correct_context, read_context
    from app.db.models.execution_plan_cancellation import ExecutionPlanCancellation
    g = task_graph
    view = create(g)
    with SessionLocal() as db:
        if change == 'context':
            value = read_context(db, g['project'], g['ctx'], now=NOW)
            correct_context(db, g['project'], g['ctx'], dict(expected_version=1,
                correction_reference=REF, intake=value['intake']), now=NOW)
        if change == 'revision':
            db.get(AuthorizationRevision, g['revision']).lifecycle_state = 'revoked'
        if change == 'cancelled':
            db.add(ExecutionPlanCancellation(execution_plan_id=g['selection'][0]['plan_id'], requested_at=NOW))
        if change == 'plan_digest':
            db.get(ExecutionPlan, g['selection'][0]['plan_id']).plan_digest = 'f' * 64
        if change == 'action':
            action = db.scalar(select(PlanAction).where(PlanAction.execution_plan_id == g['selection'][0]['plan_id']))
            action.url += '/changed'
        db.commit()
    before = snapshot()
    with pytest.raises(s.TaskError):
        call(tasks.decide_budget, g, decision(view))
    assert snapshot() == before
    current = call(tasks.inspect_task, g, view['reference'])
    assert not current['dependencies_current'] and not current['budget_current']


def test_closed_context_inspection_never_reads_transferred_target_or_plan(task_graph):
    from sqlalchemy import event
    from app.services.research_context import close_context
    g = task_graph
    view = call(tasks.decide_budget, g, decision(create(g)))
    with SessionLocal() as db:
        close_context(db, g['project'], g['ctx'], dict(expected_version=1, closure_reference=REF), now=NOW)
        db.commit()
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        current = call(tasks.inspect_task, g, view['reference'])
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert not current['dependencies_current'] and current['held_requests'] == 2
    assert all(o['phase'] == 'unavailable' for o in current['observations'])
    assert not any('from '+table in sql for sql in statements
        for table in ('targets', 'test_runs', 'execution_plans', 'test_identities', 'credential_secret_versions'))


@pytest.fixture
def wide_task_intake(monkeypatch):
    from tests import research_subject_fixtures as fixtures
    original = fixtures.intake
    def intake(ids):
        value = original(ids)
        value['budget'].update(target_requests=100, duration_seconds=1800, rate_millirequests_per_second=1000)
        return value
    monkeypatch.setattr(fixtures, 'intake', intake)


def test_hundred_exact_gets_at_maximum_task_limits(wide_task_intake, task_graph):
    from tests.research_task_fixtures import confirm
    from tests.research_intent_fixtures import approve, conversion
    g = task_graph
    selected = deepcopy(g['selection'])
    for number in range(2, 51):
        other = dict(g)
        other['manifest_input'] = deepcopy(g['manifest_input'])
        other['manifest_input']['expected_version'] = 0
        other['manifest'] = call(intent.record_manifest, g, number, other['manifest_input'])['reference']
        call(intent.decide_budget, other, dict(manifest=other['manifest'], expected_sequence=0, decision='approved', evidence=REF))
        expectations = [dict(role=role, object_key='record_id', object_value='7001',
            identity_key='subject_id', identity_value=None) for role in ('baseline', 'probe')]
        call(verify.confirm, other, number, dict(manifest=other['manifest'], expected_version=0,
            decision='confirm', expectations=expectations, evidence=REF))
        value = call(intent.convert, other, number, conversion(other))
        selected.extend(dict(intent=value['reference'], plan_id=m['plan_id'], plan_digest=m['plan_digest'], role=m['role'])
                        for m in value['body']['link']['members'])
    payload = creation(g, members=selected)
    payload['limits'].update(target_requests=100, duration_seconds=1800, rate_millirequests_per_second=1000)
    view = call(tasks.create_version, g, 1, payload)
    assert len(view['core']['members']) == 100 and view['core']['limits']['duration_seconds'] == 1800
    # Task's duration cap cannot extend any 60-second manifest/intent deadline.
    assert c.timestamp(view['core']['eligibility_until']) == NOW + timedelta(seconds=60)
    approved = call(tasks.decide_budget, g, decision(view))
    assert approved['held_requests'] == 100 and len(approved['allocations']) == 100
    assert len(c.canonical(approved)) <= s.MAX_OUTPUT and not approved['execution_authorized']


def test_changed_manifest_decision_invalidates_task_budget(task_graph):
    g = task_graph
    approved = call(tasks.decide_budget, g, decision(create(g)))
    call(intent.decide_budget, g, dict(manifest=g['manifest'], expected_sequence=1, decision='revoked', evidence=REF))
    view = call(tasks.inspect_task, g, approved['reference'])
    assert not view['budget_current'] and not view['dependencies_current'] and view['held_requests'] == 2


def test_m8_uncertainty_is_observed_never_refunded_or_replayed(task_graph):
    g = task_graph
    # Durable synthetic interrupted-attempt marker, explicitly not a TestRun or
    # a manufactured execution result. W1 cannot infer how many bytes were sent.
    with SessionLocal() as db:
        db.add(ExecutionPlanProgress(execution_plan_id=g['selection'][0]['plan_id'],
            fencing_generation=1, phase='network_started', updated_at=NOW))
        db.commit()
    view = call(tasks.decide_budget, g, decision(create(g)))
    assert view['observations'][0]['phase'] == 'network_started'
    assert view['held_requests'] == 2 and 'reconciliation_required' in view['blockers']
    revoked = call(tasks.decide_budget, g, decision(view, 'revoked'))
    assert revoked['allocations'] == view['allocations']
    assert all(o['canonical_run_id'] is None for o in revoked['observations'])


def test_concurrent_budget_approval_reserves_each_exact_get_once(task_graph):
    g = task_graph
    value = create(g)
    barrier = Barrier(2)
    def approve_one():
        barrier.wait(timeout=5)
        try:
            return call(tasks.decide_budget, g, decision(value))['state']
        except s.TaskError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: approve_one(), range(2)))
    assert sorted(results) == ['budget_approved', 'task_stale']
    fresh = call(tasks.inspect_task, g, value['reference'])
    assert fresh['held_requests'] == 2 and fresh['sequence'] == 2


def test_concurrent_membership_cannot_duplicate_allowance_across_tasks(task_graph):
    g = task_graph
    barrier = Barrier(2)
    def create_one(number):
        barrier.wait(timeout=5)
        try:
            return call(tasks.create_version, g, number, creation(g))['state']
        except s.TaskError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(create_one, [1, 2]))
    assert sorted(result) == ['awaiting_budget_approval', 'task_plan_already_bound']


@pytest.mark.parametrize('stage', ['create', 'approve'])
def test_encoding_failure_rolls_back_even_if_caller_commits(task_graph, stage):
    g = task_graph
    view = create(g) if stage == 'approve' else None
    before = snapshot()
    def fail(_):
        raise RuntimeError('synthetic-sensitive-error')
    with SessionLocal() as db:
        with pytest.raises(s.TaskError) as caught:
            if stage == 'create':
                tasks.create_version(db, g['project'], g['ctx'], 1, creation(g), now=NOW, encode=fail)
            else:
                tasks.decide_budget(db, g['project'], g['ctx'], decision(view), now=NOW, encode=fail)
        assert 'synthetic-sensitive-error' not in str(caught.value)
        db.commit()
    assert snapshot() == before


@pytest.mark.parametrize('task_graph', ['health'], indirect=True)
def test_real_health_bootstrap_plan_is_separately_bound_without_a_request(task_graph):
    g = task_graph
    view = create(g)
    member = view['core']['members'][0]
    assert member['role'] == 'health_probe' and member['depends_on'] == []
    assert view['selected_requests'] == 1 and view['observations'][0]['canonical_run_id'] is None
    assert call(tasks.decide_budget, g, decision(view))['held_requests'] == 1


def test_dedicated_ra04_dispatch_cannot_bypass_task_budget(task_graph):
    from app.services.research_verification_dispatch import dispatch
    g = task_graph
    create(g)
    before = snapshot()
    executor = Mock()
    with SessionLocal() as db:
        with pytest.raises(c.IntentError, match='task_execution_disabled'):
            dispatch(db, g['project'], g['ctx'], dict(intent=g['intent']['reference'], plan_id=g['selection'][0]['plan_id']),
                     executor=executor, now=NOW)
    executor.assert_not_called()
    assert not executor.mock_calls
    assert snapshot() == before
