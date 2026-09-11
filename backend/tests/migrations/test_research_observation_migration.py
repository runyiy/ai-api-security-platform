"""Only owned disposable schemas/fixtures; retain every legacy assertion."""
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
from app.db.models.research_observation import ObservationRecord, ObservationPayload, ObservationPreparation
from app.db.session import engine, SessionLocal
from app.services.security_report import SecurityReportService
from tests.api.test_finding_evidence_fingerprints import old_evidence
from tests.api.test_finding_structured_evidence import analyze
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401
from tests.research_observation_fixtures import TABLES, observation_context, intake_target, observation, call, NOW  # noqa: F401
from tests.research_intake_fixtures import VERIFICATION_TABLES, snapshot, KNOWLEDGE_TABLES, RULE_VALIDATION_TABLES, INTENT_TABLES
from tests.services.test_research_context import create
from tests.services.test_research_observation import accepted

REVISION, PARENT = "d8f0b2c4e6a8", "c7e9a1b3d5f7"


def existing_snapshot():
    with engine.connect() as db:
        return {t.name: list(db.execute(select(t).order_by(*t.primary_key.columns)).mappings())
                for t in Base.metadata.sorted_tables if t.name not in VERIFICATION_TABLES | INTENT_TABLES | TABLES | RULE_VALIDATION_TABLES | KNOWLEDGE_TABLES | {"research_subject_versions"}}


def test_fresh_upgrade_exact_schema_and_empty_downgrade(monkeypatch):
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["6a94cbd3f825"] and scripts.get_revision(REVISION).down_revision == PARENT
    schema = "observation_migration_"+uuid4().hex
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
        assert set(inspector.get_table_names()) == before | TABLES
        for name in TABLES:
            columns = {c["name"]: c for c in inspector.get_columns(name)}
            for c in Base.metadata.tables[name].columns:
                if c.name == "hold_generation": continue  # Added by 5f83bac2e714, checked separately.
                assert c.type.compile(dialect=engine.dialect) == columns[c.name]["type"].compile(dialect=engine.dialect)
                assert c.nullable == columns[c.name]["nullable"]
            assert all(fk["options"] == {"ondelete": "RESTRICT"} for fk in inspector.get_foreign_keys(name))
        assert any(f["constrained_columns"] == ["context_id", "observation_id"] for f in inspector.get_foreign_keys("research_observation_payloads"))
        command.downgrade(config, PARENT)
        assert set(inspect(isolated).get_table_names()) == before
    finally:
        isolated.dispose()
        with engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_populated_legacy_and_w1_unchanged_with_pair_review_report(evidence_pair, intake_target):
    config, report_id = Config("alembic.ini"), None
    create(intake_target)
    try:
        command.downgrade(config, PARENT)
        finding_id, _ = old_evidence(evidence_pair)
        assert analyze(evidence_pair).status_code == 200
        with SessionLocal() as db:
            report = SecurityReportService(db=db).generate(finding_id=finding_id)
            db.commit()
            report_id = report.id
        before = existing_snapshot()
        statements = []
        def capture(conn, cursor, statement, *args):
            statements.append(statement.lower())
        event.listen(Engine, "before_cursor_execute", capture)
        try:
            command.upgrade(config, REVISION)
        finally:
            event.remove(Engine, "before_cursor_execute", capture)
        assert existing_snapshot() == before
        assert not any("response_body" in s or "request_data" in s for s in statements)
        assert not any(s.startswith(("update ", "delete from ", "insert into test"))
                       and not s.startswith("update alembic_version ") for s in statements)
        result = analyze(evidence_pair)
        assert result.status_code == 200
        assert result.json()["finding"]["status"] == "confirmed"
        assert result.json()["finding"]["review_notes"] == "keep human review"
        after = existing_snapshot()
        assert {k:v for k,v in after.items() if k != "findings"} == {k:v for k,v in before.items() if k != "findings"}
        with SessionLocal() as db:
            assert db.get(SecurityReport, report_id).markdown_content == next(r for r in before["security_reports"] if r["id"] == report_id)["markdown_content"]
        command.downgrade(config, PARENT)
        assert existing_snapshot() == after
    finally:
        command.upgrade(config, "head")
        if report_id is not None:
            with SessionLocal() as db:
                db.execute(delete(SecurityReport).where(SecurityReport.id == report_id))
                db.commit()


def test_populated_rollback_protected_without_loss(observation_context):
    ctx, _ = observation_context
    accepted(ctx)
    before = snapshot()
    with pytest.raises(RuntimeError, match="research_observation_populated_downgrade_blocked"):
        command.downgrade(Config("alembic.ini"), PARENT)
    assert snapshot() == before
    with engine.connect() as db:
        assert MigrationContext.configure(db).get_current_revision() == "6a94cbd3f825"


def test_composite_fk_immutable_provenance_and_expiry_constraints(observation_context):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    with SessionLocal() as db:
        for statement in (ObservationPayload.__table__.update().where(ObservationPayload.observation_id == oid).values(context_id=ctx+10000),
            ObservationRecord.__table__.update().where(ObservationRecord.id == oid).values(provenance="observed_baseline"),
            ObservationRecord.__table__.update().where(ObservationRecord.id == oid).values(expires_at=NOW),
            ObservationPayload.__table__.update().where(ObservationPayload.observation_id == oid).values(canonical_payload="x"*262145)):
            with pytest.raises(IntegrityError):
                with db.begin_nested():
                    db.execute(statement)
        db.rollback()
