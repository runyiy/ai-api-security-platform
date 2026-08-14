"""link targets to authorization profiles

Revision ID: c4b8219e6d72
Revises: a1c7e4f29b63
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4b8219e6d72"
down_revision: Union[str, Sequence[str], None] = "a1c7e4f29b63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable Target authorization profile reference."""
    op.add_column(
        "targets",
        sa.Column(
            "authorization_profile_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        (
            "fk_targets_authorization_profile_id_"
            "authorization_profiles"
        ),
        "targets",
        "authorization_profiles",
        ["authorization_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_targets_authorization_profile_id",
        "targets",
        ["authorization_profile_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the Target authorization profile reference."""
    op.drop_index(
        "ix_targets_authorization_profile_id",
        table_name="targets",
    )
    op.drop_constraint(
        (
            "fk_targets_authorization_profile_id_"
            "authorization_profiles"
        ),
        "targets",
        type_="foreignkey",
    )
    op.drop_column(
        "targets",
        "authorization_profile_id",
    )
