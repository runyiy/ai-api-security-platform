from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


if TYPE_CHECKING:
    from app.db.models.credential_binding import CredentialBinding


class CredentialSecretVersion(Base):
    __tablename__ = "credential_secret_versions"

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    credential_binding_id: Mapped[int] = mapped_column(
        ForeignKey(
            "credential_bindings.id",
            name="fk_secret_versions_credential_binding_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    credential_binding: Mapped[CredentialBinding] = relationship(
        back_populates="secret_versions",
    )

    encrypted_envelope: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    envelope_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    key_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
