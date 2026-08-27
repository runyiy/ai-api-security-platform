from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.execution_plan import ExecutionPlan
from app.db.models.execution_plan_approval_record import ExecutionPlanApprovalRecord
from app.db.models.plan_action import PlanAction
from app.services.execution_plan import (
    DIGEST_VERSION,
    PlanActionInput,
    compute_plan_digest_v1,
)


class PlanIntegrityError(RuntimeError):
    pass


def recompute_persisted_plan_digest(db: Session, execution_plan_id: int) -> str:
    plan, actions = _load_plan_snapshot(db, execution_plan_id)
    _validate_digest_contract(plan)
    return compute_plan_digest_v1(
        target_id=plan.target_id,
        authorization_revision_id=plan.authorization_revision_id,
        actor_identity_id=plan.actor_identity_id,
        credential_binding_id=plan.credential_binding_id,
        policy_context=plan.policy_context,
        actions=[
            PlanActionInput(
                method=action.method,
                url=action.url,
                test_case_id=action.test_case_id,
                resource_id=action.resource_id,
            )
            for action in actions
        ],
    )


def validate_persisted_plan_integrity(db: Session, execution_plan_id: int) -> ExecutionPlan:
    plan, actions = _load_plan_snapshot(db, execution_plan_id)
    _validate_digest_contract(plan)
    if plan.action_count != len(actions):
        raise PlanIntegrityError("Persisted plan action count does not match.")
    recomputed = compute_plan_digest_v1(
        target_id=plan.target_id,
        authorization_revision_id=plan.authorization_revision_id,
        actor_identity_id=plan.actor_identity_id,
        credential_binding_id=plan.credential_binding_id,
        policy_context=plan.policy_context,
        actions=[
            PlanActionInput(
                method=action.method,
                url=action.url,
                test_case_id=action.test_case_id,
                resource_id=action.resource_id,
            )
            for action in actions
        ],
    )
    if recomputed != plan.plan_digest:
        raise PlanIntegrityError("Persisted plan digest does not match its actions.")
    return plan


def record_plan_decision(
    db: Session, *, execution_plan_id: int, decision: str
) -> ExecutionPlanApprovalRecord:
    if decision not in {"approved", "revoked"}:
        raise PlanIntegrityError("Approval decision is invalid.")
    plan = validate_persisted_plan_integrity(db, execution_plan_id)
    record = ExecutionPlanApprovalRecord(
        execution_plan_id=plan.id,
        digest_version=plan.digest_version,
        plan_digest=plan.plan_digest,
        decision=decision,
    )
    db.add(record)
    db.flush()
    return record


def is_plan_approved(db: Session, execution_plan_id: int) -> bool:
    try:
        plan = validate_persisted_plan_integrity(db, execution_plan_id)
    except PlanIntegrityError:
        return False
    latest = db.scalar(
        select(ExecutionPlanApprovalRecord)
        .where(
            ExecutionPlanApprovalRecord.execution_plan_id == plan.id,
            ExecutionPlanApprovalRecord.digest_version == plan.digest_version,
            ExecutionPlanApprovalRecord.plan_digest == plan.plan_digest,
        )
        .order_by(ExecutionPlanApprovalRecord.id.desc())
        .limit(1)
    )
    return latest is not None and latest.decision == "approved"


def _load_plan_snapshot(
    db: Session, execution_plan_id: int
) -> tuple[ExecutionPlan, list[PlanAction]]:
    plan = db.get(ExecutionPlan, execution_plan_id)
    if plan is None:
        raise PlanIntegrityError("Execution plan is unavailable.")
    actions = list(
        db.scalars(
            select(PlanAction)
            .where(PlanAction.execution_plan_id == execution_plan_id)
            .order_by(PlanAction.ordinal, PlanAction.id)
        )
    )
    if [action.ordinal for action in actions] != list(range(1, len(actions) + 1)):
        raise PlanIntegrityError("Persisted plan action ordering is invalid.")
    return plan, actions


def _validate_digest_contract(plan: ExecutionPlan) -> None:
    if plan.digest_version != DIGEST_VERSION or re.fullmatch(
        r"[0-9a-f]{64}", plan.plan_digest
    ) is None:
        raise PlanIntegrityError("Persisted plan digest contract is unsupported.")
