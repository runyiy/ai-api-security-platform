"""New synthetic knowledge domain; soft observation references never block purge."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class KnowledgeVersion(Base):
    __tablename__ = 'research_knowledge_versions'
    __table_args__ = (
        ForeignKeyConstraint(['context_id', 'target_id'], ['research_target_associations.context_id', 'research_target_associations.target_id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['context_id', 'context_version'], ['research_context_versions.context_id', 'research_context_versions.version_number'], ondelete='RESTRICT'),
        UniqueConstraint('context_id', 'scope', 'knowledge_id', 'version', name='uq_knowledge_project_version'),
        Index('uq_knowledge_shared_version', 'knowledge_id', 'version', unique=True, postgresql_where=text("scope = 'reusable_synthetic'")),
        CheckConstraint("scope IN ('project','reusable_synthetic') AND version BETWEEN 1 AND 10000", name='ck_knowledge_identity'),
        CheckConstraint("jsonb_typeof(content) = 'object' AND octet_length(content::text) <= 32768 AND digest ~ '^[0-9a-f]{64}$'", name='ck_knowledge_content'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey('research_contexts.id', ondelete='RESTRICT'), index=True)
    context_version: Mapped[int] = mapped_column()
    target_id: Mapped[int] = mapped_column()  # Qualified at admission/consumption; no automatic enrollment.
    scope: Mapped[str] = mapped_column(String(24))
    knowledge_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column()
    content: Mapped[dict] = mapped_column(JSONB)
    digest: Mapped[str] = mapped_column(String(64))
    review: Mapped[dict] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class KnowledgeEvent(Base):
    __tablename__ = 'research_knowledge_events'
    __table_args__ = (
        UniqueConstraint('version_id', 'sequence', name='uq_knowledge_event_sequence'),
        CheckConstraint("sequence BETWEEN 1 AND 16 AND action IN ('review','reuse','publish','withdraw','disable')", name='ck_knowledge_event'),
        CheckConstraint("jsonb_typeof(body) = 'object' AND octet_length(body::text) <= 4096", name='ck_knowledge_event_body'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey('research_knowledge_versions.id', ondelete='RESTRICT'), index=True)
    sequence: Mapped[int] = mapped_column()
    action: Mapped[str] = mapped_column(String(16))
    body: Mapped[dict] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class KnowledgeAudit(Base):
    __tablename__ = 'research_knowledge_audit'
    __table_args__ = (CheckConstraint("code IN ('record','decision','query','rotate') AND octet_length(review::text) <= 256", name='ck_knowledge_audit'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey('research_contexts.id', ondelete='RESTRICT'), index=True)
    code: Mapped[str] = mapped_column(String(16))
    review: Mapped[dict | None] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
