"""add revision-bound asset hostname rules

Revision ID: f0b2d4e6a8c1
Revises: e9a1c3f5b7d9
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f0b2d4e6a8c1"
down_revision: Union[str, Sequence[str], None] = "e9a1c3f5b7d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "asset_hostname_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("authorization_revision_id", sa.Integer(), nullable=False),
        sa.Column("rule_type", sa.String(length=10), nullable=False),
        sa.Column("hostname_pattern", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rule_type IN ('include', 'exclude')",
            name="ck_asset_hostname_rules_rule_type",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_revision_id"],
            ["authorization_revisions.id"],
            name="fk_asset_hostname_rules_authorization_revision_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "authorization_revision_id",
            "rule_type",
            "hostname_pattern",
            name="uq_asset_hostname_rules_revision_type_pattern",
        ),
    )
    op.create_index(
        "ix_asset_hostname_rules_authorization_revision_id",
        "asset_hostname_rules",
        ["authorization_revision_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_asset_hostname_rules_authorization_revision_id",
        table_name="asset_hostname_rules",
    )
    op.drop_table("asset_hostname_rules")
