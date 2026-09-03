"""add assertion review lineage

Revision ID: b6d8f0a2c4e5
Revises: a4c6e8b0d2f3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b6d8f0a2c4e5"
down_revision: Union[str, Sequence[str], None] = "a4c6e8b0d2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "resource_access_assertions",
        sa.Column("reviewed_assertion_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_resource_access_assertions_reviewed_assertion_id",
        "resource_access_assertions",
        "resource_access_assertions",
        ["reviewed_assertion_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ux_resource_access_assertions_reviewed_assertion_id",
        "resource_access_assertions",
        ["reviewed_assertion_id"],
        unique=True,
    )
    op.create_check_constraint(
        "ck_resource_access_assertions_review_not_self",
        "resource_access_assertions",
        "reviewed_assertion_id IS NULL OR reviewed_assertion_id <> id",
    )
    op.create_check_constraint(
        "ck_resource_access_assertions_review_provenance",
        "resource_access_assertions",
        "reviewed_assertion_id IS NULL OR provenance = 'human_verified'",
    )
    op.create_check_constraint(
        "ck_resource_access_assertions_review_state",
        "resource_access_assertions",
        "reviewed_assertion_id IS NULL OR verification_state IN ('verified', 'rejected')",
    )
    op.create_check_constraint(
        "ck_resource_access_assertions_review_source_run",
        "resource_access_assertions",
        "reviewed_assertion_id IS NULL OR source_test_run_id IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_resource_access_assertions_review_source_run",
        "resource_access_assertions",
        type_="check",
    )
    op.drop_constraint(
        "ck_resource_access_assertions_review_state",
        "resource_access_assertions",
        type_="check",
    )
    op.drop_constraint(
        "ck_resource_access_assertions_review_provenance",
        "resource_access_assertions",
        type_="check",
    )
    op.drop_constraint(
        "ck_resource_access_assertions_review_not_self",
        "resource_access_assertions",
        type_="check",
    )
    op.drop_index(
        "ux_resource_access_assertions_reviewed_assertion_id",
        table_name="resource_access_assertions",
    )
    op.drop_constraint(
        "fk_resource_access_assertions_reviewed_assertion_id",
        "resource_access_assertions",
        type_="foreignkey",
    )
    op.drop_column("resource_access_assertions", "reviewed_assertion_id")
