from collections.abc import Iterator
import base64
import json
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select

from app.analyzers.bola import AnalysisOutcome
from app.api.routes.test_cases import generate_bola_cases
from app.core.config import settings
from app.credentials.bearer import BearerCredentialService
from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.credential_binding import CredentialBinding
from app.db.models.credential_secret_version import CredentialSecretVersion
from app.db.models.endpoint import Endpoint
from app.db.models.finding import Finding
from app.db.models.resource import Resource
from app.db.models.scope import Scope
from app.db.models.safety_decision_record import SafetyDecisionRecord
from app.db.models.target import Target
from app.db.models.test_case import TestCase as StoredCase
from app.db.models.test_identity import TestIdentity as StoredIdentity
from app.db.models.test_run import TestRun as StoredRun
from app.db.session import SessionLocal
from app.executors.http import PolicyEnforcedHTTPExecutor
from app.executors.rate_limit import InMemoryRateLimiter
from app.generators.bola import BOLA_CROSS_OWNER, OWNER_BASELINE
from app.policies.scope_policy import ScopePolicyEngine
from app.schemas.test_case import GenerateBOLATestCasesRequest
from app.services.finding_analysis import FindingAnalysisService
from app.services.test_execution import (
    TestExecutionError,
    TestExecutionService as ExecutionService,
)
from tests.labs.bola_lab import BOLALabMode, RunningBOLALab, run_bola_lab


class RecordingScopePolicyEngine(ScopePolicyEngine):
    def __init__(self, platform_allowed_hosts: set[str]) -> None:
        super().__init__(platform_allowed_hosts)
        self.evaluation_count = 0

    def evaluate(self, **kwargs):
        self.evaluation_count += 1
        return super().evaluate(**kwargs)


@pytest.fixture(autouse=True)
def credential_key(monkeypatch: pytest.MonkeyPatch) -> None:
    encoded_key = base64.urlsafe_b64encode(b"l" * 32).decode("ascii")
    monkeypatch.setattr(
        settings,
        "credential_encryption_key",
        SecretStr(encoded_key),
    )
    monkeypatch.setattr(settings, "credential_encryption_key_version", "lab-v1")


@pytest.fixture(params=[BOLALabMode.SECURE, BOLALabMode.VULNERABLE])
def bola_lab(request: pytest.FixtureRequest) -> Iterator[RunningBOLALab]:
    with run_bola_lab(request.param) as lab:
        yield lab


def _seed_workflow(
    lab: RunningBOLALab,
    *,
    provision_credentials: bool = True,
) -> tuple[int, int, int, int]:
    unique_name = f"bola-lab-{lab.mode}-{uuid4()}"

    with SessionLocal() as db:
        authorization_profile = AuthorizationProfile(
            name=f"{unique_name}-authorization",
            program_name="Deterministic local BOLA lab",
            authorization_type="self_owned",
            automation_allowed=True,
            max_requests_per_second=1000.0,
            allow_get=True,
            require_human_execution_approval=False,
        )
        db.add(authorization_profile)
        db.flush()
        authorization_revision = AuthorizationRevision(
            authorization_profile_id=authorization_profile.id,
            revision_number=1,
            lifecycle_state="active",
            name=authorization_profile.name,
            program_name=authorization_profile.program_name,
            authorization_type=authorization_profile.authorization_type,
            automation_allowed=True,
            max_requests_per_second=1000.0,
            allow_get=True,
            require_human_execution_approval=False,
        )
        db.add(authorization_revision)
        db.flush()

        target = Target(
            name=unique_name,
            base_url=lab.base_url,
            environment="test",
            is_enabled=True,
            authorization_profile_id=authorization_profile.id,
            authorization_revision_id=authorization_revision.id,
        )
        db.add(target)
        db.flush()

        scope = Scope(
            target_id=target.id,
            hostname=lab.hostname,
            path_pattern="/orders/*",
            allowed_methods=["GET"],
            is_active=True,
        )
        alice = StoredIdentity(
            target_id=target.id,
            name="Alice",
            role="customer",
            auth_type="bearer",
            credentials=(
                None
                if provision_credentials
                else {"access_token": "legacy-alice-token"}
            ),
            is_active=True,
        )
        bob = StoredIdentity(
            target_id=target.id,
            name="Bob",
            role="customer",
            auth_type="bearer",
            credentials=(
                None
                if provision_credentials
                else {"access_token": "legacy-bob-token"}
            ),
            is_active=True,
        )
        endpoint = Endpoint(
            target_id=target.id,
            path="/orders/{order_id}",
            method="GET",
            operation_id="get_order",
            requires_auth=True,
            parameters=[],
            request_body=None,
            security=None,
        )
        db.add_all([scope, alice, bob, endpoint])
        db.flush()
        if provision_credentials:
            credential_service = BearerCredentialService(db=db)
            credential_service.provision(
                identity=alice,
                token=SecretStr("alice-token"),
            )
            credential_service.provision(
                identity=bob,
                token=SecretStr("bob-token"),
            )

        alice_order = Resource(
            target_id=target.id,
            resource_type="order",
            external_id="1001",
            owner_identity_id=alice.id,
        )
        bob_order = Resource(
            target_id=target.id,
            resource_type="order",
            external_id="2001",
            owner_identity_id=bob.id,
        )
        db.add_all([alice_order, bob_order])
        db.commit()

        generation = generate_bola_cases(
            payload=GenerateBOLATestCasesRequest(target_id=target.id),
            db=db,
        )
        assert generation.generated == 4
        assert generation.created == 4

        owner_case_id = db.scalar(
            select(StoredCase.id).where(
                StoredCase.endpoint_id == endpoint.id,
                StoredCase.actor_identity_id == alice.id,
                StoredCase.resource_id == alice_order.id,
                StoredCase.test_type == OWNER_BASELINE,
            )
        )
        cross_owner_case_id = db.scalar(
            select(StoredCase.id).where(
                StoredCase.endpoint_id == endpoint.id,
                StoredCase.actor_identity_id == bob.id,
                StoredCase.resource_id == alice_order.id,
                StoredCase.test_type == BOLA_CROSS_OWNER,
            )
        )

        assert owner_case_id is not None
        assert cross_owner_case_id is not None
        return (
            target.id,
            authorization_profile.id,
            owner_case_id,
            cross_owner_case_id,
        )


