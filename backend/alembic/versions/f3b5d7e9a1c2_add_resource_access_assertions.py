"""add resource access assertions

Revision ID: f3b5d7e9a1c2
Revises: e2a4c6e8b0d3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3b5d7e9a1c2"
down_revision: Union[str, Sequence[str], None] = "e2a4c6e8b0d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resource_access_assertions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("test_identity_id", sa.Integer(), nullable=False),
        sa.Column("relationship", sa.String(length=16), nullable=False),
        sa.Column("expected_access", sa.String(length=16), nullable=False),
        sa.Column("provenance", sa.String(length=24), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("verification_state", sa.String(length=16), nullable=False),
        sa.Column(
            "asserted_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "relationship IN ('owner', 'shared', 'non_owner', 'unspecified')",
            name="ck_resource_access_assertions_relationship",
        ),
        sa.CheckConstraint(
            "expected_access IN ('allowed', 'denied', 'unspecified')",
            name="ck_resource_access_assertions_expected_access",
        ),
        sa.CheckConstraint(
            "relationship <> 'unspecified' OR expected_access <> 'unspecified'",
            name="ck_resource_access_assertions_meaningful",
        ),
        sa.CheckConstraint(
            "provenance IN ('human_verified', 'target_fixture', "
            "'observed_baseline', 'inferred_candidate')",
            name="ck_resource_access_assertions_provenance",
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 100",
            name="ck_resource_access_assertions_confidence",
        ),
        sa.CheckConstraint(
            "verification_state IN ('candidate', 'verified', 'rejected')",
            name="ck_resource_access_assertions_verification_state",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR "
            "(valid_from IS NOT NULL AND valid_until > valid_from)",
            name="ck_resource_access_assertions_validity_window",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resources.id"],
            name="fk_resource_access_assertions_resource_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["test_identity_id"], ["test_identities.id"],
            name="fk_resource_access_assertions_test_identity_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_resource_access_assertions_resource_id",
        "resource_access_assertions", ["resource_id"],
    )
    op.create_index(
        "ix_resource_access_assertions_test_identity_id",
        "resource_access_assertions", ["test_identity_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_resource_access_assertions_test_identity_id",
        table_name="resource_access_assertions",
    )
    op.drop_index(
        "ix_resource_access_assertions_resource_id",
        table_name="resource_access_assertions",
    )
    op.drop_table("resource_access_assertions")
