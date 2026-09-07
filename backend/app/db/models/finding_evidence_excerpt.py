from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FindingEvidenceExcerpt(Base):
    __tablename__ = "finding_evidence_excerpts"
    __table_args__ = (
        UniqueConstraint("finding_evidence_record_id", name="uq_finding_evidence_excerpts_evidence_record_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_evidence_record_id: Mapped[int] = mapped_column(
        ForeignKey("finding_evidence_records.id",
                   name="fk_finding_evidence_excerpts_evidence_record_id", ondelete="RESTRICT"),
        nullable=False,
    )
    extractor_id: Mapped[str] = mapped_column(String(48), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(16), nullable=False)
    baseline_excerpt: Mapped[str] = mapped_column(String(192), nullable=False)
    probe_excerpt: Mapped[str] = mapped_column(String(192), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
