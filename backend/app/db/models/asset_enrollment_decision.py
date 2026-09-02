from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


ENROLLMENT_REASON_CODES = (
    "ownership_confirmed",
    "scope_confirmed",
    "out_of_scope",
    "dns_risk",
    "manual_review",
    "other",
)


class AssetEnrollmentDecision(Base):
    __tablename__ = "asset_enrollment_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_asset_enrollment_decisions_decision",
        ),
        CheckConstraint(
            "reason_code IS NULL OR reason_code IN (" + ", ".join(
                f"'{code}'" for code in ENROLLMENT_REASON_CODES
            ) + ")",
            name="ck_asset_enrollment_decisions_reason_code",
        ),
        Index(
            "ix_enrollment_decisions_dns_validation_id",
            "asset_candidate_dns_validation_id",
        ),
        Index(
            "ix_enrollment_decisions_revision_id",
            "authorization_revision_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_candidate_dns_validation_id: Mapped[int] = mapped_column(
        ForeignKey(
            "asset_candidate_dns_validations.id",
            name="fk_enrollment_decisions_dns_validation_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    authorization_revision_id: Mapped[int] = mapped_column(
        ForeignKey(
            "authorization_revisions.id",
            name="fk_enrollment_decisions_authorization_revision_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    normalized_hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
