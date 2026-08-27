"""add execution plans

Revision ID: d5f7a9c1e3b5
Revises: c3e5a7b9d1f2
Create Date: 2026-08-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d5f7a9c1e3b5"
down_revision: Union[str, Sequence[str], None] = "c3e5a7b9d1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("authorization_revision_id", sa.Integer(), nullable=False),
        sa.Column("actor_identity_id", sa.Integer(), nullable=False),
        sa.Column("credential_binding_id", sa.Integer(), nullable=True),
        sa.Column("digest_version", sa.String(length=10), nullable=False),
        sa.Column("plan_digest", sa.String(length=64), nullable=False),
        sa.Column("action_count", sa.Integer(), nullable=False),
        sa.Column("policy_context", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action_count > 0 AND action_count <= 100",
            name="ck_execution_plans_action_count_bounded",
        ),
        sa.CheckConstraint(
            "digest_version = 'v1'", name="ck_execution_plans_digest_version"
        ),
        sa.CheckConstraint(
            "plan_digest ~ '^[0-9a-f]{64}$'",
            name="ck_execution_plans_digest_shape",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"], ["targets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["authorization_revision_id"],
            ["authorization_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_identity_id"], ["test_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["credential_binding_id"],
            ["credential_bindings.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "target_id",
        "authorization_revision_id",
        "actor_identity_id",
        "credential_binding_id",
    ):
        op.create_index(
            f"ix_execution_plans_{column}", "execution_plans", [column]
        )

    op.create_table(
        "plan_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_plan_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("test_case_id", sa.Integer(), nullable=True),
        sa.Column("resource_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("method = 'GET'", name="ck_plan_actions_method_get"),
        sa.CheckConstraint(
            "ordinal > 0", name="ck_plan_actions_ordinal_positive"
        ),
        sa.ForeignKeyConstraint(
            ["execution_plan_id"],
            ["execution_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["test_case_id"], ["test_cases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resources.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "execution_plan_id", "ordinal", name="uq_plan_actions_plan_ordinal"
        ),
    )
    for column in ("execution_plan_id", "test_case_id", "resource_id"):
        op.create_index(f"ix_plan_actions_{column}", "plan_actions", [column])


def downgrade() -> None:
    op.drop_table("plan_actions")
    op.drop_table("execution_plans")
