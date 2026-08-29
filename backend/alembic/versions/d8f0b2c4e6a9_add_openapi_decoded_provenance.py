"""add OpenAPI decoded document provenance

Revision ID: d8f0b2c4e6a9
Revises: c4e6a8b0d2f4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8f0b2c4e6a9"
down_revision: Union[str, Sequence[str], None] = "c4e6a8b0d2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "openapi_import_records",
        sa.Column("content_encoding", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "openapi_import_records",
        sa.Column("decoded_document_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "openapi_import_records",
        sa.Column("decoded_document_size_bytes", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE openapi_import_records
        SET content_encoding = 'identity',
            decoded_document_sha256 = document_sha256,
            decoded_document_size_bytes = document_size_bytes
        """
    )
    op.alter_column("openapi_import_records", "content_encoding", nullable=False)
    op.alter_column(
        "openapi_import_records", "decoded_document_sha256", nullable=False
    )
    op.alter_column(
        "openapi_import_records", "decoded_document_size_bytes", nullable=False
    )
    op.create_check_constraint(
        "ck_openapi_import_records_content_encoding",
        "openapi_import_records",
        "content_encoding IN ('identity', 'gzip')",
    )
    op.create_check_constraint(
        "ck_openapi_import_records_decoded_sha256",
        "openapi_import_records",
        "decoded_document_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_openapi_import_records_decoded_size",
        "openapi_import_records",
        "decoded_document_size_bytes BETWEEN 0 AND 1000000",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_openapi_import_records_decoded_size",
        "openapi_import_records",
        type_="check",
    )
    op.drop_constraint(
        "ck_openapi_import_records_decoded_sha256",
        "openapi_import_records",
        type_="check",
    )
    op.drop_constraint(
        "ck_openapi_import_records_content_encoding",
        "openapi_import_records",
        type_="check",
    )
    op.drop_column("openapi_import_records", "decoded_document_size_bytes")
    op.drop_column("openapi_import_records", "decoded_document_sha256")
    op.drop_column("openapi_import_records", "content_encoding")
