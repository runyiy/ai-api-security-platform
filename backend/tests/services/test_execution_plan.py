from __future__ import annotations

from datetime import datetime, timezone
import re
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    AuthorizationProfile,
    AuthorizationRevision,
    CredentialBinding,
    Endpoint,
    ExecutionPlan,
    PlanAction,
    Resource,
    Target,
    TestCase as StoredTestCase,
    TestIdentity as StoredTestIdentity,
)
from app.db.session import engine
from app.services.execution_plan import (
    MAX_PLAN_ACTIONS,
    ExecutionPlanValidationError,
    PlanActionInput,
    compute_plan_digest_v1,
    create_execution_plan,
)


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


def build_graph(db: Session, *, active: bool = True) -> dict[str, object]:
    profile = AuthorizationProfile(
        name=f"plan-profile-{uuid4()}",
        program_name="Plan Program",
        authorization_type="self_owned",
        automation_allowed=True,
        max_requests_per_second=1.0,
        allow_get=True,
        require_human_execution_approval=True,
    )
    revision = AuthorizationRevision(
        authorization_profile=profile,
        revision_number=1,
        lifecycle_state="active" if active else "draft",
        name="Plan revision",
        program_name="Plan Program",
        authorization_type="self_owned",
        automation_allowed=True,
        max_requests_per_second=1.0,
        allow_get=True,
        require_human_execution_approval=True,
    )
    target = Target(
        name=f"plan-target-{uuid4()}",
        base_url="https://example.test/api",
        environment="test",
        is_enabled=True,
        authorization_profile=profile,
    )
    db.add_all([revision, target])
    db.flush()
    target.authorization_revision_id = revision.id
    actor = StoredTestIdentity(
        target_id=target.id,
        name=f"actor-{uuid4()}",
        auth_type="none",
        credentials=None,
        is_active=True,
    )
    db.add(actor)
    db.flush()
    binding = CredentialBinding(
        test_identity_id=actor.id,
        auth_type="bearer",
        source_type="stored_secret",
        is_active=True,
    )
    db.add(binding)
    db.flush()
    return {
        "profile": profile,
        "revision": revision,
        "target": target,
        "actor": actor,
        "binding": binding,
    }


def create_plan(
    db: Session,
    graph: dict[str, object],
    **overrides: object,
) -> ExecutionPlan:
    target = graph["target"]
    revision = graph["revision"]
    actor = graph["actor"]
    assert isinstance(target, Target)
    assert isinstance(revision, AuthorizationRevision)
    assert isinstance(actor, StoredTestIdentity)
    values = {
        "target_id": target.id,
        "authorization_revision_id": revision.id,
        "actor_identity_id": actor.id,
        "credential_binding_id": None,
        "actions": [PlanActionInput("GET", "https://example.test/api/items/1")],
        "policy_context": {"scope_ids": [3, 1], "source": "test"},
    }
    values.update(overrides)
    return create_execution_plan(db, **values)  # type: ignore[arg-type]


def test_plan_persists_ordered_snapshots_and_anonymous_binding(
    db: Session,
) -> None:
    graph = build_graph(db)
    actions = [
        PlanActionInput("GET", "https://example.test/api/items/2"),
        PlanActionInput("GET", "https://example.test/api/items/1?view=full"),
    ]
    plan = create_plan(db, graph, actions=actions)

    db.expire_all()
    loaded = db.get(ExecutionPlan, plan.id)
    assert loaded is not None
    assert loaded.credential_binding_id is None
    assert loaded.action_count == 2
    assert [action.ordinal for action in loaded.actions] == [1, 2]
    assert [action.url for action in loaded.actions] == [item.url for item in actions]
    assert "updated_at" not in ExecutionPlan.__table__.columns
    assert "updated_at" not in PlanAction.__table__.columns
    assert not ExecutionPlan.actions.property.cascade.delete_orphan
    assert not ExecutionPlan.actions.property.cascade.delete


@pytest.mark.parametrize("count", [1, MAX_PLAN_ACTIONS])
def test_plan_action_bounds_accept_limits(db: Session, count: int) -> None:
    graph = build_graph(db)
    plan = create_plan(
        db,
        graph,
        actions=[
            PlanActionInput("GET", f"https://example.test/api/items/{index}")
            for index in range(count)
        ],
    )
    assert plan.action_count == count


@pytest.mark.parametrize("count", [0, MAX_PLAN_ACTIONS + 1])
def test_plan_action_bounds_reject_outside_limits(db: Session, count: int) -> None:
    graph = build_graph(db)
    with pytest.raises(ExecutionPlanValidationError):
        create_plan(
            db,
            graph,
            actions=[
                PlanActionInput("GET", f"https://example.test/api/{index}")
                for index in range(count)
            ],
        )
    assert db.query(ExecutionPlan).count() == 0


