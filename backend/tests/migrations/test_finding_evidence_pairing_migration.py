from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.session import engine
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401


REVISION = "c7e9a1b3d5f6"
PARENT = "b6d8f0a2c4e5"


def test_clean_postgres_upgrade_downgrade_upgrade(monkeypatch):
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["c7e9a1b3d5f7"]
    assert scripts.get_revision(REVISION).down_revision == PARENT
    schema = f"finding_migration_{uuid4().hex}"
    with engine.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(settings.database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    isolated = create_engine(url)
    try:
        # Alembic passes this URL through ConfigParser interpolation.
        monkeypatch.setattr(settings, "database_url",
                            url.render_as_string(hide_password=False).replace("%", "%%"))
        command.upgrade(config, "head")
        with isolated.connect() as db:
            assert MigrationContext.configure(db).get_current_revision() == "c7e9a1b3d5f7"
        command.downgrade(config, PARENT)
        assert "baseline_test_run_id" not in {
            column["name"] for column in inspect(isolated).get_columns("findings")
        }
        command.upgrade(config, "head")
        with isolated.connect() as db:
            assert MigrationContext.configure(db).get_current_revision() == "c7e9a1b3d5f7"
    finally:
        isolated.dispose()
        with engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_finding_pair_migration_preserves_legacy_and_restricts_baseline(evidence_pair):
    ids = evidence_pair
    config = Config("alembic.ini")
    try:
        command.downgrade(config, PARENT)
        old_columns = inspect(engine).get_columns("findings")
        old_indexes = inspect(engine).get_indexes("findings")
        old_fks = inspect(engine).get_foreign_keys("findings")
        with engine.begin() as db:
            ids["finding"] = db.scalar(text("""
                INSERT INTO findings
                    (target_id, endpoint_id, test_run_id, category, severity,
                     confidence, status, title, description, review_notes)
                VALUES (:target, :endpoint, :probe, 'BOLA', 'high', 0.9,
                        'confirmed', 'legacy title', 'legacy description', 'keep')
                RETURNING id
            """), ids)
            before = dict(db.execute(text("SELECT * FROM findings WHERE id = :finding"), ids).mappings().one())
        command.upgrade(config, REVISION)
        inspector = inspect(engine)
        column = next(c for c in inspector.get_columns("findings") if c["name"] == "baseline_test_run_id")
        assert column["nullable"] is True
        fk = next(f for f in inspector.get_foreign_keys("findings")
                  if f["constrained_columns"] == ["baseline_test_run_id"])
        assert fk["referred_table"] == "test_runs"
        assert fk["referred_columns"] == ["id"]
        assert fk["options"] == {"ondelete": "RESTRICT"}
        assert any(i["column_names"] == ["baseline_test_run_id"] and not i["unique"]
                   for i in inspector.get_indexes("findings"))
        with engine.begin() as db:
            after = dict(db.execute(text("SELECT * FROM findings WHERE id = :finding"), ids).mappings().one())
            assert after == {**before, "baseline_test_run_id": None}
            assert db.scalar(text("SELECT count(*) FROM findings WHERE target_id = :target"), ids) == 1
            db.execute(text("UPDATE findings SET baseline_test_run_id = :baseline WHERE id = :finding"), ids)
        with pytest.raises(IntegrityError) as missing:
            with engine.begin() as db:
                db.execute(text("UPDATE findings SET baseline_test_run_id = 999999999 WHERE id = :finding"), ids)
        assert missing.value.orig.diag.constraint_name == fk["name"]
        with pytest.raises(IntegrityError) as restricted:
            with engine.begin() as db:
                db.execute(text("DELETE FROM test_runs WHERE id = :baseline"), ids)
        assert restricted.value.orig.diag.constraint_name == fk["name"]
        command.downgrade(config, PARENT)
        # Only this revision's column/index/FK disappear.
        inspector = inspect(engine)
        assert [c["name"] for c in inspector.get_columns("findings")] == [c["name"] for c in old_columns]
        assert inspector.get_indexes("findings") == old_indexes
        assert inspector.get_foreign_keys("findings") == old_fks
        with engine.connect() as db:
            assert dict(db.execute(text("SELECT * FROM findings WHERE id = :finding"), ids).mappings().one()) == before
        command.upgrade(config, REVISION)
        with engine.connect() as db:
            assert dict(db.execute(text("SELECT * FROM findings WHERE id = :finding"), ids).mappings().one()) == {
                **before, "baseline_test_run_id": None,
            }
    finally:
        command.upgrade(config, "head")
