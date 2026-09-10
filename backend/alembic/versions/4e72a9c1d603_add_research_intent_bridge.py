"""Bounded W1 intent/mapping/budget records, no seeded decisions or legacy rewrite."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='4e72a9c1d603'
down_revision='a1c3e5f7b9d0'
branch_labels=None
depends_on=None
TABLES=('research_intent_mappings','research_intent_manifests','research_intent_versions',
        'research_intent_budget_decisions','research_intent_plan_members','research_intent_audit')


def upgrade():
    for table,name in zip(TABLES[:3],('intent_mapping','intent_manifest','intent')):
        extra=[]
        if name=='intent':
            extra=[sa.Column('link',JSONB(),nullable=False),sa.Column('link_digest',sa.String(64),nullable=False),
                sa.CheckConstraint("jsonb_typeof(link) = 'object' AND octet_length(link::text) <= 4096 AND link_digest ~ '^[0-9a-f]{64}$'",name='ck_intent_link')]
        op.create_table(table,
            sa.Column('id',sa.Integer(),primary_key=True),sa.Column('context_id',sa.Integer(),nullable=False),
            sa.Column('context_version',sa.Integer(),nullable=False),sa.Column('target_id',sa.Integer(),nullable=False),
            sa.Column('number',sa.Integer(),nullable=False),sa.Column('version',sa.Integer(),nullable=False),
            sa.Column('body',JSONB(),nullable=False),sa.Column('digest',sa.String(64),nullable=False),
            sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),sa.Column('valid_until',sa.DateTime(timezone=True),nullable=False),
            *extra,
            sa.UniqueConstraint('context_id','number','version',name='uq_'+name+'_version'),
            sa.ForeignKeyConstraint(['context_id','context_version'],['research_context_versions.context_id','research_context_versions.version_number'],ondelete='RESTRICT'),
            sa.ForeignKeyConstraint(['context_id','target_id'],['research_target_associations.context_id','research_target_associations.target_id'],ondelete='RESTRICT'),
            sa.CheckConstraint('number BETWEEN 1 AND 1024 AND version BETWEEN 1 AND 1024',name='ck_'+name+'_numbers'),
            sa.CheckConstraint("jsonb_typeof(body) = 'object' AND octet_length(body::text) <= 65536 AND digest ~ '^[0-9a-f]{64}$'",name='ck_'+name+'_body'),
            sa.CheckConstraint('valid_until > recorded_at',name='ck_'+name+'_window'))
        op.create_index('ix_'+table+'_context_id',table,['context_id'])
    op.create_table(TABLES[3],sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('manifest_id',sa.Integer(),sa.ForeignKey(TABLES[1]+'.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('sequence',sa.Integer(),nullable=False),sa.Column('decision',sa.String(16),nullable=False),
        sa.Column('body',JSONB(),nullable=False),sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('manifest_id','sequence',name='uq_intent_budget_sequence'),
        sa.CheckConstraint("sequence BETWEEN 1 AND 16 AND decision IN ('approved','revoked') AND octet_length(body::text) <= 4096",name='ck_intent_budget_body'))
    op.create_index('ix_'+TABLES[3]+'_manifest_id',TABLES[3],['manifest_id'])
    op.create_table(TABLES[4],sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('intent_id',sa.Integer(),sa.ForeignKey(TABLES[2]+'.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('role',sa.String(16),nullable=False),
        sa.Column('plan_id',sa.Integer(),sa.ForeignKey('execution_plans.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('action_id',sa.Integer(),sa.ForeignKey('plan_actions.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('test_case_id',sa.Integer(),sa.ForeignKey('test_cases.id',ondelete='RESTRICT'),nullable=False),
        sa.UniqueConstraint('intent_id','role',name='uq_intent_role'),sa.UniqueConstraint('plan_id',name='uq_intent_plan'),
        sa.CheckConstraint("role IN ('baseline','probe','health')",name='ck_intent_role'))
    for column in ('intent_id','plan_id','test_case_id'):
        op.create_index('ix_'+TABLES[4]+'_'+column,TABLES[4],[column])
    op.create_table(TABLES[5],sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('context_id',sa.Integer(),sa.ForeignKey('research_contexts.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('code',sa.String(16),nullable=False),sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("code IN ('mapping','manifest','budget','intent','read')",name='ck_intent_audit_code'))
    op.create_index('ix_'+TABLES[5]+'_context_id',TABLES[5],['context_id'])
    for table in TABLES:
        op.execute(f'CREATE TRIGGER intent_immutable BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION research_knowledge_immutable()')


def downgrade():
    db=op.get_bind()
    db.execute(sa.text('LOCK TABLE '+', '.join(TABLES)+', test_cases, execution_plans IN ACCESS EXCLUSIVE MODE'))
    if (any(db.execute(sa.text(f'SELECT EXISTS (SELECT 1 FROM {table})')).scalar_one() for table in TABLES)
        or db.execute(sa.text("SELECT EXISTS (SELECT 1 FROM test_cases WHERE test_type LIKE 'ra\\_%' ESCAPE '\\')")).scalar_one()
        or db.execute(sa.text("SELECT EXISTS (SELECT 1 FROM execution_plans WHERE policy_context ? 'research_intent')")).scalar_one()):
        raise RuntimeError('research_intent_populated_downgrade_blocked')
    for table in reversed(TABLES): op.drop_table(table)
