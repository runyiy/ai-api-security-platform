"""add exact-evaluation DNS validation provenance

Revision ID: b4d6f8a0c2e5
Revises: a2c4e6f8b0d3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4d6f8a0c2e5"
down_revision: Union[str, Sequence[str], None] = "a2c4e6f8b0d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "asset_candidate_dns_validations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asset_candidate_evaluation_id", sa.Integer(), nullable=False),
        sa.Column("authorization_revision_id", sa.Integer(), nullable=False),
        sa.Column("decision_code", sa.String(length=64), nullable=False),
        sa.Column("normalized_hostname", sa.String(length=253), nullable=False),
        sa.Column("terminal_hostname", sa.String(length=253), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint(
            "decision_code IN ("
            "'asset_candidate_dns_public_only', "
            "'asset_candidate_dns_private_local_only', "
            "'asset_candidate_dns_prohibited', "
            "'asset_candidate_dns_resolution_failed', "
            "'asset_candidate_dns_invalid', "
            "'asset_candidate_dns_cname_cycle', "
            "'asset_candidate_dns_cname_limit_exceeded', "
            "'asset_candidate_dns_address_limit_exceeded')",
            name="ck_asset_candidate_dns_validations_decision_code",
        ),
        sa.ForeignKeyConstraint(
            ["asset_candidate_evaluation_id"],
            ["asset_candidate_evaluations.id"],
            name="fk_dns_validations_asset_candidate_evaluation_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_revision_id"], ["authorization_revisions.id"],
            name="fk_dns_validations_authorization_revision_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dns_validations_evaluation_id",
        "asset_candidate_dns_validations", ["asset_candidate_evaluation_id"],
    )
    op.create_index(
        "ix_dns_validations_revision_id",
        "asset_candidate_dns_validations", ["authorization_revision_id"],
    )

    op.create_table(
        "asset_candidate_dns_cname_hops",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dns_validation_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("hostname", sa.String(length=253), nullable=False),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 8",
            name="ck_asset_candidate_dns_cname_hops_ordinal",
        ),
        sa.ForeignKeyConstraint(
            ["dns_validation_id"], ["asset_candidate_dns_validations.id"],
            name="fk_dns_cname_hops_dns_validation_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dns_validation_id", "ordinal",
            name="uq_asset_candidate_dns_cname_hops_validation_ordinal",
        ),
    )
    op.create_index(
        "ix_dns_cname_hops_validation_id",
        "asset_candidate_dns_cname_hops", ["dns_validation_id"],
    )

    op.create_table(
        "asset_candidate_dns_addresses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dns_validation_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(length=45), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 16",
            name="ck_asset_candidate_dns_addresses_ordinal",
        ),
        sa.CheckConstraint(
            "category IN ('loopback', 'private', 'link_local', 'unspecified', "
            "'multicast', 'special', 'public')",
            name="ck_asset_candidate_dns_addresses_category",
        ),
        sa.ForeignKeyConstraint(
            ["dns_validation_id"], ["asset_candidate_dns_validations.id"],
            name="fk_dns_addresses_dns_validation_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dns_validation_id", "ordinal",
            name="uq_asset_candidate_dns_addresses_validation_ordinal",
        ),
    )
    op.create_index(
        "ix_dns_addresses_validation_id",
        "asset_candidate_dns_addresses", ["dns_validation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dns_addresses_validation_id",
        table_name="asset_candidate_dns_addresses",
    )
    op.drop_table("asset_candidate_dns_addresses")
    op.drop_index(
        "ix_dns_cname_hops_validation_id",
        table_name="asset_candidate_dns_cname_hops",
    )
    op.drop_table("asset_candidate_dns_cname_hops")
    op.drop_index(
        "ix_dns_validations_revision_id",
        table_name="asset_candidate_dns_validations",
    )
    op.drop_index(
        "ix_dns_validations_evaluation_id",
        table_name="asset_candidate_dns_validations",
    )
    op.drop_table("asset_candidate_dns_validations")
