"""Synthetic observation domain; payload deletion never touches legacy evidence."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class ObservationControl(Base):
    __tablename__ = "research_observation_controls"
    context_id: Mapped[int] = mapped_column(ForeignKey("research_contexts.id", ondelete="RESTRICT"), primary_key=True)
    recovery_token: Mapped[str | None] = mapped_column(String(36))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    review: Mapped[dict] = mapped_column(JSONB)


class ObservationPreparation(Base):
    __tablename__ = "research_observation_preparations"
    __table_args__ = (
        UniqueConstraint("context_id", "preparation_ref", name="uq_observation_preparation_context_ref"),
        UniqueConstraint("context_id", "id", name="uq_observation_preparation_context_id"),
        ForeignKeyConstraint(["context_id", "target_id"],
            ["research_target_associations.context_id", "research_target_associations.target_id"], ondelete="RESTRICT"),
        CheckConstraint("octet_length(registry::text) <= 32768", name="ck_observation_registry_size"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey("research_contexts.id", ondelete="RESTRICT"))
    target_id: Mapped[int] = mapped_column(nullable=False)
    preparation_ref: Mapped[str] = mapped_column(String(64))
    registry: Mapped[dict] = mapped_column(JSONB)
    origin: Mapped[str] = mapped_column(String(256))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ObservationRecord(Base):
    __tablename__ = "research_observation_records"
    __table_args__ = (
        UniqueConstraint("context_id", "batch_ref", name="uq_observation_batch_context"),
        UniqueConstraint("context_id", "id", name="uq_observation_record_context_id"),
        ForeignKeyConstraint(["context_id", "preparation_id"],
            ["research_observation_preparations.context_id", "research_observation_preparations.id"], ondelete="RESTRICT"),
        CheckConstraint("format_version = 'ra-observation/1' AND lifecycle_version = 'ra-observation-lifecycle/1' AND provenance = 'operator_import_unverified'", name="ck_observation_provenance"),
        CheckConstraint("state IN ('available','deleted','quarantined')", name="ck_observation_state"),
        CheckConstraint("expires_at > accepted_at AND expires_at <= accepted_at + interval '2592000 seconds'", name="ck_observation_expiry"),
        CheckConstraint("hold_until IS NULL OR (hold_started_at IS NOT NULL AND hold_until > hold_started_at AND hold_until <= hold_started_at + interval '2592000 seconds')", name="ck_observation_hold"),
        CheckConstraint("jsonb_array_length(entry_order) BETWEEN 1 AND 128", name="ck_observation_entries"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey("research_contexts.id", ondelete="RESTRICT"))
    preparation_id: Mapped[int] = mapped_column(nullable=False)
    batch_ref: Mapped[str] = mapped_column(String(64))
    format_version: Mapped[str] = mapped_column(String(32))
    lifecycle_version: Mapped[str] = mapped_column(String(40))
    provenance: Mapped[str] = mapped_column(String(40))
    digest: Mapped[str] = mapped_column(String(64))
    entry_order: Mapped[list] = mapped_column(JSONB)
    corrects_id: Mapped[int | None] = mapped_column()  # Project-validated soft history reference; may expire.
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16), default="available")
    unavailable_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hold_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hold_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hold_review: Mapped[dict | None] = mapped_column(JSONB)
    hold_reason: Mapped[str | None] = mapped_column(String(32))


class ObservationPayload(Base):
    __tablename__ = "research_observation_payloads"
    __table_args__ = (
        ForeignKeyConstraint(["context_id", "observation_id"],
            ["research_observation_records.context_id", "research_observation_records.id"], ondelete="RESTRICT"),
        CheckConstraint("octet_length(canonical_payload) <= 262144", name="ck_observation_payload_bytes"),
    )
    observation_id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(nullable=False)
    canonical_payload: Mapped[str] = mapped_column()  # Only qualified, canonical minimized JSON.


class ObservationEvent(Base):
    __tablename__ = "research_observation_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey("research_contexts.id", ondelete="RESTRICT"))
    observation_id: Mapped[int | None] = mapped_column()  # No indefinite content/FK retention.
    hold_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    code: Mapped[str] = mapped_column(String(32))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    review: Mapped[dict | None] = mapped_column(JSONB)
