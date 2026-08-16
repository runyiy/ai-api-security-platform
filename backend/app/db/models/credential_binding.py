from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


if TYPE_CHECKING:
    from app.db.models.credential_secret_version import CredentialSecretVersion
    from app.db.models.test_identity import TestIdentity


class CredentialBinding(Base):
    __tablename__ = "credential_bindings"

    secret_versions: Mapped[list[CredentialSecretVersion]] = relationship(
        back_populates="credential_binding",
        passive_deletes="all",
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    test_identity_id: Mapped[int] = mapped_column(
        ForeignKey(
            "test_identities.id",
            name=(
                "fk_credential_bindings_test_identity_id_"
                "test_identities"
            ),
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    test_identity: Mapped[TestIdentity] = relationship(
        back_populates="credential_bindings",
    )

    auth_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
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
