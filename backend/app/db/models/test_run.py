from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TestRun(Base):
    __tablename__ = "test_runs"

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    test_case_id: Mapped[int] = mapped_column(
        ForeignKey(
            "test_cases.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    request_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )

    response_status: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    response_body: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    authorization_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "authorization_revisions.id",
            name="fk_test_runs_authorization_revision_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    execution_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "execution_plans.id",
            name="fk_test_runs_execution_plan_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        unique=True,
    )
