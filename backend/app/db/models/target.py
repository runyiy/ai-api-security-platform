from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.authorization_profile import AuthorizationProfile


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    authorization_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "authorization_profiles.id",
            name=(
                "fk_targets_authorization_profile_id_"
                "authorization_profiles"
            ),
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    authorization_profile: Mapped[AuthorizationProfile | None] = relationship(
        back_populates="targets",
    )

    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    base_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    environment: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="development",
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
