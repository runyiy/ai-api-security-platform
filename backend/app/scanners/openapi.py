import hashlib
import json
import zlib
from collections.abc import Callable

from app.auth.context import (
    AuthenticationContext,
    AuthenticationContextError,
    apply_authentication_context,
)

from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.executors.rate_limit import (
    RateLimiter,
    RateLimitConfigurationError,
    RateLimitCoordinationError,
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
MAX_OPENAPI_DECOMPRESSED_BYTES = 1_000_000
MAX_OPENAPI_NESTING_DEPTH = 32
MAX_OPENAPI_STRUCTURE_NODES = 20_000
MAX_OPENAPI_PATHS = 2_000
MAX_OPENAPI_ENDPOINTS = 4_000
MAX_OPENAPI_PARAMETERS_PER_ENDPOINT = 128
MAX_OPENAPI_PATH_LENGTH = 500
MAX_OPENAPI_OPERATION_ID_LENGTH = 255


class OpenAPIScanError(RuntimeError):
    pass


class OpenAPIParseError(OpenAPIScanError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


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


@dataclass(frozen=True)
class OpenAPIScanResult:
    source_url: str
    document_sha256: str
    document_size_bytes: int
    content_encoding: str
    decoded_document_sha256: str
    decoded_document_size_bytes: int
    endpoints: list[ParsedEndpoint]
    credential_binding_id: int | None = None

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


def validate_openapi_structure(document: Any) -> None:
    nodes_seen = 0
    pending: list[tuple[Any, int]] = [(document, 1)]

    while pending:
        value, depth = pending.pop()
        if depth > MAX_OPENAPI_NESTING_DEPTH:
            raise OpenAPIParseError("openapi_document_too_deep")

        nodes_seen += 1
        if nodes_seen > MAX_OPENAPI_STRUCTURE_NODES:
            raise OpenAPIParseError("openapi_document_too_complex")

        if isinstance(value, dict):
            if "$ref" in value:
                raise OpenAPIParseError("openapi_references_not_supported")
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)


def decode_openapi_document(
    wire_body: bytes,
    content_encoding: str | None,
) -> tuple[str, bytes]:
    normalized = (
        "identity"
        if content_encoding is None
        else content_encoding.strip().lower()
    )
    if normalized not in {"identity", "gzip"}:
        raise OpenAPIParseError("openapi_content_encoding_not_supported")
    if normalized == "identity":
        if len(wire_body) > MAX_OPENAPI_DECOMPRESSED_BYTES:
            raise OpenAPIParseError("openapi_decompressed_body_too_large")
        return normalized, wire_body

    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    decoded = bytearray()
    pending = wire_body
    try:
        while pending:
            chunk, pending = pending[:64 * 1024], pending[64 * 1024:]
            while chunk:
                output = decoder.decompress(
                    chunk,
                    MAX_OPENAPI_DECOMPRESSED_BYTES - len(decoded) + 1,
                )
                decoded.extend(output)
                if len(decoded) > MAX_OPENAPI_DECOMPRESSED_BYTES:
                    raise OpenAPIParseError(
                        "openapi_decompressed_body_too_large"
                    )
                chunk = decoder.unconsumed_tail
                if decoder.eof:
                    if decoder.unused_data or chunk or pending:
                        raise OpenAPIParseError("openapi_compressed_body_invalid")
                    break

        if not decoder.eof:
            raise OpenAPIParseError("openapi_compressed_body_invalid")
        decoded.extend(
            decoder.flush(MAX_OPENAPI_DECOMPRESSED_BYTES - len(decoded) + 1)
        )
    except zlib.error as exc:
        raise OpenAPIParseError("openapi_compressed_body_invalid") from exc

    if len(decoded) > MAX_OPENAPI_DECOMPRESSED_BYTES:
        raise OpenAPIParseError("openapi_decompressed_body_too_large")
    return normalized, bytes(decoded)


def parse_openapi_schema(schema: dict[str, Any]) -> list[ParsedEndpoint]:
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI schema does not contain a valid 'paths' object")
    if len(paths) > MAX_OPENAPI_PATHS:
        raise OpenAPIParseError("openapi_too_many_paths")

    root_security = schema.get("security")
    if root_security is not None and not isinstance(root_security, list):
        raise ValueError("OpenAPI root security must be a list")

    endpoints: list[ParsedEndpoint] = []
    for path, path_item in paths.items():
        if not isinstance(path, str):
            continue
        if len(path) > MAX_OPENAPI_PATH_LENGTH:
            raise OpenAPIParseError("openapi_path_too_long")
        if not isinstance(path_item, dict):
            continue

        raw_path_parameters = path_item.get("parameters", [])
        path_parameters = (
            raw_path_parameters if isinstance(raw_path_parameters, list) else []
        )

        for method in SUPPORTED_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            if len(endpoints) >= MAX_OPENAPI_ENDPOINTS:
                raise OpenAPIParseError("openapi_too_many_endpoints")

            raw_operation_parameters = operation.get("parameters", [])
            operation_parameters = (
                raw_operation_parameters
                if isinstance(raw_operation_parameters, list)
                else []
            )
            parameters = merge_parameters(path_parameters, operation_parameters)
            if len(parameters) > MAX_OPENAPI_PARAMETERS_PER_ENDPOINT:
                raise OpenAPIParseError("openapi_too_many_parameters")

            security = operation.get("security", root_security)
            if security is not None and not isinstance(security, list):
                security = None

            request_body = operation.get("requestBody")
            if not isinstance(request_body, dict):
                request_body = None

            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str):
                operation_id = None
            elif len(operation_id) > MAX_OPENAPI_OPERATION_ID_LENGTH:
                raise OpenAPIParseError("openapi_operation_id_too_long")

            endpoints.append(ParsedEndpoint(
                path=path,
                method=method.upper(),
                operation_id=operation_id,
                requires_auth=security_requires_auth(security),
                parameters=parameters,
                request_body=request_body,
                security=security,
            ))

    return endpoints


