"""Append-only synthetic proposal history; observation references are soft."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class ResearchSubjectVersion(Base):
    __tablename__ = "research_subject_versions"
    __table_args__ = (
        UniqueConstraint("context_id", "proposal_number", "version_number", name="uq_research_subject_version"),
        ForeignKeyConstraint(["context_id", "target_id"],
            ["research_target_associations.context_id", "research_target_associations.target_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["context_id", "context_version"],
            ["research_context_versions.context_id", "research_context_versions.version_number"], ondelete="RESTRICT"),
        CheckConstraint("proposal_number BETWEEN 1 AND 1024 AND version_number BETWEEN 1 AND 1024", name="ck_research_subject_numbers"),
        CheckConstraint("jsonb_typeof(proposal) = 'object' AND octet_length(proposal::text) <= 16384", name="ck_research_subject_size"),
        CheckConstraint("provenance = 'operator_proposed_unverified'", name="ck_research_subject_provenance"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(index=True)
    target_id: Mapped[int] = mapped_column()
    context_version: Mapped[int] = mapped_column()
    proposal_number: Mapped[int] = mapped_column()
    version_number: Mapped[int] = mapped_column()
    proposal: Mapped[dict] = mapped_column(JSONB)
    credential_version_id: Mapped[int | None] = mapped_column()  # Metadata only; never ciphertext.
    correction_reference: Mapped[dict | None] = mapped_column(JSONB)
    provenance: Mapped[str] = mapped_column(String(40), default="operator_proposed_unverified")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
