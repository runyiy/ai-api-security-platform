from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FindingEvidenceRecord(Base):
    __tablename__ = "finding_evidence_records"
    __table_args__ = (
        UniqueConstraint("finding_id", name="uq_finding_evidence_records_finding_id"),
        CheckConstraint(
            "baseline_test_run_id <> probe_test_run_id",
            name="ck_finding_evidence_records_distinct_runs",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(
        ForeignKey("findings.id", name="fk_finding_evidence_records_finding_id", ondelete="RESTRICT"),
        nullable=False,
    )
    probe_test_run_id: Mapped[int] = mapped_column(
        ForeignKey("test_runs.id", name="fk_finding_evidence_records_probe_test_run_id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    baseline_test_run_id: Mapped[int] = mapped_column(
        ForeignKey("test_runs.id", name="fk_finding_evidence_records_baseline_test_run_id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    evidence_type: Mapped[str] = mapped_column(String(40), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(48), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_status_code: Mapped[int] = mapped_column(nullable=False)
    probe_status_code: Mapped[int] = mapped_column(nullable=False)
    baseline_resource_identifier_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    probe_resource_identifier_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
