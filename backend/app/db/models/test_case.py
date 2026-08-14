from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TestCase(Base):
    __tablename__ = "test_cases"

    __table_args__ = (
        UniqueConstraint(
            "endpoint_id",
            "actor_identity_id",
            "resource_id",
            "test_type",
            name="uq_test_case_endpoint_actor_resource_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey(
            "endpoints.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    actor_identity_id: Mapped[int] = mapped_column(
        ForeignKey(
            "test_identities.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    resource_id: Mapped[int] = mapped_column(
        ForeignKey(
            "resources.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    test_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    ownership_relation: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    expected_statuses: Mapped[list[int]] = mapped_column(
        ARRAY(Integer),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )