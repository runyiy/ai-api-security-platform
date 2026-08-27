from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


if TYPE_CHECKING:
    from app.db.models.execution_plan import ExecutionPlan


class ExecutionPlanApprovalRecord(Base):
    __tablename__ = "execution_plan_approval_records"

    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'revoked')",
            name="ck_execution_plan_approval_records_decision",
        ),
        CheckConstraint(
            "digest_version = 'v1'",
            name="ck_execution_plan_approval_records_digest_version",
        ),
        CheckConstraint(
            "plan_digest ~ '^[0-9a-f]{64}$'",
            name="ck_execution_plan_approval_records_digest_shape",
        ),
        Index(
            "ix_execution_plan_approval_exact_decision",
            "execution_plan_id",
            "digest_version",
            "plan_digest",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_plan_id: Mapped[int] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="RESTRICT"), nullable=False
    )
    digest_version: Mapped[str] = mapped_column(String(10), nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    execution_plan: Mapped[ExecutionPlan] = relationship(
        back_populates="approval_records"
    )
