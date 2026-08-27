"""add safety decision records

Revision ID: e6a8c0d2f4b7
Revises: d5f7a9c1e3b5
Create Date: 2026-08-27 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6a8c0d2f4b7"
down_revision: Union[str, Sequence[str], None] = "d5f7a9c1e3b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "safety_decision_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("authorization_revision_id", sa.Integer(), nullable=True),
        sa.Column("execution_plan_id", sa.Integer(), nullable=True),
        sa.Column("plan_action_id", sa.Integer(), nullable=True),
        sa.Column("test_case_id", sa.Integer(), nullable=True),
        sa.Column("test_run_id", sa.Integer(), nullable=True),
        sa.Column("code", sa.String(length=100), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("matched_scope_id", sa.Integer(), nullable=True),
        sa.Column("policy_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage IN ('plan', 'policy', 'execution')",
            name="ck_safety_decision_records_stage",
        ),
        sa.CheckConstraint(
            "operation IN ('testcase_plan', 'policy_check', "
            "'test_execution', 'openapi_import')",
            name="ck_safety_decision_records_operation",
        ),
        sa.CheckConstraint(
            "outcome IN ('created', 'allowed', 'blocked', 'succeeded', 'failed')",
            name="ck_safety_decision_records_outcome",
        ),
        sa.CheckConstraint(
            "(stage = 'plan' AND outcome = 'created') OR "
            "(stage = 'policy' AND outcome IN ('allowed', 'blocked')) OR "
            "(stage = 'execution' AND outcome IN ('blocked', 'succeeded', 'failed'))",
            name="ck_safety_decision_records_stage_outcome",
        ),
        sa.CheckConstraint(
            "stage <> 'plan' OR execution_plan_id IS NOT NULL",
            name="ck_safety_decision_records_plan_identified",
        ),
        sa.ForeignKeyConstraint(["target_id"], ["targets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["authorization_revision_id"],
            ["authorization_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_plan_id"], ["execution_plans.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["plan_action_id"], ["plan_actions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["test_case_id"], ["test_cases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["test_run_id"], ["test_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "target_id",
        "authorization_revision_id",
        "execution_plan_id",
        "plan_action_id",
        "test_case_id",
        "test_run_id",
    ):
        op.create_index(
            f"ix_safety_decision_records_{column}",
            "safety_decision_records",
            [column],
        )


def downgrade() -> None:
    op.drop_table("safety_decision_records")
