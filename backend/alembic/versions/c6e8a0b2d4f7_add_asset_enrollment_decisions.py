"""add explicit human asset enrollment decisions

Revision ID: c6e8a0b2d4f7
Revises: b4d6f8a0c2e5
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6e8a0b2d4f7"
down_revision: Union[str, Sequence[str], None] = "b4d6f8a0c2e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "asset_enrollment_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "asset_candidate_dns_validation_id", sa.Integer(), nullable=False
        ),
        sa.Column("authorization_revision_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=10), nullable=False),
        sa.Column("normalized_hostname", sa.String(length=253), nullable=False),
        sa.Column("reason_code", sa.String(length=40), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_asset_enrollment_decisions_decision",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code IN ("
            "'ownership_confirmed', 'scope_confirmed', 'out_of_scope', "
            "'dns_risk', 'manual_review', 'other')",
            name="ck_asset_enrollment_decisions_reason_code",
        ),
        sa.ForeignKeyConstraint(
            ["asset_candidate_dns_validation_id"],
            ["asset_candidate_dns_validations.id"],
            name="fk_enrollment_decisions_dns_validation_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_revision_id"], ["authorization_revisions.id"],
            name="fk_enrollment_decisions_authorization_revision_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_enrollment_decisions_dns_validation_id",
        "asset_enrollment_decisions",
        ["asset_candidate_dns_validation_id"],
    )
    op.create_index(
        "ix_enrollment_decisions_revision_id",
        "asset_enrollment_decisions",
        ["authorization_revision_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_enrollment_decisions_revision_id",
        table_name="asset_enrollment_decisions",
    )
    op.drop_index(
        "ix_enrollment_decisions_dns_validation_id",
        table_name="asset_enrollment_decisions",
    )
    op.drop_table("asset_enrollment_decisions")
