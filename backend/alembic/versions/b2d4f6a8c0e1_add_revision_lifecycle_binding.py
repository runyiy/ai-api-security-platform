"""add revision lifecycle binding

Revision ID: b2d4f6a8c0e1
Revises: f8c6d5e4b3a2
Create Date: 2026-08-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2d4f6a8c0e1"
down_revision: Union[str, Sequence[str], None] = "f8c6d5e4b3a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "targets",
        sa.Column("authorization_revision_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_targets_authorization_revision_id_authorization_revisions",
        "targets",
        "authorization_revisions",
        ["authorization_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_targets_authorization_revision_id",
        "targets",
        ["authorization_revision_id"],
        unique=False,
    )
    op.create_index(
        "uq_authorization_revisions_one_active_per_profile",
        "authorization_revisions",
        ["authorization_profile_id"],
        unique=True,
        postgresql_where=sa.text("lifecycle_state = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_authorization_revisions_one_active_per_profile",
        table_name="authorization_revisions",
    )
    op.drop_index("ix_targets_authorization_revision_id", table_name="targets")
    op.drop_constraint(
        "fk_targets_authorization_revision_id_authorization_revisions",
        "targets",
        type_="foreignkey",
    )
    op.drop_column("targets", "authorization_revision_id")
