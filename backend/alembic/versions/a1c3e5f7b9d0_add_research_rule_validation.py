"""W3 immutable offline validation/feedback; no approvals or legacy backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'a1c3e5f7b9d0'
down_revision = 'f0b2d4e6a8c0'
branch_labels = None
depends_on = None
TABLES = ('research_rule_validations', 'research_rule_feedback', 'research_rule_feedback_reviews')


def upgrade():
    op.create_table(TABLES[0],
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('version_id', sa.Integer(), sa.ForeignKey('research_knowledge_versions.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('body', JSONB(), nullable=False),
        sa.Column('digest', sa.String(64), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('valid_until', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("jsonb_typeof(body) = 'object' AND octet_length(body::text) <= 32768 AND digest ~ '^[0-9a-f]{64}$'", name='ck_rule_validation_body'),
        sa.CheckConstraint('valid_until > recorded_at', name='ck_rule_validation_window'))
    op.create_index('ix_research_rule_validations_version_id', TABLES[0], ['version_id'])
    op.create_table(TABLES[1],
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('version_id', sa.Integer(), sa.ForeignKey('research_knowledge_versions.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('validation_id', sa.Integer(), sa.ForeignKey('research_rule_validations.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('body', JSONB(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("jsonb_typeof(body) = 'object' AND octet_length(body::text) <= 4096", name='ck_rule_feedback_body'))
    op.create_index('ix_research_rule_feedback_version_id', TABLES[1], ['version_id'])
    op.create_table(TABLES[2],
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('feedback_id', sa.Integer(), sa.ForeignKey('research_rule_feedback.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('decision', sa.String(16), nullable=False),
        sa.Column('body', JSONB(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('feedback_id', name='uq_rule_feedback_review'),
        sa.CheckConstraint("decision IN ('accept','reject','invalidate') AND octet_length(body::text) <= 4096", name='ck_rule_feedback_review'))
    op.create_index('ix_research_rule_feedback_reviews_feedback_id', TABLES[2], ['feedback_id'])
    for table in TABLES:
        op.execute(f'CREATE TRIGGER knowledge_immutable BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION research_knowledge_immutable()')


def downgrade():
    db = op.get_bind()
    db.execute(sa.text('LOCK TABLE '+', '.join(TABLES)+' IN ACCESS EXCLUSIVE MODE'))
    if any(db.execute(sa.text(f'SELECT EXISTS (SELECT 1 FROM {table})')).scalar_one() for table in TABLES):
        raise RuntimeError('research_rule_validation_populated_downgrade_blocked')
    for table in reversed(TABLES):
        op.drop_table(table)