def _delete_workflow(target_id: int, profile_id: int) -> None:
    with SessionLocal() as db:
        db.execute(
            delete(SafetyDecisionRecord).where(
                SafetyDecisionRecord.target_id == target_id
            )
        )
        test_case_ids = select(StoredCase.id).join(Endpoint).where(
            Endpoint.target_id == target_id
        )
        db.execute(delete(Finding).where(Finding.target_id == target_id))
        db.execute(delete(StoredRun).where(StoredRun.test_case_id.in_(test_case_ids)))
        db.execute(delete(StoredCase).where(StoredCase.id.in_(test_case_ids)))
        db.execute(delete(Resource).where(Resource.target_id == target_id))
        binding_ids = select(CredentialBinding.id).join(StoredIdentity).where(
            StoredIdentity.target_id == target_id
        )
        db.execute(
            delete(CredentialSecretVersion).where(
                CredentialSecretVersion.credential_binding_id.in_(binding_ids)
            )
        )
        db.execute(
            delete(CredentialBinding).where(CredentialBinding.id.in_(binding_ids))
        )
        db.execute(delete(Target).where(Target.id == target_id))
        db.execute(
            delete(AuthorizationRevision).where(
                AuthorizationRevision.authorization_profile_id == profile_id
            )
        )
        db.execute(
            delete(AuthorizationProfile).where(
                AuthorizationProfile.id == profile_id
            )
        )
        db.commit()


def test_local_bola_workflow_end_to_end(bola_lab: RunningBOLALab) -> None:
    (
        target_id,
        profile_id,
        owner_case_id,
        cross_owner_case_id,
    ) = _seed_workflow(bola_lab)
    policy_engine = RecordingScopePolicyEngine({bola_lab.hostname})
    executor = PolicyEnforcedHTTPExecutor(
        policy_engine=policy_engine,
        rate_limiter=InMemoryRateLimiter(requests_per_second=1000.0),
    )

    try:
        with SessionLocal() as db:
            class TransactionCheckingExecutor:
                def execute(self, **kwargs):
                    assert db.in_transaction() is False
                    return executor.execute(**kwargs)

            execution = ExecutionService(db=db, executor=executor)
            execution.executor = TransactionCheckingExecutor()
            owner_run = execution.execute(test_case_id=owner_case_id)
            cross_owner_run = execution.execute(test_case_id=cross_owner_case_id)

            assert policy_engine.evaluation_count == 4
            assert owner_run.response_status == 200
            assert json.loads(owner_run.response_body or "null")["id"] == 1001
            assert owner_run.request_data["headers"]["Authorization"] == "[REDACTED]"
            assert "alice-token" not in json.dumps(owner_run.request_data)
            assert "bob-token" not in json.dumps(cross_owner_run.request_data)

            analysis = FindingAnalysisService(db=db).analyze_test_run(
                test_run_id=cross_owner_run.id
            )

            if bola_lab.mode == BOLALabMode.SECURE:
                assert cross_owner_run.response_status == 403
                assert analysis.analysis.outcome == AnalysisOutcome.PASS
                assert analysis.finding is None
            else:
                assert cross_owner_run.response_status == 200
                response = json.loads(cross_owner_run.response_body or "null")
                assert response["id"] == 1001
                assert response["owner"] == "alice"
                assert analysis.analysis.outcome == AnalysisOutcome.POTENTIAL_BOLA
                assert analysis.finding is not None
                assert analysis.finding.status == "potential"
    finally:
        _delete_workflow(target_id, profile_id)


def test_legacy_plaintext_only_bearer_identity_fails_closed(
    bola_lab: RunningBOLALab,
) -> None:
    (
        target_id,
        profile_id,
        owner_case_id,
        _,
    ) = _seed_workflow(bola_lab, provision_credentials=False)

    class NeverCalledExecutor:
        def execute(self, **kwargs):
            raise AssertionError("network execution must not occur")

    try:
        with SessionLocal() as db:
            with pytest.raises(
                TestExecutionError,
                match="Bearer credential is unavailable",
            ) as raised:
                ExecutionService(
                    db=db,
                    executor=NeverCalledExecutor(),
                ).execute(test_case_id=owner_case_id)

            assert "legacy-alice-token" not in str(raised.value)
    finally:
        _delete_workflow(target_id, profile_id)
