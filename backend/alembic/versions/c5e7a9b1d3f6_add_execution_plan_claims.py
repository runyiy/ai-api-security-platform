"""add execution plan claims

Revision ID: c5e7a9b1d3f6
Revises: b3d5f7a9c1e4
Create Date: 2026-08-27 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5e7a9b1d3f6"
down_revision: Union[str, Sequence[str], None] = "b3d5f7a9c1e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_plan_claims",
        sa.Column("execution_plan_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=True),
        sa.Column("fencing_generation", sa.BigInteger(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "fencing_generation > 0",
            name="ck_execution_plan_claims_generation_positive",
        ),
        sa.ForeignKeyConstraint(
            ["execution_plan_id"], ["execution_plans.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("execution_plan_id"),
    )


def downgrade() -> None:
    op.drop_table("execution_plan_claims")
