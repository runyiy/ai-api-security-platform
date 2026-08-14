from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
from urllib.parse import unquote, urlsplit

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.scope import Scope
from app.db.models.target import Target


MVP_HTTP_METHODS = frozenset(
    {
        "GET",
        "POST",
        "PATCH",
        "DELETE",
    }
)


AUTHORIZATION_METHOD_FIELDS = {
    "GET": "allow_get",
    "POST": "allow_post",
    "PATCH": "allow_patch",
    "DELETE": "allow_delete",
}


@dataclass(frozen=True)
class Origin:
    scheme: str
    hostname: str
    port: int


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str
    reason: str
    matched_scope_id: int | None = None


class PolicyValidationError(ValueError):
    pass


def normalize_hostname(hostname: str) -> str:
    value = hostname.strip().lower().rstrip(".")

    if not value:
        raise PolicyValidationError("hostname is empty")

    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return value


def parse_origin(url: str) -> Origin:
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise PolicyValidationError("invalid URL") from exc

    if parsed.scheme not in {"http", "https"}:
        raise PolicyValidationError(
            "only http and https URLs are supported"
        )

    if parsed.username is not None or parsed.password is not None:
        raise PolicyValidationError(
            "URL userinfo is not allowed"
        )

    if not parsed.hostname:
        raise PolicyValidationError(
            "URL hostname is missing"
        )

    try:
        explicit_port = parsed.port
    except ValueError as exc:
        raise PolicyValidationError(
            "invalid URL port"
        ) from exc

    if explicit_port is not None:
        port = explicit_port
    elif parsed.scheme == "http":
        port = 80
    else:
        port = 443

    return Origin(
        scheme=parsed.scheme,
        hostname=normalize_hostname(parsed.hostname),
        port=port,
    )


def normalize_request_path(url: str) -> str:
    parsed = urlsplit(url)

    raw_path = parsed.path or "/"
    lower_path = raw_path.lower()

    # 第一版采取保守策略：
    # 遇到这些容易产生解析歧义的编码，直接拒绝。
    blocked_encodings = (
        "%2f",   # encoded /
        "%5c",   # encoded backslash
        "%00",   # null byte
        "%25",   # encoded %, 避免部分 double encoding 场景
    )

    if any(item in lower_path for item in blocked_encodings):
        raise PolicyValidationError(
            "ambiguous encoded path is not allowed"
        )

    decoded_path = unquote(raw_path)

    if "\x00" in decoded_path:
        raise PolicyValidationError(
            "null byte is not allowed in path"
        )

    if "\\" in decoded_path:
        raise PolicyValidationError(
            "backslash is not allowed in path"
        )

    if "//" in decoded_path:
        raise PolicyValidationError(
            "double slash is not allowed in path"
        )

    segments = decoded_path.split("/")

    if any(segment in {".", ".."} for segment in segments):
        raise PolicyValidationError(
            "dot path segments are not allowed"
        )

    if not decoded_path.startswith("/"):
        raise PolicyValidationError(
            "path must start with '/'"
        )

    return decoded_path


def normalize_scope_path_pattern(pattern: str) -> str:
    value = pattern.strip()

    if not value.startswith("/"):
        raise PolicyValidationError(
            "scope path must start with '/'"
        )

    if "*" not in value:
        return value

    if value.count("*") != 1:
        raise PolicyValidationError(
            "only one wildcard is allowed"
        )

    if not value.endswith("/*"):
        raise PolicyValidationError(
            "wildcard is only supported as trailing /*"
        )

    return value


def path_matches_scope(
    request_path: str,
    scope_pattern: str,
) -> bool:
    pattern = normalize_scope_path_pattern(
        scope_pattern
    )

    if "*" not in pattern:
        return request_path == pattern

    base_path = pattern[:-2]

    if base_path == "":
        return True

    return (
        request_path == base_path
        or request_path.startswith(
            f"{base_path}/"
        )
    )


