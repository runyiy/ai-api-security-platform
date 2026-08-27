from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Endpoint,
    ExecutionPlan,
    ExecutionPlanApprovalRecord,
    PlanAction,
    Resource,
    TestCase as StoredTestCase,
)
from app.db.session import engine
from app.services.execution_plan import PlanActionInput
from app.services.execution_plan_approval import (
    PlanIntegrityError,
    is_plan_approved,
    recompute_persisted_plan_digest,
    record_plan_decision,
)
from tests.services.test_execution_plan import build_graph, create_plan


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


def test_approval_snapshots_exact_plan_digest(db: Session) -> None:
    plan = create_plan(db, build_graph(db))
    record = record_plan_decision(db, execution_plan_id=plan.id, decision="approved")
    assert record.execution_plan_id == plan.id
    assert record.digest_version == plan.digest_version == "v1"
    assert record.plan_digest == plan.plan_digest
    assert record.decision == "approved"
    assert "updated_at" not in ExecutionPlanApprovalRecord.__table__.columns
    forbidden = {"notes", "details", "headers", "body", "credentials"}
    assert forbidden.isdisjoint(ExecutionPlanApprovalRecord.__table__.columns.keys())


@pytest.mark.parametrize("decision", ["approved", "revoked"])
def test_exact_decisions_persist(db: Session, decision: str) -> None:
    plan = create_plan(db, build_graph(db))
    assert record_plan_decision(
        db, execution_plan_id=plan.id, decision=decision
    ).decision == decision


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "pending"),
        ("digest_version", "v2"),
        ("plan_digest", "A" * 64),
        ("plan_digest", "a" * 63),
    ],
)
def test_database_constraints_match_plan_digest_contract(
    db: Session, field: str, value: str
) -> None:
    plan = create_plan(db, build_graph(db))
    values = {
        "execution_plan_id": plan.id,
        "digest_version": "v1",
        "plan_digest": plan.plan_digest,
        "decision": "approved",
    }
    values[field] = value
    nested = db.begin_nested()
    db.add(ExecutionPlanApprovalRecord(**values))
    with pytest.raises(IntegrityError):
        db.flush()
    nested.rollback()


def test_plan_delete_is_restricted_when_approval_history_exists(db: Session) -> None:
    plan = create_plan(db, build_graph(db))
    record_plan_decision(db, execution_plan_id=plan.id, decision="approved")
    nested = db.begin_nested()
    db.delete(plan)
    with pytest.raises(IntegrityError):
        db.flush()
    nested.rollback()


def test_intact_plan_recomputes_exact_digest_with_canonical_action_order(
    db: Session,
) -> None:
    plan = create_plan(
        db,
        build_graph(db),
        actions=[
            PlanActionInput("GET", "https://example.test/api/items/1"),
            PlanActionInput("GET", "https://example.test/api/items/2"),
        ],
    )
    plan.actions[0].ordinal = 3
    db.flush()
    plan.actions[1].ordinal = 1
    db.flush()
    plan.actions[0].ordinal = 2
    db.flush()
    assert recompute_persisted_plan_digest(db, plan.id) != plan.plan_digest
    with pytest.raises(PlanIntegrityError):
        record_plan_decision(db, execution_plan_id=plan.id, decision="approved")


@pytest.mark.parametrize(
    "mutation",
    [
        "target",
        "revision",
        "actor",
        "credential",
        "policy",
        "url",
        "resource",
        "test_case",
        "action_count",
    ],
)
def test_material_persisted_mutation_invalidates_integrity_and_approval(
    db: Session, mutation: str
) -> None:
    graph = build_graph(db)
    plan = create_plan(db, graph)
    record_plan_decision(db, execution_plan_id=plan.id, decision="approved")
    other_graph = build_graph(db)
    other_target = other_graph["target"]
    other_actor = other_graph["actor"]
    other_resource = Resource(
        target_id=other_target.id,
        resource_type="item",
        external_id=f"other-{uuid4()}",
        owner_identity_id=other_actor.id,
    )
    other_endpoint = Endpoint(
        target_id=other_target.id,
        path="/api/items/{id}",
        method="GET",
        requires_auth=False,
        parameters=[],
    )
    db.add_all([other_resource, other_endpoint])
    db.flush()
    other_test_case = StoredTestCase(
        endpoint_id=other_endpoint.id,
        actor_identity_id=other_actor.id,
        resource_id=other_resource.id,
        test_type="bola",
        ownership_relation="owned",
        expected_statuses=[200],
        status="pending",
    )
    db.add(other_test_case)
    db.flush()

    if mutation == "target":
        plan.target_id = other_graph["target"].id
    elif mutation == "revision":
        plan.authorization_revision_id = other_graph["revision"].id
    elif mutation == "actor":
        plan.actor_identity_id = other_graph["actor"].id
    elif mutation == "credential":
        plan.credential_binding_id = other_graph["binding"].id
    elif mutation == "policy":
        plan.policy_context = {"scope_ids": [99]}
    elif mutation == "url":
        plan.actions[0].url = "https://example.test/api/changed"
    elif mutation == "resource":
        plan.actions[0].resource_id = other_resource.id
    elif mutation == "test_case":
        plan.actions[0].test_case_id = other_test_case.id
    else:
        plan.action_count = 2

    assert is_plan_approved(db, plan.id) is False
    with pytest.raises(PlanIntegrityError):
        record_plan_decision(db, execution_plan_id=plan.id, decision="approved")


def test_database_prevents_persisted_action_method_mutation(db: Session) -> None:
    plan = create_plan(db, build_graph(db))
    record_plan_decision(db, execution_plan_id=plan.id, decision="approved")
    nested = db.begin_nested()
    plan.actions[0].method = "POST"
    with pytest.raises(IntegrityError):
        db.flush()
    nested.rollback()


def test_action_insertion_and_deletion_invalidate_integrity(db: Session) -> None:
    for change in ("insert", "delete"):
        plan = create_plan(db, build_graph(db))
        record_plan_decision(db, execution_plan_id=plan.id, decision="approved")
        if change == "insert":
            db.add(
                PlanAction(
                    execution_plan_id=plan.id,
                    ordinal=2,
                    method="GET",
                    url="https://example.test/api/extra",
                )
            )
        else:
            db.execute(delete(PlanAction).where(PlanAction.execution_plan_id == plan.id))
        assert is_plan_approved(db, plan.id) is False


def test_effective_approval_uses_latest_exact_digest_decision(db: Session) -> None:
    plan = create_plan(db, build_graph(db))
    assert is_plan_approved(db, plan.id) is False
    approved = record_plan_decision(db, execution_plan_id=plan.id, decision="approved")
    assert is_plan_approved(db, plan.id) is True
    revoked = record_plan_decision(db, execution_plan_id=plan.id, decision="revoked")
    assert revoked.id > approved.id
    assert is_plan_approved(db, plan.id) is False


def test_approval_is_bound_to_one_plan(db: Session) -> None:
    graph = build_graph(db)
    plan_a = create_plan(db, graph)
    plan_b = create_plan(db, graph)
    assert plan_a.plan_digest == plan_b.plan_digest
    record_plan_decision(db, execution_plan_id=plan_a.id, decision="approved")
    assert is_plan_approved(db, plan_a.id) is True
    assert is_plan_approved(db, plan_b.id) is False


def test_missing_or_unsupported_plan_fails_closed(db: Session) -> None:
    assert is_plan_approved(db, 999999) is False
