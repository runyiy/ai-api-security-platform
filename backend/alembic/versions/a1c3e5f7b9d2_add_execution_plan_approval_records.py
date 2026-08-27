"""add execution plan approval records

Revision ID: a1c3e5f7b9d2
Revises: f7b9d1e3a5c8
Create Date: 2026-08-27 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1c3e5f7b9d2"
down_revision: Union[str, Sequence[str], None] = "f7b9d1e3a5c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_plan_approval_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_plan_id", sa.Integer(), nullable=False),
        sa.Column("digest_version", sa.String(length=10), nullable=False),
        sa.Column("plan_digest", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'revoked')",
            name="ck_execution_plan_approval_records_decision",
        ),
        sa.CheckConstraint(
            "digest_version = 'v1'",
            name="ck_execution_plan_approval_records_digest_version",
        ),
        sa.CheckConstraint(
            "plan_digest ~ '^[0-9a-f]{64}$'",
            name="ck_execution_plan_approval_records_digest_shape",
        ),
        sa.ForeignKeyConstraint(
            ["execution_plan_id"], ["execution_plans.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_plan_approval_exact_decision",
        "execution_plan_approval_records",
        ["execution_plan_id", "digest_version", "plan_digest", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_plan_approval_exact_decision",
        table_name="execution_plan_approval_records",
    )
    op.drop_table("execution_plan_approval_records")
