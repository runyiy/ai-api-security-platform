import pytest
from sqlalchemy import delete, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import SafetyDecisionRecord, Target
from app.db.session import engine
from app.services.test_case_planning import create_test_case_execution_plan
from tests.services.test_test_case_planning import build_graph


@pytest.fixture
def db() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_audit_schema_is_minimal_append_only_and_restricts_references(
    db: Session,
) -> None:
    graph = build_graph(db)
    test_case = graph["test_case"]
    target = graph["target"]
    plan = create_test_case_execution_plan(
        db,
        test_case_id=test_case.id,
        credential_binding_id=None,
    )
    record = (
        db.query(SafetyDecisionRecord)
        .filter(SafetyDecisionRecord.target_id == target.id)
        .one()
    )

    assert set(inspect(SafetyDecisionRecord).columns.keys()) == {
        "id",
        "stage",
        "operation",
        "outcome",
        "target_id",
        "authorization_revision_id",
        "execution_plan_id",
        "plan_action_id",
        "test_case_id",
        "test_run_id",
        "code",
        "reason",
        "matched_scope_id",
        "policy_evaluated_at",
        "created_at",
    }
    assert not hasattr(record, "updated_at")
    assert plan.id == record.execution_plan_id

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(delete(Target).where(Target.id == target.id))


@pytest.mark.parametrize(
    ("stage", "operation", "outcome"),
    [
        ("invalid", "policy_check", "blocked"),
        ("policy", "invalid", "blocked"),
        ("policy", "policy_check", "created"),
        ("execution", "test_execution", "allowed"),
    ],
)
def test_database_rejects_invalid_audit_values(
    db: Session,
    stage: str,
    operation: str,
    outcome: str,
) -> None:
    graph = build_graph(db)
    target = graph["target"]
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(
                SafetyDecisionRecord(
                    stage=stage,
                    operation=operation,
                    outcome=outcome,
                    target_id=target.id,
                )
            )
            db.flush()
