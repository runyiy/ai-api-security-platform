"""add approved enrollment provenance to targets

Revision ID: d0f2a4c6e8b1
Revises: c6e8a0b2d4f7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d0f2a4c6e8b1"
down_revision: Union[str, Sequence[str], None] = "c6e8a0b2d4f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "targets",
        sa.Column("asset_enrollment_decision_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_targets_asset_enrollment_decision_id",
        "targets",
        "asset_enrollment_decisions",
        ["asset_enrollment_decision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_targets_asset_enrollment_decision_id",
        "targets",
        ["asset_enrollment_decision_id"],
    )
    op.create_index(
        "ix_targets_asset_enrollment_decision_id",
        "targets",
        ["asset_enrollment_decision_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_targets_asset_enrollment_decision_id", table_name="targets"
    )
    op.drop_constraint(
        "uq_targets_asset_enrollment_decision_id",
        "targets",
        type_="unique",
    )
    op.drop_constraint(
        "fk_targets_asset_enrollment_decision_id",
        "targets",
        type_="foreignkey",
    )
    op.drop_column("targets", "asset_enrollment_decision_id")
