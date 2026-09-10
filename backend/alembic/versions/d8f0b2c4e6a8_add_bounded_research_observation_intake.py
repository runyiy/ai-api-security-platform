"""add bounded research observation intake

Revision ID: d8f0b2c4e6a8
Revises: c7e9a1b3d5f7
Create Date: 2026-09-09 19:20:02.625979

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd8f0b2c4e6a8'
down_revision: Union[str, Sequence[str], None] = 'c7e9a1b3d5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('research_observation_controls',
    sa.Column('context_id', sa.Integer(), nullable=False),
    sa.Column('recovery_token', sa.String(length=36), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('review', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.ForeignKeyConstraint(['context_id'], ['research_contexts.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('context_id')
    )
    op.create_table('research_observation_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('context_id', sa.Integer(), nullable=False),
    sa.Column('observation_id', sa.Integer(), nullable=True),
    sa.Column('hold_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('review', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.ForeignKeyConstraint(['context_id'], ['research_contexts.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('research_observation_preparations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('context_id', sa.Integer(), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('preparation_ref', sa.String(length=64), nullable=False),
    sa.Column('registry', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('origin', sa.String(length=256), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('octet_length(registry::text) <= 32768', name='ck_observation_registry_size'),
    sa.ForeignKeyConstraint(['context_id', 'target_id'], ['research_target_associations.context_id', 'research_target_associations.target_id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['context_id'], ['research_contexts.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('context_id', 'id', name='uq_observation_preparation_context_id'),
    sa.UniqueConstraint('context_id', 'preparation_ref', name='uq_observation_preparation_context_ref')
    )
    op.create_table('research_observation_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('context_id', sa.Integer(), nullable=False),
    sa.Column('preparation_id', sa.Integer(), nullable=False),
    sa.Column('batch_ref', sa.String(length=64), nullable=False),
    sa.Column('format_version', sa.String(length=32), nullable=False),
    sa.Column('lifecycle_version', sa.String(length=40), nullable=False),
    sa.Column('provenance', sa.String(length=40), nullable=False),
    sa.Column('digest', sa.String(length=64), nullable=False),
    sa.Column('entry_order', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('corrects_id', sa.Integer(), nullable=True),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('unavailable_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('hold_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('hold_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('hold_review', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('hold_reason', sa.String(length=32), nullable=True),
    sa.CheckConstraint("expires_at > accepted_at AND expires_at <= accepted_at + interval '2592000 seconds'", name='ck_observation_expiry'),
    sa.CheckConstraint("hold_until IS NULL OR (hold_started_at IS NOT NULL AND hold_until > hold_started_at AND hold_until <= hold_started_at + interval '2592000 seconds')", name='ck_observation_hold'),
    sa.CheckConstraint("format_version = 'ra-observation/1' AND lifecycle_version = 'ra-observation-lifecycle/1' AND provenance = 'operator_import_unverified'", name='ck_observation_provenance'),
    sa.CheckConstraint("state IN ('available','deleted','quarantined')", name='ck_observation_state'),
    sa.CheckConstraint('jsonb_array_length(entry_order) BETWEEN 1 AND 128', name='ck_observation_entries'),
    sa.ForeignKeyConstraint(['context_id', 'preparation_id'], ['research_observation_preparations.context_id', 'research_observation_preparations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['context_id'], ['research_contexts.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('context_id', 'batch_ref', name='uq_observation_batch_context'),
    sa.UniqueConstraint('context_id', 'id', name='uq_observation_record_context_id')
    )
    op.create_table('research_observation_payloads',
    sa.Column('observation_id', sa.Integer(), nullable=False),
    sa.Column('context_id', sa.Integer(), nullable=False),
    sa.Column('canonical_payload', sa.String(), nullable=False),
    sa.CheckConstraint('octet_length(canonical_payload) <= 262144', name='ck_observation_payload_bytes'),
    sa.ForeignKeyConstraint(['context_id', 'observation_id'], ['research_observation_records.context_id', 'research_observation_records.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('observation_id')
    )


def downgrade() -> None:
    """Empty-only rollback; lock before checking to exclude concurrent intake."""
    tables = ("research_observation_controls", "research_observation_events",
              "research_observation_preparations", "research_observation_records",
              "research_observation_payloads")
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE " + ", ".join(tables) + " IN ACCESS EXCLUSIVE MODE"))
    for table in tables:
        if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM " + table + ")")):
            raise RuntimeError("research_observation_populated_downgrade_blocked")
    op.drop_table('research_observation_payloads')
    op.drop_table('research_observation_records')
    op.drop_table('research_observation_preparations')
    op.drop_table('research_observation_events')
    op.drop_table('research_observation_controls')
