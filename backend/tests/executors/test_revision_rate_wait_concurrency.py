from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from app.db.models import AuthorizationProfile, AuthorizationRevision, Scope, Target
from app.db.session import SessionLocal, engine
from app.executors.http import ExecutionBlockedError, PolicyEnforcedHTTPExecutor
from app.policies.scope_policy import ScopePolicyEngine
from app.scanners.openapi import (
    OpenAPIExecutionBlocked,
    OpenAPIPolicyDenied,
    OpenAPIScanner,
)
from app.services.authorization_revision import transition_revision
from app.services.execution_authorization import (
    build_execution_authorization_refresh,
    load_execution_authorization,
)
from tests.network_gateway_fakes import HandlerNetworkGateway, StaticJSONNetworkGateway


class BlockingRateLimiter:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    def wait(self, **kwargs) -> None:
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("rate wait release timed out")


@pytest.mark.parametrize("component", ["executor", "openapi"])
@pytest.mark.parametrize("change", ["revoke", "supersede", "rebind"])
def test_rate_wait_revalidation_blocks_persisted_revision_change(
    component: str,
    change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with SessionLocal() as db:
        profile = AuthorizationProfile(
            name=f"rate-wait-{uuid4()}",
            program_name="Program",
            authorization_type="self_owned",
        )
        db.add(profile)
        db.flush()
        revisions = [
            AuthorizationRevision(
                authorization_profile_id=profile.id,
                revision_number=number,
                lifecycle_state="active" if number == 1 else "draft",
                name=profile.name,
                program_name=profile.program_name,
                authorization_type=profile.authorization_type,
                automation_allowed=True,
                max_requests_per_second=1.0,
                allow_get=True,
                require_human_execution_approval=False,
            )
            for number in (1, 2)
        ]
        db.add_all(revisions)
        db.flush()
        target = Target(
            name=f"rate-wait-target-{uuid4()}",
            base_url="https://example.test",
            environment="test",
            authorization_profile_id=profile.id,
            authorization_revision_id=revisions[0].id,
        )
        db.add(target)
        db.flush()
        db.add(
            Scope(
                target_id=target.id,
                hostname="example.test",
                path_pattern="/*",
                allowed_methods=["GET"],
                is_active=True,
            )
        )
        db.commit()
        profile_id = profile.id
        target_id = target.id
        first_id, second_id = (revision.id for revision in revisions)

    with SessionLocal() as db:
        target, revision, scopes = load_execution_authorization(db, target_id)
        db.expunge_all()

    limiter = BlockingRateLimiter()
    network_calls = 0

    def network_response(*args, **kwargs):
        nonlocal network_calls
        network_calls += 1
        return httpx.Response(200, json={"paths": {}})

    policy = ScopePolicyEngine({"example.test"})
    refresh = build_execution_authorization_refresh(engine, target_id)
    if component == "executor":
        runner = PolicyEnforcedHTTPExecutor(
            policy_engine=policy,
            rate_limiter=limiter,
            network_gateway=HandlerNetworkGateway(
                lambda request: network_response()
            ),
        )
        operation = lambda: runner.execute(
            target=target,
            authorization_revision=revision,
            scopes=scopes,
            method="GET",
            url="https://example.test/resource",
            headers={},
            refresh_authorization=refresh,
            policy_decision_observer=lambda decision: None,
        )
        expected_error = ExecutionBlockedError
    else:
        runner = OpenAPIScanner(policy, limiter, StaticJSONNetworkGateway())
        monkeypatch.setattr(runner, "_fetch_schema", network_response)
        operation = lambda: runner.scan(
            target=target,
            authorization_revision=revision,
            scopes=scopes,
            source_url="https://example.test/openapi.json",
            refresh_authorization=refresh,
            policy_decision_observer=lambda decision: None,
        )
        expected_error = (
            OpenAPIExecutionBlocked if change == "rebind" else OpenAPIPolicyDenied
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(operation)
            assert limiter.entered.wait(timeout=5)
            with SessionLocal() as db:
                if change == "revoke":
                    transition_revision(db, profile_id, first_id, "revoked")
                else:
                    transition_revision(db, profile_id, second_id, "active")
                    if change == "rebind":
                        persisted_target = db.scalar(
                            select(Target)
                            .where(Target.id == target_id)
                            .with_for_update()
                        )
                        persisted_target.authorization_revision_id = second_id
                        db.commit()
            limiter.release.set()
            with pytest.raises(expected_error):
                future.result(timeout=5)
        assert network_calls == 0
    finally:
        limiter.release.set()
        with SessionLocal() as db:
            db.execute(delete(Target).where(Target.id == target_id))
            db.execute(
                delete(AuthorizationRevision).where(
                    AuthorizationRevision.authorization_profile_id
                    == profile_id
                )
            )
            db.execute(
                delete(AuthorizationProfile).where(
                    AuthorizationProfile.id == profile_id
                )
            )
            db.commit()
