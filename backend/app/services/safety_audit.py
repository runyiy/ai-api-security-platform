from collections.abc import Callable

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.execution_plan import ExecutionPlan
from app.db.models.safety_decision_record import SafetyDecisionRecord
from app.db.models.test_run import TestRun
from app.policies.scope_policy import PolicyDecision


PolicyDecisionObserver = Callable[[PolicyDecision], None]


class SafetyAuditService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def append_plan_created(
        self, *, plan: ExecutionPlan, test_case_id: int
    ) -> SafetyDecisionRecord:
        action = plan.actions[0] if len(plan.actions) == 1 else None
        record = SafetyDecisionRecord(
            stage="plan",
            operation="testcase_plan",
            outcome="created",
            target_id=plan.target_id,
            authorization_revision_id=plan.authorization_revision_id,
            execution_plan_id=plan.id,
            plan_action_id=action.id if action is not None else None,
            test_case_id=test_case_id,
        )
        self.db.add(record)
        self.db.flush()
        return record

    def append_policy_decision(
        self,
        *,
        operation: str,
        target_id: int,
        decision: PolicyDecision,
        test_case_id: int | None = None,
        execution_plan_id: int | None = None,
        plan_action_id: int | None = None,
    ) -> SafetyDecisionRecord:
        if operation not in {"policy_check", "test_execution", "openapi_import"}:
            raise ValueError("unsupported policy audit operation")
        record = SafetyDecisionRecord(
            stage="policy",
            operation=operation,
            outcome="allowed" if decision.allowed else "blocked",
            target_id=target_id,
            authorization_revision_id=decision.authorization_revision_id,
            execution_plan_id=execution_plan_id,
            plan_action_id=plan_action_id,
            test_case_id=test_case_id,
            code=decision.code,
            reason=decision.reason,
            matched_scope_id=decision.matched_scope_id,
            policy_evaluated_at=decision.evaluated_at,
        )
        self.db.add(record)
        self.db.flush()
        return record

    def append_execution_outcome(
        self,
        *,
        outcome: str,
        target_id: int,
        authorization_revision_id: int | None,
        test_case_id: int,
        execution_plan_id: int | None = None,
        plan_action_id: int | None = None,
        test_run: TestRun | None = None,
        code: str | None = None,
        reason: str | None = None,
    ) -> SafetyDecisionRecord:
        if outcome not in {"blocked", "succeeded", "failed"}:
            raise ValueError("unsupported execution audit outcome")
        record = SafetyDecisionRecord(
            stage="execution",
            operation="test_execution",
            outcome=outcome,
            target_id=target_id,
            authorization_revision_id=authorization_revision_id,
            test_case_id=test_case_id,
            execution_plan_id=execution_plan_id,
            plan_action_id=plan_action_id,
            test_run_id=test_run.id if test_run is not None else None,
            code=code,
            reason=reason,
        )
        self.db.add(record)
        self.db.flush()
        return record


def build_policy_decision_observer(
    bind: Engine,
    *,
    operation: str,
    target_id: int,
    test_case_id: int | None = None,
    execution_plan_id: int | None = None,
    plan_action_id: int | None = None,
) -> PolicyDecisionObserver:
    audit_session = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)

    def observe(decision: PolicyDecision) -> None:
        with audit_session.begin() as db:
            SafetyAuditService(db).append_policy_decision(
                operation=operation,
                target_id=target_id,
                decision=decision,
                test_case_id=test_case_id,
                execution_plan_id=execution_plan_id,
                plan_action_id=plan_action_id,
            )

    return observe
