"""create authorization profiles table

Revision ID: a1c7e4f29b63
Revises: ca4a0c7159a9
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1c7e4f29b63"
down_revision: Union[str, Sequence[str], None] = "ca4a0c7159a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the standalone authorization profile table."""
    op.create_table(
        "authorization_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
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
            server_default=sa.text("1.0"),
            nullable=False,
        ),
        sa.Column(
            "allow_get",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "allow_post",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "allow_patch",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "allow_put",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "allow_delete",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "max_requests_per_second > 0",
            name=(
                "ck_authorization_profiles_"
                "max_requests_per_second_positive"
            ),
        ),
        sa.CheckConstraint(
            (
                "valid_from IS NULL OR "
                "valid_until IS NULL OR "
                "valid_until > valid_from"
            ),
            name="ck_authorization_profiles_validity_window",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Remove only the standalone authorization profile table."""
    op.drop_table("authorization_profiles")