class OpenAPIScanner:
    def __init__(
        self,
        policy_engine: ScopePolicyEngine,
        rate_limiter: RateLimiter,
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
        source_url: str,
        refresh_authorization: Callable[
            [], tuple[Target, AuthorizationRevision | None, list[Scope]]
        ] | None = None,
        policy_decision_observer: Callable[[PolicyDecision], None] | None = None,
        credential_binding_id: int | None = None,
        refresh_credential: Callable[[], AuthenticationContext] | None = None,
    ) -> OpenAPIScanResult:

        decision = (
            self.policy_engine.evaluate(
                target=target,
                authorization_revision=authorization_revision,
                scopes=scopes,
                request_url=source_url,
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
        except RateLimitCoordinationError as exc:
            raise OpenAPIExecutionBlocked(
                code="shared_rate_coordination_failed",
                reason="Shared request rate coordination failed.",
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
                request_url=source_url,
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

        authentication_context = None
        if credential_binding_id is not None:
            if refresh_credential is None:
                raise OpenAPIExecutionBlocked(
                    code="openapi_credential_unavailable",
                    reason="The selected OpenAPI credential is unavailable.",
                )
            try:
                authentication_context = refresh_credential()
            except Exception:
                raise OpenAPIExecutionBlocked(
                    code="openapi_credential_unavailable",
                    reason="The selected OpenAPI credential is unavailable.",
                ) from None

        (
            document_sha256,
            document_size_bytes,
            content_encoding,
            decoded_document_sha256,
            decoded_document_size_bytes,
            schema,
        ) = self._fetch_schema(
            target=target,
            url=source_url,
            authentication_context=authentication_context,
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

        return OpenAPIScanResult(
            source_url=source_url,
            document_sha256=document_sha256,
            document_size_bytes=document_size_bytes,
            endpoints=endpoints,
            content_encoding=content_encoding,
            decoded_document_sha256=decoded_document_sha256,
            decoded_document_size_bytes=decoded_document_size_bytes,
            credential_binding_id=credential_binding_id,
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
        authentication_context: AuthenticationContext | None = None,
    ) -> tuple[str, int, str, str, int, dict[str, Any]]:
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }
        if authentication_context is not None:
            try:
                headers = apply_authentication_context(
                    request_headers=headers,
                    context=authentication_context,
                )
            except AuthenticationContextError:
                raise OpenAPIExecutionBlocked(
                    code="openapi_credential_unavailable",
                    reason="The selected OpenAPI credential is unavailable.",
                ) from None
        try:
            response = self.network_gateway.request(
                target_id=target.id,
                network_mode=target.network_mode,
                method="GET",
                url=url,
                headers=headers,
                max_response_bytes=MAX_OPENAPI_RESPONSE_BYTES,
                timeout_seconds=5.0,
            )
        except NetworkGatewayError as exc:
            raise OpenAPIScanError(
                f"{exc.code}: {exc.reason}"
            ) from exc
        if response.status_code >= 400:
            raise OpenAPIScanError("OpenAPI retrieval returned an error status")

        # Provenance is derived once from the exact bounded gateway body before
        # decoding or parsing can transform it.
        document_sha256 = hashlib.sha256(response.body).hexdigest()
        document_size_bytes = len(response.body)
        content_encoding, decoded_body = decode_openapi_document(
            response.body, response.content_encoding
        )
        decoded_document_sha256 = hashlib.sha256(decoded_body).hexdigest()
        decoded_document_size_bytes = len(decoded_body)

        try:
            data = json.loads(decoded_body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise OpenAPIScanError(
                "OpenAPI response is not valid JSON"
            ) from exc
        except RecursionError as exc:
            raise OpenAPIParseError("openapi_document_too_deep") from exc

        validate_openapi_structure(data)

        if not isinstance(data, dict):
            raise OpenAPIScanError(
                "OpenAPI root must be a JSON object"
            )

        return (
            document_sha256,
            document_size_bytes,
            content_encoding,
            decoded_document_sha256,
            decoded_document_size_bytes,
            data,
        )
