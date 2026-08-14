from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Endpoint(Base):
    __tablename__ = "endpoints"

    __table_args__ = (
        UniqueConstraint(
            "target_id",
            "path",
            "method",
            name="uq_endpoint_target_path_method",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    target_id: Mapped[int] = mapped_column(
        ForeignKey(
            "targets.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    method: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    operation_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    requires_auth: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    parameters: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    request_body: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    security: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )