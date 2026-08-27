from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SafetyDecisionRecord(Base):
    __tablename__ = "safety_decision_records"

    __table_args__ = (
        CheckConstraint(
            "stage IN ('plan', 'policy', 'execution')",
            name="ck_safety_decision_records_stage",
        ),
        CheckConstraint(
            "operation IN ('testcase_plan', 'policy_check', "
            "'test_execution', 'openapi_import')",
            name="ck_safety_decision_records_operation",
        ),
        CheckConstraint(
            "outcome IN ('created', 'allowed', 'blocked', 'succeeded', 'failed')",
            name="ck_safety_decision_records_outcome",
        ),
        CheckConstraint(
            "(stage = 'plan' AND outcome = 'created') OR "
            "(stage = 'policy' AND outcome IN ('allowed', 'blocked')) OR "
            "(stage = 'execution' AND outcome IN ('blocked', 'succeeded', 'failed'))",
            name="ck_safety_decision_records_stage_outcome",
        ),
        CheckConstraint(
            "stage <> 'plan' OR execution_plan_id IS NOT NULL",
            name="ck_safety_decision_records_plan_identified",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[int] = mapped_column(
        ForeignKey("targets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    authorization_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("authorization_revisions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    execution_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    plan_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("plan_actions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    test_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    test_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_runs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    matched_scope_id: Mapped[int | None] = mapped_column(nullable=True)
    policy_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
