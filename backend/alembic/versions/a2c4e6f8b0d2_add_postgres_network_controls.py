"""add PostgreSQL network controls

Revision ID: a2c4e6f8b0d2
Revises: f1b3d5e7a9c1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a2c4e6f8b0d2"
down_revision: Union[str, Sequence[str], None] = "f1b3d5e7a9c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "network_global_control",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_enabled", sa.Boolean(), nullable=False),
        sa.Column("maximum_concurrency", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_network_global_control_singleton"),
        sa.CheckConstraint(
            "maximum_concurrency BETWEEN 1 AND 1024",
            name="ck_network_global_control_concurrency",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "network_disabled_targets",
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["target_id"], ["targets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("target_id"),
    )
    op.execute(
        "INSERT INTO network_global_control "
        "(id, global_enabled, maximum_concurrency, updated_at) "
        "VALUES (1, true, 4, clock_timestamp())"
    )


def downgrade() -> None:
    op.drop_table("network_disabled_targets")
    op.drop_table("network_global_control")
