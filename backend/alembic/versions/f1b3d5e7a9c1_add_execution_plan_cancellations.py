"""add durable execution plan cancellations

Revision ID: f1b3d5e7a9c1
Revises: e9a1c3d5f7b9
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1b3d5e7a9c1"
down_revision: Union[str, Sequence[str], None] = "e9a1c3d5f7b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_plan_cancellations",
        sa.Column("execution_plan_id", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["execution_plan_id"], ["execution_plans.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("execution_plan_id"),
    )


def downgrade() -> None:
    op.drop_table("execution_plan_cancellations")
