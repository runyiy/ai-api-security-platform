"""Bind findings to an exact baseline TestRun without backfilling legacy rows."""
from alembic import op
import sqlalchemy as sa


revision = "c7e9a1b3d5f6"
down_revision = "b6d8f0a2c4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("findings", sa.Column("baseline_test_run_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_findings_baseline_test_run_id", "findings", "test_runs",
        ["baseline_test_run_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_findings_baseline_test_run_id", "findings", ["baseline_test_run_id"])


def downgrade() -> None:
    op.drop_index("ix_findings_baseline_test_run_id", table_name="findings")
    op.drop_constraint("fk_findings_baseline_test_run_id", "findings", type_="foreignkey")
    op.drop_column("findings", "baseline_test_run_id")
