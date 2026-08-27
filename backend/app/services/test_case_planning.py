from __future__ import annotations

__test__ = False

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.endpoint import Endpoint
from app.db.models.execution_plan import ExecutionPlan
from app.db.models.resource import Resource
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.db.models.test_case import TestCase
from app.db.models.test_identity import TestIdentity
from app.policies.scope_policy import (
    PolicyValidationError,
    normalize_hostname,
    normalize_request_path,
    normalize_scope_path_pattern,
    parse_origin,
    path_matches_scope,
)
from app.services.execution_plan import PlanActionInput, create_execution_plan
from app.services.test_execution import TestExecutionError, build_test_case_url


POLICY_CONTEXT_VERSION = "v1"


class TestCasePlanningError(ValueError):
    pass


def _build_scope_context(
    *,
    db: Session,
    target_id: int,
    request_url: str,
) -> dict[str, object]:
    try:
        request_origin = parse_origin(request_url)
        request_path = normalize_request_path(request_url)
    except PolicyValidationError as exc:
        raise TestCasePlanningError("planned action URL is unsafe") from exc

    scopes = db.scalars(
        select(Scope)
        .where(
            Scope.target_id == target_id,
            Scope.is_active.is_(True),
        )
        .order_by(Scope.id)
    ).all()
    for scope in scopes:
        try:
            hostname = normalize_hostname(scope.hostname)
            path_pattern = normalize_scope_path_pattern(scope.path_pattern)
            allowed_methods = sorted(
                {method.strip().upper() for method in scope.allowed_methods}
            )
            matches = (
                hostname == request_origin.hostname
                and "GET" in allowed_methods
                and path_matches_scope(request_path, path_pattern)
            )
        except PolicyValidationError:
            continue
        if matches:
            return {
                "context_version": POLICY_CONTEXT_VERSION,
                "matched_scope": {
                    "id": scope.id,
                    "hostname": hostname,
                    "path_pattern": path_pattern,
                    "allowed_methods": allowed_methods,
                },
            }

    raise TestCasePlanningError("no active Scope matches the planned action")


def create_test_case_execution_plan(
    db: Session,
    *,
    test_case_id: int,
    credential_binding_id: int | None,
) -> ExecutionPlan:
    test_case = db.get(TestCase, test_case_id)
    if test_case is None:
        raise TestCasePlanningError("TestCase not found")

    endpoint = db.get(Endpoint, test_case.endpoint_id)
    resource = db.get(Resource, test_case.resource_id)
    actor = db.get(TestIdentity, test_case.actor_identity_id)
    if endpoint is None or resource is None or actor is None:
        raise TestCasePlanningError("TestCase graph is incomplete")

    target = db.scalar(
        select(Target).where(Target.id == endpoint.target_id).with_for_update()
    )
    if target is None:
        raise TestCasePlanningError("Target not found")
    if not target.is_enabled:
        raise TestCasePlanningError("Target is disabled")
    if resource.target_id != target.id or actor.target_id != target.id:
        raise TestCasePlanningError("TestCase graph crosses Targets")
    if endpoint.method != "GET":
        raise TestCasePlanningError("M5 planning supports GET endpoints only")

    revision_id = target.authorization_revision_id
    if revision_id is None:
        raise TestCasePlanningError("Target has no bound AuthorizationRevision")
    revision = db.scalar(
        select(AuthorizationRevision)
        .where(AuthorizationRevision.id == revision_id)
        .with_for_update()
    )
    if (
        revision is None
        or revision.authorization_profile_id != target.authorization_profile_id
        or revision.lifecycle_state != "active"
    ):
        raise TestCasePlanningError(
            "Target-bound AuthorizationRevision is not active and consistent"
        )

    try:
        request_url = build_test_case_url(
            target=target,
            endpoint=endpoint,
            resource=resource,
        )
    except TestExecutionError as exc:
        raise TestCasePlanningError(str(exc)) from exc

    policy_context = _build_scope_context(
        db=db,
        target_id=target.id,
        request_url=request_url,
    )
    return create_execution_plan(
        db,
        target_id=target.id,
        authorization_revision_id=revision.id,
        actor_identity_id=actor.id,
        credential_binding_id=credential_binding_id,
        actions=[
            PlanActionInput(
                method="GET",
                url=request_url,
                test_case_id=test_case.id,
                resource_id=resource.id,
            )
        ],
        policy_context=policy_context,
    )
