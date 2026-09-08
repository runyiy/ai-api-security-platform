from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FindingEvidenceSimilarity(Base):
    __tablename__ = "finding_evidence_similarities"
    __table_args__ = (
        UniqueConstraint("finding_evidence_fingerprint_id", name="uq_finding_evidence_similarities_fingerprint_id"),
        CheckConstraint("comparator_id = 'sha256_exact_and_length_ratio'", name="ck_finding_evidence_similarities_comparator"),
        CheckConstraint("comparator_version = '1'", name="ck_finding_evidence_similarities_version"),
        CheckConstraint("length_similarity_bps >= 0 AND length_similarity_bps <= 10000", name="ck_finding_evidence_similarities_length_ratio"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_evidence_fingerprint_id: Mapped[int] = mapped_column(
        ForeignKey("finding_evidence_fingerprints.id",
                   name="fk_finding_evidence_similarities_fingerprint_id", ondelete="RESTRICT"),
        nullable=False,
    )
    comparator_id: Mapped[str] = mapped_column(String(48), nullable=False)
    comparator_version: Mapped[str] = mapped_column(String(16), nullable=False)
    exact_digest_match: Mapped[bool] = mapped_column(nullable=False)
    length_similarity_bps: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
