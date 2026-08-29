"""add immutable OpenAPI import provenance

Revision ID: c4e6a8b0d2f4
Revises: a2c4e6f8b0d2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e6a8b0d2f4"
down_revision: Union[str, Sequence[str], None] = "a2c4e6f8b0d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "openapi_import_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("document_sha256", sa.String(length=64), nullable=False),
        sa.Column("document_size_bytes", sa.Integer(), nullable=False),
        sa.Column("discovered_endpoint_count", sa.Integer(), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_openapi_import_records_sha256",
        ),
        sa.CheckConstraint(
            "document_size_bytes BETWEEN 0 AND 1000000",
            name="ck_openapi_import_records_document_size",
        ),
        sa.CheckConstraint(
            "discovered_endpoint_count >= 0",
            name="ck_openapi_import_records_endpoint_count",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"], ["targets.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("openapi_import_records")
