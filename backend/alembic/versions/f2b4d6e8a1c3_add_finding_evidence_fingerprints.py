"""Append exact response fingerprints without backfilling existing evidence."""
from alembic import op
import sqlalchemy as sa


revision = "f2b4d6e8a1c3"
down_revision = "e1a3c5d7f9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finding_evidence_fingerprints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("finding_evidence_record_id", sa.Integer(), nullable=False),
        sa.Column("algorithm", sa.String(16), nullable=False),
        sa.Column("fingerprint_version", sa.String(16), nullable=False),
        sa.Column("baseline_digest", sa.String(64), nullable=False),
        sa.Column("probe_digest", sa.String(64), nullable=False),
        sa.Column("baseline_body_bytes", sa.Integer(), nullable=False),
        sa.Column("probe_body_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["finding_evidence_record_id"], ["finding_evidence_records.id"],
            name="fk_finding_evidence_fingerprints_evidence_record_id", ondelete="RESTRICT",
        ),
        # The unique index also serves exact lookup/audit queries.
        sa.UniqueConstraint("finding_evidence_record_id", name="uq_finding_evidence_fingerprints_evidence_record_id"),
        sa.CheckConstraint("algorithm = 'sha256'", name="ck_finding_evidence_fingerprints_algorithm"),
        sa.CheckConstraint("fingerprint_version = '1'", name="ck_finding_evidence_fingerprints_version"),
        sa.CheckConstraint("length(baseline_digest) = 64 AND baseline_digest ~ '^[0-9a-f]{64}$'", name="ck_finding_evidence_fingerprints_baseline_digest"),
        sa.CheckConstraint("length(probe_digest) = 64 AND probe_digest ~ '^[0-9a-f]{64}$'", name="ck_finding_evidence_fingerprints_probe_digest"),
        sa.CheckConstraint("baseline_body_bytes >= 0", name="ck_finding_evidence_fingerprints_baseline_body_bytes"),
        sa.CheckConstraint("probe_body_bytes >= 0", name="ck_finding_evidence_fingerprints_probe_body_bytes"),
    )


def downgrade() -> None:
    op.drop_table("finding_evidence_fingerprints")
