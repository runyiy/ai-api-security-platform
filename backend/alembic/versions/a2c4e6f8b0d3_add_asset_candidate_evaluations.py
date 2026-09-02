"""add revision-bound asset candidate evaluations

Revision ID: a2c4e6f8b0d3
Revises: f0b2d4e6a8c1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a2c4e6f8b0d3"
down_revision: Union[str, Sequence[str], None] = "f0b2d4e6a8c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "asset_candidate_evaluations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("authorization_revision_id", sa.Integer(), nullable=False),
        sa.Column("normalized_hostname", sa.String(length=253), nullable=False),
        sa.Column("decision_code", sa.String(length=32), nullable=False),
        sa.Column("matched_include_rule_id", sa.Integer(), nullable=True),
        sa.Column("matched_exclude_rule_id", sa.Integer(), nullable=True),
        sa.Column(
            "source_type", sa.String(length=32),
            server_default="operator_supplied", nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint(
            "decision_code IN ('asset_candidate_included', "
            "'asset_candidate_excluded', 'asset_candidate_not_included')",
            name="ck_asset_candidate_evaluations_decision_code",
        ),
        sa.CheckConstraint(
            "source_type = 'operator_supplied'",
            name="ck_asset_candidate_evaluations_source_type",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_revision_id"], ["authorization_revisions.id"],
            name="fk_asset_candidate_evaluations_authorization_revision_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["matched_include_rule_id"], ["asset_hostname_rules.id"],
            name="fk_asset_candidate_evaluations_matched_include_rule_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["matched_exclude_rule_id"], ["asset_hostname_rules.id"],
            name="fk_asset_candidate_evaluations_matched_exclude_rule_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_asset_candidate_evaluations_authorization_revision_id",
        "asset_candidate_evaluations", ["authorization_revision_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_asset_candidate_evaluations_authorization_revision_id",
        table_name="asset_candidate_evaluations",
    )
    op.drop_table("asset_candidate_evaluations")
