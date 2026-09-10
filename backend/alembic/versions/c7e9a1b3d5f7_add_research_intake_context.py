"""Add isolated synthetic research intake metadata; never backfill legacy rows.

Revision ID: c7e9a1b3d5f7
Revises: b5d7f9a1c3e6
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c7e9a1b3d5f7"
down_revision = "b5d7f9a1c3e6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("research_contexts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closure_reference", postgresql.JSONB()),
        sa.UniqueConstraint("project_number", name="uq_research_context_project"),
        sa.CheckConstraint("project_number BETWEEN 1 AND 1000000", name="ck_research_context_project"))
    op.create_table("research_target_associations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("context_id", sa.Integer(), sa.ForeignKey("research_contexts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("review_reference", postgresql.JSONB(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("context_id", "target_id", name="uq_research_association_context_target"))
    op.create_index("ix_research_target_associations_context_id", "research_target_associations", ["context_id"])
    op.create_index("ix_research_target_associations_target_id", "research_target_associations", ["target_id"])
    op.create_index("uq_research_target_active_context", "research_target_associations", ["target_id"],
                    unique=True, postgresql_where=sa.text("released_at IS NULL"))
    op.create_table("research_context_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("context_id", sa.Integer(), sa.ForeignKey("research_contexts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("intake", postgresql.JSONB(), nullable=False),
        sa.Column("permission_snapshots", postgresql.JSONB(), nullable=False),
        sa.Column("correction_reference", postgresql.JSONB()),
        sa.Column("provenance", sa.String(40), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("context_id", "version_number", name="uq_research_context_version"),
        sa.CheckConstraint("version_number BETWEEN 1 AND 10000", name="ck_research_context_version"),
        sa.CheckConstraint("jsonb_typeof(intake) = 'object' AND octet_length(intake::text) <= 32768",
                           name="ck_research_intake_size"))
    op.create_index("ix_research_context_versions_context_id", "research_context_versions", ["context_id"])


def downgrade():
    # Application rollback may leave these tables intact. Never silently drop populated intake history.
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE research_contexts, research_target_associations, "
                         "research_context_versions IN ACCESS EXCLUSIVE MODE"))
    for table in ("research_contexts", "research_target_associations", "research_context_versions"):
        if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar():
            raise RuntimeError("research_intake_populated_downgrade_blocked")
    op.drop_table("research_context_versions")
    op.drop_table("research_target_associations")
    op.drop_table("research_contexts")
