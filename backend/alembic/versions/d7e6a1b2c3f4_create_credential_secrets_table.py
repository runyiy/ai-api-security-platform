"""create credential secrets table

Revision ID: d7e6a1b2c3f4
Revises: c4b8219e6d72
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e6a1b2c3f4"
down_revision: Union[str, Sequence[str], None] = "c4b8219e6d72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the encrypted credential secret storage primitive."""
    op.create_table(
        "credential_secrets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("format_version", sa.Integer(), nullable=False),
        sa.Column("key_version", sa.String(length=50), nullable=False),
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
            "format_version > 0",
            name="ck_credential_secrets_format_version_positive",
        ),
        sa.CheckConstraint(
            "char_length(key_version) > 0",
            name="ck_credential_secrets_key_version_not_empty",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Remove only the encrypted credential secret table."""
    op.drop_table("credential_secrets")
