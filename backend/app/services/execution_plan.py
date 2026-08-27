from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.credential_binding import CredentialBinding
from app.db.models.endpoint import Endpoint
from app.db.models.execution_plan import ExecutionPlan
from app.db.models.plan_action import PlanAction
from app.db.models.resource import Resource
from app.db.models.target import Target
from app.db.models.test_case import TestCase
from app.db.models.test_identity import TestIdentity
from app.policies.scope_policy import normalize_request_path, parse_origin


MAX_PLAN_ACTIONS = 100
MAX_POLICY_CONTEXT_BYTES = 16 * 1024
DIGEST_VERSION = "v1"
MAX_ACTION_URL_LENGTH = 2048


class ExecutionPlanValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PlanActionInput:
    method: str
    url: str
    test_case_id: int | None = None
    resource_id: int | None = None


_SECRET_KEY = re.compile(r"[^a-z0-9]+")
_SECRET_KEY_BASES = frozenset(
    {
        "authorization",
        "cookie",
        "setcookie",
        "apikey",
        "xapikey",
        "bearer",
        "bearertoken",
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "password",
        "passwd",
        "session",
        "sessionid",
        "sessiontoken",
    }
)
_SECRET_KEY_QUALIFIERS = (
    "material",
    "header",
    "secret",
    "token",
    "value",
)


def _is_secret_bearing_key(key: str) -> bool:
    normalized = _SECRET_KEY.sub("", key.lower())
    for base in _SECRET_KEY_BASES:
        if not normalized.startswith(base):
            continue
        remainder = normalized[len(base) :]
        while remainder:
            qualifier = next(
                (
                    item
                    for item in _SECRET_KEY_QUALIFIERS
                    if remainder.startswith(item)
                ),
                None,
            )
            if qualifier is None:
                break
            remainder = remainder[len(qualifier) :]
        if not remainder:
            return True
    return False


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ExecutionPlanValidationError(
            "policy_context must be JSON-serializable"
        ) from exc


