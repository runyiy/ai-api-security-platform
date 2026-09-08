"""Bind the universal v1 release policy to current and historical M13 evidence."""
from alembic import op
import sqlalchemy as sa


revision = "b5d7f9a1c3e6"
down_revision = "a3c5e7f9b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bindings = op.create_table(
        "finding_evidence_retention_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("finding_evidence_record_id", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.String(48), nullable=False),
        sa.Column("policy_version", sa.String(16), nullable=False),
        sa.Column("retention_mode", sa.String(32), nullable=False),
        sa.Column("automatic_deletion_enabled", sa.Boolean(), nullable=False),
        sa.Column("raw_response_body_retained", sa.Boolean(), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["finding_evidence_record_id"], ["finding_evidence_records.id"],
            name="fk_finding_evidence_retention_evidence_record_id", ondelete="RESTRICT",
        ),
        # The unique index also serves exact lookup/audit queries.
        sa.UniqueConstraint("finding_evidence_record_id", name="uq_finding_evidence_retention_evidence_record_id"),
        sa.CheckConstraint("policy_id = 'm13_minimized_finding_evidence'", name="ck_finding_evidence_retention_policy_id"),
        sa.CheckConstraint("policy_version = '1'", name="ck_finding_evidence_retention_policy_version"),
        sa.CheckConstraint("retention_mode = 'explicit_management_only'", name="ck_finding_evidence_retention_mode"),
        sa.CheckConstraint("automatic_deletion_enabled = false", name="ck_finding_evidence_retention_no_automatic_deletion"),
        sa.CheckConstraint("raw_response_body_retained = false", name="ck_finding_evidence_retention_no_raw_body"),
    )
    # Universal release policy needs only existing evidence IDs, no historical inference.
    evidence = sa.table("finding_evidence_records", sa.column("id", sa.Integer()))
    op.execute(bindings.insert().from_select(
        ["finding_evidence_record_id", "policy_id", "policy_version", "retention_mode",
         "automatic_deletion_enabled", "raw_response_body_retained"],
        sa.select(evidence.c.id, sa.literal("m13_minimized_finding_evidence"), sa.literal("1"),
                  sa.literal("explicit_management_only"), sa.false(), sa.false()).order_by(evidence.c.id),
    ))


def downgrade() -> None:
    op.drop_table("finding_evidence_retention_bindings")