@pytest.mark.parametrize(
    "action",
    [
        PlanActionInput("POST", "https://example.test/api/items"),
        PlanActionInput("GET", "https://evil.test/api/items"),
        PlanActionInput("GET", "https://example.test/api/../admin"),
        PlanActionInput("GET", "https://example.test/api/%2fadmin"),
        PlanActionInput("GET", "https://example.test/api/items?access_token=secret"),
        PlanActionInput("GET", "https://example.test/api/items#secret"),
    ],
)
def test_non_get_cross_origin_and_unsafe_paths_are_rejected(
    db: Session, action: PlanActionInput
) -> None:
    graph = build_graph(db)
    with pytest.raises((ExecutionPlanValidationError, ValueError)):
        create_plan(db, graph, actions=[action])
    assert db.query(ExecutionPlan).count() == 0


def test_exact_active_bound_revision_is_required(db: Session) -> None:
    graph = build_graph(db)
    target = graph["target"]
    revision = graph["revision"]
    assert isinstance(target, Target)
    assert isinstance(revision, AuthorizationRevision)

    with pytest.raises(ExecutionPlanValidationError):
        create_plan(db, graph, authorization_revision_id=revision.id + 999999)
    revision.lifecycle_state = "revoked"
    with pytest.raises(ExecutionPlanValidationError):
        create_plan(db, graph)
    revision.lifecycle_state = "active"
    target.authorization_revision_id = None
    with pytest.raises(ExecutionPlanValidationError):
        create_plan(db, graph)


def test_profile_mismatch_is_rejected(db: Session) -> None:
    graph = build_graph(db)
    target = graph["target"]
    assert isinstance(target, Target)
    other = build_graph(db)
    other_revision = other["revision"]
    assert isinstance(other_revision, AuthorizationRevision)
    target.authorization_revision_id = other_revision.id
    with pytest.raises(ExecutionPlanValidationError):
        create_plan(db, graph, authorization_revision_id=other_revision.id)


def test_actor_and_credential_must_belong_to_selected_graph(db: Session) -> None:
    graph = build_graph(db)
    other = build_graph(db)
    other_actor = other["actor"]
    other_binding = other["binding"]
    binding = graph["binding"]
    assert isinstance(other_actor, StoredTestIdentity)
    assert isinstance(other_binding, CredentialBinding)
    assert isinstance(binding, CredentialBinding)

    with pytest.raises(ExecutionPlanValidationError):
        create_plan(db, graph, actor_identity_id=other_actor.id)
    with pytest.raises(ExecutionPlanValidationError):
        create_plan(db, graph, credential_binding_id=other_binding.id)
    binding.is_active = False
    with pytest.raises(ExecutionPlanValidationError):
        create_plan(db, graph, credential_binding_id=binding.id)


def test_authenticated_actor_requires_exact_credential_binding(db: Session) -> None:
    graph = build_graph(db)
    actor = graph["actor"]
    binding = graph["binding"]
    assert isinstance(actor, StoredTestIdentity)
    assert isinstance(binding, CredentialBinding)
    actor.auth_type = "bearer"
    with pytest.raises(ExecutionPlanValidationError):
        create_plan(db, graph)
    plan = create_plan(db, graph, credential_binding_id=binding.id)
    assert plan.credential_binding_id == binding.id


def test_provenance_must_belong_to_target_and_url_remains_snapshot(db: Session) -> None:
    graph = build_graph(db)
    actor = graph["actor"]
    target = graph["target"]
    assert isinstance(actor, StoredTestIdentity)
    assert isinstance(target, Target)
    resource = Resource(
        target_id=target.id,
        resource_type="item",
        external_id=f"item-{uuid4()}",
        owner_identity_id=actor.id,
    )
    endpoint = Endpoint(
        target_id=target.id,
        path="/api/items/{id}",
        method="GET",
        requires_auth=False,
        parameters=[],
    )
    db.add_all([resource, endpoint])
    db.flush()
    test_case = StoredTestCase(
        endpoint_id=endpoint.id,
        actor_identity_id=actor.id,
        resource_id=resource.id,
        test_type="bola",
        ownership_relation="owned",
        expected_statuses=[200],
        status="pending",
    )
    db.add(test_case)
    db.flush()
    original_url = "https://example.test/api/items/frozen"
    plan = create_plan(
        db,
        graph,
        actions=[PlanActionInput("GET", original_url, test_case.id, resource.id)],
    )
    endpoint.path = "/changed"
    resource.external_id = "changed"
    db.flush()
    assert plan.actions[0].url == original_url

    other = build_graph(db)
    other_actor = other["actor"]
    other_target = other["target"]
    assert isinstance(other_actor, StoredTestIdentity)
    assert isinstance(other_target, Target)
    other_resource = Resource(
        target_id=other_target.id,
        resource_type="item",
        external_id=f"other-{uuid4()}",
        owner_identity_id=other_actor.id,
    )
    db.add(other_resource)
    db.flush()
    with pytest.raises(ExecutionPlanValidationError):
        create_plan(
            db,
            graph,
            actions=[
                PlanActionInput(
                    "GET",
                    "https://example.test/api/items/1",
                    resource_id=other_resource.id,
                )
            ],
        )


