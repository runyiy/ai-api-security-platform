"""PostgreSQL schema, deterministic backfill, and policy DB authority."""
from dataclasses import asdict
import re
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, event, inspect, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.base import Base
from app.db.models import FindingEvidenceFingerprint, FindingEvidenceSimilarity
from app.db.session import SessionLocal, engine
from tests.api.test_finding_evidence_retention import VALUES, TABLE, retention_rows, binding_model
from tests.api.test_finding_structured_evidence import analyze
from tests.api.test_finding_evidence_fingerprints import old_evidence, typed_result
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401

REVISION = "b5d7f9a1c3e6"
PARENT = "a3c5e7f9b2d4"


def test_clean_postgres_round_trip_creates_only_exact_retention_table(monkeypatch):
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["e9a1c3d5f7b8"]
    assert scripts.get_revision(REVISION).down_revision == PARENT
    schema = f"retention_migration_{uuid4().hex}"
    with engine.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(settings.database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    isolated = create_engine(url)
    try:
        monkeypatch.setattr(settings, "database_url", url.render_as_string(hide_password=False).replace("%", "%%"))
        command.upgrade(config, PARENT)
        before = set(inspect(isolated).get_table_names())
        for _ in range(2):
            command.upgrade(config, REVISION)
            inspector = inspect(isolated)
            assert set(inspector.get_table_names()) == before | {TABLE}
            with isolated.connect() as db:
                assert MigrationContext.configure(db).get_current_revision() == REVISION
            columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
            assert set(columns) == {"id", "finding_evidence_record_id", *VALUES, "bound_at"}
            assert all(not c["nullable"] for c in columns.values())
            for name in ("policy_id", "policy_version", "retention_mode"):
                kind = columns[name]["type"]
                assert isinstance(kind, String) and not isinstance(kind, Text)
                assert 0 < kind.length <= 64
            for name in ("id", "finding_evidence_record_id"):
                assert isinstance(columns[name]["type"], Integer)
            for name in ("automatic_deletion_enabled", "raw_response_body_retained"):
                assert isinstance(columns[name]["type"], Boolean)
            assert isinstance(columns["bound_at"]["type"], DateTime)
            assert columns["bound_at"]["type"].timezone
            assert columns["bound_at"]["default"] is not None
            assert inspector.get_pk_constraint(TABLE)["constrained_columns"] == ["id"]
            fk, = inspector.get_foreign_keys(TABLE)
            assert fk["constrained_columns"] == ["finding_evidence_record_id"]
            assert fk["referred_table"] == "finding_evidence_records"
            assert fk["referred_columns"] == ["id"]
            assert fk["options"] == {"ondelete": "RESTRICT"}
            assert any(i["column_names"] == ["finding_evidence_record_id"] and i["unique"] for i in inspector.get_indexes(TABLE))
            assert len(inspector.get_check_constraints(TABLE)) == 5
            for column in binding_model().__table__.columns:
                assert column.type.compile(dialect=engine.dialect) == columns[column.name]["type"].compile(dialect=engine.dialect)
            command.downgrade(config, PARENT)
            assert set(inspect(isolated).get_table_names()) == before
    finally:
        isolated.dispose()
        with engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_deterministic_backfill_preserves_all_old_rows_and_reanalysis(evidence_pair):
    config = Config("alembic.ini")
    ids = evidence_pair
    try:
        command.downgrade(config, PARENT)
        _, evidence_id = old_evidence(ids)
        # A second historical structured record has no derived fingerprint/similarity.
        old_evidence({**ids, "probe": ids["decoy"]})
        result = typed_result(ids)
        with SessionLocal() as db:
            fingerprint = FindingEvidenceFingerprint(finding_evidence_record_id=evidence_id,
                **asdict(result.fingerprint_evidence))
            db.add(fingerprint)
            db.flush()
            db.add(FindingEvidenceSimilarity(finding_evidence_fingerprint_id=fingerprint.id,
                **asdict(result.similarity_evidence)))
            db.commit()
        def old_snapshot():
            with engine.connect() as db:
                return {table.name: list(db.execute(select(table).order_by(*table.primary_key.columns)).mappings())
                        for table in Base.metadata.sorted_tables if table.name in inspect(engine).get_table_names() and table.name != TABLE}
        before = old_snapshot()
        before_schema = {name: inspect(engine).get_columns(name) for name in before}
        # Audit executed upgrade SQL: the sole data read must be the evidence ID.
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.lower())
        for _ in range(2):
            event.listen(Engine, "before_cursor_execute", capture)
            try:
                command.upgrade(config, REVISION)
            finally:
                event.remove(Engine, "before_cursor_execute", capture)
            assert old_snapshot() == before
            for name, columns in before_schema.items():
                assert [(c["name"], str(c["type"]), c["nullable"], c["default"]) for c in inspect(engine).get_columns(name)] == [
                    (c["name"], str(c["type"]), c["nullable"], c["default"]) for c in columns]
            with engine.connect() as db:
                bindings = list(db.execute(select(binding_model().__table__)).mappings())
            assert len(bindings) == len(before["finding_evidence_records"])
            assert {r["finding_evidence_record_id"] for r in bindings} == {r["id"] for r in before["finding_evidence_records"]}
            for row in bindings:
                assert {k: row[k] for k in VALUES} == VALUES
                assert row["bound_at"].tzinfo is not None
            assert not any(re.search(r"\b" + token + r"\b", sql) for sql in statements for token in (
                "test_runs", "resources", "response_body", "request_data", "finding_evidence_excerpts",
                "finding_evidence_fingerprints", "finding_evidence_similarities"))
            data_sql = [sql for sql in statements if "insert into finding_evidence_retention_bindings" in sql]
            assert len(data_sql) == _ + 1
            assert all("finding_evidence_records.id" in sql and "select" in sql for sql in data_sql)
            assert not any("update findings" in sql or "delete from" in sql for sql in statements)
            if _ == 0:
                command.downgrade(config, PARENT)
                assert old_snapshot() == before
        original = retention_rows(ids)
        for _ in range(3):
            response = analyze(ids)
            assert response.status_code == 200
            assert response.json()["finding"]["status"] == "confirmed"
            assert response.json()["finding"]["review_notes"] == "keep human review"
            assert retention_rows(ids) == original
            after = old_snapshot()
            assert {k: v for k, v in after.items() if k != "findings"} == {k: v for k, v in before.items() if k != "findings"}
    finally:
        command.upgrade(config, "head")


