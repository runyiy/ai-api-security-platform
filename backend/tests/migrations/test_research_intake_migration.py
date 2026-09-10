"""Additive upgrade, legacy preservation and non-destructive rollback."""
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, delete, event, inspect, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.base import Base
from app.db.models import SecurityReport
from app.db.models.research_context import ResearchContext, ResearchContextVersion, ResearchTargetAssociation
from app.db.session import engine, SessionLocal
from app.services.security_report import SecurityReportService
from tests.api.test_finding_evidence_fingerprints import old_evidence
from tests.api.test_finding_structured_evidence import analyze
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401
from tests.research_intake_fixtures import NEW_TABLES, intake_target, snapshot  # noqa: F401
from tests.services.test_research_context import create

REVISION = "c7e9a1b3d5f7"
PARENT = "b5d7f9a1c3e6"


def test_fresh_upgrade_exact_tables_constraints_and_empty_rollback(monkeypatch):
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["d8f0b2c4e6a8"]
    assert scripts.get_revision(REVISION).down_revision == PARENT
    schema = "intake_migration_"+uuid4().hex
    with engine.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(settings.database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    isolated = create_engine(url)
    try:
        monkeypatch.setattr(settings, "database_url", url.render_as_string(hide_password=False).replace("%", "%%"))
        command.upgrade(config, PARENT)
        before = set(inspect(isolated).get_table_names())
        command.upgrade(config, REVISION)
        inspector = inspect(isolated)
        assert set(inspector.get_table_names()) == before | NEW_TABLES
        for name in NEW_TABLES:
            columns = {c["name"]: c for c in inspector.get_columns(name)}
            for c in Base.metadata.tables[name].columns:
                assert c.type.compile(dialect=engine.dialect) == columns[c.name]["type"].compile(dialect=engine.dialect)
                assert c.nullable == columns[c.name]["nullable"]
            assert all(fk["options"] == {"ondelete": "RESTRICT"} for fk in inspector.get_foreign_keys(name))
        partial, = [i for i in inspector.get_indexes("research_target_associations") if i["name"] == "uq_research_target_active_context"]
        assert partial["unique"] and "released_at IS NULL" in str(partial["dialect_options"])
        command.downgrade(config, PARENT)
        assert set(inspect(isolated).get_table_names()) == before
    finally:
        isolated.dispose()
        with engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_populated_legacy_upgrade_preserves_pair_fingerprint_review_report(evidence_pair):
    config = Config("alembic.ini")
    ids = evidence_pair
    report_id = None
    try:
        command.downgrade(config, PARENT)
        finding_id, _ = old_evidence(ids)
        assert analyze(ids).status_code == 200
        with SessionLocal() as db:
            report = SecurityReportService(db=db).generate(finding_id=finding_id)
            db.commit()
            report_id = report.id
        before = snapshot(legacy=True)
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.lower())
        event.listen(Engine, "before_cursor_execute", capture)
        try:
            command.upgrade(config, REVISION)
        finally:
            event.remove(Engine, "before_cursor_execute", capture)
        assert snapshot(legacy=True) == before
        assert not any(s.startswith(("insert into test", "update target", "update finding", "delete from")) for s in statements)
        assert not any("response_body" in s or "request_data" in s for s in statements)
        response = analyze(ids)
        assert response.status_code == 200
        assert response.json()["finding"]["status"] == "confirmed"
        assert response.json()["finding"]["review_notes"] == "keep human review"
        after = snapshot(legacy=True)
        assert {k: v for k, v in after.items() if k != "findings"} == {k: v for k, v in before.items() if k != "findings"}
        with SessionLocal() as db:
            stored = db.get(SecurityReport, report_id)
            original = next(r for r in before["security_reports"] if r["id"] == report_id)
            assert stored.markdown_content == original["markdown_content"]
        command.downgrade(config, PARENT)
        assert snapshot(legacy=True) == after
    finally:
        command.upgrade(config, "head")
        if report_id is not None:
            with SessionLocal() as db:
                db.execute(delete(SecurityReport).where(SecurityReport.id == report_id))
                db.commit()


def test_populated_intake_blocks_destructive_downgrade(intake_target):
    create(intake_target)
    before = snapshot()
    with pytest.raises(RuntimeError, match="research_intake_populated_downgrade_blocked"):
        command.downgrade(Config("alembic.ini"), PARENT)
    with engine.connect() as db:
        assert MigrationContext.configure(db).get_current_revision() == "d8f0b2c4e6a8"
    assert snapshot() == before


def test_database_uniqueness_fk_and_json_bounds(intake_target):
    result = create(intake_target)
    with SessionLocal() as db:
        values = {"context_id": result["context_id"], "target_id": intake_target["target"], "review_reference": {}}
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(ResearchTargetAssociation.__table__.insert().values(**values))
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(delete(ResearchContext).where(ResearchContext.id == result["context_id"]))
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(ResearchContextVersion.__table__.insert().values(context_id=result["context_id"],
                    version_number=2, intake={"x": "x"*32768}, permission_snapshots={},
                    provenance="operator_recorded_unverified"))
        db.rollback()
