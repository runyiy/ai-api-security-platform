"""Append-only task versions, local decisions, exact membership and held GETs."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ResearchTask(Base):
    __tablename__ = 'research_tasks'
    __table_args__ = (
        UniqueConstraint('context_id', 'number', name='uq_task_number'),
        UniqueConstraint('id', 'context_id', 'target_id', name='uq_task_context_target'),
        ForeignKeyConstraint(['context_id', 'target_id'],
            ['research_target_associations.context_id', 'research_target_associations.target_id'], ondelete='RESTRICT'),
        CheckConstraint('number BETWEEN 1 AND 1024', name='ck_task_number'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(nullable=False, index=True)
    target_id: Mapped[int] = mapped_column(nullable=False)
    number: Mapped[int] = mapped_column(nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchTaskVersion(Base):
    __tablename__ = 'research_task_versions'
    __table_args__ = (
        UniqueConstraint('task_id', 'version', name='uq_task_version'),
        UniqueConstraint('task_id', 'id', name='uq_task_version_task'),
        ForeignKeyConstraint(['task_id', 'context_id', 'target_id'],
            ['research_tasks.id', 'research_tasks.context_id', 'research_tasks.target_id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['context_id', 'context_version'],
            ['research_context_versions.context_id', 'research_context_versions.version_number'], ondelete='RESTRICT'),
        CheckConstraint('version BETWEEN 1 AND 16', name='ck_task_version'),
        CheckConstraint("jsonb_typeof(body)='object' AND octet_length(body::text)<=65536 AND digest ~ '^[0-9a-f]{64}$'", name='ck_task_version_body'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(nullable=False, index=True)
    context_id: Mapped[int] = mapped_column(nullable=False)
    context_version: Mapped[int] = mapped_column(nullable=False)
    target_id: Mapped[int] = mapped_column(nullable=False)
    authorization_revision_id: Mapped[int] = mapped_column(ForeignKey('authorization_revisions.id', ondelete='RESTRICT'), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchTaskMember(Base):
    __tablename__ = 'research_task_members'
    __table_args__ = (
        UniqueConstraint('version_id', 'plan_id', name='uq_task_member_plan'),
        UniqueConstraint('version_id', 'ordinal', name='uq_task_member_ordinal'),
        CheckConstraint('ordinal BETWEEN 1 AND 100', name='ck_task_member_ordinal'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey('research_task_versions.id', ondelete='RESTRICT'), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey('execution_plans.id', ondelete='RESTRICT'), nullable=False, index=True)
    intent_id: Mapped[int] = mapped_column(ForeignKey('research_intent_versions.id', ondelete='RESTRICT'), nullable=False)
    manifest_id: Mapped[int] = mapped_column(ForeignKey('research_intent_manifests.id', ondelete='RESTRICT'), nullable=False)


class ResearchTaskEvent(Base):
    __tablename__ = 'research_task_events'
    __table_args__ = (
        UniqueConstraint('task_id', 'sequence', name='uq_task_event_sequence'),
        ForeignKeyConstraint(['task_id', 'version_id'], ['research_task_versions.task_id', 'research_task_versions.id'], ondelete='RESTRICT'),
        CheckConstraint('sequence BETWEEN 1 AND 64', name='ck_task_event_sequence'),
        CheckConstraint("kind IN ('version_created','approved','revoked','paused','cancelled') AND jsonb_typeof(body)='object' AND octet_length(body::text)<=4096", name='ck_task_event_body'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(nullable=False, index=True)
    version_id: Mapped[int] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchTaskAllocation(Base):
    __tablename__ = 'research_task_allocations'
    __table_args__ = (
        UniqueConstraint('task_id', 'slot', name='uq_task_allocation_slot'),
        ForeignKeyConstraint(['task_id', 'first_version_id'], ['research_task_versions.task_id', 'research_task_versions.id'], ondelete='RESTRICT'),
        CheckConstraint("slot BETWEEN 1 AND 100 AND requests=1 AND state='held_unreconciled'", name='ck_task_allocation'),
    )
    # A plan cannot create allowance in another task, even after revocation.
    plan_id: Mapped[int] = mapped_column(ForeignKey('execution_plans.id', ondelete='RESTRICT'), primary_key=True)
    task_id: Mapped[int] = mapped_column(nullable=False, index=True)
    first_version_id: Mapped[int] = mapped_column(nullable=False)
    slot: Mapped[int] = mapped_column(nullable=False)
    requests: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


TABLES = (ResearchTask, ResearchTaskVersion, ResearchTaskMember, ResearchTaskEvent, ResearchTaskAllocation)
