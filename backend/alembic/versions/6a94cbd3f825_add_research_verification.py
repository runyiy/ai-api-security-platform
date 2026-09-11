"""W2 exact platform provenance and minimized evidence; no legacy backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '6a94cbd3f825'
down_revision = '5f83bac2e714'
branch_labels = None
depends_on = None
TABLES = ('research_verification_contracts', 'research_verification_health_selections',
          'research_verification_attempts', 'research_verification_witnesses',
          'research_verification_pairs', 'research_verification_audit')
FAULT = 'research_verification_clock_faults'


def upgrade():
    # Frozen DDL, never import evolving application metadata into migrations.
    for table, name in zip(TABLES[:-1], ('contract', 'health_selection', 'attempt', 'witness', 'pair')):
        extra = []
        if name == 'contract':
            extra = [sa.Column('context_version', sa.Integer(), nullable=False),
                     sa.Column('target_id', sa.Integer(), nullable=False),
                     sa.Column('number', sa.Integer(), nullable=False),
                     sa.Column('version', sa.Integer(), nullable=False),
                     sa.Column('valid_until', sa.DateTime(timezone=True), nullable=False),
                     sa.Column('manifest_id', sa.Integer(), sa.ForeignKey('research_intent_manifests.id', ondelete='RESTRICT'), nullable=False),
                     sa.UniqueConstraint('context_id', 'number', 'version', name='uq_verification_contract_version'),
                     sa.UniqueConstraint('manifest_id', name='uq_verification_contract_manifest'),
                     sa.UniqueConstraint('context_id', 'id', name='uq_verification_contract_context'),
                     sa.ForeignKeyConstraint(['context_id','context_version'], ['research_context_versions.context_id','research_context_versions.version_number'], ondelete='RESTRICT'),
                     sa.ForeignKeyConstraint(['context_id','target_id'], ['research_target_associations.context_id','research_target_associations.target_id'], ondelete='RESTRICT'),
                     sa.CheckConstraint('number BETWEEN 1 AND 1024 AND version BETWEEN 1 AND 1024', name='ck_verification_contract_numbers'),
                     sa.CheckConstraint('valid_until > recorded_at', name='ck_verification_contract_window')]
        elif name == 'health_selection':
            extra = [sa.Column('manifest_id', sa.Integer(), sa.ForeignKey('research_intent_manifests.id', ondelete='RESTRICT'), nullable=False),
                     sa.Column('contract_id', sa.Integer(), nullable=False),
                     sa.UniqueConstraint('manifest_id', name='uq_verification_health_manifest'),
                     sa.ForeignKeyConstraint(['context_id','contract_id'], [TABLES[0]+'.context_id',TABLES[0]+'.id'], ondelete='RESTRICT')]
        elif name == 'attempt':
            extra = [sa.Column('manifest_id', sa.Integer(), sa.ForeignKey('research_intent_manifests.id', ondelete='RESTRICT'), nullable=False),
                     sa.Column('intent_id', sa.Integer(), sa.ForeignKey('research_intent_versions.id', ondelete='RESTRICT'), nullable=False),
                     sa.Column('plan_id', sa.Integer(), sa.ForeignKey('execution_plans.id', ondelete='RESTRICT'), nullable=False),
                     sa.Column('slot', sa.String(16), nullable=False),
                     sa.UniqueConstraint('plan_id', name='uq_verification_attempt_plan'),
                     sa.UniqueConstraint('manifest_id','slot', name='uq_verification_attempt_slot'),
                     sa.UniqueConstraint('context_id','id', name='uq_verification_attempt_context'),
                     sa.CheckConstraint("slot IN ('baseline','probe','health_baseline','health_probe')", name='ck_verification_attempt_slot')]
        elif name == 'witness':
            extra = [sa.Column('intent_id', sa.Integer(), sa.ForeignKey('research_intent_versions.id', ondelete='RESTRICT'), nullable=False),
                     sa.Column('plan_id', sa.Integer(), sa.ForeignKey('execution_plans.id', ondelete='RESTRICT'), nullable=False),
                     sa.Column('run_id', sa.Integer(), sa.ForeignKey('test_runs.id', ondelete='RESTRICT'), nullable=False),
                     sa.Column('attempt_id', sa.Integer(), nullable=False),
                     sa.UniqueConstraint('plan_id', name='uq_verification_witness_plan'),
                     sa.UniqueConstraint('run_id', name='uq_verification_witness_run'),
                     sa.UniqueConstraint('attempt_id', name='uq_verification_witness_attempt'),
                     sa.UniqueConstraint('context_id','id', name='uq_verification_witness_context'),
                     sa.ForeignKeyConstraint(['context_id','attempt_id'], [TABLES[2]+'.context_id',TABLES[2]+'.id'], ondelete='RESTRICT')]
        else:
            extra = [sa.Column('intent_id', sa.Integer(), sa.ForeignKey('research_intent_versions.id', ondelete='RESTRICT'), nullable=False),
                     sa.Column('baseline_id', sa.Integer(), nullable=False), sa.Column('probe_id', sa.Integer(), nullable=True),
                     sa.UniqueConstraint('intent_id', name='uq_verification_pair_intent'),
                     sa.ForeignKeyConstraint(['context_id','baseline_id'], [TABLES[3]+'.context_id',TABLES[3]+'.id'], ondelete='RESTRICT'),
                     sa.ForeignKeyConstraint(['context_id','probe_id'], [TABLES[3]+'.context_id',TABLES[3]+'.id'], ondelete='RESTRICT')]
        context = sa.Column('context_id', sa.Integer(), nullable=False) if name == 'contract' else sa.Column('context_id', sa.Integer(), sa.ForeignKey('research_contexts.id', ondelete='RESTRICT'), nullable=False)
        limit = 65536 if name == 'contract' else 4096
        op.create_table(table, sa.Column('id', sa.Integer(), primary_key=True), context,
            sa.Column('body', JSONB(), nullable=False), sa.Column('digest', sa.String(64), nullable=False),
            sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False), *extra,
            sa.CheckConstraint(f"jsonb_typeof(body) = 'object' AND octet_length(body::text) <= {limit} AND digest ~ '^[0-9a-f]{{64}}$'", name='ck_verification_'+name+'_body'))
        op.create_index('ix_'+table+'_context_id', table, ['context_id'])
        if name == 'witness': op.create_index('ix_'+table+'_intent_id', table, ['intent_id'])
    op.create_table(TABLES[-1], sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('context_id', sa.Integer(), sa.ForeignKey('research_contexts.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('code', sa.String(16), nullable=False), sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("code IN ('confirm','select_health','approve','dispatch','witness','pair','read')", name='ck_verification_audit_code'))
    op.create_index('ix_'+TABLES[-1]+'_context_id', TABLES[-1], ['context_id'])
    op.create_table(FAULT,sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('context_id',sa.Integer(),nullable=False),sa.Column('intent_id',sa.Integer(),nullable=False),
        sa.Column('digest',sa.String(64),nullable=False),sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('intent_id',name='uq_verification_clock_fault_intent'),
        sa.CheckConstraint("intent_id > 0 AND context_id > 0 AND digest ~ '^[0-9a-f]{64}$'",name='ck_verification_clock_fault_reference'))
    op.create_index('ix_'+FAULT+'_context_id',FAULT,['context_id'])
    for table in (*TABLES,FAULT):
        op.execute(f'CREATE TRIGGER verification_immutable BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION research_knowledge_immutable()')


def downgrade():
    db = op.get_bind()
    db.execute(sa.text('LOCK TABLE '+', '.join((*TABLES,FAULT))+' IN ACCESS EXCLUSIVE MODE'))
    if any(db.execute(sa.text(f'SELECT EXISTS (SELECT 1 FROM {t})')).scalar_one() for t in (*TABLES,FAULT)):
        raise RuntimeError('research_verification_populated_downgrade_blocked')
    for table in reversed((*TABLES,FAULT)): op.drop_table(table)
