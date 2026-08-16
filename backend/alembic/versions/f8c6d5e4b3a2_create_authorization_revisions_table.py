"""create authorization revisions table

Revision ID: f8c6d5e4b3a2
Revises: e7a5b4c3d2f1
Create Date: 2026-08-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8c6d5e4b3a2"
down_revision: Union[str, Sequence[str], None] = "e7a5b4c3d2f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create immutable authorization revision snapshots."""
    op.create_table(
        "authorization_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("authorization_profile_id", sa.Integer(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column(
            "lifecycle_state",
            sa.String(length=20),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("program_name", sa.String(length=200), nullable=False),
        sa.Column("program_url", sa.String(length=500), nullable=True),
        sa.Column("authorization_type", sa.String(length=50), nullable=False),
        sa.Column(
            "authorization_reference",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "automation_allowed",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "max_requests_per_second",
            sa.Float(),
            server_default="1.0",
            nullable=False,
        ),
        sa.Column("allow_get", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("allow_post", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("allow_patch", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("allow_put", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("allow_delete", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "require_human_execution_approval",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('draft', 'active', 'superseded', 'revoked')",
            name="ck_authorization_revisions_lifecycle_state",
        ),
        sa.CheckConstraint(
            "max_requests_per_second > 0",
            name="ck_authorization_revisions_max_requests_per_second_positive",
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_authorization_revisions_revision_number_positive",
        ),
        sa.CheckConstraint(
            (
                "valid_from IS NULL OR valid_until IS NULL OR "
                "valid_until > valid_from"
            ),
            name="ck_authorization_revisions_validity_window",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_profile_id"],
            ["authorization_profiles.id"],
            name="fk_authorization_revisions_profile_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "authorization_profile_id",
            "revision_number",
            name="uq_authorization_revisions_profile_revision_number",
        ),
    )
    op.create_index(
        "ix_authorization_revisions_authorization_profile_id",
        "authorization_revisions",
        ["authorization_profile_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only the authorization revision schema."""
    op.drop_index(
        "ix_authorization_revisions_authorization_profile_id",
        table_name="authorization_revisions",
    )
    op.drop_table("authorization_revisions")
