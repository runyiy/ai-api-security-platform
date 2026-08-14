from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SecurityReport(Base):
    __tablename__ = "security_reports"

    __table_args__ = (
        UniqueConstraint(
            "finding_id",
            "version",
            name="uq_security_report_finding_version",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    finding_id: Mapped[int] = mapped_column(
        ForeignKey(
            "findings.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    target_id: Mapped[int] = mapped_column(
        ForeignKey(
            "targets.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    report_format: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="markdown",
    )

    report_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )

    markdown_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )