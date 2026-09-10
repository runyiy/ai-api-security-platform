"""Add synthetic W3 proposal history without backfilling existing domains."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e9a1c3d5f7b8"
down_revision = "d8f0b2c4e6a8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("research_subject_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("context_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("proposal_number", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("proposal", postgresql.JSONB(), nullable=False),
        sa.Column("credential_version_id", sa.Integer(), nullable=True),
        sa.Column("correction_reference", postgresql.JSONB(), nullable=True),
        sa.Column("provenance", sa.String(40), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("context_id", "proposal_number", "version_number", name="uq_research_subject_version"),
        sa.ForeignKeyConstraint(["context_id", "target_id"],
            ["research_target_associations.context_id", "research_target_associations.target_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["context_id", "context_version"],
            ["research_context_versions.context_id", "research_context_versions.version_number"], ondelete="RESTRICT"),
        sa.CheckConstraint("proposal_number BETWEEN 1 AND 1024 AND version_number BETWEEN 1 AND 1024", name="ck_research_subject_numbers"),
        sa.CheckConstraint("jsonb_typeof(proposal) = 'object' AND octet_length(proposal::text) <= 16384", name="ck_research_subject_size"),
        sa.CheckConstraint("provenance = 'operator_proposed_unverified'", name="ck_research_subject_provenance"))
    op.create_index("ix_research_subject_versions_context_id", "research_subject_versions", ["context_id"])


def downgrade():
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE research_subject_versions IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM research_subject_versions)")).scalar_one():
        raise RuntimeError("research_subject_populated_downgrade_blocked")
    op.drop_table("research_subject_versions")
