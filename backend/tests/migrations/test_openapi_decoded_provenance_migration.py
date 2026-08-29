import hashlib

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.db.session import engine


REVISION = "d8f0b2c4e6a9"
PARENT = "c4e6a8b0d2f4"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_decoded_provenance_migration_backfill_and_round_trip() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision(REVISION).down_revision == PARENT
    assert scripts.get_heads() == [REVISION]
    wire = b'{"paths":{}}'
    digest = hashlib.sha256(wire).hexdigest()
    source_url = "https://migration.test/m9-03.json"
    try:
        command.downgrade(config, PARENT)
        assert current_revision() == PARENT
        with engine.begin() as db:
            target_id = db.scalar(text("SELECT id FROM targets ORDER BY id LIMIT 1"))
            assert target_id is not None
            db.execute(text("""
                INSERT INTO openapi_import_records
                    (target_id, source_url, document_sha256,
                     document_size_bytes, discovered_endpoint_count)
                VALUES
                    (:target_id, :source_url, :digest, :size, 0)
            """), {
                "target_id": target_id,
                "source_url": source_url,
                "digest": digest,
                "size": len(wire),
            })

        command.upgrade(config, REVISION)
        assert current_revision() == REVISION
        inspector = inspect(engine)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("openapi_import_records")
        }
        assert columns["content_encoding"]["nullable"] is False
        assert columns["content_encoding"]["type"].length == 8
        assert columns["decoded_document_sha256"]["nullable"] is False
        assert columns["decoded_document_sha256"]["type"].length == 64
        assert columns["decoded_document_size_bytes"]["nullable"] is False
        constraints = {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "openapi_import_records"
            )
        }
        assert {
            "ck_openapi_import_records_content_encoding",
            "ck_openapi_import_records_decoded_sha256",
            "ck_openapi_import_records_decoded_size",
        } <= constraints
        with engine.begin() as db:
            row = db.execute(text("""
                SELECT content_encoding, decoded_document_sha256,
                       decoded_document_size_bytes
                FROM openapi_import_records
                WHERE source_url = :source_url
            """), {"source_url": source_url}).one()
            assert row == ("identity", digest, len(wire))
            db.execute(text(
                "DELETE FROM openapi_import_records WHERE source_url = :source_url"
            ), {"source_url": source_url})

        command.downgrade(config, PARENT)
        parent_columns = {
            column["name"]
            for column in inspect(engine).get_columns("openapi_import_records")
        }
        assert "content_encoding" not in parent_columns
        assert "decoded_document_sha256" not in parent_columns
        assert "decoded_document_size_bytes" not in parent_columns
    finally:
        command.upgrade(config, "head")
