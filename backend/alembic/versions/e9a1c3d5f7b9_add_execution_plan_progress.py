"""add durable execution plan progress

Revision ID: e9a1c3d5f7b9
Revises: d7f9b1c3e5a7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e9a1c3d5f7b9"
down_revision: Union[str, Sequence[str], None] = "d7f9b1c3e5a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_plan_progress",
        sa.Column("execution_plan_id", sa.Integer(), nullable=False),
        sa.Column("fencing_generation", sa.BigInteger(), nullable=False),
        sa.Column("phase", sa.String(length=24), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "fencing_generation > 0", name="ck_execution_plan_progress_generation"
        ),
        sa.CheckConstraint(
            "phase IN ('pre_network', 'network_started', 'in_doubt')",
            name="ck_execution_plan_progress_phase",
        ),
        sa.ForeignKeyConstraint(
            ["execution_plan_id"], ["execution_plans.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("execution_plan_id"),
    )
    op.execute(
        """INSERT INTO execution_plan_progress (
            execution_plan_id, fencing_generation, phase, updated_at
        )
        SELECT claim.execution_plan_id, claim.fencing_generation,
               'in_doubt', clock_timestamp()
        FROM execution_plan_claims AS claim
        WHERE NOT EXISTS (
            SELECT 1 FROM test_runs AS run
            WHERE run.execution_plan_id = claim.execution_plan_id
        )"""
    )


def downgrade() -> None:
    op.drop_table("execution_plan_progress")
