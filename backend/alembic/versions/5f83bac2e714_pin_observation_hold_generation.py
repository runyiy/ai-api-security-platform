"""Durable observation hold provenance for W1 dependencies; no history rewrite."""
from alembic import op
import sqlalchemy as sa

revision = '5f83bac2e714'
down_revision = '4e72a9c1d603'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('research_observation_records', sa.Column(
        'hold_generation', sa.Integer(), nullable=False, server_default='0'))
    op.create_check_constraint('ck_observation_hold_generation', 'research_observation_records',
                               'hold_generation BETWEEN 0 AND 2147483647')


def downgrade():
    db = op.get_bind()
    db.execute(sa.text('LOCK TABLE research_observation_records, research_intent_manifests, '
                       'research_intent_versions IN ACCESS EXCLUSIVE MODE'))
    if db.execute(sa.text('SELECT EXISTS (SELECT 1 FROM research_observation_records '
                          'WHERE hold_generation <> 0)')).scalar_one():
        raise RuntimeError('observation_lifecycle_populated_downgrade_blocked')
    if any(db.execute(sa.text(f'SELECT EXISTS (SELECT 1 FROM {table})')).scalar_one()
           for table in ('research_intent_manifests', 'research_intent_versions')):
        raise RuntimeError('research_intent_populated_downgrade_blocked')
    op.drop_constraint('ck_observation_hold_generation', 'research_observation_records', type_='check')
    op.drop_column('research_observation_records', 'hold_generation')
