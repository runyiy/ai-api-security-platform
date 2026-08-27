"""add test run revision provenance

Revision ID: c3e5a7b9d1f2
Revises: b2d4f6a8c0e1
Create Date: 2026-08-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3e5a7b9d1f2"
down_revision: Union[str, Sequence[str], None] = "b2d4f6a8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_runs",
        sa.Column("authorization_revision_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_test_runs_authorization_revision_id",
        "test_runs",
        "authorization_revisions",
        ["authorization_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_test_runs_authorization_revision_id",
        "test_runs",
        ["authorization_revision_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_test_runs_authorization_revision_id",
        table_name="test_runs",
    )
    op.drop_constraint(
        "fk_test_runs_authorization_revision_id",
        "test_runs",
        type_="foreignkey",
    )
    op.drop_column("test_runs", "authorization_revision_id")
