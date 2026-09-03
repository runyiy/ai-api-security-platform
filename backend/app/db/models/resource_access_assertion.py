from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func,
)
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
        CheckConstraint(
            "provenance <> 'observed_baseline' OR source_test_run_id IS NOT NULL",
            name="ck_resource_access_assertions_observed_source",
        ),
        CheckConstraint(
            "reviewed_assertion_id IS NULL OR reviewed_assertion_id <> id",
            name="ck_resource_access_assertions_review_not_self",
        ),
        CheckConstraint(
            "reviewed_assertion_id IS NULL OR provenance = 'human_verified'",
            name="ck_resource_access_assertions_review_provenance",
        ),
        CheckConstraint(
            "reviewed_assertion_id IS NULL OR "
            "verification_state IN ('verified', 'rejected')",
            name="ck_resource_access_assertions_review_state",
        ),
        CheckConstraint(
            "reviewed_assertion_id IS NULL OR source_test_run_id IS NULL",
            name="ck_resource_access_assertions_review_source_run",
        ),
        Index(
            "ux_resource_access_assertions_source_test_run_id",
            "source_test_run_id",
            unique=True,
        ),
        Index(
            "ux_resource_access_assertions_reviewed_assertion_id",
            "reviewed_assertion_id",
            unique=True,
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
    source_test_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewed_assertion_id: Mapped[int | None] = mapped_column(
        ForeignKey("resource_access_assertions.id", ondelete="RESTRICT"),
        nullable=True,
    )
