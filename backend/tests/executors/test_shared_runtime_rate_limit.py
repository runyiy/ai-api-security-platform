import httpx

from app.api.routes import openapi as openapi_routes
from app.api.routes import test_runs as test_run_routes
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.executors.http import PolicyEnforcedHTTPExecutor
from app.executors.rate_limit import InMemoryRateLimiter
from app.executors.runtime import platform_rate_limiter
from app.policies.scope_policy import ScopePolicyEngine
from app.scanners.openapi import OpenAPIScanner


class FakeTime:
    def __init__(self) -> None:
        self.current = 100.0
        self.delays: list[float] = []

    def monotonic(self) -> float:
        return self.current

    def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        self.current += delay


def build_policy_objects(
    target_id: int,
    revision_id: int,
) -> tuple[Target, AuthorizationRevision, Scope]:
    target = Target(
        id=target_id,
        authorization_profile_id=revision_id,
        authorization_revision_id=revision_id,
        name=f"Target {target_id}",
        base_url="https://example.test",
        environment="test",
        is_enabled=True,
    )
    revision = AuthorizationRevision(
        id=revision_id,
        authorization_profile_id=revision_id,
        revision_number=1,
        lifecycle_state="active",
        name=f"Authorization {revision_id}",
        program_name="Self-controlled test",
        authorization_type="self_owned",
        automation_allowed=True,
        max_requests_per_second=100.0,
        allow_get=True,
        require_human_execution_approval=False,
    )
    scope = Scope(
        id=target_id,
        target_id=target_id,
        hostname="example.test",
        path_pattern="/*",
        allowed_methods=["GET"],
        is_active=True,
    )
    return target, revision, scope


def test_production_paths_share_limiter_identity() -> None:
    assert test_run_routes.executor.rate_limiter is platform_rate_limiter
    assert openapi_routes.scanner.rate_limiter is platform_rate_limiter
    assert (
        test_run_routes.executor.rate_limiter
        is openapi_routes.scanner.rate_limiter
    )


def test_cross_path_reservations_share_target_schedule(
    monkeypatch,
) -> None:
    fake_time = FakeTime()
    shared_limiter = InMemoryRateLimiter(
        requests_per_second=2.0,
        monotonic=fake_time.monotonic,
        sleep=fake_time.sleep,
    )
    policy_engine = ScopePolicyEngine({"example.test"})
    executor = PolicyEnforcedHTTPExecutor(
        policy_engine=policy_engine,
        rate_limiter=shared_limiter,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": True})
        ),
    )
    scanner = OpenAPIScanner(policy_engine, shared_limiter)
    monkeypatch.setattr(
        scanner,
        "_fetch_schema",
        lambda url: {"paths": {}},
    )
    target, revision, scope = build_policy_objects(1, 101)

    executor.execute(
        target=target,
        authorization_revision=revision,
        scopes=[scope],
        method="GET",
        url="https://example.test/projects/1",
        headers={},
        refresh_authorization=lambda: (target, revision, [scope]),
    )
    scanner.scan(
        target=target,
        authorization_revision=revision,
        scopes=[scope],
        refresh_authorization=lambda: (target, revision, [scope]),
    )

    assert fake_time.delays == [0.5]

    other_target, other_revision, other_scope = build_policy_objects(2, 202)
    scanner.scan(
        target=other_target,
        authorization_revision=other_revision,
        scopes=[other_scope],
        refresh_authorization=lambda: (
            other_target,
            other_revision,
            [other_scope],
        ),
    )

    assert fake_time.delays == [0.5]
