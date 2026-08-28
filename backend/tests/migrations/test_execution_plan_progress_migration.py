from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from app.db.session import engine
from app.services.execution_plan_claim import ExecutionPlanClaimService
from tests.services.test_plan_execution_integration import approved_plan


REVISION = "e9a1c3d5f7b9"
PARENT = "d7f9b1c3e5a7"
TABLE = "execution_plan_progress"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_m8_04_schema_backfill_and_round_trip(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    ExecutionPlanClaimService(bind=engine).acquire(
        plan_id, "historical-owner", lease_seconds=30
    )
    config = Config("alembic.ini")
    try:
        command.downgrade(config, PARENT)
        assert TABLE not in inspect(engine).get_table_names()
        command.upgrade(config, REVISION)
        assert current_revision() == REVISION
        inspector = inspect(engine)
        assert {item["name"] for item in inspector.get_columns(TABLE)} == {
            "execution_plan_id", "fencing_generation", "phase", "updated_at"
        }
        assert inspector.get_foreign_keys(TABLE)[0]["options"] == {
            "ondelete": "RESTRICT"
        }
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT fencing_generation, phase FROM execution_plan_progress "
                    "WHERE execution_plan_id=:plan_id"
                ),
                {"plan_id": plan_id},
            ).one()
        assert row.phase == "in_doubt"
        assert row.fencing_generation == 1
    finally:
        command.upgrade(config, "head")
