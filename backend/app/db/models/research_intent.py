"""Immutable W1 records. Observation provenance remains a soft reference."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class VersionRecord:
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(index=True)
    context_version: Mapped[int] = mapped_column()
    target_id: Mapped[int] = mapped_column()
    number: Mapped[int] = mapped_column()
    version: Mapped[int] = mapped_column()
    body: Mapped[dict] = mapped_column(JSONB)
    digest: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def constraints(name):
    return (
        UniqueConstraint('context_id', 'number', 'version', name='uq_'+name+'_version'),
        ForeignKeyConstraint(['context_id','context_version'], ['research_context_versions.context_id','research_context_versions.version_number'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['context_id','target_id'], ['research_target_associations.context_id','research_target_associations.target_id'], ondelete='RESTRICT'),
        CheckConstraint('number BETWEEN 1 AND 1024 AND version BETWEEN 1 AND 1024', name='ck_'+name+'_numbers'),
        CheckConstraint("jsonb_typeof(body) = 'object' AND octet_length(body::text) <= 65536 AND digest ~ '^[0-9a-f]{64}$'", name='ck_'+name+'_body'),
        CheckConstraint('valid_until > recorded_at', name='ck_'+name+'_window'),
    )


class IntentMapping(VersionRecord, Base):
    __tablename__ = 'research_intent_mappings'
    __table_args__ = constraints('intent_mapping')


class IntentManifest(VersionRecord, Base):
    __tablename__ = 'research_intent_manifests'
    __table_args__ = constraints('intent_manifest')


class IntentVersion(VersionRecord, Base):
    __tablename__ = 'research_intent_versions'
    __table_args__ = constraints('intent') + (CheckConstraint("jsonb_typeof(link) = 'object' AND octet_length(link::text) <= 4096 AND link_digest ~ '^[0-9a-f]{64}$'", name='ck_intent_link'),)
    link: Mapped[dict] = mapped_column(JSONB)
    link_digest: Mapped[str] = mapped_column(String(64))


class IntentBudgetDecision(Base):
    __tablename__ = 'research_intent_budget_decisions'
    __table_args__ = (
        UniqueConstraint('manifest_id','sequence', name='uq_intent_budget_sequence'),
        CheckConstraint("sequence BETWEEN 1 AND 16 AND decision IN ('approved','revoked') AND octet_length(body::text) <= 4096", name='ck_intent_budget_body'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    manifest_id: Mapped[int] = mapped_column(ForeignKey('research_intent_manifests.id', ondelete='RESTRICT'), index=True)
    sequence: Mapped[int] = mapped_column()
    decision: Mapped[str] = mapped_column(String(16))
    body: Mapped[dict] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IntentPlanMember(Base):
    __tablename__ = 'research_intent_plan_members'
    __table_args__ = (
        UniqueConstraint('intent_id','role', name='uq_intent_role'),
        UniqueConstraint('plan_id', name='uq_intent_plan'),
        CheckConstraint("role IN ('baseline','probe','health')", name='ck_intent_role'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    intent_id: Mapped[int] = mapped_column(ForeignKey('research_intent_versions.id', ondelete='RESTRICT'), index=True)
    role: Mapped[str] = mapped_column(String(16))
    plan_id: Mapped[int] = mapped_column(ForeignKey('execution_plans.id', ondelete='RESTRICT'), index=True)
    action_id: Mapped[int] = mapped_column(ForeignKey('plan_actions.id', ondelete='RESTRICT'))
    test_case_id: Mapped[int] = mapped_column(ForeignKey('test_cases.id', ondelete='RESTRICT'), index=True)


class IntentAudit(Base):
    __tablename__ = 'research_intent_audit'
    __table_args__ = (CheckConstraint("code IN ('mapping','manifest','budget','intent','read')", name='ck_intent_audit_code'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey('research_contexts.id', ondelete='RESTRICT'), index=True)
    code: Mapped[str] = mapped_column(String(16))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
