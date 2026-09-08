from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FindingEvidenceFingerprint(Base):
    __tablename__ = "finding_evidence_fingerprints"
    __table_args__ = (
        UniqueConstraint("finding_evidence_record_id", name="uq_finding_evidence_fingerprints_evidence_record_id"),
        CheckConstraint("algorithm = 'sha256'", name="ck_finding_evidence_fingerprints_algorithm"),
        CheckConstraint("fingerprint_version = '1'", name="ck_finding_evidence_fingerprints_version"),
        CheckConstraint("length(baseline_digest) = 64 AND baseline_digest ~ '^[0-9a-f]{64}$'", name="ck_finding_evidence_fingerprints_baseline_digest"),
        CheckConstraint("length(probe_digest) = 64 AND probe_digest ~ '^[0-9a-f]{64}$'", name="ck_finding_evidence_fingerprints_probe_digest"),
        CheckConstraint("baseline_body_bytes >= 0", name="ck_finding_evidence_fingerprints_baseline_body_bytes"),
        CheckConstraint("probe_body_bytes >= 0", name="ck_finding_evidence_fingerprints_probe_body_bytes"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_evidence_record_id: Mapped[int] = mapped_column(
        ForeignKey("finding_evidence_records.id",
                   name="fk_finding_evidence_fingerprints_evidence_record_id", ondelete="RESTRICT"),
        nullable=False,
    )
    algorithm: Mapped[str] = mapped_column(String(16), nullable=False)
    fingerprint_version: Mapped[str] = mapped_column(String(16), nullable=False)
    baseline_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    probe_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_body_bytes: Mapped[int] = mapped_column(nullable=False)
    probe_body_bytes: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
