"""create credential bindings table

Revision ID: d6f4a3b2c1e0
Revises: c4b8219e6d72
Create Date: 2026-08-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d6f4a3b2c1e0"
down_revision: Union[str, Sequence[str], None] = "c4b8219e6d72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the non-sensitive credential binding table."""
    op.create_table(
        "credential_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("test_identity_id", sa.Integer(), nullable=False),
        sa.Column("auth_type", sa.String(length=30), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["test_identity_id"],
            ["test_identities.id"],
            name=(
                "fk_credential_bindings_test_identity_id_"
                "test_identities"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_credential_bindings_test_identity_id",
        "credential_bindings",
        ["test_identity_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only the credential binding schema."""
    op.drop_index(
        "ix_credential_bindings_test_identity_id",
        table_name="credential_bindings",
    )
    op.drop_table("credential_bindings")
