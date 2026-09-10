from dataclasses import asdict
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.models import Finding, FindingEvidenceRecord, FindingEvidenceExcerpt, FindingEvidenceFingerprint
from app.db.session import SessionLocal, engine
from tests.api.test_finding_evidence_fingerprints import old_evidence, typed_result
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401

REVISION = "a3c5e7f9b2d4"
PARENT = "f2b4d6e8a1c3"
TABLE = "finding_evidence_similarities"
VALUES = dict(comparator_id="sha256_exact_and_length_ratio", comparator_version="1",
              exact_digest_match=False, length_similarity_bps=3333)


def test_clean_postgres_round_trip_adds_only_bounded_similarity_table(monkeypatch):
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["a1c3e5f7b9d0"]
    assert scripts.get_revision(REVISION).down_revision == PARENT
    schema = f"similarity_migration_{uuid4().hex}"
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
            command.upgrade(config, REVISION)
            inspector = inspect(isolated)
            assert set(inspector.get_table_names()) == before | {TABLE}
            with isolated.connect() as db:
                assert MigrationContext.configure(db).get_current_revision() == REVISION
            columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
            assert set(columns) == {"id", "finding_evidence_fingerprint_id", *VALUES, "created_at"}
            assert all(not c["nullable"] for c in columns.values())
            for name in ("comparator_id", "comparator_version"):
                kind = columns[name]["type"]
                assert isinstance(kind, String) and not isinstance(kind, Text)
                assert 0 < kind.length <= 64
            for name in ("id", "finding_evidence_fingerprint_id", "length_similarity_bps"):
                assert isinstance(columns[name]["type"], Integer)
            assert isinstance(columns["exact_digest_match"]["type"], Boolean)
            assert isinstance(columns["created_at"]["type"], DateTime)
            assert columns["created_at"]["type"].timezone
            assert columns["created_at"]["default"] is not None
            assert inspector.get_pk_constraint(TABLE)["constrained_columns"] == ["id"]
            fk, = inspector.get_foreign_keys(TABLE)
            assert fk["constrained_columns"] == ["finding_evidence_fingerprint_id"]
            assert fk["referred_table"] == "finding_evidence_fingerprints"
            assert fk["referred_columns"] == ["id"]
            assert fk["options"] == {"ondelete": "RESTRICT"}
            assert any(i["column_names"] == ["finding_evidence_fingerprint_id"] and i["unique"]
                       for i in inspector.get_indexes(TABLE))
            from app.db.models import FindingEvidenceSimilarity
            for column in FindingEvidenceSimilarity.__table__.columns:
                assert column.type.compile(dialect=engine.dialect) == columns[column.name]["type"].compile(dialect=engine.dialect)
            command.downgrade(config, PARENT)
            assert set(inspect(isolated).get_table_names()) == before
    finally:
        isolated.dispose()
        with engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_no_backfill_old_rows_unchanged_and_database_constraints(evidence_pair):
    config = Config("alembic.ini")
    try:
        command.downgrade(config, PARENT)
        _, evidence_id = old_evidence(evidence_pair)
        with SessionLocal() as db:
            fingerprint = FindingEvidenceFingerprint(finding_evidence_record_id=evidence_id,
                **asdict(typed_result(evidence_pair).fingerprint_evidence))
            db.add(fingerprint)
            db.commit()
            fingerprint_id = fingerprint.id
        models = (Finding, FindingEvidenceRecord, FindingEvidenceExcerpt, FindingEvidenceFingerprint)
        def snapshot():
            with engine.connect() as db:
                return [list(db.execute(select(model.__table__)).mappings()) for model in models]
        before = snapshot()
        old_schema = {m.__tablename__: [(c["name"], str(c["type"])) for c in inspect(engine).get_columns(m.__tablename__)]
                      for m in models}
        command.upgrade(config, "head")
        assert snapshot() == before
        assert {table: [(c["name"], str(c["type"])) for c in inspect(engine).get_columns(table)]
                for table in old_schema} == old_schema
        inspector = inspect(engine)
        unique, = inspector.get_unique_constraints(TABLE)
        fk, = inspector.get_foreign_keys(TABLE)
        assert len(inspector.get_check_constraints(TABLE)) == 3
        insert = text("""INSERT INTO finding_evidence_similarities
            (finding_evidence_fingerprint_id, comparator_id, comparator_version, exact_digest_match, length_similarity_bps)
            VALUES (:fingerprint, :comparator_id, :comparator_version, :exact_digest_match, :length_similarity_bps)""")
        values = {**VALUES, "fingerprint": fingerprint_id}
        with engine.begin() as db:
            assert db.scalar(text("SELECT count(*) FROM finding_evidence_similarities")) == 0
            with pytest.raises(IntegrityError) as missing:
                with db.begin_nested():
                    db.execute(insert, {**values, "fingerprint": 999999999})
            assert missing.value.orig.diag.constraint_name == fk["name"]
            for field, invalid in (("comparator_id", "other"), ("comparator_version", "2"),
                                   ("length_similarity_bps", -1), ("length_similarity_bps", 10001)):
                with pytest.raises(IntegrityError):
                    with db.begin_nested():
                        db.execute(insert, {**values, field: invalid})
            for boundary in (0, 10000):
                with db.begin_nested() as attempt:
                    db.execute(insert, {**values, "length_similarity_bps": boundary})
                    attempt.rollback()
            db.execute(insert, values)
            with pytest.raises(IntegrityError) as duplicate:
                with db.begin_nested():
                    db.execute(insert, values)
            assert duplicate.value.orig.diag.constraint_name == unique["name"]
            with pytest.raises(IntegrityError) as restricted:
                with db.begin_nested():
                    db.execute(text("DELETE FROM finding_evidence_fingerprints WHERE id = :fingerprint"), values)
            assert restricted.value.orig.diag.constraint_name == fk["name"]
        command.downgrade(config, PARENT)
        assert snapshot() == before
        command.upgrade(config, "head")
        assert snapshot() == before
        with engine.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM finding_evidence_similarities")) == 0
    finally:
        command.upgrade(config, "head")