def test_graph_creation_is_atomic_when_later_action_is_invalid(db: Session) -> None:
    graph = build_graph(db)
    with pytest.raises(ExecutionPlanValidationError):
        create_plan(
            db,
            graph,
            actions=[
                PlanActionInput("GET", "https://example.test/api/valid"),
                PlanActionInput("DELETE", "https://example.test/api/invalid"),
            ],
        )
    assert db.query(ExecutionPlan).count() == 0
    assert db.query(PlanAction).count() == 0


def test_database_enforces_action_constraints(db: Session) -> None:
    graph = build_graph(db)
    plan = create_plan(db, graph)
    nested = db.begin_nested()
    db.add(
        PlanAction(
            execution_plan_id=plan.id,
            ordinal=1,
            method="GET",
            url="https://example.test/x",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    nested.rollback()


def digest(**overrides: object) -> str:
    values = {
        "target_id": 1,
        "authorization_revision_id": 2,
        "actor_identity_id": 3,
        "credential_binding_id": None,
        "policy_context": {"b": 2, "a": 1},
        "actions": [PlanActionInput("GET", "https://example.test/a", None, 7)],
    }
    values.update(overrides)
    return compute_plan_digest_v1(**values)  # type: ignore[arg-type]


def test_digest_is_deterministic_versioned_sha256_and_ignores_database_values() -> None:
    first = digest(policy_context={"a": 1, "b": 2})
    second = digest(policy_context={"b": 2, "a": 1})
    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first)


@pytest.mark.parametrize(
    "change",
    [
        {"target_id": 9},
        {"authorization_revision_id": 9},
        {"actor_identity_id": 9},
        {"credential_binding_id": 9},
        {"policy_context": {"a": 2, "b": 2}},
        {"actions": [PlanActionInput("GET", "https://example.test/b", None, 7)]},
        {"actions": [PlanActionInput("POST", "https://example.test/a", None, 7)]},
        {"actions": [PlanActionInput("GET", "https://example.test/a", None, 8)]},
        {"actions": [PlanActionInput("GET", "https://example.test/a", 8, 7)]},
        {
            "actions": [
                PlanActionInput("GET", "https://example.test/a", None, 7),
                PlanActionInput("GET", "https://example.test/b"),
            ]
        },
        {
            "actions": [
                PlanActionInput("GET", "https://example.test/b"),
                PlanActionInput("GET", "https://example.test/a", None, 7),
            ]
        },
    ],
)
def test_each_material_plan_change_changes_digest(change: dict[str, object]) -> None:
    assert digest(**change) != digest()


@pytest.mark.parametrize(
    "context",
    [
        {"Authorization": "Bearer secret"},
        {"headers": {"Cookie": "session=secret"}},
        {"x-api-key": "secret"},
        {"bearer": "secret"},
        {"access_token": "secret"},
        {"note": "Bearer secret"},
    ],
)
def test_secret_bearing_policy_context_is_rejected(
    db: Session, context: dict[str, object]
) -> None:
    graph = build_graph(db)
    with pytest.raises(ExecutionPlanValidationError):
        create_plan(db, graph, policy_context=context)
    assert db.query(ExecutionPlan).count() == 0


def test_policy_context_must_be_json_and_is_size_bounded(db: Session) -> None:
    graph = build_graph(db)
    with pytest.raises(ExecutionPlanValidationError):
        create_plan(db, graph, policy_context={"value": object()})
    with pytest.raises(ExecutionPlanValidationError):
        create_plan(db, graph, policy_context={"value": "x" * (16 * 1024)})


def test_referenced_domain_rows_and_plan_are_restrict_retained(db: Session) -> None:
    graph = build_graph(db)
    binding = graph["binding"]
    target = graph["target"]
    revision = graph["revision"]
    actor = graph["actor"]
    assert isinstance(binding, CredentialBinding)
    assert isinstance(target, Target)
    assert isinstance(revision, AuthorizationRevision)
    assert isinstance(actor, StoredTestIdentity)
    actor.auth_type = "bearer"
    plan = create_plan(db, graph, credential_binding_id=binding.id)

    for model, row_id in (
        (Target, target.id),
        (AuthorizationRevision, revision.id),
        (StoredTestIdentity, actor.id),
        (CredentialBinding, binding.id),
        (ExecutionPlan, plan.id),
    ):
        nested = db.begin_nested()
        with pytest.raises(IntegrityError):
            db.execute(delete(model).where(model.id == row_id))
            db.flush()
        nested.rollback()
    assert db.get(PlanAction, plan.actions[0].id) is not None
