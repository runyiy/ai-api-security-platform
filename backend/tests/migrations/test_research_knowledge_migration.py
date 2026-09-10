"""Owned disposable schemas and legacy fixtures; no operator database cleanup."""
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text, delete, event
from sqlalchemy.engine import make_url
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine, SessionLocal
from app.db.models import SecurityReport
from app.services.security_report import SecurityReportService
from tests.research_knowledge_fixtures import knowledge_pair, subject_pair, two_intake_targets, content, record, call, NOW, REF  # noqa: F401
from tests.research_intake_fixtures import INTENT_TABLES, snapshot, KNOWLEDGE_TABLES, RULE_VALIDATION_TABLES
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401
from tests.api.test_finding_evidence_fingerprints import old_evidence
from tests.api.test_finding_structured_evidence import analyze

REVISION='f0b2d4e6a8c0'
PARENT='e9a1c3d5f7b8'


def previous_snapshot():
    with engine.connect() as db:
        return {t.name:list(db.execute(select(t).order_by(*t.primary_key.columns)).mappings()) for t in Base.metadata.sorted_tables if t.name not in INTENT_TABLES | RULE_VALIDATION_TABLES | KNOWLEDGE_TABLES}


def test_fresh_exact_schema_and_empty_rollback(monkeypatch):
    config=Config('alembic.ini');scripts=ScriptDirectory.from_config(config)
    assert scripts.get_heads()==['4e72a9c1d603'] and scripts.get_revision(REVISION).down_revision==PARENT
    schema='knowledge_migration_'+uuid4().hex
    with engine.begin() as db:db.execute(text(f'CREATE SCHEMA "{schema}"'))
    url=make_url(settings.database_url).update_query_dict({'options':f'-csearch_path={schema}'})
    isolated=create_engine(url)
    try:
        monkeypatch.setattr(settings,'database_url',url.render_as_string(hide_password=False).replace('%','%%'))
        command.upgrade(config,PARENT);before=set(inspect(isolated).get_table_names())
        command.upgrade(config,REVISION);inspector=inspect(isolated)
        assert set(inspector.get_table_names())==before|KNOWLEDGE_TABLES
        for table in KNOWLEDGE_TABLES:
            columns={c['name']:c for c in inspector.get_columns(table)}
            for c in Base.metadata.tables[table].columns:
                assert columns[c.name]['type'].compile(dialect=engine.dialect)==c.type.compile(dialect=engine.dialect)
                assert columns[c.name]['nullable']==c.nullable
            assert all(f['options']=={'ondelete':'RESTRICT'} for f in inspector.get_foreign_keys(table))
        assert not any(f['referred_table'].startswith('research_observation') for t in KNOWLEDGE_TABLES for f in inspector.get_foreign_keys(t))
        command.downgrade(config,PARENT)
        assert set(inspect(isolated).get_table_names())==before
    finally:
        isolated.dispose()
        with engine.begin() as db:db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_populated_legacy_w1_w2_w3_rows_pair_and_report_unchanged(knowledge_pair,evidence_pair):
    config=Config('alembic.ini');report_id=None
    try:
        command.downgrade(config,PARENT)
        finding_id,_=old_evidence(evidence_pair)
        assert analyze(evidence_pair).status_code==200
        with SessionLocal() as db:
            report=SecurityReportService(db=db).generate(finding_id=finding_id);db.commit();report_id=report.id
        before=previous_snapshot();statements=[]
        def capture(conn,cursor,statement,*args):statements.append(statement.lower())
        event.listen(engine,'before_cursor_execute',capture)
        try:command.upgrade(config,REVISION)
        finally:event.remove(engine,'before_cursor_execute',capture)
        assert previous_snapshot()==before
        assert not any('response_body' in q or 'request_data' in q for q in statements)
        assert not any(q.startswith(('update ','delete ','insert into ')) and 'alembic_version' not in q for q in statements)
        result=analyze(evidence_pair)
        assert result.status_code==200 and result.json()['finding']['status']=='confirmed'
        assert result.json()['finding']['review_notes']=='keep human review'
        after=previous_snapshot()
        assert {k:v for k,v in before.items() if k!='findings'}=={k:v for k,v in after.items() if k!='findings'}
        with SessionLocal() as db:assert db.get(SecurityReport,report_id).markdown_content==next(r for r in before['security_reports'] if r['id']==report_id)['markdown_content']
        command.downgrade(config,PARENT);assert previous_snapshot()==after
    finally:
        command.upgrade(config,'head')
        if report_id:
            with SessionLocal() as db:db.execute(delete(SecurityReport).where(SecurityReport.id==report_id));db.commit()


def test_nonempty_rollback_refused_without_history_loss(knowledge_pair):
    g,_=knowledge_pair;record(g);before=snapshot()
    with pytest.raises(RuntimeError,match='research_knowledge_populated_downgrade_blocked'):
        command.downgrade(Config('alembic.ini'),PARENT)
    assert snapshot()==before
    with engine.connect() as db:assert MigrationContext.configure(db).get_current_revision()=='4e72a9c1d603'
