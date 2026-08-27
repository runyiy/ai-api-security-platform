from __future__ import annotations

import inspect
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

from app.db.models import (
    AuthorizationProfile,
    AuthorizationRevision,
    CredentialBinding,
    Endpoint,
    ExecutionPlan,
    PlanAction,
    Resource,
    SafetyDecisionRecord,
    Scope,
    Target,
    TestCase as StoredTestCase,
    TestIdentity as StoredTestIdentity,
)
from app.db.session import engine
from app.services.test_case_planning import (
    TestCasePlanningError as PlanningError,
    create_test_case_execution_plan,
)
from app.services.test_execution import build_test_case_url
from app.services.safety_audit import SafetyAuditService


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


def build_graph(
    db: Session,
    *,
    actor_auth_type: str = "anonymous",
    endpoint_method: str = "GET",
) -> dict[str, object]:
    unique = str(uuid4())
    profile = AuthorizationProfile(
        name=f"planner-profile-{unique}",
        program_name="Planning Program",
        authorization_type="self_owned",
        automation_allowed=True,
        max_requests_per_second=1.0,
        allow_get=True,
        require_human_execution_approval=True,
    )
    revision = AuthorizationRevision(
        authorization_profile=profile,
        revision_number=1,
        lifecycle_state="active",
        name="Planning revision",
        program_name="Planning Program",
        authorization_type="self_owned",
        automation_allowed=True,
        max_requests_per_second=1.0,
        allow_get=True,
        require_human_execution_approval=True,
    )
    target = Target(
        name=f"planner-target-{unique}",
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
        name=f"planner-actor-{unique}",
        auth_type=actor_auth_type,
        credentials=None,
        is_active=True,
    )
    owner = StoredTestIdentity(
        target_id=target.id,
        name=f"planner-owner-{unique}",
        auth_type="anonymous",
        credentials=None,
        is_active=True,
    )
    endpoint = Endpoint(
        target_id=target.id,
        path="/projects/{project_id}",
        method=endpoint_method,
        operation_id="get_project",
        requires_auth=actor_auth_type not in {"none", "anonymous"},
        parameters=[],
    )
    scope = Scope(
        target_id=target.id,
        hostname="EXAMPLE.TEST.",
        path_pattern="/api/projects/*",
        allowed_methods=["POST", "GET", "GET"],
        is_active=True,
    )
    db.add_all([actor, owner, endpoint, scope])
    db.flush()
    resource = Resource(
        target_id=target.id,
        resource_type="project",
        external_id="project 100",
        owner_identity_id=owner.id,
    )
    db.add(resource)
    db.flush()
    test_case = StoredTestCase(
        endpoint_id=endpoint.id,
        actor_identity_id=actor.id,
        resource_id=resource.id,
        test_type="bola_cross_owner",
        ownership_relation="cross_owner",
        expected_statuses=[403, 404],
        status="pending",
    )
    db.add(test_case)
    db.flush()
    return {
        "profile": profile,
        "revision": revision,
        "target": target,
        "actor": actor,
        "owner": owner,
        "endpoint": endpoint,
        "scope": scope,
        "resource": resource,
        "test_case": test_case,
    }


def plan(
    db: Session,
    graph: dict[str, object],
    *,
    credential_binding_id: int | None = None,
) -> ExecutionPlan:
    test_case = graph["test_case"]
    assert isinstance(test_case, StoredTestCase)
    return create_test_case_execution_plan(
        db,
        test_case_id=test_case.id,
        credential_binding_id=credential_binding_id,
    )


def test_valid_test_case_derives_one_get_action_and_scope_snapshot(db: Session) -> None:
    graph = build_graph(db)
    target = graph["target"]
    revision = graph["revision"]
    actor = graph["actor"]
    resource = graph["resource"]
    test_case = graph["test_case"]
    scope = graph["scope"]
    assert isinstance(target, Target)
    assert isinstance(revision, AuthorizationRevision)
    assert isinstance(actor, StoredTestIdentity)
    assert isinstance(resource, Resource)
    assert isinstance(test_case, StoredTestCase)
    assert isinstance(scope, Scope)

    execution_plan = plan(db, graph)

    assert execution_plan.target_id == target.id
    assert execution_plan.authorization_revision_id == revision.id
    assert execution_plan.actor_identity_id == actor.id
    assert execution_plan.credential_binding_id is None
    assert execution_plan.action_count == 1
    assert len(execution_plan.actions) == 1
    action = execution_plan.actions[0]
    assert action.ordinal == 1
    assert action.method == "GET"
    assert action.test_case_id == test_case.id
    assert action.resource_id == resource.id
    assert action.url == "https://example.test/api/projects/project%20100"
    assert execution_plan.policy_context == {
        "context_version": "v1",
        "matched_scope": {
            "id": scope.id,
            "hostname": "example.test",
            "path_pattern": "/api/projects/*",
            "allowed_methods": ["GET", "POST"],
        },
    }
    assert test_case.status == "pending"
    assert revision.require_human_execution_approval is True
    records = (
        db.query(SafetyDecisionRecord)
        .filter(SafetyDecisionRecord.target_id == target.id)
        .all()
    )
    assert len(records) == 1
    record = records[0]
    assert (record.stage, record.operation, record.outcome) == (
        "plan",
        "testcase_plan",
        "created",
    )
    assert record.target_id == target.id
    assert record.authorization_revision_id == revision.id
    assert record.execution_plan_id == execution_plan.id
    assert record.plan_action_id == action.id
    assert record.test_case_id == test_case.id


