"""Bounded knowledge versions/events; no seed publication or legacy backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'f0b2d4e6a8c0'
down_revision = 'e9a1c3d5f7b8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('research_knowledge_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('context_id', sa.Integer(), sa.ForeignKey('research_contexts.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('context_version', sa.Integer(), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=False),
        sa.Column('scope', sa.String(24), nullable=False),
        sa.Column('knowledge_id', sa.String(64), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('content', JSONB(), nullable=False),
        sa.Column('digest', sa.String(64), nullable=False),
        sa.Column('review', JSONB(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['context_id','target_id'],['research_target_associations.context_id','research_target_associations.target_id'],ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['context_id','context_version'],['research_context_versions.context_id','research_context_versions.version_number'],ondelete='RESTRICT'),
        sa.UniqueConstraint('context_id','scope','knowledge_id','version', name='uq_knowledge_project_version'),
        sa.CheckConstraint("scope IN ('project','reusable_synthetic') AND version BETWEEN 1 AND 10000", name='ck_knowledge_identity'),
        sa.CheckConstraint("jsonb_typeof(content) = 'object' AND octet_length(content::text) <= 32768 AND digest ~ '^[0-9a-f]{64}$'", name='ck_knowledge_content'))
    op.create_index('ix_research_knowledge_versions_context_id','research_knowledge_versions',['context_id'])
    op.create_index('uq_knowledge_shared_version','research_knowledge_versions',['knowledge_id','version'], unique=True, postgresql_where=sa.text("scope = 'reusable_synthetic'"))
    op.create_table('research_knowledge_events',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('version_id',sa.Integer(),sa.ForeignKey('research_knowledge_versions.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('sequence',sa.Integer(),nullable=False),
        sa.Column('action',sa.String(16),nullable=False),
        sa.Column('body',JSONB(),nullable=False),
        sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('version_id','sequence',name='uq_knowledge_event_sequence'),
        sa.CheckConstraint("sequence BETWEEN 1 AND 16 AND action IN ('review','reuse','publish','withdraw','disable')",name='ck_knowledge_event'),
        sa.CheckConstraint("jsonb_typeof(body) = 'object' AND octet_length(body::text) <= 4096",name='ck_knowledge_event_body'))
    op.create_index('ix_research_knowledge_events_version_id','research_knowledge_events',['version_id'])
    op.create_table('research_knowledge_audit',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('context_id',sa.Integer(),sa.ForeignKey('research_contexts.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('code',sa.String(16),nullable=False),
        sa.Column('review',JSONB(),nullable=True),
        sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("code IN ('record','decision','query','rotate') AND octet_length(review::text) <= 256",name='ck_knowledge_audit'))
    op.create_index('ix_research_knowledge_audit_context_id','research_knowledge_audit',['context_id'])
    op.execute("""CREATE FUNCTION research_knowledge_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'knowledge_history_immutable'; END $$""")
    for table in ('research_knowledge_versions','research_knowledge_events'):
        op.execute(f'CREATE TRIGGER knowledge_immutable BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION research_knowledge_immutable()')


def downgrade():
    connection=op.get_bind()
    tables=('research_knowledge_audit','research_knowledge_events','research_knowledge_versions')
    connection.execute(sa.text('LOCK TABLE '+', '.join(tables)+' IN ACCESS EXCLUSIVE MODE'))
    if any(connection.execute(sa.text(f'SELECT EXISTS (SELECT 1 FROM {table})')).scalar_one() for table in tables):
        raise RuntimeError('research_knowledge_populated_downgrade_blocked')
    for table in tables:
        op.drop_table(table)
    op.execute('DROP FUNCTION research_knowledge_immutable()')
