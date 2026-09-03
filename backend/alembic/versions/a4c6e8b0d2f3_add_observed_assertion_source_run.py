"""add observed assertion source run

Revision ID: a4c6e8b0d2f3
Revises: f3b5d7e9a1c2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4c6e8b0d2f3"
down_revision: Union[str, Sequence[str], None] = "f3b5d7e9a1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "resource_access_assertions",
        sa.Column("source_test_run_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_resource_access_assertions_source_test_run_id",
        "resource_access_assertions",
        "test_runs",
        ["source_test_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ux_resource_access_assertions_source_test_run_id",
        "resource_access_assertions",
        ["source_test_run_id"],
        unique=True,
    )
    op.create_check_constraint(
        "ck_resource_access_assertions_observed_source",
        "resource_access_assertions",
        "provenance <> 'observed_baseline' OR source_test_run_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_resource_access_assertions_observed_source",
        "resource_access_assertions",
        type_="check",
    )
    op.drop_index(
        "ux_resource_access_assertions_source_test_run_id",
        table_name="resource_access_assertions",
    )
    op.drop_constraint(
        "fk_resource_access_assertions_source_test_run_id",
        "resource_access_assertions",
        type_="foreignkey",
    )
    op.drop_column("resource_access_assertions", "source_test_run_id")
