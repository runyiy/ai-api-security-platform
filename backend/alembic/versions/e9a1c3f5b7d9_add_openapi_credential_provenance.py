"""add OpenAPI credential binding provenance

Revision ID: e9a1c3f5b7d9
Revises: d8f0b2c4e6a9
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e9a1c3f5b7d9"
down_revision: Union[str, Sequence[str], None] = "d8f0b2c4e6a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "openapi_import_records",
        sa.Column("credential_binding_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_openapi_import_records_credential_binding_id",
        "openapi_import_records",
        "credential_bindings",
        ["credential_binding_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_openapi_import_records_credential_binding_id",
        "openapi_import_records",
        type_="foreignkey",
    )
    op.drop_column("openapi_import_records", "credential_binding_id")
