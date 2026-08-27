from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


if TYPE_CHECKING:
    from app.db.models.plan_action import PlanAction


class ExecutionPlan(Base):
    __tablename__ = "execution_plans"

    __table_args__ = (
        CheckConstraint(
            "action_count > 0 AND action_count <= 100",
            name="ck_execution_plans_action_count_bounded",
        ),
        CheckConstraint(
            "digest_version = 'v1'",
            name="ck_execution_plans_digest_version",
        ),
        CheckConstraint(
            "plan_digest ~ '^[0-9a-f]{64}$'",
            name="ck_execution_plans_digest_shape",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(
        ForeignKey("targets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    authorization_revision_id: Mapped[int] = mapped_column(
        ForeignKey("authorization_revisions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    actor_identity_id: Mapped[int] = mapped_column(
        ForeignKey("test_identities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    credential_binding_id: Mapped[int | None] = mapped_column(
        ForeignKey("credential_bindings.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    digest_version: Mapped[str] = mapped_column(String(10), nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    action_count: Mapped[int] = mapped_column(nullable=False)
    policy_context: Mapped[dict[str, Any] | list[Any]] = mapped_column(
        JSONB, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    actions: Mapped[list[PlanAction]] = relationship(
        back_populates="execution_plan",
        order_by="PlanAction.ordinal",
        passive_deletes="all",
    )
