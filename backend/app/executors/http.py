from dataclasses import dataclass
import time
from collections.abc import Callable

import httpx

from app.db.models.authorization_revision import AuthorizationRevision
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


AUTO_EXECUTABLE_METHODS = frozenset(
    {
        "GET",
    }
)


MAX_RESPONSE_BYTES = 1_000_000


class HTTPExecutionError(RuntimeError):
    pass


class ExecutionBlockedError(
    HTTPExecutionError
):
    def __init__(
        self,
        *,
        code: str,
        reason: str,
    ) -> None:
        self.code = code
        self.reason = reason

        super().__init__(
            f"{code}: {reason}"
        )


@dataclass(frozen=True)
class HTTPExecutionResult:
    status_code: int
    body: bytes
    duration_ms: int


class PolicyEnforcedHTTPExecutor:
    def __init__(
        self,
        *,
        policy_engine: ScopePolicyEngine,
        rate_limiter: InMemoryRateLimiter,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.policy_engine = policy_engine
        self.rate_limiter = rate_limiter
        self.transport = transport

    def execute(
        self,
        *,
        target: Target,
        authorization_revision: AuthorizationRevision | None,
        scopes: list[Scope],
        method: str,
        url: str,
        headers: dict[str, str],
        refresh_authorization: Callable[
            [], tuple[Target, AuthorizationRevision | None, list[Scope]]
        ] | None = None,
        policy_decision_observer: Callable[[PolicyDecision], None] | None = None,
    ) -> HTTPExecutionResult:
        normalized_method = (
            method.strip().upper()
        )

        if (
            normalized_method
            not in AUTO_EXECUTABLE_METHODS
        ):
            raise ExecutionBlockedError(
                code="automatic_method_blocked",
                reason=(
                    "Automatic execution is currently "
                    "limited to GET requests."
                ),
            )

        decision = self.policy_engine.evaluate(
            target=target,
            authorization_revision=authorization_revision,
            scopes=scopes,
            request_url=url,
            method=normalized_method,
        )

        if not decision.allowed:
            self._observe_policy_decision(decision, policy_decision_observer)
            self._raise_if_denied(decision)
        if refresh_authorization is None:
            raise ExecutionBlockedError(
                code="authorization_refresh_missing",
                reason=(
                    "Persisted authorization refresh is required before "
                    "network execution."
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
            raise ExecutionBlockedError(
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
            raise ExecutionBlockedError(
                code="authorization_revision_changed",
                reason=(
                    "Target authorization revision changed before execution."
                ),
            )

        decision = self.policy_engine.evaluate(
            target=target,
            authorization_revision=authorization_revision,
            scopes=scopes,
            request_url=url,
            method=normalized_method,
        )

        self._observe_policy_decision(decision, policy_decision_observer)
        self._raise_if_denied(decision)

        return self._send(
            method=normalized_method,
            url=url,
            headers=headers,
        )

    @staticmethod
    def _observe_policy_decision(
        decision: PolicyDecision,
        observer: Callable[[PolicyDecision], None] | None,
    ) -> None:
        if observer is None:
            raise ExecutionBlockedError(
                code="safety_audit_observer_missing",
                reason="Required safety audit observer is unavailable.",
            )
        try:
            observer(decision)
        except ExecutionBlockedError:
            raise
        except Exception as exc:
            raise ExecutionBlockedError(
                code="safety_audit_persistence_failed",
                reason="Required safety audit record could not be persisted.",
            ) from exc

    @staticmethod
    def _raise_if_denied(
        decision: PolicyDecision,
    ) -> None:
        if decision.allowed:
            return

        raise ExecutionBlockedError(
            code=decision.code,
            reason=decision.reason,
        )

    def _send(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
    ) -> HTTPExecutionResult:
        timeout = httpx.Timeout(
            5.0
        )

        started_at = time.monotonic()

        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                with client.stream(
                    method,
                    url,
                    headers=headers,
                ) as response:
                    chunks: list[bytes] = []
                    total_size = 0

                    for chunk in response.iter_bytes():
                        total_size += len(chunk)

                        if (
                            total_size
                            > MAX_RESPONSE_BYTES
                        ):
                            raise HTTPExecutionError(
                                "Response exceeded "
                                "maximum allowed size."
                            )

                        chunks.append(chunk)

                    body = b"".join(chunks)

        except HTTPExecutionError:
            raise

        except httpx.HTTPError as exc:
            raise HTTPExecutionError(
                f"HTTP request failed: {exc}"
            ) from exc

        duration_ms = int(
            (
                time.monotonic()
                - started_at
            )
            * 1000
        )

        return HTTPExecutionResult(
            status_code=response.status_code,
            body=body,
            duration_ms=duration_ms,
        )
