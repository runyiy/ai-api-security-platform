from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.db.models import (
    AuthorizationRevision,
    ExecutionPlan,
    PlanAction,
    SafetyDecisionRecord,
    Target,
)
from app.db.session import engine
from app.executors.http import HTTPExecutionResult
from app.executors.http import ExecutionBlockedError
from app.services.execution_plan_approval import record_plan_decision
from app.services.execution_plan import PlanActionInput, create_execution_plan
from app.services.plan_execution import PlanExecutionService
from app.services.test_case_planning import create_test_case_execution_plan
from app.services.test_execution import TestExecutionService
from app.services.safety_audit import SafetyAuditService
from tests.services.test_test_case_planning import build_graph as build_planning_graph


@pytest.fixture
def db() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def build_plan(db: Session, *, approval_required: bool) -> ExecutionPlan:
    graph = build_planning_graph(db, actor_auth_type="anonymous")
    revision = graph["revision"]
    assert isinstance(revision, AuthorizationRevision)
    revision.require_human_execution_approval = approval_required
    db.flush()
    plan = create_test_case_execution_plan(
        db,
        test_case_id=graph["test_case"].id,
        credential_binding_id=None,
    )
    db.flush()
    return plan


def successful_executor() -> Mock:
    executor = Mock()
    executor.execute.return_value = HTTPExecutionResult(
        status_code=200,
        body=b"{}",
        duration_ms=1,
    )
    return executor


def test_exact_single_action_plan_executes_without_required_approval(
    db: Session,
) -> None:
    plan = build_plan(db, approval_required=False)
    executor = successful_executor()

    result = PlanExecutionService(db=db, executor=executor).execute(
        execution_plan_id=plan.id
    )

    assert result.response_status == 200
    call = executor.execute.call_args.kwargs
    assert call["target"].id == plan.target_id
    assert call["authorization_revision"].id == plan.authorization_revision_id
    assert call["url"] == plan.actions[0].url
    assert call["method"] == plan.actions[0].method
    outcome = (
        db.query(SafetyDecisionRecord)
        .filter(SafetyDecisionRecord.stage == "execution")
        .order_by(SafetyDecisionRecord.id.desc())
        .first()
    )
    assert outcome is not None
    assert outcome.execution_plan_id == plan.id
    assert outcome.plan_action_id == plan.actions[0].id


def test_required_exact_approval_executes_and_missing_or_revoked_blocks(
    db: Session,
) -> None:
    plan = build_plan(db, approval_required=True)
    executor = successful_executor()
    service = PlanExecutionService(db=db, executor=executor)

    with pytest.raises(Exception, match="approval"):
        service.execute(execution_plan_id=plan.id)
    executor.execute.assert_not_called()
    blocked = (
        db.query(SafetyDecisionRecord)
        .filter(SafetyDecisionRecord.stage == "execution")
        .order_by(SafetyDecisionRecord.id.desc())
        .first()
    )
    assert blocked is not None
    assert blocked.execution_plan_id == plan.id
    assert blocked.plan_action_id == plan.actions[0].id

    record_plan_decision(db, execution_plan_id=plan.id, decision="approved")
    assert service.execute(execution_plan_id=plan.id).response_status == 200

    executor.reset_mock()
    record_plan_decision(db, execution_plan_id=plan.id, decision="revoked")
    with pytest.raises(Exception, match="approval"):
        service.execute(execution_plan_id=plan.id)
    executor.execute.assert_not_called()


def test_plan_drift_fails_before_executor(db: Session) -> None:
    plan = build_plan(db, approval_required=False)
    executor = successful_executor()
    action = db.get(PlanAction, plan.actions[0].id)
    assert action is not None
    action.url += "?changed=true"

    with pytest.raises(Exception, match="integrity"):
        PlanExecutionService(db=db, executor=executor).execute(
            execution_plan_id=plan.id
        )
    executor.execute.assert_not_called()

def test_plan_target_is_authoritative_and_frozen_url_is_used(db: Session) -> None:
    plan = build_plan(db, approval_required=False)
    target = db.get(Target, plan.target_id)
    assert target is not None
    frozen_url = plan.actions[0].url
    target.base_url = "https://later-change.invalid"
    executor = successful_executor()

    PlanExecutionService(db=db, executor=executor).execute(execution_plan_id=plan.id)

    assert executor.execute.call_args.kwargs["target"].id == plan.target_id
    assert executor.execute.call_args.kwargs["url"] == frozen_url


def test_multi_action_plan_fails_closed_before_executor(db: Session) -> None:
    single = build_plan(db, approval_required=False)
    action = single.actions[0]
    multi = create_execution_plan(
        db,
        target_id=single.target_id,
        authorization_revision_id=single.authorization_revision_id,
        actor_identity_id=single.actor_identity_id,
        credential_binding_id=None,
        actions=[
            PlanActionInput(
                "GET", action.url, action.test_case_id, action.resource_id
            ),
            PlanActionInput(
                "GET", f"{action.url}?second=true", action.test_case_id, action.resource_id
            ),
        ],
        policy_context=single.policy_context,
    )
    executor = successful_executor()

    with pytest.raises(Exception, match="exactly one"):
        PlanExecutionService(db=db, executor=executor).execute(
            execution_plan_id=multi.id
        )

    executor.execute.assert_not_called()


@pytest.mark.parametrize("change", ["disabled", "inactive", "rebound"])
def test_current_target_and_exact_revision_can_only_narrow_plan(
    db: Session, change: str
) -> None:
    plan = build_plan(db, approval_required=False)
    target = db.get(Target, plan.target_id)
    revision = db.get(AuthorizationRevision, plan.authorization_revision_id)
    assert target is not None
    assert revision is not None
    if change == "disabled":
        target.is_enabled = False
    elif change == "inactive":
        revision.lifecycle_state = "revoked"
    else:
        other = build_planning_graph(db, actor_auth_type="anonymous")
        other_revision = other["revision"]
        assert isinstance(other_revision, AuthorizationRevision)
        target.authorization_revision_id = other_revision.id
    db.flush()
    executor = successful_executor()

    with pytest.raises(ExecutionBlockedError):
        PlanExecutionService(db=db, executor=executor).execute(
            execution_plan_id=plan.id
        )

    executor.execute.assert_not_called()


def test_preflight_audit_failure_blocks_before_executor(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_plan(db, approval_required=True)
    executor = successful_executor()

    def fail_audit(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(SafetyAuditService, "append_execution_outcome", fail_audit)
    with pytest.raises(ExecutionBlockedError) as raised:
        PlanExecutionService(db=db, executor=executor).execute(
            execution_plan_id=plan.id
        )

    assert raised.value.code == "safety_audit_persistence_failed"
    executor.execute.assert_not_called()


def test_legacy_execution_requires_plan_when_human_approval_is_required(
    db: Session,
) -> None:
    plan = build_plan(db, approval_required=True)
    executor = successful_executor()

    with pytest.raises(ExecutionBlockedError) as raised:
        TestExecutionService(db=db, executor=executor).execute(
            test_case_id=plan.actions[0].test_case_id
        )

    assert raised.value.code == "plan_bound_execution_required"
    executor.execute.assert_not_called()


def test_legacy_execution_remains_compatible_without_required_approval(
    db: Session,
) -> None:
    plan = build_plan(db, approval_required=False)
    executor = successful_executor()

    result = TestExecutionService(db=db, executor=executor).execute(
        test_case_id=plan.actions[0].test_case_id
    )

    assert result.response_status == 200
    executor.execute.assert_called_once()
