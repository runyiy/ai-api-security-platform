"""add endpoint resource bindings

Revision ID: e2a4c6e8b0d3
Revises: d0f2a4c6e8b1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2a4c6e8b0d3"
down_revision: Union[str, Sequence[str], None] = "d0f2a4c6e8b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "endpoint_resource_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("endpoint_id", sa.Integer(), nullable=False),
        sa.Column("location", sa.String(length=16), nullable=False),
        sa.Column("selector", sa.String(length=500), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("review_state", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint(
            "location IN ('path', 'query', 'body')",
            name="ck_endpoint_resource_bindings_location",
        ),
        sa.CheckConstraint(
            "length(selector) BETWEEN 1 AND 500",
            name="ck_endpoint_resource_bindings_selector_length",
        ),
        sa.CheckConstraint(
            "provenance IN ('operator_supplied', 'openapi_inferred', "
            "'heuristic_inferred')",
            name="ck_endpoint_resource_bindings_provenance",
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 100",
            name="ck_endpoint_resource_bindings_confidence",
        ),
        sa.CheckConstraint(
            "review_state IN ('candidate', 'confirmed', 'rejected')",
            name="ck_endpoint_resource_bindings_review_state",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"], ["endpoints.id"],
            name="fk_endpoint_resource_bindings_endpoint_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "endpoint_id", "location", "selector", "provenance",
            name="uq_endpoint_resource_binding_exact",
        ),
    )
    op.create_index(
        "ix_endpoint_resource_bindings_endpoint_id",
        "endpoint_resource_bindings",
        ["endpoint_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_endpoint_resource_bindings_endpoint_id",
        table_name="endpoint_resource_bindings",
    )
    op.drop_table("endpoint_resource_bindings")
