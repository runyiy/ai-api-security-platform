import json

import httpx

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.executors.rate_limit import (
    InMemoryRateLimiter,
    RateLimitConfigurationError,
)
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
    ) -> None:
        self.policy_engine = policy_engine
        self.rate_limiter = rate_limiter

    def scan(
        self,
        *,
        target: Target,
        authorization_profile: AuthorizationProfile | None,
        scopes: list[Scope],
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
                authorization_profile=authorization_profile,
                scopes=scopes,
                request_url=openapi_url,
                method="GET",
            )
        )

        if not decision.allowed:
            raise OpenAPIPolicyDenied(
                decision
            )

        try:
            self.rate_limiter.wait(
                key=f"target:{target.id}",
                requested_requests_per_second=(
                    authorization_profile.max_requests_per_second
                ),
            )
        except RateLimitConfigurationError as exc:
            raise OpenAPIPolicyDenied(
                PolicyDecision(
                    allowed=False,
                    code="invalid_authorization_rate_limit",
                    reason=(
                        "AuthorizationProfile request rate limit "
                        "must be finite and greater than zero."
                    ),
                )
            ) from exc

        decision = (
            self.policy_engine.evaluate(
                target=target,
                authorization_profile=authorization_profile,
                scopes=scopes,
                request_url=openapi_url,
                method="GET",
            )
        )

        if not decision.allowed:
            raise OpenAPIPolicyDenied(
                decision
            )

        schema = self._fetch_schema(
            openapi_url
        )

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

    def _fetch_schema(
        self,
        url: str,
    ) -> dict[str, Any]:
        timeout = httpx.Timeout(
            5.0
        )

        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    "GET",
                    url,
                    headers={
                        "Accept": (
                            "application/json"
                        )
                    },
                ) as response:
                    response.raise_for_status()

                    chunks: list[bytes] = []
                    total_size = 0

                    for chunk in (
                        response.iter_bytes()
                    ):
                        total_size += len(chunk)

                        if (
                            total_size
                            > MAX_OPENAPI_RESPONSE_BYTES
                        ):
                            raise OpenAPIScanError(
                                "OpenAPI response "
                                "exceeded size limit"
                            )

                        chunks.append(
                            chunk
                        )

        except httpx.HTTPError as exc:
            raise OpenAPIScanError(
                f"Unable to fetch OpenAPI schema: "
                f"{exc}"
            ) from exc

        raw = b"".join(chunks)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpenAPIScanError(
                "OpenAPI response is not valid JSON"
            ) from exc

        if not isinstance(data, dict):
            raise OpenAPIScanError(
                "OpenAPI root must be a JSON object"
            )

        return data
