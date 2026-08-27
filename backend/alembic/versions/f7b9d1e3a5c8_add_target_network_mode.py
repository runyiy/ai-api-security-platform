"""add target network mode

Revision ID: f7b9d1e3a5c8
Revises: e6a8c0d2f4b7
Create Date: 2026-08-27 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7b9d1e3a5c8"
down_revision: Union[str, Sequence[str], None] = "e6a8c0d2f4b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "targets",
        sa.Column(
            "network_mode",
            sa.String(length=40),
            server_default="private_local",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_targets_network_mode",
        "targets",
        "network_mode IN ('private_local', 'external_public_authorized')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_targets_network_mode", "targets", type_="check")
    op.drop_column("targets", "network_mode")
