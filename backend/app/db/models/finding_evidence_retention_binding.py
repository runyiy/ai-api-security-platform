from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FindingEvidenceRetentionBinding(Base):
    __tablename__ = "finding_evidence_retention_bindings"
    __table_args__ = (
        UniqueConstraint("finding_evidence_record_id", name="uq_finding_evidence_retention_evidence_record_id"),
        CheckConstraint("policy_id = 'm13_minimized_finding_evidence'", name="ck_finding_evidence_retention_policy_id"),
        CheckConstraint("policy_version = '1'", name="ck_finding_evidence_retention_policy_version"),
        CheckConstraint("retention_mode = 'explicit_management_only'", name="ck_finding_evidence_retention_mode"),
        CheckConstraint("automatic_deletion_enabled = false", name="ck_finding_evidence_retention_no_automatic_deletion"),
        CheckConstraint("raw_response_body_retained = false", name="ck_finding_evidence_retention_no_raw_body"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_evidence_record_id: Mapped[int] = mapped_column(
        ForeignKey("finding_evidence_records.id",
                   name="fk_finding_evidence_retention_evidence_record_id", ondelete="RESTRICT"),
        nullable=False,
    )
    policy_id: Mapped[str] = mapped_column(String(48), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(16), nullable=False)
    retention_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    automatic_deletion_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    raw_response_body_retained: Mapped[bool] = mapped_column(Boolean, nullable=False)
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
