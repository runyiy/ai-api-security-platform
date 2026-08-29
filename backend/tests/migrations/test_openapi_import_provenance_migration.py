from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from app.db.session import engine


REVISION = "c4e6a8b0d2f4"
PARENT = "a2c4e6f8b0d2"


def current_revision():
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_openapi_import_provenance_schema_and_round_trip() -> None:
    config = Config("alembic.ini")
    try:
        command.downgrade(config, PARENT)
        assert "openapi_import_records" not in inspect(engine).get_table_names()
        command.upgrade(config, REVISION)
        assert current_revision() == REVISION
        inspector = inspect(engine)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("openapi_import_records")
        }
        assert set(columns) == {
            "id", "target_id", "source_url", "document_sha256",
            "document_size_bytes", "discovered_endpoint_count", "fetched_at",
        }
        assert columns["source_url"]["type"].length == 2048
        assert columns["document_sha256"]["type"].length == 64
        assert columns["fetched_at"]["type"].timezone is True
        assert inspector.get_pk_constraint("openapi_import_records")[
            "constrained_columns"
        ] == ["id"]
        foreign_key = inspector.get_foreign_keys("openapi_import_records")[0]
        assert foreign_key["referred_table"] == "targets"
        assert foreign_key["options"]["ondelete"] == "RESTRICT"
        with engine.connect() as db:
            assert db.scalar(text(
                "SELECT count(*) FROM openapi_import_records"
            )) == 0
    finally:
        command.upgrade(config, "head")
