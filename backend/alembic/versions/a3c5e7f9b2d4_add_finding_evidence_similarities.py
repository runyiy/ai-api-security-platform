"""Append bounded response similarity metadata without backfilling evidence."""
from alembic import op
import sqlalchemy as sa


revision = "a3c5e7f9b2d4"
down_revision = "f2b4d6e8a1c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finding_evidence_similarities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("finding_evidence_fingerprint_id", sa.Integer(), nullable=False),
        sa.Column("comparator_id", sa.String(48), nullable=False),
        sa.Column("comparator_version", sa.String(16), nullable=False),
        sa.Column("exact_digest_match", sa.Boolean(), nullable=False),
        sa.Column("length_similarity_bps", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["finding_evidence_fingerprint_id"], ["finding_evidence_fingerprints.id"],
            name="fk_finding_evidence_similarities_fingerprint_id", ondelete="RESTRICT",
        ),
        # The unique index also serves exact lookup/audit queries.
        sa.UniqueConstraint("finding_evidence_fingerprint_id", name="uq_finding_evidence_similarities_fingerprint_id"),
        sa.CheckConstraint("comparator_id = 'sha256_exact_and_length_ratio'", name="ck_finding_evidence_similarities_comparator"),
        sa.CheckConstraint("comparator_version = '1'", name="ck_finding_evidence_similarities_version"),
        sa.CheckConstraint("length_similarity_bps >= 0 AND length_similarity_bps <= 10000", name="ck_finding_evidence_similarities_length_ratio"),
    )


def downgrade() -> None:
    op.drop_table("finding_evidence_similarities")
