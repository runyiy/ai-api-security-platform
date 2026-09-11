"""W2 additive schema; legacy/M13 preservation and populated rollback refusal."""
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.migration import MigrationContext
from sqlalchemy import create_engine,inspect,select,text,update
from sqlalchemy.engine import make_url
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine,SessionLocal
from app.db.models.research_verification import VerificationContract
from tests.research_intake_fixtures import VERIFICATION_TABLES,snapshot
from tests.research_intent_fixtures import intent_graph,subject_pair,two_intake_targets,call,NOW,REF
from tests.finding_evidence_fixtures import evidence_pair
from tests.api.test_finding_evidence_fingerprints import old_evidence
from tests.api.test_finding_structured_evidence import analyze
from tests.migrations.test_research_intent_migration import test_populated_legacy_evidence_review_report_survive as legacy_cycle
from app.services import research_verification as verify

REVISION='6a94cbd3f825'
PARENT='5f83bac2e714'


def previous():
    with engine.connect() as db:
        return {t.name:list(db.execute(select(t).order_by(*t.primary_key.columns)).mappings())
            for t in Base.metadata.sorted_tables if t.name not in VERIFICATION_TABLES}


def test_fresh_additive_schema_and_empty_downgrade(monkeypatch):
    config=Config('alembic.ini');scripts=ScriptDirectory.from_config(config)
    assert scripts.get_heads()==[REVISION] and scripts.get_revision(REVISION).down_revision==PARENT
    name='verification_'+uuid4().hex
    with engine.begin() as db:db.execute(text(f'CREATE SCHEMA "{name}"'))
    url=make_url(settings.database_url).update_query_dict({'options':f'-csearch_path={name}'})
    isolated=create_engine(url)
    try:
        monkeypatch.setattr(settings,'database_url',url.render_as_string(hide_password=False).replace('%','%%'))
        command.upgrade(config,PARENT);before=set(inspect(isolated).get_table_names())
        command.upgrade(config,REVISION);inspector=inspect(isolated)
        assert set(inspector.get_table_names())==before|VERIFICATION_TABLES
        for table in VERIFICATION_TABLES:
            cols={c['name']:c for c in inspector.get_columns(table)}
            for column in Base.metadata.tables[table].columns:
                assert column.type.compile(dialect=engine.dialect)==cols[column.name]['type'].compile(dialect=engine.dialect)
                assert column.nullable==cols[column.name]['nullable']
            assert all(f['options']=={'ondelete':'RESTRICT'} for f in inspector.get_foreign_keys(table))
        command.check(config)
        command.downgrade(config,PARENT);assert set(inspect(isolated).get_table_names())==before
        command.upgrade(config,REVISION)
    finally:
        isolated.dispose()
        with engine.begin() as db:db.execute(text(f'DROP SCHEMA "{name}" CASCADE'))


def test_populated_w1_metadata_unchanged_upgrade(intent_graph):
    before=previous();config=Config('alembic.ini')
    try:
        command.downgrade(config,PARENT);assert previous()==before
        command.upgrade(config,REVISION);assert previous()==before
    finally:command.upgrade(config,'head')


def test_populated_legacy_plan_run_m13_review_and_report(subject_pair,evidence_pair):
    # Reuse the actual legacy fixture's comparisons at the new migration parent.
    import tests.migrations.test_research_intent_migration as legacy
    old_parent,old_revision=legacy.PARENT,legacy.REVISION
    try:
        legacy.PARENT,legacy.REVISION=PARENT,REVISION
        legacy_cycle(subject_pair,evidence_pair)
    finally:legacy.PARENT,legacy.REVISION=old_parent,old_revision


def test_immutable_record_and_populated_downgrade(intent_graph):
    g=intent_graph
    expectations=[dict(role=role,object_key='record_id',object_value='7001',identity_key='subject_id',identity_value=None if role=='baseline' else 'fixture_p') for role in ('baseline','probe')]
    try:
        value=call(verify.confirm,g,1,dict(manifest=g['manifest'],expected_version=0,decision='confirm',expectations=expectations,evidence=REF))
        before=snapshot()
        with SessionLocal() as db:
            with pytest.raises(Exception,match='immutable'):
                with db.begin_nested():db.execute(update(VerificationContract).values(body={}))
            db.rollback()
        with pytest.raises(RuntimeError,match='verification_populated_downgrade_blocked'):command.downgrade(Config('alembic.ini'),PARENT)
        assert snapshot()==before
        with engine.connect() as db:assert MigrationContext.configure(db).get_current_revision()==REVISION
    finally:
        from sqlalchemy import delete
        from app.db.models.research_verification import VerificationAudit
        with SessionLocal() as db:
            db.execute(delete(VerificationContract).where(VerificationContract.context_id==g['ctx']))
            db.execute(delete(VerificationAudit).where(VerificationAudit.context_id==g['ctx']));db.commit()
