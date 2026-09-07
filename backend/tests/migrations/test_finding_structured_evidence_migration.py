from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.session import engine
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401


REVISION = "d9f1b3c5e7a8"
PARENT = "c7e9a1b3d5f6"
TABLE = "finding_evidence_records"


def test_clean_migration_adds_only_fixed_shape_evidence_table(monkeypatch):
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [REVISION]
    assert scripts.get_revision(REVISION).down_revision == PARENT
    schema = f"structured_evidence_{uuid4().hex}"
    with engine.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(settings.database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    isolated = create_engine(url)
    try:
        monkeypatch.setattr(settings, "database_url",
                            url.render_as_string(hide_password=False).replace("%", "%%"))
        command.upgrade(config, PARENT)
        before = set(inspect(isolated).get_table_names())
        command.upgrade(config, "head")
        inspector = inspect(isolated)
        assert set(inspector.get_table_names()) == before | {TABLE}
        columns = {column["name"]: column for column in inspector.get_columns(TABLE)}
        assert set(columns) == {
            "id", "finding_id", "probe_test_run_id", "baseline_test_run_id",
            "evidence_type", "rule_id", "rule_version", "reason_code",
            "baseline_status_code", "probe_status_code",
            "baseline_resource_identifier_present", "probe_resource_identifier_present", "created_at",
        }
        assert all(not column["nullable"] for column in columns.values())
        for name in ("evidence_type", "rule_id", "rule_version", "reason_code"):
            assert isinstance(columns[name]["type"], String)
            assert not isinstance(columns[name]["type"], Text)
            assert 0 < columns[name]["type"].length <= 128
        for name in ("id", "finding_id", "probe_test_run_id", "baseline_test_run_id",
                     "baseline_status_code", "probe_status_code"):
            assert isinstance(columns[name]["type"], Integer)
        for name in ("baseline_resource_identifier_present", "probe_resource_identifier_present"):
            assert isinstance(columns[name]["type"], Boolean)
        assert isinstance(columns["created_at"]["type"], DateTime)
        assert columns["created_at"]["type"].timezone
        assert columns["created_at"]["default"] is not None
        fks = {fk["constrained_columns"][0]: fk for fk in inspector.get_foreign_keys(TABLE)}
        assert set(fks) == {"finding_id", "probe_test_run_id", "baseline_test_run_id"}
        for name, fk in fks.items():
            assert fk["referred_table"] == ("findings" if name == "finding_id" else "test_runs")
            assert fk["referred_columns"] == ["id"]
            assert fk["options"] == {"ondelete": "RESTRICT"}
        assert {tuple(index["column_names"]) for index in inspector.get_indexes(TABLE)} >= {
            ("baseline_test_run_id",), ("probe_test_run_id",),
        }
        command.downgrade(config, PARENT)
        assert set(inspect(isolated).get_table_names()) == before
        command.upgrade(config, REVISION)
        assert set(inspect(isolated).get_table_names()) == before | {TABLE}
    finally:
        isolated.dispose()
        with engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.mark.parametrize("exact", [False, True])
def test_no_backfill_and_database_constraints(evidence_pair, exact):
    ids = evidence_pair
    config = Config("alembic.ini")
    insert = text("""
        INSERT INTO finding_evidence_records
            (finding_id, probe_test_run_id, baseline_test_run_id,
             evidence_type, rule_id, rule_version, reason_code,
             baseline_status_code, probe_status_code,
             baseline_resource_identifier_present, probe_resource_identifier_present)
        VALUES (:finding, :probe, :baseline, 'bola_resource_identifier_pair',
                'bola_resource_identifier_presence', '1',
                'baseline_and_probe_contain_target_resource_identifier', 200, 200, true, true)
    """)
    try:
        command.downgrade(config, PARENT)
        with engine.begin() as db:
            ids["finding_run"] = ids["probe"] if exact else ids["decoy"]
            ids["finding_baseline"] = ids["baseline"] if exact else None
            ids["finding"] = db.scalar(text("""
                INSERT INTO findings
                    (target_id, endpoint_id, test_run_id, baseline_test_run_id,
                     category, severity, confidence, status, title, description, review_notes)
                VALUES (:target, :endpoint, :finding_run, :finding_baseline, 'BOLA', 'high', 0.99,
                        'confirmed', 'legacy', 'legacy description', 'keep review') RETURNING id
            """), ids)
            before = dict(db.execute(text("SELECT * FROM findings WHERE id = :finding"), ids).mappings().one())
        command.upgrade(config, REVISION)
        inspector = inspect(engine)
        unique = next(c["name"] for c in inspector.get_unique_constraints(TABLE)
                      if c["column_names"] == ["finding_id"])
        check = next(c["name"] for c in inspector.get_check_constraints(TABLE)
                     if "baseline_test_run_id" in c["sqltext"] and "probe_test_run_id" in c["sqltext"])
        fks = {fk["constrained_columns"][0]: fk["name"] for fk in inspector.get_foreign_keys(TABLE)}
        with engine.begin() as db:
            assert dict(db.execute(text("SELECT * FROM findings WHERE id = :finding"), ids).mappings().one()) == before
            assert db.scalar(text("SELECT count(*) FROM finding_evidence_records")) == 0
            with pytest.raises(IntegrityError) as same_run:
                with db.begin_nested():
                    db.execute(insert, {**ids, "baseline": ids["probe"]})
            assert same_run.value.orig.diag.constraint_name == check
            db.execute(insert, ids)
            with pytest.raises(IntegrityError) as duplicate:
                with db.begin_nested():
                    db.execute(insert, ids)
            assert duplicate.value.orig.diag.constraint_name == unique
            # Temporarily detach the Finding's own run FKs to prove the
            # evidence FKs enforce RESTRICT independently; restore afterwards.
            with db.begin_nested() as detach:
                db.execute(text("UPDATE findings SET test_run_id = :decoy, baseline_test_run_id = NULL WHERE id = :finding"), ids)
                for field, table, identifier in (
                    ("finding_id", "findings", ids["finding"]),
                    ("probe_test_run_id", "test_runs", ids["probe"]),
                    ("baseline_test_run_id", "test_runs", ids["baseline"]),
                ):
                    with pytest.raises(IntegrityError) as restricted:
                        with db.begin_nested():
                            db.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": identifier})
                    assert restricted.value.orig.diag.constraint_name == fks[field]
                    with pytest.raises(IntegrityError) as missing:
                        with db.begin_nested():
                            db.execute(text(f"UPDATE finding_evidence_records SET {field} = 999999999 WHERE finding_id = :finding"), ids)
                    assert missing.value.orig.diag.constraint_name == fks[field]
                detach.rollback()
        command.downgrade(config, PARENT)
        with engine.connect() as db:
            assert dict(db.execute(text("SELECT * FROM findings WHERE id = :finding"), ids).mappings().one()) == before
        command.upgrade(config, REVISION)
        with engine.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM finding_evidence_records")) == 0
    finally:
        command.upgrade(config, "head")
