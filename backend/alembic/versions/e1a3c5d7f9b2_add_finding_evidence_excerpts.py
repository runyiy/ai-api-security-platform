"""Append bounded redacted excerpts without backfilling structured evidence."""
from alembic import op
import sqlalchemy as sa


revision = "e1a3c5d7f9b2"
down_revision = "d9f1b3c5e7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finding_evidence_excerpts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("finding_evidence_record_id", sa.Integer(), nullable=False),
        sa.Column("extractor_id", sa.String(48), nullable=False),
        sa.Column("extractor_version", sa.String(16), nullable=False),
        sa.Column("baseline_excerpt", sa.String(192), nullable=False),
        sa.Column("probe_excerpt", sa.String(192), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["finding_evidence_record_id"], ["finding_evidence_records.id"],
            name="fk_finding_evidence_excerpts_evidence_record_id", ondelete="RESTRICT",
        ),
        # PostgreSQL's unique index also serves exact lookup and audit queries.
        sa.UniqueConstraint("finding_evidence_record_id", name="uq_finding_evidence_excerpts_evidence_record_id"),
    )


def downgrade() -> None:
    op.drop_table("finding_evidence_excerpts")
