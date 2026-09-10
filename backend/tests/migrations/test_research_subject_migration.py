"""Fresh/populated owned schemas only; no valuable data downgrade."""
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, delete, event, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.db.models.research_subject import ResearchSubjectVersion
from app.db.models import SecurityReport
from app.services.security_report import SecurityReportService
from tests.research_subject_fixtures import subject_pair, two_intake_targets, proposal, call, NOW, REF  # noqa: F401
from tests.services.test_research_subject import record
from tests.research_intake_fixtures import snapshot
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401
from tests.api.test_finding_evidence_fingerprints import old_evidence
from tests.api.test_finding_structured_evidence import analyze

REVISION="e9a1c3d5f7b8"
PARENT="d8f0b2c4e6a8"
TABLE="research_subject_versions"


def existing():
    with engine.connect() as db:
        return {t.name:list(db.execute(select(t).order_by(*t.primary_key.columns)).mappings())
                for t in Base.metadata.sorted_tables if t.name!=TABLE}


def test_fresh_exact_schema_and_empty_rollback(monkeypatch):
    config=Config("alembic.ini");scripts=ScriptDirectory.from_config(config)
    assert scripts.get_heads()==[REVISION] and scripts.get_revision(REVISION).down_revision==PARENT
    schema="subject_migration_"+uuid4().hex
    with engine.begin() as db:db.execute(text(f'CREATE SCHEMA "{schema}"'))
    url=make_url(settings.database_url).update_query_dict({"options":f"-csearch_path={schema}"})
    isolated=create_engine(url)
    try:
        monkeypatch.setattr(settings,"database_url",url.render_as_string(hide_password=False).replace("%","%%"))
        command.upgrade(config,PARENT);before=set(inspect(isolated).get_table_names())
        command.upgrade(config,REVISION);inspector=inspect(isolated)
        assert set(inspector.get_table_names())==before|{TABLE}
        cols={c["name"]:c for c in inspector.get_columns(TABLE)}
        for c in ResearchSubjectVersion.__table__.columns:
            assert c.type.compile(dialect=engine.dialect)==cols[c.name]["type"].compile(dialect=engine.dialect)
            assert c.nullable==cols[c.name]["nullable"]
        assert {tuple(f["constrained_columns"]) for f in inspector.get_foreign_keys(TABLE)}=={("context_id","target_id"),("context_id","context_version")}
        assert all(f["options"]=={"ondelete":"RESTRICT"} for f in inspector.get_foreign_keys(TABLE))
        command.downgrade(config,PARENT)
        assert set(inspect(isolated).get_table_names())==before
    finally:
        isolated.dispose()
        with engine.begin() as db:db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_populated_legacy_w1_w2_pair_history_and_report_unchanged(subject_pair,evidence_pair):
    config=Config("alembic.ini");report_id=None
    try:
        command.downgrade(config,PARENT)
        finding_id,_=old_evidence(evidence_pair)
        assert analyze(evidence_pair).status_code==200
        with SessionLocal() as db:
            report=SecurityReportService(db=db).generate(finding_id=finding_id);db.commit();report_id=report.id
        before=existing();statements=[]
        def capture(conn,cursor,statement,*args):statements.append(statement.lower())
        event.listen(engine,"before_cursor_execute",capture)
        try:command.upgrade(config,REVISION)
        finally:event.remove(engine,"before_cursor_execute",capture)
        assert existing()==before
        assert not any("response_body" in s or "request_data" in s for s in statements)
        assert not any(s.startswith(("update ","delete ","insert into ")) and "alembic_version" not in s for s in statements)
        result=analyze(evidence_pair)
        assert result.status_code==200 and result.json()["finding"]["status"]=="confirmed"
        assert result.json()["finding"]["review_notes"]=="keep human review"
        after=existing()
        assert {k:v for k,v in after.items() if k!="findings"}=={k:v for k,v in before.items() if k!="findings"}
        with SessionLocal() as db:
            assert db.get(SecurityReport,report_id).markdown_content==next(r for r in before["security_reports"] if r["id"]==report_id)["markdown_content"]
        command.downgrade(config,PARENT);assert existing()==after
    finally:
        command.upgrade(config,"head")
        if report_id:
            with SessionLocal() as db:db.execute(delete(SecurityReport).where(SecurityReport.id==report_id));db.commit()


def test_populated_rollback_and_cross_context_fk_protected(subject_pair):
    a,b=subject_pair;record(a);before=snapshot()
    with pytest.raises(RuntimeError,match="research_subject_populated_downgrade_blocked"):
        command.downgrade(Config("alembic.ini"),PARENT)
    assert snapshot()==before
    with engine.connect() as db:assert MigrationContext.configure(db).get_current_revision()==REVISION
    with SessionLocal() as db:
        row=db.scalar(select(ResearchSubjectVersion).where(ResearchSubjectVersion.context_id==a["ctx"]))
        for values in ({"target_id":b["target"]},{"context_version":2},{"provenance":"observed_baseline"},{"proposal_number":0},{"proposal":{"x":"x"*16384}}):
            with pytest.raises(IntegrityError):
                with db.begin_nested():db.execute(ResearchSubjectVersion.__table__.update().where(ResearchSubjectVersion.id==row.id).values(**values))
        db.rollback()
    assert snapshot()==before
