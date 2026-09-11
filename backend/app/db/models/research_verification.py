"""W2 immutable confirmations, exact network provenance and minimized evidence."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.models.research_intent import VersionRecord, constraints


class VerificationContract(VersionRecord, Base):
    __tablename__ = 'research_verification_contracts'
    __table_args__ = constraints('verification_contract') + (
        UniqueConstraint('manifest_id', name='uq_verification_contract_manifest'),
        UniqueConstraint('context_id', 'id', name='uq_verification_contract_context'),
    )
    manifest_id: Mapped[int] = mapped_column(ForeignKey('research_intent_manifests.id', ondelete='RESTRICT'))


class EvidenceRecord:
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey('research_contexts.id', ondelete='RESTRICT'), index=True)
    body: Mapped[dict] = mapped_column(JSONB)
    digest: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def bounded(name):
    return (CheckConstraint("jsonb_typeof(body) = 'object' AND octet_length(body::text) <= 4096 AND digest ~ '^[0-9a-f]{64}$'", name='ck_'+name+'_body'),)


class VerificationHealthSelection(EvidenceRecord, Base):
    __tablename__ = 'research_verification_health_selections'
    __table_args__ = bounded('verification_health_selection') + (
        UniqueConstraint('manifest_id', name='uq_verification_health_manifest'),
        ForeignKeyConstraint(['context_id', 'contract_id'], ['research_verification_contracts.context_id', 'research_verification_contracts.id'], ondelete='RESTRICT'),
    )
    manifest_id: Mapped[int] = mapped_column(ForeignKey('research_intent_manifests.id', ondelete='RESTRICT'))
    contract_id: Mapped[int] = mapped_column()


class VerificationAttempt(EvidenceRecord, Base):
    __tablename__ = 'research_verification_attempts'
    __table_args__ = bounded('verification_attempt') + (
        UniqueConstraint('plan_id', name='uq_verification_attempt_plan'),
        UniqueConstraint('manifest_id', 'slot', name='uq_verification_attempt_slot'),
        UniqueConstraint('context_id', 'id', name='uq_verification_attempt_context'),
        CheckConstraint("slot IN ('baseline','probe','health_baseline','health_probe')", name='ck_verification_attempt_slot'),
    )
    manifest_id: Mapped[int] = mapped_column(ForeignKey('research_intent_manifests.id', ondelete='RESTRICT'))
    intent_id: Mapped[int] = mapped_column(ForeignKey('research_intent_versions.id', ondelete='RESTRICT'))
    plan_id: Mapped[int] = mapped_column(ForeignKey('execution_plans.id', ondelete='RESTRICT'))
    slot: Mapped[str] = mapped_column(String(16))


class VerificationWitness(EvidenceRecord, Base):
    __tablename__ = 'research_verification_witnesses'
    __table_args__ = bounded('verification_witness') + (
        UniqueConstraint('plan_id', name='uq_verification_witness_plan'),
        UniqueConstraint('run_id', name='uq_verification_witness_run'),
        UniqueConstraint('attempt_id', name='uq_verification_witness_attempt'),
        UniqueConstraint('context_id', 'id', name='uq_verification_witness_context'),
        ForeignKeyConstraint(['context_id', 'attempt_id'], ['research_verification_attempts.context_id', 'research_verification_attempts.id'], ondelete='RESTRICT'),
    )
    intent_id: Mapped[int] = mapped_column(ForeignKey('research_intent_versions.id', ondelete='RESTRICT'), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('execution_plans.id', ondelete='RESTRICT'))
    run_id: Mapped[int] = mapped_column(ForeignKey('test_runs.id', ondelete='RESTRICT'))
    attempt_id: Mapped[int] = mapped_column()


class VerificationPair(EvidenceRecord, Base):
    __tablename__ = 'research_verification_pairs'
    __table_args__ = bounded('verification_pair') + (
        UniqueConstraint('intent_id', name='uq_verification_pair_intent'),
        ForeignKeyConstraint(['context_id', 'baseline_id'], ['research_verification_witnesses.context_id', 'research_verification_witnesses.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['context_id', 'probe_id'], ['research_verification_witnesses.context_id', 'research_verification_witnesses.id'], ondelete='RESTRICT'),
    )
    intent_id: Mapped[int] = mapped_column(ForeignKey('research_intent_versions.id', ondelete='RESTRICT'))
    baseline_id: Mapped[int] = mapped_column()
    probe_id: Mapped[int | None] = mapped_column()


class VerificationAudit(Base):
    __tablename__ = 'research_verification_audit'
    __table_args__ = (CheckConstraint("code IN ('confirm','select_health','approve','dispatch','witness','pair','read')", name='ck_verification_audit_code'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey('research_contexts.id', ondelete='RESTRICT'), index=True)
    code: Mapped[str] = mapped_column(String(16))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VerificationClockFault(Base):
    """Durable safety fence, independently committed even when a consumer rolls back.

    Soft exact provenance intentionally avoids FK locks held by the failed
    consumer. The writer selects an existing owned immutable intent; one fence
    per intent bounds this table by the existing intent capacity.
    """
    __tablename__ = 'research_verification_clock_faults'
    __table_args__ = (UniqueConstraint('intent_id',name='uq_verification_clock_fault_intent'),
        CheckConstraint("intent_id > 0 AND context_id > 0 AND digest ~ '^[0-9a-f]{64}$'",name='ck_verification_clock_fault_reference'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(index=True)
    intent_id: Mapped[int] = mapped_column()
    digest: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
