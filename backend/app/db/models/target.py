from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.authorization_revision import AuthorizationRevision


class Target(Base):
    __tablename__ = "targets"

    __table_args__ = (
        CheckConstraint(
            "network_mode IN ('private_local', 'external_public_authorized')",
            name="ck_targets_network_mode",
        ),
        UniqueConstraint(
            "asset_enrollment_decision_id",
            name="uq_targets_asset_enrollment_decision_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    asset_enrollment_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "asset_enrollment_decisions.id",
            name="fk_targets_asset_enrollment_decision_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
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

    authorization_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "authorization_revisions.id",
            name="fk_targets_authorization_revision_id_authorization_revisions",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    authorization_revision: Mapped[AuthorizationRevision | None] = relationship(
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

    network_mode: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="private_local",
        server_default="private_local",
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
