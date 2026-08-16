"""create credential secret versions table

Revision ID: e7a5b4c3d2f1
Revises: d6f4a3b2c1e0
Create Date: 2026-08-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e7a5b4c3d2f1"
down_revision: Union[str, Sequence[str], None] = "d6f4a3b2c1e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create binding-owned encrypted secret version storage."""
    op.create_table(
        "credential_secret_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("credential_binding_id", sa.Integer(), nullable=False),
        sa.Column("encrypted_envelope", sa.Text(), nullable=False),
        sa.Column("envelope_version", sa.Integer(), nullable=False),
        sa.Column("key_version", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["credential_binding_id"],
            ["credential_bindings.id"],
            name="fk_secret_versions_credential_binding_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_credential_secret_versions_credential_binding_id",
        "credential_secret_versions",
        ["credential_binding_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only binding-owned encrypted secret version storage."""
    op.drop_index(
        "ix_credential_secret_versions_credential_binding_id",
        table_name="credential_secret_versions",
    )
    op.drop_table("credential_secret_versions")
