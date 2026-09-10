"""Append-only W3 evidence and proposals. No observation/body copies."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class RuleValidation(Base):
    __tablename__ = 'research_rule_validations'
    __table_args__ = (
        CheckConstraint("jsonb_typeof(body) = 'object' AND octet_length(body::text) <= 32768 AND digest ~ '^[0-9a-f]{64}$'", name='ck_rule_validation_body'),
        CheckConstraint('valid_until > recorded_at', name='ck_rule_validation_window'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey('research_knowledge_versions.id', ondelete='RESTRICT'), index=True)
    body: Mapped[dict] = mapped_column(JSONB)
    digest: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RuleFeedback(Base):
    __tablename__ = 'research_rule_feedback'
    __table_args__ = (CheckConstraint("jsonb_typeof(body) = 'object' AND octet_length(body::text) <= 4096", name='ck_rule_feedback_body'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey('research_knowledge_versions.id', ondelete='RESTRICT'), index=True)
    validation_id: Mapped[int] = mapped_column(ForeignKey('research_rule_validations.id', ondelete='RESTRICT'))
    body: Mapped[dict] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RuleFeedbackReview(Base):
    __tablename__ = 'research_rule_feedback_reviews'
    __table_args__ = (
        UniqueConstraint('feedback_id', name='uq_rule_feedback_review'),
        CheckConstraint("decision IN ('accept','reject','invalidate') AND octet_length(body::text) <= 4096", name='ck_rule_feedback_review'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    feedback_id: Mapped[int] = mapped_column(ForeignKey('research_rule_feedback.id', ondelete='RESTRICT'), index=True)
    decision: Mapped[str] = mapped_column(String(16))
    body: Mapped[dict] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
