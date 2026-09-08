from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import DateTime, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DataError, IntegrityError

from app.core.config import settings
from app.db.models import Finding, FindingEvidenceRecord, FindingEvidenceExcerpt
from app.db.session import SessionLocal, engine
from tests.api.test_finding_structured_evidence import FACTS, old_finding
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401


REVISION = "f2b4d6e8a1c3"
PARENT = "e1a3c5d7f9b2"
TABLE = "finding_evidence_fingerprints"
VALUES = dict(algorithm="sha256", fingerprint_version="1", baseline_digest="a" * 64,
              probe_digest="b" * 64, baseline_body_bytes=0, probe_body_bytes=123)


def test_clean_postgres_round_trip_adds_only_bounded_fingerprint_table(monkeypatch):
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [REVISION]
    assert scripts.get_revision(REVISION).down_revision == PARENT
    schema = f"fingerprint_migration_{uuid4().hex}"
    with engine.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(settings.database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    isolated = create_engine(url)
    try:
        monkeypatch.setattr(settings, "database_url",
                            url.render_as_string(hide_password=False).replace("%", "%%"))
        command.upgrade(config, PARENT)
        before = set(inspect(isolated).get_table_names())
        for _ in range(2):
            command.upgrade(config, "head")
            inspector = inspect(isolated)
            assert set(inspector.get_table_names()) == before | {TABLE}
            with isolated.connect() as db:
                assert MigrationContext.configure(db).get_current_revision() == REVISION
            columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
            assert set(columns) == {"id", "finding_evidence_record_id", *VALUES, "created_at"}
            assert all(not c["nullable"] for c in columns.values())
            for name in ("algorithm", "fingerprint_version", "baseline_digest", "probe_digest"):
                kind = columns[name]["type"]
                assert isinstance(kind, String) and not isinstance(kind, Text)
                assert 0 < kind.length <= 64
                if name.endswith("digest"):
                    assert kind.length == 64
            for name in ("id", "finding_evidence_record_id", "baseline_body_bytes", "probe_body_bytes"):
                assert isinstance(columns[name]["type"], Integer)
            assert isinstance(columns["created_at"]["type"], DateTime)
            assert columns["created_at"]["type"].timezone
            assert columns["created_at"]["default"] is not None
            assert inspector.get_pk_constraint(TABLE)["constrained_columns"] == ["id"]
            fk, = inspector.get_foreign_keys(TABLE)
            assert fk["constrained_columns"] == ["finding_evidence_record_id"]
            assert fk["referred_table"] == "finding_evidence_records"
            assert fk["referred_columns"] == ["id"]
            assert fk["options"] == {"ondelete": "RESTRICT"}
            assert any(i["column_names"] == ["finding_evidence_record_id"] and i["unique"]
                       for i in inspector.get_indexes(TABLE))
            from app.db.models import FindingEvidenceFingerprint
            for column in FindingEvidenceFingerprint.__table__.columns:
                assert column.type.compile(dialect=engine.dialect) == columns[column.name]["type"].compile(dialect=engine.dialect)
            command.downgrade(config, PARENT)
            assert set(inspect(isolated).get_table_names()) == before
    finally:
        isolated.dispose()
        with engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_no_backfill_preserves_old_rows_and_database_enforces_constraints(evidence_pair):
    ids = evidence_pair
    config = Config("alembic.ini")
    try:
        command.downgrade(config, PARENT)
        finding_id = old_finding(ids)
        with SessionLocal() as db:
            evidence = FindingEvidenceRecord(**FACTS, finding_id=finding_id,
                probe_test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"])
            db.add(evidence)
            db.commit()
            evidence_id = evidence.id
            db.add(FindingEvidenceExcerpt(finding_evidence_record_id=evidence_id,
                extractor_id="bola_matched_identifier_field", extractor_version="1",
                baseline_excerpt='{"id":"[MATCHED_RESOURCE_IDENTIFIER]"}',
                probe_excerpt='{"id":"[MATCHED_RESOURCE_IDENTIFIER]"}'))
            db.commit()

        def snapshot():
            with engine.connect() as db:
                return [list(db.execute(select(model.__table__)).mappings())
                        for model in (Finding, FindingEvidenceRecord, FindingEvidenceExcerpt)]

        before = snapshot()
        old_schema = {table: [(c["name"], str(c["type"])) for c in inspect(engine).get_columns(table)]
                      for table in ("finding_evidence_records", "finding_evidence_excerpts")}
        command.upgrade(config, "head")
        assert snapshot() == before
        assert {table: [(c["name"], str(c["type"])) for c in inspect(engine).get_columns(table)]
                for table in old_schema} == old_schema
        inspector = inspect(engine)
        unique, = inspector.get_unique_constraints(TABLE)
        fk, = inspector.get_foreign_keys(TABLE)
        insert = text("""INSERT INTO finding_evidence_fingerprints
            (finding_evidence_record_id, algorithm, fingerprint_version, baseline_digest, probe_digest,
             baseline_body_bytes, probe_body_bytes)
            VALUES (:evidence, :algorithm, :fingerprint_version, :baseline_digest, :probe_digest,
                    :baseline_body_bytes, :probe_body_bytes)""")
        values = {**VALUES, "evidence": evidence_id}
        with engine.begin() as db:
            assert db.scalar(text("SELECT count(*) FROM finding_evidence_fingerprints")) == 0
            with pytest.raises(IntegrityError) as missing:
                with db.begin_nested():
                    db.execute(insert, {**values, "evidence": 999999999})
            assert missing.value.orig.diag.constraint_name == fk["name"]
            for field, invalid in (
                ("algorithm", "md5"), ("fingerprint_version", "2"),
                ("baseline_digest", "a" * 63), ("probe_digest", "b" * 63),
                ("baseline_digest", "A" * 64), ("probe_digest", "g" * 64),
                ("baseline_body_bytes", -1), ("probe_body_bytes", -1),
            ):
                with pytest.raises(IntegrityError):
                    with db.begin_nested():
                        db.execute(insert, {**values, field: invalid})
            for field in ("baseline_digest", "probe_digest"):
                with pytest.raises(DataError):
                    with db.begin_nested():
                        db.execute(insert, {**values, field: "a" * 65})
            db.execute(insert, values)
            with pytest.raises(IntegrityError) as duplicate:
                with db.begin_nested():
                    db.execute(insert, values)
            assert duplicate.value.orig.diag.constraint_name == unique["name"]
            # Isolate the new FK from the excerpt's independent RESTRICT FK.
            with db.begin_nested() as detach:
                db.execute(text("DELETE FROM finding_evidence_excerpts WHERE finding_evidence_record_id = :evidence"), values)
                with pytest.raises(IntegrityError) as restricted:
                    with db.begin_nested():
                        db.execute(text("DELETE FROM finding_evidence_records WHERE id = :evidence"), values)
                assert restricted.value.orig.diag.constraint_name == fk["name"]
                detach.rollback()
        command.downgrade(config, PARENT)
        assert snapshot() == before
        command.upgrade(config, "head")
        assert snapshot() == before
        with engine.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM finding_evidence_fingerprints")) == 0
    finally:
        command.upgrade(config, "head")
