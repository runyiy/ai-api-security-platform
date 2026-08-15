from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


if TYPE_CHECKING:
    from app.db.models.credential_binding import CredentialBinding


class TestIdentity(Base):
    __tablename__ = "test_identities"

    __table_args__ = (
        UniqueConstraint(
            "target_id",
            "name",
            name="uq_test_identity_target_name",
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

    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    role: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
    )

    auth_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    credentials: Mapped[
        dict[str, Any] | None
    ] = mapped_column(
        JSONB,
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
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

    credential_bindings: Mapped[list[CredentialBinding]] = relationship(
        back_populates="test_identity",
        passive_deletes="all",
    )
