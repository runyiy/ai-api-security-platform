from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CredentialSecret(Base):
    __tablename__ = "credential_secrets"

    __table_args__ = (
        CheckConstraint(
            "format_version > 0",
            name="ck_credential_secrets_format_version_positive",
        ),
        CheckConstraint(
            "char_length(key_version) > 0",
            name="ck_credential_secrets_key_version_not_empty",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    encrypted_payload: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    format_version: Mapped[int] = mapped_column(
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

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
