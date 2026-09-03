import hashlib

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.db.session import engine


REVISION = "e9a1c3f5b7d9"
PARENT = "d8f0b2c4e6a9"
HEAD = "a4c6e8b0d2f3"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_openapi_credential_provenance_migration_round_trip() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision(REVISION).down_revision == PARENT
    assert scripts.get_heads() == [HEAD]
    digest = hashlib.sha256(b'{"paths":{}}').hexdigest()
    target_id = identity_id = binding_id = import_record_id = None
    try:
        command.downgrade(config, PARENT)
        assert current_revision() == PARENT
        with engine.begin() as db:
            target_id = db.scalar(text("""
                INSERT INTO targets (name, base_url, environment, is_enabled)
                VALUES ('m9-04 migration target', 'https://migration.test',
                        'test', true)
                RETURNING id
            """))
            identity_id = db.scalar(text("""
                INSERT INTO test_identities
                    (target_id, name, role, auth_type, credentials, is_active)
                VALUES (:target_id, 'docs', 'docs', 'bearer', NULL, true)
                RETURNING id
            """), {"target_id": target_id})
            binding_id = db.scalar(text("""
                INSERT INTO credential_bindings
                    (test_identity_id, auth_type, source_type, is_active)
                VALUES (:identity_id, 'bearer', 'stored_secret', true)
                RETURNING id
            """), {"identity_id": identity_id})
            import_record_id = db.scalar(text("""
                INSERT INTO openapi_import_records
                    (target_id, source_url, document_sha256,
                     document_size_bytes, content_encoding,
                     decoded_document_sha256, decoded_document_size_bytes,
                     discovered_endpoint_count)
                VALUES (:target_id, 'https://migration.test/openapi.json',
                        :digest, 12, 'identity', :digest, 12, 0)
                RETURNING id
            """), {"target_id": target_id, "digest": digest})

        command.upgrade(config, REVISION)
        columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("openapi_import_records")
        }
        assert columns["credential_binding_id"]["nullable"] is True
        assert str(columns["credential_binding_id"]["type"]) == "INTEGER"
        foreign_keys = {
            foreign_key["name"]: foreign_key
            for foreign_key in inspect(engine).get_foreign_keys(
                "openapi_import_records"
            )
        }
        foreign_key = foreign_keys[
            "fk_openapi_import_records_credential_binding_id"
        ]
        assert foreign_key["constrained_columns"] == ["credential_binding_id"]
        assert foreign_key["referred_table"] == "credential_bindings"
        assert foreign_key["referred_columns"] == ["id"]
        assert foreign_key["options"]["ondelete"] == "RESTRICT"
        with engine.begin() as db:
            assert db.scalar(text("""
                SELECT credential_binding_id FROM openapi_import_records
                WHERE id = :id
            """), {"id": import_record_id}) is None
            db.execute(text("""
                UPDATE openapi_import_records SET credential_binding_id = :binding_id
                WHERE id = :id
            """), {"binding_id": binding_id, "id": import_record_id})

        command.downgrade(config, PARENT)
        assert "credential_binding_id" not in {
            column["name"]
            for column in inspect(engine).get_columns("openapi_import_records")
        }
    finally:
        command.upgrade(config, "head")
        with engine.begin() as db:
            if import_record_id is not None:
                db.execute(text(
                    "DELETE FROM openapi_import_records WHERE id = :id"
                ), {"id": import_record_id})
            if binding_id is not None:
                db.execute(text(
                    "DELETE FROM credential_bindings WHERE id = :id"
                ), {"id": binding_id})
            if identity_id is not None:
                db.execute(text(
                    "DELETE FROM test_identities WHERE id = :id"
                ), {"id": identity_id})
            if target_id is not None:
                db.execute(text("DELETE FROM targets WHERE id = :id"), {
                    "id": target_id
                })
