from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine


REVISION = "e2a4c6e8b0d3"
PARENT = "d0f2a4c6e8b1"


def test_endpoint_resource_binding_migration_contract_and_round_trip() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision(REVISION).down_revision == PARENT
    assert scripts.get_heads() == ["b6d8f0a2c4e5"]
    target_id = endpoint_id = None
    try:
        command.downgrade(config, PARENT)
        parent_tables = set(inspect(engine).get_table_names())
        assert "endpoint_resource_bindings" not in parent_tables
        with engine.begin() as db:
            endpoint_count = db.scalar(text("SELECT count(*) FROM endpoints"))
        command.upgrade(config, REVISION)
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) - parent_tables == {
            "endpoint_resource_bindings"
        }
        columns = {item["name"]: item for item in inspector.get_columns(
            "endpoint_resource_bindings"
        )}
        assert set(columns) == {
            "id", "endpoint_id", "location", "selector", "provenance",
            "confidence", "review_state", "created_at",
        }
        assert columns["endpoint_id"]["nullable"] is False
        assert columns["selector"]["type"].length == 500
        assert columns["confidence"]["type"].python_type is int
        assert columns["created_at"]["default"] is not None
        assert {item["name"] for item in inspector.get_check_constraints(
            "endpoint_resource_bindings"
        )} == {
            "ck_endpoint_resource_bindings_location",
            "ck_endpoint_resource_bindings_selector_length",
            "ck_endpoint_resource_bindings_provenance",
            "ck_endpoint_resource_bindings_confidence",
            "ck_endpoint_resource_bindings_review_state",
        }
        fk = inspector.get_foreign_keys("endpoint_resource_bindings")
        assert len(fk) == 1
        assert fk[0]["referred_table"] == "endpoints"
        assert fk[0]["options"] == {"ondelete": "RESTRICT"}
        assert "ix_endpoint_resource_bindings_endpoint_id" in {
            item["name"] for item in inspector.get_indexes(
                "endpoint_resource_bindings"
            )
        }
        with engine.begin() as db:
            assert db.scalar(text(
                "SELECT count(*) FROM endpoint_resource_bindings"
            )) == 0
            assert db.scalar(text("SELECT count(*) FROM endpoints")) == endpoint_count
            target_id = db.scalar(text("""
                INSERT INTO targets
                    (name, base_url, environment, network_mode, is_enabled)
                VALUES ('m11 migration', 'https://m11.example.test', 'test',
                        'private_local', true) RETURNING id
            """))
            endpoint_id = db.scalar(text("""
                INSERT INTO endpoints
                    (target_id, path, method, requires_auth, parameters)
                VALUES (:target, '/orders/{id}', 'GET', false, '[]'::jsonb)
                RETURNING id
            """), {"target": target_id})
            db.execute(text("""
                INSERT INTO endpoint_resource_bindings
                    (endpoint_id, location, selector, provenance, confidence,
                     review_state)
                VALUES (:endpoint, 'body', '/order/id', 'operator_supplied',
                        100, 'candidate')
            """), {"endpoint": endpoint_id})
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text("DELETE FROM endpoints WHERE id = :id"), {
                    "id": endpoint_id
                })
        invalid_rows = (
            ("header", "/id", "operator_supplied", 50, "candidate"),
            ("body", "", "operator_supplied", 50, "candidate"),
            ("body", "/id", "caller_inferred", 50, "candidate"),
            ("body", "/id", "operator_supplied", -1, "candidate"),
            ("body", "/id", "operator_supplied", 101, "candidate"),
            ("body", "/id", "operator_supplied", 50, "approved"),
        )
        for row in invalid_rows:
            with pytest.raises(IntegrityError):
                with engine.begin() as db:
                    db.execute(text("""
                        INSERT INTO endpoint_resource_bindings
                            (endpoint_id, location, selector, provenance,
                             confidence, review_state)
                        VALUES (:endpoint, :location, :selector, :provenance,
                                :confidence, :review_state)
                    """), dict(
                        endpoint=endpoint_id, location=row[0],
                        selector=row[1],
                        provenance=row[2], confidence=row[3], review_state=row[4],
                    ))
        command.downgrade(config, PARENT)
        assert "endpoint_resource_bindings" not in inspect(engine).get_table_names()
    finally:
        command.upgrade(config, "head")
        with engine.begin() as db:
            if endpoint_id is not None:
                db.execute(text(
                    "DELETE FROM endpoint_resource_bindings WHERE endpoint_id = :id"
                ), {"id": endpoint_id})
                db.execute(text("DELETE FROM endpoints WHERE id = :id"), {
                    "id": endpoint_id
                })
            if target_id is not None:
                db.execute(text("DELETE FROM targets WHERE id = :id"), {
                    "id": target_id
                })
