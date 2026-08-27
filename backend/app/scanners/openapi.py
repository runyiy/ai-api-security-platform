import json
from collections.abc import Callable

from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.executors.rate_limit import (
    InMemoryRateLimiter,
    RateLimitConfigurationError,
)
from app.network_safety.destination import PRIVATE_LOCAL
from app.network_safety.gateway import NetworkGateway, NetworkGatewayError
from app.policies.scope_policy import (
    PolicyDecision,
    ScopePolicyEngine,
)

from dataclasses import dataclass
from typing import Any


SUPPORTED_METHODS = (
    "get",
    "post",
    "patch",
    "delete",
)

MAX_OPENAPI_RESPONSE_BYTES = 1_000_000


class OpenAPIScanError(RuntimeError):
    pass


class OpenAPIAuditError(OpenAPIScanError):
    pass


class OpenAPIExecutionBlocked(OpenAPIScanError):
    def __init__(self, *, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


class OpenAPIPolicyDenied(
    OpenAPIScanError
):
    def __init__(
        self,
        decision: PolicyDecision,
    ) -> None:
        self.decision = decision

        super().__init__(
            f"OpenAPI request denied: "
            f"{decision.code}"
        )


@dataclass(frozen=True)
class ParsedEndpoint:
    path: str
    method: str
    operation_id: str | None
    requires_auth: bool
    parameters: list[dict[str, Any]]
    request_body: dict[str, Any] | None
    security: list[dict[str, Any]] | None

def merge_parameters(
    path_parameters: list[dict[str, Any]],
    operation_parameters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    positions: dict[tuple[str, str], int] = {}

    for parameter in (
        path_parameters + operation_parameters
    ):
        if not isinstance(parameter, dict):
            continue

        name = parameter.get("name")
        location = parameter.get("in")

        if not isinstance(name, str):
            merged.append(parameter)
            continue

        if not isinstance(location, str):
            merged.append(parameter)
            continue

        key = (
            name,
            location,
        )

        if key in positions:
            merged[
                positions[key]
            ] = parameter
        else:
            positions[key] = len(merged)
            merged.append(parameter)

    return merged

def security_requires_auth(
    security: list[dict[str, Any]] | None,
) -> bool:
    if not security:
        return False

    if any(
        requirement == {}
        for requirement in security
    ):
        return False

    return True

def parse_openapi_schema(
    schema: dict[str, Any],
) -> list[ParsedEndpoint]:
    paths = schema.get("paths")

    if not isinstance(paths, dict):
        raise ValueError(
            "OpenAPI schema does not contain "
            "a valid 'paths' object"
        )

    root_security = schema.get("security")

    if root_security is not None:
        if not isinstance(root_security, list):
            raise ValueError(
                "OpenAPI root security must be a list"
            )

    endpoints: list[ParsedEndpoint] = []

    for path, path_item in paths.items():
        if not isinstance(path, str):
            continue

        if not isinstance(path_item, dict):
            continue

        raw_path_parameters = (
            path_item.get("parameters", [])
        )

        path_parameters = (
            raw_path_parameters
            if isinstance(
                raw_path_parameters,
                list,
            )
            else []
        )

        for method in SUPPORTED_METHODS:
            operation = path_item.get(method)

            if not isinstance(operation, dict):
                continue

            raw_operation_parameters = (
                operation.get(
                    "parameters",
                    [],
                )
            )

            operation_parameters = (
                raw_operation_parameters
                if isinstance(
                    raw_operation_parameters,
                    list,
                )
                else []
            )

            parameters = merge_parameters(
                path_parameters,
                operation_parameters,
            )

            security = operation.get(
                "security",
                root_security,
            )

            if security is not None:
                if not isinstance(security, list):
                    security = None

            request_body = operation.get(
                "requestBody"
            )

            if not isinstance(
                request_body,
                dict,
            ):
                request_body = None

            operation_id = operation.get(
                "operationId"
            )

            if not isinstance(
                operation_id,
                str,
            ):
                operation_id = None

            endpoints.append(
                ParsedEndpoint(
                    path=path,
                    method=method.upper(),
                    operation_id=operation_id,
                    requires_auth=(
                        security_requires_auth(
                            security
                        )
                    ),
                    parameters=parameters,
                    request_body=request_body,
                    security=security,
                )
            )

    return endpoints

class OpenAPIScanner:
    def __init__(
        self,
        policy_engine: ScopePolicyEngine,
        rate_limiter: InMemoryRateLimiter,
        network_gateway: NetworkGateway,
    ) -> None:
        self.policy_engine = policy_engine
        self.rate_limiter = rate_limiter
        self.network_gateway = network_gateway

    def scan(
        self,
        *,
        target: Target,
        authorization_revision: AuthorizationRevision | None,
        scopes: list[Scope],
        refresh_authorization: Callable[
            [], tuple[Target, AuthorizationRevision | None, list[Scope]]
        ] | None = None,
        policy_decision_observer: Callable[[PolicyDecision], None] | None = None,
    ) -> tuple[
        str,
        list[ParsedEndpoint],
    ]:
        openapi_url = (
            f"{target.base_url.rstrip('/')}"
            "/openapi.json"
        )

        decision = (
            self.policy_engine.evaluate(
                target=target,
                authorization_revision=authorization_revision,
                scopes=scopes,
                request_url=openapi_url,
                method="GET",
            )
        )

        if not decision.allowed:
            self._observe_policy_decision(decision, policy_decision_observer)
            raise OpenAPIPolicyDenied(
                decision
            )
        if refresh_authorization is None:
            raise OpenAPIExecutionBlocked(
                code="authorization_refresh_missing",
                reason=(
                    "Persisted authorization refresh is required before "
                    "OpenAPI fetch."
                ),
            )
        selected_revision_id = decision.authorization_revision_id

        try:
            self.rate_limiter.wait(
                key=f"target:{target.id}",
                requested_requests_per_second=(
                    authorization_revision.max_requests_per_second
                ),
            )
        except RateLimitConfigurationError as exc:
            raise OpenAPIExecutionBlocked(
                code="invalid_authorization_rate_limit",
                reason=(
                    "AuthorizationRevision request rate limit "
                    "must be finite and greater than zero."
                ),
            ) from exc

        target, authorization_revision, scopes = refresh_authorization()

        if (
            target.authorization_revision_id != selected_revision_id
            or authorization_revision is None
            or authorization_revision.id != selected_revision_id
        ):
            raise OpenAPIExecutionBlocked(
                code="authorization_revision_changed",
                reason="Target authorization revision changed before fetch.",
            )

        decision = (
            self.policy_engine.evaluate(
                target=target,
                authorization_revision=authorization_revision,
                scopes=scopes,
                request_url=openapi_url,
                method="GET",
            )
        )

        self._observe_policy_decision(decision, policy_decision_observer)

        if not decision.allowed:
            raise OpenAPIPolicyDenied(
                decision
            )

        if target.network_mode != PRIVATE_LOCAL:
            raise OpenAPIExecutionBlocked(
                code="external_network_mode_not_ready",
                reason=(
                    "External/public-authorized OpenAPI retrieval is not "
                    "available until the remaining release gates exist."
                ),
            )

        schema = self._fetch_schema(target=target, url=openapi_url)

        try:
            endpoints = (
                parse_openapi_schema(
                    schema
                )
            )
        except ValueError as exc:
            raise OpenAPIScanError(
                "OpenAPI schema structure is invalid"
            ) from exc

        return (
            openapi_url,
            endpoints,
        )

    @staticmethod
    def _observe_policy_decision(
        decision: PolicyDecision,
        observer: Callable[[PolicyDecision], None] | None,
    ) -> None:
        if observer is None:
            raise OpenAPIAuditError(
                "Required safety audit observer is unavailable."
            )
        try:
            observer(decision)
        except Exception as exc:
            raise OpenAPIAuditError(
                "Required safety audit record could not be persisted."
            ) from exc

    def _fetch_schema(
        self,
        *,
        target: Target,
        url: str,
    ) -> dict[str, Any]:
        try:
            response = self.network_gateway.request(
                target_id=target.id,
                network_mode=target.network_mode,
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
                max_response_bytes=MAX_OPENAPI_RESPONSE_BYTES,
                timeout_seconds=5.0,
            )
        except NetworkGatewayError as exc:
            raise OpenAPIScanError(
                f"{exc.code}: {exc.reason}"
            ) from exc
        if response.status_code >= 400:
            raise OpenAPIScanError("OpenAPI retrieval returned an error status")

        try:
            data = json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise OpenAPIScanError(
                "OpenAPI response is not valid JSON"
            ) from exc

        if not isinstance(data, dict):
            raise OpenAPIScanError(
                "OpenAPI root must be a JSON object"
            )

        return data
