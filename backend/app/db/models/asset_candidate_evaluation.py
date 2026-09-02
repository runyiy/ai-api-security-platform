from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssetCandidateEvaluation(Base):
    __tablename__ = "asset_candidate_evaluations"
    __table_args__ = (
        CheckConstraint(
            "decision_code IN ('asset_candidate_included', "
            "'asset_candidate_excluded', 'asset_candidate_not_included')",
            name="ck_asset_candidate_evaluations_decision_code",
        ),
        CheckConstraint(
            "source_type = 'operator_supplied'",
            name="ck_asset_candidate_evaluations_source_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    authorization_revision_id: Mapped[int] = mapped_column(
        ForeignKey(
            "authorization_revisions.id",
            name="fk_asset_candidate_evaluations_authorization_revision_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    normalized_hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    decision_code: Mapped[str] = mapped_column(String(32), nullable=False)
    matched_include_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "asset_hostname_rules.id",
            name="fk_asset_candidate_evaluations_matched_include_rule_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    matched_exclude_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "asset_hostname_rules.id",
            name="fk_asset_candidate_evaluations_matched_exclude_rule_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="operator_supplied"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
