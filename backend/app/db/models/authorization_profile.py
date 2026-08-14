from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    String,
    Text,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuthorizationProfile(Base):
    __tablename__ = "authorization_profiles"

    __table_args__ = (
        CheckConstraint(
            "max_requests_per_second > 0",
            name=(
                "ck_authorization_profiles_"
                "max_requests_per_second_positive"
            ),
        ),
        CheckConstraint(
            (
                "valid_from IS NULL OR "
                "valid_until IS NULL OR "
                "valid_until > valid_from"
            ),
            name=(
                "ck_authorization_profiles_"
                "validity_window"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    program_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    program_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    authorization_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

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

    notes: Mapped[str | None] = mapped_column(
        Text,
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