def test_plan_audit_failure_rolls_back_plan_graph(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = build_graph(db)
    plan_count = db.query(ExecutionPlan).count()
    action_count = db.query(PlanAction).count()

    def fail_append(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(SafetyAuditService, "append_plan_created", fail_append)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        with db.begin_nested():
            plan(db, graph)

    assert db.query(ExecutionPlan).count() == plan_count
    assert db.query(PlanAction).count() == action_count


def test_planner_uses_existing_url_builder_semantics(db: Session) -> None:
    graph = build_graph(db)
    target = graph["target"]
    endpoint = graph["endpoint"]
    resource = graph["resource"]
    assert isinstance(target, Target)
    assert isinstance(endpoint, Endpoint)
    assert isinstance(resource, Resource)
    expected = build_test_case_url(
        target=target,
        endpoint=endpoint,
        resource=resource,
    )
    assert plan(db, graph).actions[0].url == expected


def test_planner_inputs_cannot_override_derived_state(db: Session) -> None:
    parameters = inspect.signature(create_test_case_execution_plan).parameters
    assert set(parameters) == {"db", "test_case_id", "credential_binding_id"}
    graph = build_graph(db)
    test_case = graph["test_case"]
    assert isinstance(test_case, StoredTestCase)
    with pytest.raises(TypeError):
        create_test_case_execution_plan(
            db,
            test_case_id=test_case.id,
            credential_binding_id=None,
            policy_context={"authorization": "secret"},
        )


@pytest.mark.parametrize(
    "mutation",
    ["resource_type", "unresolved_path", "non_get"],
)
def test_invalid_endpoint_resource_rendering_fails_closed(
    db: Session,
    mutation: str,
) -> None:
    graph = build_graph(db)
    endpoint = graph["endpoint"]
    resource = graph["resource"]
    assert isinstance(endpoint, Endpoint)
    assert isinstance(resource, Resource)
    if mutation == "resource_type":
        resource.resource_type = "document"
    elif mutation == "unresolved_path":
        endpoint.path = "/orgs/{org_id}/projects/{project_id}"
    else:
        endpoint.method = "POST"
    with pytest.raises(PlanningError):
        plan(db, graph)
    assert db.query(ExecutionPlan).count() == 0
    assert db.query(PlanAction).count() == 0


@pytest.mark.parametrize("member", ["endpoint", "resource", "actor"])
def test_cross_target_test_case_graph_fails_closed(
    db: Session,
    member: str,
) -> None:
    graph = build_graph(db)
    other = build_graph(db)
    other_target = other["target"]
    assert isinstance(other_target, Target)
    if member == "endpoint":
        other_endpoint = other["endpoint"]
        assert isinstance(other_endpoint, Endpoint)
        other_endpoint.path = "/other/{project_id}"
    elif member == "resource":
        other_resource = other["resource"]
        assert isinstance(other_resource, Resource)
        other_resource.external_id = "other"
    db.flush()
    row = graph[member]
    assert isinstance(row, (Endpoint, Resource, StoredTestIdentity))
    row.target_id = other_target.id
    with pytest.raises(PlanningError):
        plan(db, graph)


def test_disabled_target_fails_closed(db: Session) -> None:
    graph = build_graph(db)
    target = graph["target"]
    assert isinstance(target, Target)
    target.is_enabled = False
    with pytest.raises(PlanningError):
        plan(db, graph)


@pytest.mark.parametrize("revision_state", ["missing", "mismatched", "revoked"])
def test_exact_active_bound_revision_is_required(
    db: Session,
    revision_state: str,
) -> None:
    graph = build_graph(db)
    target = graph["target"]
    revision = graph["revision"]
    assert isinstance(target, Target)
    assert isinstance(revision, AuthorizationRevision)
    if revision_state == "missing":
        target.authorization_revision_id = None
    elif revision_state == "mismatched":
        other = build_graph(db)
        other_revision = other["revision"]
        assert isinstance(other_revision, AuthorizationRevision)
        target.authorization_revision_id = other_revision.id
    else:
        revision.lifecycle_state = "revoked"
    with pytest.raises(PlanningError):
        plan(db, graph)


def test_authenticated_planning_requires_explicit_matching_binding(db: Session) -> None:
    graph = build_graph(db, actor_auth_type="bearer")
    actor = graph["actor"]
    assert isinstance(actor, StoredTestIdentity)
    matching = CredentialBinding(
        test_identity_id=actor.id,
        auth_type="bearer",
        source_type="stored_secret",
        is_active=True,
    )
    wrong_type = CredentialBinding(
        test_identity_id=actor.id,
        auth_type="api_key",
        source_type="stored_secret",
        is_active=True,
    )
    inactive = CredentialBinding(
        test_identity_id=actor.id,
        auth_type="bearer",
        source_type="stored_secret",
        is_active=False,
    )
    other_actor = StoredTestIdentity(
        target_id=actor.target_id,
        name=f"other-actor-{uuid4()}",
        auth_type="bearer",
        credentials=None,
        is_active=True,
    )
    other_binding = CredentialBinding(
        test_identity=other_actor,
        auth_type="bearer",
        source_type="stored_secret",
        is_active=True,
    )
    db.add_all([matching, wrong_type, inactive, other_binding])
    db.flush()

    with pytest.raises(ValueError):
        plan(db, graph)
    with pytest.raises(ValueError):
        plan(db, graph, credential_binding_id=wrong_type.id)
    with pytest.raises(ValueError):
        plan(db, graph, credential_binding_id=inactive.id)
    with pytest.raises(ValueError):
        plan(db, graph, credential_binding_id=other_binding.id)
    execution_plan = plan(db, graph, credential_binding_id=matching.id)
    assert execution_plan.credential_binding_id == matching.id


def test_anonymous_planning_rejects_explicit_binding(db: Session) -> None:
    graph = build_graph(db)
    actor = graph["actor"]
    assert isinstance(actor, StoredTestIdentity)
    binding = CredentialBinding(
        test_identity_id=actor.id,
        auth_type="bearer",
        source_type="stored_secret",
        is_active=True,
    )
    db.add(binding)
    db.flush()
    with pytest.raises(ValueError):
        plan(db, graph, credential_binding_id=binding.id)


@pytest.mark.parametrize(
    "scope_change",
    ["hostname", "path", "method", "inactive"],
)
def test_non_matching_or_inactive_scope_fails_closed(
    db: Session,
    scope_change: str,
) -> None:
    graph = build_graph(db)
    scope = graph["scope"]
    assert isinstance(scope, Scope)
    if scope_change == "hostname":
        scope.hostname = "other.test"
    elif scope_change == "path":
        scope.path_pattern = "/other/*"
    elif scope_change == "method":
        scope.allowed_methods = ["POST"]
    else:
        scope.is_active = False
    with pytest.raises(PlanningError):
        plan(db, graph)
    assert db.query(ExecutionPlan).count() == 0


def test_lowest_matching_scope_id_is_selected_deterministically(db: Session) -> None:
    graph = build_graph(db)
    target = graph["target"]
    first = graph["scope"]
    assert isinstance(target, Target)
    assert isinstance(first, Scope)
    second = Scope(
        target_id=target.id,
        hostname="example.test",
        path_pattern="/api/projects/*",
        allowed_methods=["GET"],
        is_active=True,
    )
    db.add(second)
    db.flush()
    execution_plan = plan(db, graph)
    assert execution_plan.policy_context["matched_scope"]["id"] == min(
        first.id,
        second.id,
    )


def test_identical_state_produces_identical_digest(db: Session) -> None:
    graph = build_graph(db)
    first = plan(db, graph)
    second = plan(db, graph)
    assert first.id != second.id
    assert first.created_at != second.created_at or first.id != second.id
    assert first.plan_digest == second.plan_digest


def test_resource_change_updates_new_plan_without_mutating_prior_snapshot(
    db: Session,
) -> None:
    graph = build_graph(db)
    resource = graph["resource"]
    assert isinstance(resource, Resource)
    first = plan(db, graph)
    first_url = first.actions[0].url
    resource.external_id = "changed"
    db.flush()
    second = plan(db, graph)
    assert first.actions[0].url == first_url
    assert second.actions[0].url.endswith("/projects/changed")
    assert second.plan_digest != first.plan_digest


def test_scope_change_updates_new_plan_without_mutating_prior_context(
    db: Session,
) -> None:
    graph = build_graph(db)
    scope = graph["scope"]
    assert isinstance(scope, Scope)
    first = plan(db, graph)
    first_context = first.policy_context
    scope.path_pattern = "/api/projects/project 100"
    db.flush()
    second = plan(db, graph)
    assert first.policy_context == first_context
    assert second.policy_context["matched_scope"]["path_pattern"] == scope.path_pattern
    assert second.plan_digest != first.plan_digest


def test_planning_performs_no_http_and_mutates_no_source_rows(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = build_graph(db)
    test_case = graph["test_case"]
    endpoint = graph["endpoint"]
    resource = graph["resource"]
    scope = graph["scope"]
    assert isinstance(test_case, StoredTestCase)
    assert isinstance(endpoint, Endpoint)
    assert isinstance(resource, Resource)
    assert isinstance(scope, Scope)
    source_values = (
        test_case.status,
        endpoint.path,
        resource.external_id,
        tuple(scope.allowed_methods),
    )

    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("planning attempted network I/O")

    monkeypatch.setattr(httpx.Client, "send", fail_network)
    plan(db, graph)
    assert (
        test_case.status,
        endpoint.path,
        resource.external_id,
        tuple(scope.allowed_methods),
    ) == source_values
