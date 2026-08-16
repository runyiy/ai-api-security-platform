from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


if TYPE_CHECKING:
    from app.db.models.authorization_profile import AuthorizationProfile


class AuthorizationRevision(Base):
    __tablename__ = "authorization_revisions"

    __table_args__ = (
        UniqueConstraint(
            "authorization_profile_id",
            "revision_number",
            name="uq_authorization_revisions_profile_revision_number",
        ),
        CheckConstraint(
            "revision_number > 0",
            name="ck_authorization_revisions_revision_number_positive",
        ),
        CheckConstraint(
            "max_requests_per_second > 0",
            name=(
                "ck_authorization_revisions_"
                "max_requests_per_second_positive"
            ),
        ),
        CheckConstraint(
            (
                "valid_from IS NULL OR "
                "valid_until IS NULL OR "
                "valid_until > valid_from"
            ),
            name="ck_authorization_revisions_validity_window",
        ),
        CheckConstraint(
            (
                "lifecycle_state IN "
                "('draft', 'active', 'superseded', 'revoked')"
            ),
            name="ck_authorization_revisions_lifecycle_state",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    authorization_profile_id: Mapped[int] = mapped_column(
        ForeignKey(
            "authorization_profiles.id",
            name="fk_authorization_revisions_profile_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    authorization_profile: Mapped[AuthorizationProfile] = relationship(
        back_populates="revisions",
    )

    revision_number: Mapped[int] = mapped_column(nullable=False)

    lifecycle_state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
        server_default="draft",
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    program_name: Mapped[str] = mapped_column(String(200), nullable=False)
    program_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    authorization_type: Mapped[str] = mapped_column(String(50), nullable=False)
    authorization_reference: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    automation_allowed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    max_requests_per_second: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        server_default="1.0",
    )
    allow_get: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    allow_post: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    allow_patch: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    allow_put: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    allow_delete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    require_human_execution_approval: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
