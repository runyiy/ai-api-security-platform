"""Separate synthetic intake domain. No legacy authorization/evidence writes."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ResearchContext(Base):
    __tablename__ = "research_contexts"
    __table_args__ = (
        UniqueConstraint("project_number", name="uq_research_context_project"),
        CheckConstraint("project_number BETWEEN 1 AND 1000000", name="ck_research_context_project"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_number: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_reference: Mapped[dict | None] = mapped_column(JSONB)


class ResearchTargetAssociation(Base):
    __tablename__ = "research_target_associations"
    __table_args__ = (
        UniqueConstraint("context_id", "target_id", name="uq_research_association_context_target"),
        Index("uq_research_target_active_context", "target_id", unique=True,
              postgresql_where=text("released_at IS NULL")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey("research_contexts.id", ondelete="RESTRICT"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id", ondelete="RESTRICT"), index=True)
    review_reference: Mapped[dict] = mapped_column(JSONB)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchContextVersion(Base):
    __tablename__ = "research_context_versions"
    __table_args__ = (
        UniqueConstraint("context_id", "version_number", name="uq_research_context_version"),
        CheckConstraint("version_number BETWEEN 1 AND 10000", name="ck_research_context_version"),
        CheckConstraint("jsonb_typeof(intake) = 'object' AND octet_length(intake::text) <= 32768",
                        name="ck_research_intake_size"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey("research_contexts.id", ondelete="RESTRICT"), index=True)
    version_number: Mapped[int] = mapped_column(nullable=False)
    intake: Mapped[dict] = mapped_column(JSONB)
    # Digests only of selected authorization metadata; no legacy descriptions or credentials.
    permission_snapshots: Mapped[dict] = mapped_column(JSONB)
    correction_reference: Mapped[dict | None] = mapped_column(JSONB)
    provenance: Mapped[str] = mapped_column(String(40), default="operator_recorded_unverified")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
