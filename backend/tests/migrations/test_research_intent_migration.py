"""Owned fresh/populated schemas; preserve legacy evidence and refuse destructive rollback."""
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select, text, delete
from sqlalchemy.engine import make_url
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine,SessionLocal
from app.db.models import SecurityReport,TestCase as StoredCase
from app.services.security_report import SecurityReportService
from tests.research_intake_fixtures import INTENT_TABLES,snapshot
from tests.research_intent_fixtures import intent_graph,subject_pair,two_intake_targets,NOW  # noqa: F401
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401
from tests.api.test_finding_evidence_fingerprints import old_evidence
from tests.api.test_finding_structured_evidence import analyze

REVISION='4e72a9c1d603'
PARENT='a1c3e5f7b9d0'


def legacy_snapshot():
    with engine.connect() as db:
        return {t.name:list(db.execute(select(t).order_by(*t.primary_key.columns)).mappings())
            for t in Base.metadata.sorted_tables if t.name not in INTENT_TABLES}


def test_fresh_additive_schema_and_empty_roundtrip(monkeypatch):
    config=Config('alembic.ini');scripts=ScriptDirectory.from_config(config)
    assert scripts.get_heads()==[REVISION] and scripts.get_revision(REVISION).down_revision==PARENT
    name='intent_migration_'+uuid4().hex
    with engine.begin() as db:db.execute(text(f'CREATE SCHEMA "{name}"'))
    url=make_url(settings.database_url).update_query_dict({'options':f'-csearch_path={name}'})
    isolated=create_engine(url)
    try:
        monkeypatch.setattr(settings,'database_url',url.render_as_string(hide_password=False).replace('%','%%'))
        command.upgrade(config,PARENT);before=set(inspect(isolated).get_table_names())
        command.upgrade(config,REVISION);inspector=inspect(isolated)
        assert set(inspector.get_table_names())==before|INTENT_TABLES
        for name in INTENT_TABLES:
            cols={c['name']:c for c in inspector.get_columns(name)}
            for column in Base.metadata.tables[name].columns:
                assert column.type.compile(dialect=engine.dialect)==cols[column.name]['type'].compile(dialect=engine.dialect)
                assert column.nullable==cols[column.name]['nullable']
            assert all(f['options']=={'ondelete':'RESTRICT'} for f in inspector.get_foreign_keys(name))
        command.downgrade(config,PARENT);assert set(inspect(isolated).get_table_names())==before
    finally:
        isolated.dispose();schema=url.query['options'].split('=')[1]
        with engine.begin() as db:db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_populated_legacy_evidence_review_report_survive(subject_pair,evidence_pair):
    from app.db.models import Target,ExecutionPlan,PlanAction,ExecutionPlanApprovalRecord
    from app.services.execution_plan import create_execution_plan,PlanActionInput
    from app.services.execution_plan_approval import record_plan_decision,is_plan_approved,validate_persisted_plan_integrity
    config=Config('alembic.ini');rid=pid=cid=None;g=subject_pair[0]
    try:
        with SessionLocal() as db:
            case=StoredCase(endpoint_id=g['endpoint'],actor_identity_id=g['anonymous'],resource_id=g['resource'],
                test_type='anonymous_access',ownership_relation='anonymous',expected_statuses=[401,403],status='pending')
            db.add(case);db.flush();cid=case.id
            plan=create_execution_plan(db,target_id=g['target'],authorization_revision_id=g['revision'],
                actor_identity_id=g['anonymous'],credential_binding_id=None,
                actions=[PlanActionInput('GET',db.get(Target,g['target']).base_url.rstrip('/')+'/folders/1',cid,g['resource'])])
            pid=plan.id;plan_digest=plan.plan_digest
            record_plan_decision(db,execution_plan_id=pid,decision='approved');db.commit()
        finding_id,_=old_evidence(evidence_pair);assert analyze(evidence_pair).status_code==200
        with SessionLocal() as db:
            report=SecurityReportService(db=db).generate(finding_id=finding_id);db.commit();rid=report.id
        before=legacy_snapshot()
        assert before['execution_plans'] and before['execution_plan_approval_records']
        command.downgrade(config,PARENT);assert legacy_snapshot()==before
        command.upgrade(config,REVISION);assert legacy_snapshot()==before
        with SessionLocal() as db:
            assert validate_persisted_plan_integrity(db,pid).plan_digest==plan_digest
            assert is_plan_approved(db,pid)
        result=analyze(evidence_pair)
        assert result.status_code==200 and result.json()['finding']['status']=='confirmed'
        assert result.json()['finding']['review_notes']=='keep human review'
        after=legacy_snapshot()
        assert {k:v for k,v in before.items() if k!='findings'}=={k:v for k,v in after.items() if k!='findings'}
    finally:
        command.upgrade(config,'head')
        if rid:
            with SessionLocal() as db:db.execute(delete(SecurityReport).where(SecurityReport.id==rid));db.commit()
        with SessionLocal() as db:
            if pid:
                db.execute(delete(ExecutionPlanApprovalRecord).where(ExecutionPlanApprovalRecord.execution_plan_id==pid))
                db.execute(delete(PlanAction).where(PlanAction.execution_plan_id==pid))
                db.execute(delete(ExecutionPlan).where(ExecutionPlan.id==pid))
            if cid:db.execute(delete(StoredCase).where(StoredCase.id==cid))
            db.commit()


def test_populated_downgrade_refused_atomically(intent_graph):
    before=snapshot()
    with pytest.raises(RuntimeError,match='research_intent_populated_downgrade_blocked'):
        command.downgrade(Config('alembic.ini'),PARENT)
    assert snapshot()==before
    with engine.connect() as db:assert MigrationContext.configure(db).get_current_revision()==REVISION


def test_orphan_new_case_also_blocks_downgrade(evidence_pair):
    with SessionLocal() as db:
        case=db.get(StoredCase,evidence_pair['probe_case']);old=case.test_type;case.test_type='ra_intent_get_v1';db.commit()
    try:
        before=snapshot()
        with pytest.raises(RuntimeError,match='research_intent_populated_downgrade_blocked'):
            command.downgrade(Config('alembic.ini'),PARENT)
        assert snapshot()==before
    finally:
        with SessionLocal() as db:db.get(StoredCase,evidence_pair['probe_case']).test_type=old;db.commit()


def test_incomplete_orphan_plan_marker_blocks_empty_domain_downgrade(subject_pair):
    from app.db.models import ExecutionPlan
    g=subject_pair[0]
    with SessionLocal() as db:
        # A malformed new-purpose record must not be made executable by rollback.
        plan=ExecutionPlan(target_id=g['target'],authorization_revision_id=g['revision'],
            actor_identity_id=g['anonymous'],credential_binding_id=None,digest_version='v1',
            plan_digest='a'*64,action_count=1,policy_context={'research_intent':{}})
        db.add(plan);db.commit();pid=plan.id
    try:
        before=snapshot()
        with pytest.raises(RuntimeError,match='research_intent_populated_downgrade_blocked'):
            command.downgrade(Config('alembic.ini'),PARENT)
        assert snapshot()==before
    finally:
        with SessionLocal() as db:db.execute(delete(ExecutionPlan).where(ExecutionPlan.id==pid));db.commit()
