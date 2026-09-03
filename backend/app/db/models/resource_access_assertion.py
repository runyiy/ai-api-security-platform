from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ResourceAccessAssertion(Base):
    __tablename__ = "resource_access_assertions"
    __table_args__ = (
        CheckConstraint(
            "relationship IN ('owner', 'shared', 'non_owner', 'unspecified')",
            name="ck_resource_access_assertions_relationship",
        ),
        CheckConstraint(
            "expected_access IN ('allowed', 'denied', 'unspecified')",
            name="ck_resource_access_assertions_expected_access",
        ),
        CheckConstraint(
            "relationship <> 'unspecified' OR expected_access <> 'unspecified'",
            name="ck_resource_access_assertions_meaningful",
        ),
        CheckConstraint(
            "provenance IN ('human_verified', 'target_fixture', "
            "'observed_baseline', 'inferred_candidate')",
            name="ck_resource_access_assertions_provenance",
        ),
        CheckConstraint(
            "confidence BETWEEN 0 AND 100",
            name="ck_resource_access_assertions_confidence",
        ),
        CheckConstraint(
            "verification_state IN ('candidate', 'verified', 'rejected')",
            name="ck_resource_access_assertions_verification_state",
        ),
        CheckConstraint(
            "valid_until IS NULL OR "
            "(valid_from IS NOT NULL AND valid_until > valid_from)",
            name="ck_resource_access_assertions_validity_window",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    resource_id: Mapped[int] = mapped_column(
        ForeignKey("resources.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    test_identity_id: Mapped[int] = mapped_column(
        ForeignKey("test_identities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    relationship: Mapped[str] = mapped_column(String(16), nullable=False)
    expected_access: Mapped[str] = mapped_column(String(16), nullable=False)
    provenance: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    verification_state: Mapped[str] = mapped_column(String(16), nullable=False)
    asserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
