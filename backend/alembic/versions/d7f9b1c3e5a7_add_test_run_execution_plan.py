"""add exact execution plan provenance to test runs

Revision ID: d7f9b1c3e5a7
Revises: c5e7a9b1d3f6
Create Date: 2026-08-27 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7f9b1c3e5a7"
down_revision: Union[str, Sequence[str], None] = "c5e7a9b1d3f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_runs", sa.Column("execution_plan_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_test_runs_execution_plan_id",
        "test_runs",
        "execution_plans",
        ["execution_plan_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_test_runs_execution_plan_id", "test_runs", ["execution_plan_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_test_runs_execution_plan_id", "test_runs", type_="unique"
    )
    op.drop_constraint(
        "fk_test_runs_execution_plan_id", "test_runs", type_="foreignkey"
    )
    op.drop_column("test_runs", "execution_plan_id")