def test_database_enforces_exact_constants_unique_and_restrict(evidence_pair):
    from sqlalchemy import insert
    model = binding_model()
    _, evidence_id = old_evidence(evidence_pair)
    # Leave only the parent so the RESTRICT error can only be this new FK.
    with engine.begin() as db:
        db.execute(text("DELETE FROM finding_evidence_excerpts WHERE finding_evidence_record_id = :id"), {"id": evidence_id})
    values = dict(finding_evidence_record_id=evidence_id, **VALUES)
    inspector = inspect(engine)
    unique, = inspector.get_unique_constraints(TABLE)
    fk, = inspector.get_foreign_keys(TABLE)
    with engine.begin() as db:
        with pytest.raises(IntegrityError) as missing:
            with db.begin_nested():
                db.execute(insert(model).values(**{**values, "finding_evidence_record_id": 999999999}))
        assert missing.value.orig.diag.constraint_name == fk["name"]
        for field in VALUES:
            for invalid in (None, True if isinstance(VALUES[field], bool) else "other"):
                with pytest.raises(IntegrityError) as rejected:
                    with db.begin_nested():
                        db.execute(insert(model).values(**{**values, field: invalid}))
                if invalid is not None:
                    assert rejected.value.orig.diag.constraint_name.startswith("ck_finding_evidence_retention_")
        db.execute(insert(model).values(**values))
        with pytest.raises(IntegrityError) as duplicate:
            with db.begin_nested():
                db.execute(insert(model).values(**values))
        assert duplicate.value.orig.diag.constraint_name == unique["name"]
        with pytest.raises(IntegrityError) as restricted:
            with db.begin_nested():
                db.execute(text("DELETE FROM finding_evidence_records WHERE id = :id"), {"id": evidence_id})
        assert restricted.value.orig.diag.constraint_name == fk["name"]