class ScopePolicyEngine:
    def __init__(
        self,
        platform_allowed_hosts: set[str],
    ) -> None:
        self.platform_allowed_hosts = {
            normalize_hostname(host)
            for host in platform_allowed_hosts
        }

    def evaluate(
        self,
        *,
        target: Target,
        authorization_profile: AuthorizationProfile | None,
        scopes: list[Scope],
        request_url: str,
        method: str,
        evaluation_time: datetime | None = None,
    ) -> PolicyDecision:
        if not target.is_enabled:
            return PolicyDecision(
                allowed=False,
                code="target_disabled",
                reason="Target is disabled.",
            )

        if target.authorization_profile_id is None:
            return PolicyDecision(
                allowed=False,
                code="authorization_profile_missing",
                reason=(
                    "Target has no AuthorizationProfile binding."
                ),
            )

        if authorization_profile is None:
            return PolicyDecision(
                allowed=False,
                code="authorization_profile_missing",
                reason=(
                    "The bound AuthorizationProfile was not provided."
                ),
            )

        if (
            authorization_profile.id
            != target.authorization_profile_id
        ):
            return PolicyDecision(
                allowed=False,
                code="authorization_profile_mismatch",
                reason=(
                    "AuthorizationProfile does not match "
                    "the Target binding."
                ),
            )

        current_time = evaluation_time or datetime.now(timezone.utc)

        if current_time.tzinfo is None:
            raise ValueError("evaluation_time must be timezone-aware")

        current_time = current_time.astimezone(timezone.utc)

        if (
            authorization_profile.valid_from is not None
            and current_time < authorization_profile.valid_from
        ):
            return PolicyDecision(
                allowed=False,
                code="authorization_not_yet_valid",
                reason="Authorization is not yet valid.",
            )

        if (
            authorization_profile.valid_until is not None
            and current_time >= authorization_profile.valid_until
        ):
            return PolicyDecision(
                allowed=False,
                code="authorization_expired",
                reason="Authorization has expired.",
            )

        if not authorization_profile.automation_allowed:
            return PolicyDecision(
                allowed=False,
                code="automation_not_allowed",
                reason=(
                    "AuthorizationProfile does not allow automation."
                ),
            )

        if authorization_profile.require_human_execution_approval:
            return PolicyDecision(
                allowed=False,
                code="human_approval_required",
                reason=(
                    "AuthorizationProfile requires human execution "
                    "approval, which is not available."
                ),
            )

        try:
            request_origin = parse_origin(
                request_url
            )
        except PolicyValidationError as exc:
            return PolicyDecision(
                allowed=False,
                code="invalid_request_url",
                reason=str(exc),
            )

        if (
            request_origin.hostname
            not in self.platform_allowed_hosts
        ):
            return PolicyDecision(
                allowed=False,
                code="host_not_in_platform_allowlist",
                reason=(
                    f"Host "
                    f"{request_origin.hostname!r} "
                    "is not in the platform allowlist."
                ),
            )

        try:
            target_origin = parse_origin(
                target.base_url
            )
        except PolicyValidationError as exc:
            return PolicyDecision(
                allowed=False,
                code="invalid_target_url",
                reason=str(exc),
            )

        if request_origin != target_origin:
            return PolicyDecision(
                allowed=False,
                code="target_origin_mismatch",
                reason=(
                    "Request origin does not match "
                    "the configured target origin."
                ),
            )

        normalized_method = (
            method.strip().upper()
        )

        if normalized_method not in MVP_HTTP_METHODS:
            return PolicyDecision(
                allowed=False,
                code="unsupported_http_method",
                reason=(
                    f"HTTP method "
                    f"{normalized_method!r} "
                    "is not supported by the MVP."
                ),
            )

        method_field = AUTHORIZATION_METHOD_FIELDS[normalized_method]

        if not getattr(authorization_profile, method_field):
            return PolicyDecision(
                allowed=False,
                code="authorization_method_not_allowed",
                reason=(
                    "AuthorizationProfile does not allow "
                    f"HTTP method {normalized_method!r}."
                ),
            )

        try:
            request_path = normalize_request_path(
                request_url
            )
        except PolicyValidationError as exc:
            return PolicyDecision(
                allowed=False,
                code="unsafe_request_path",
                reason=str(exc),
            )

        for scope in scopes:
            if not scope.is_active:
                continue

            try:
                scope_hostname = (
                    normalize_hostname(
                        scope.hostname
                    )
                )
            except PolicyValidationError:
                continue

            if (
                scope_hostname
                != request_origin.hostname
            ):
                continue

            scope_methods = {
                item.strip().upper()
                for item in scope.allowed_methods
            }

            if (
                normalized_method
                not in scope_methods
            ):
                continue

            try:
                path_matches = (
                    path_matches_scope(
                        request_path,
                        scope.path_pattern,
                    )
                )
            except PolicyValidationError:
                continue

            if not path_matches:
                continue

            return PolicyDecision(
                allowed=True,
                code="allowed_by_scope",
                reason=(
                    "Request matches an active scope."
                ),
                matched_scope_id=scope.id,
            )

        return PolicyDecision(
            allowed=False,
            code="no_matching_scope",
            reason=(
                "No active scope allows this "
                "hostname, path, and HTTP method."
            ),
        )
