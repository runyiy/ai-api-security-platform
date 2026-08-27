from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


if TYPE_CHECKING:
    from app.db.models.execution_plan import ExecutionPlan


class PlanAction(Base):
    __tablename__ = "plan_actions"

    __table_args__ = (
        UniqueConstraint(
            "execution_plan_id", "ordinal", name="uq_plan_actions_plan_ordinal"
        ),
        CheckConstraint("ordinal > 0", name="ck_plan_actions_ordinal_positive"),
        CheckConstraint("method = 'GET'", name="ck_plan_actions_method_get"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_plan_id: Mapped[int] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    test_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    resource_id: Mapped[int | None] = mapped_column(
        ForeignKey("resources.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    execution_plan: Mapped[ExecutionPlan] = relationship(back_populates="actions")