def _reject_secret_context(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ExecutionPlanValidationError(
                    "policy_context object keys must be strings"
                )
            if _is_secret_bearing_key(key):
                raise ExecutionPlanValidationError(
                    "policy_context contains secret-bearing material"
                )
            _reject_secret_context(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_secret_context(nested)
    elif isinstance(value, str) and re.match(r"^\s*bearer\s+\S", value, re.I):
        raise ExecutionPlanValidationError(
            "policy_context contains secret-bearing material"
        )


def _reject_secret_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.fragment:
        raise ExecutionPlanValidationError("action URL fragments are not allowed")
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if _is_secret_bearing_key(key) or re.match(
            r"^\s*bearer\s+\S", value, re.I
        ):
            raise ExecutionPlanValidationError(
                "action URL contains secret-bearing material"
            )


def canonicalize_policy_context(
    policy_context: Mapping[str, Any] | Sequence[Any] | None,
) -> dict[str, Any] | list[Any]:
    value: object = {} if policy_context is None else policy_context
    if not isinstance(value, (Mapping, list, tuple)):
        raise ExecutionPlanValidationError(
            "policy_context must be a JSON object or array"
        )
    _reject_secret_context(value)
    canonical = _canonical_json(value)
    if len(canonical.encode("utf-8")) > MAX_POLICY_CONTEXT_BYTES:
        raise ExecutionPlanValidationError("policy_context exceeds size limit")
    loaded = json.loads(canonical)
    assert isinstance(loaded, (dict, list))
    return loaded


def compute_plan_digest_v1(
    *,
    target_id: int,
    authorization_revision_id: int,
    actor_identity_id: int,
    credential_binding_id: int | None,
    policy_context: dict[str, Any] | list[Any],
    actions: Sequence[PlanActionInput],
) -> str:
    payload = {
        "digest_version": DIGEST_VERSION,
        "target_id": target_id,
        "authorization_revision_id": authorization_revision_id,
        "actor_identity_id": actor_identity_id,
        "credential_binding_id": credential_binding_id,
        "policy_context": policy_context,
        "action_count": len(actions),
        "actions": [
            {
                "ordinal": ordinal,
                "method": action.method,
                "url": action.url,
                "test_case_id": action.test_case_id,
                "resource_id": action.resource_id,
            }
            for ordinal, action in enumerate(actions, start=1)
        ],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def create_execution_plan(
    db: Session,
    *,
    target_id: int,
    authorization_revision_id: int,
    actor_identity_id: int,
    credential_binding_id: int | None,
    actions: Sequence[PlanActionInput],
    policy_context: Mapping[str, Any] | Sequence[Any] | None = None,
) -> ExecutionPlan:
    frozen_actions = tuple(actions)
    if not 1 <= len(frozen_actions) <= MAX_PLAN_ACTIONS:
        raise ExecutionPlanValidationError(
            f"plan must contain 1..{MAX_PLAN_ACTIONS} actions"
        )

    target = db.scalar(
        select(Target).where(Target.id == target_id).with_for_update()
    )
    revision = db.scalar(
        select(AuthorizationRevision)
        .where(AuthorizationRevision.id == authorization_revision_id)
        .with_for_update()
    )
    if target is None or revision is None:
        raise ExecutionPlanValidationError("target and revision must exist")
    if target.authorization_revision_id != revision.id:
        raise ExecutionPlanValidationError("revision is not bound to target")
    if (
        target.authorization_profile_id != revision.authorization_profile_id
        or revision.lifecycle_state != "active"
    ):
        raise ExecutionPlanValidationError("revision is not active for target")

    actor = db.scalar(
        select(TestIdentity)
        .where(TestIdentity.id == actor_identity_id)
        .with_for_update()
    )
    if actor is None or actor.target_id != target.id:
        raise ExecutionPlanValidationError("actor does not belong to target")
    actor_is_anonymous = actor.auth_type in {"none", "anonymous"}
    if actor_is_anonymous and credential_binding_id is not None:
        raise ExecutionPlanValidationError(
            "anonymous actor cannot select a credential binding"
        )
    if not actor_is_anonymous and credential_binding_id is None:
        raise ExecutionPlanValidationError(
            "authenticated actor requires an exact credential binding"
        )

    if credential_binding_id is not None:
        binding = db.scalar(
            select(CredentialBinding)
            .where(CredentialBinding.id == credential_binding_id)
            .with_for_update()
        )
        if (
            binding is None
            or binding.test_identity_id != actor.id
            or not binding.is_active
            or binding.auth_type != actor.auth_type
        ):
            raise ExecutionPlanValidationError(
                "credential binding is not active for actor"
            )

    target_origin = parse_origin(target.base_url)
    for action in frozen_actions:
        if action.method != "GET":
            raise ExecutionPlanValidationError("planned actions must use GET")
        if not action.url or len(action.url) > MAX_ACTION_URL_LENGTH:
            raise ExecutionPlanValidationError("action URL is invalid or too long")
        if parse_origin(action.url) != target_origin:
            raise ExecutionPlanValidationError("action URL is outside target origin")
        normalize_request_path(action.url)
        _reject_secret_url(action.url)
        if action.resource_id is not None:
            resource = db.get(Resource, action.resource_id)
            if resource is None or resource.target_id != target.id:
                raise ExecutionPlanValidationError(
                    "resource provenance does not belong to target"
                )
        if action.test_case_id is not None:
            test_case = db.get(TestCase, action.test_case_id)
            if test_case is None:
                raise ExecutionPlanValidationError(
                    "test case provenance does not belong to target"
                )
            endpoint = db.get(Endpoint, test_case.endpoint_id)
            test_case_resource = db.get(Resource, test_case.resource_id)
            test_case_actor = db.get(TestIdentity, test_case.actor_identity_id)
            if (
                endpoint is None
                or endpoint.target_id != target.id
                or test_case_resource is None
                or test_case_resource.target_id != target.id
                or test_case_actor is None
                or test_case_actor.target_id != target.id
                or (
                    action.resource_id is not None
                    and action.resource_id != test_case.resource_id
                )
            ):
                raise ExecutionPlanValidationError(
                    "test case provenance does not belong to target"
                )

    frozen_context = canonicalize_policy_context(policy_context)
    digest = compute_plan_digest_v1(
        target_id=target.id,
        authorization_revision_id=revision.id,
        actor_identity_id=actor.id,
        credential_binding_id=credential_binding_id,
        policy_context=frozen_context,
        actions=frozen_actions,
    )
    plan = ExecutionPlan(
        target_id=target.id,
        authorization_revision_id=revision.id,
        actor_identity_id=actor.id,
        credential_binding_id=credential_binding_id,
        digest_version=DIGEST_VERSION,
        plan_digest=digest,
        action_count=len(frozen_actions),
        policy_context=frozen_context,
        actions=[
            PlanAction(
                ordinal=ordinal,
                method=action.method,
                url=action.url,
                test_case_id=action.test_case_id,
                resource_id=action.resource_id,
            )
            for ordinal, action in enumerate(frozen_actions, start=1)
        ],
    )
    db.add(plan)
    db.flush()
    return plan
