"""Add bounded structured finding evidence without backfilling existing findings."""
from alembic import op
import sqlalchemy as sa


revision = "d9f1b3c5e7a8"
down_revision = "c7e9a1b3d5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finding_evidence_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("finding_id", sa.Integer(), nullable=False),
        sa.Column("probe_test_run_id", sa.Integer(), nullable=False),
        sa.Column("baseline_test_run_id", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(40), nullable=False),
        sa.Column("rule_id", sa.String(48), nullable=False),
        sa.Column("rule_version", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("baseline_status_code", sa.Integer(), nullable=False),
        sa.Column("probe_status_code", sa.Integer(), nullable=False),
        sa.Column("baseline_resource_identifier_present", sa.Boolean(), nullable=False),
        sa.Column("probe_resource_identifier_present", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"],
                                name="fk_finding_evidence_records_finding_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["probe_test_run_id"], ["test_runs.id"],
                                name="fk_finding_evidence_records_probe_test_run_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["baseline_test_run_id"], ["test_runs.id"],
                                name="fk_finding_evidence_records_baseline_test_run_id", ondelete="RESTRICT"),
        sa.UniqueConstraint("finding_id", name="uq_finding_evidence_records_finding_id"),
        sa.CheckConstraint("baseline_test_run_id <> probe_test_run_id",
                           name="ck_finding_evidence_records_distinct_runs"),
    )
    op.create_index("ix_finding_evidence_records_probe_test_run_id", "finding_evidence_records", ["probe_test_run_id"])
    op.create_index("ix_finding_evidence_records_baseline_test_run_id", "finding_evidence_records", ["baseline_test_run_id"])


def downgrade() -> None:
    op.drop_index("ix_finding_evidence_records_baseline_test_run_id", table_name="finding_evidence_records")
    op.drop_index("ix_finding_evidence_records_probe_test_run_id", table_name="finding_evidence_records")
    op.drop_table("finding_evidence_records")
