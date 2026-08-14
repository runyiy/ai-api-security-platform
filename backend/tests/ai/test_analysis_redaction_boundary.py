import json
from unittest.mock import Mock

from sqlalchemy.orm import Session

from app.ai.redaction import NON_JSON_RESPONSE_REDACTED
from app.ai.schemas import (
    AIAnalysisResult,
    SanitizedFindingEvidence,
)
from app.db.models.endpoint import Endpoint
from app.db.models.finding import Finding
from app.db.models.resource import Resource
from app.db.models.test_case import TestCase as StoredCase
from app.db.models.test_run import TestRun as StoredRun
from app.services.ai_analysis import AIAnalysisService


class CapturingProvider:
    provider_name = "capturing"
    model_name = "test-model"

    def __init__(self) -> None:
        self.evidence: SanitizedFindingEvidence | None = None

    def analyze(
        self,
        *,
        evidence: SanitizedFindingEvidence,
    ) -> AIAnalysisResult:
        self.evidence = evidence

        return AIAnalysisResult(
            category="BOLA",
            confidence=0.9,
            severity="high",
            false_positive_risk="low",
            reason="Captured sanitized evidence.",
            recommended_review="Review the finding.",
            fix_recommendation="Enforce ownership.",
        )


def analyze_response_body(
    response_body: str,
) -> SanitizedFindingEvidence:
    finding = Finding(
        id=1,
        target_id=2,
        endpoint_id=3,
        test_run_id=4,
        category="BOLA",
        severity="high",
        confidence=0.95,
        status="potential",
        title="Potential BOLA",
        description="Cross-owner access succeeded.",
    )
    test_run = StoredRun(
        id=4,
        test_case_id=5,
        request_data={},
        response_status=200,
        response_body=response_body,
        duration_ms=10,
        error_message=None,
    )
    test_case = StoredCase(
        id=5,
        endpoint_id=3,
        actor_identity_id=6,
        resource_id=7,
        test_type="bola_cross_owner",
        ownership_relation="cross_owner",
        expected_statuses=[403, 404],
        status="completed",
    )
    endpoint = Endpoint(
        id=3,
        target_id=2,
        path="/projects/{project_id}",
        method="GET",
        requires_auth=True,
        parameters=[],
    )
    resource = Resource(
        id=7,
        target_id=2,
        resource_type="project",
        external_id="2001",
        owner_identity_id=8,
    )
    objects = {
        (Finding, 1): finding,
        (StoredRun, 4): test_run,
        (StoredCase, 5): test_case,
        (Endpoint, 3): endpoint,
        (Resource, 7): resource,
    }
    db = Mock(spec=Session)
    db.get.side_effect = (
        lambda model, object_id: objects.get(
            (model, object_id)
        )
    )
    provider = CapturingProvider()

    AIAnalysisService(
        db=db,
        provider=provider,
    ).analyze_finding(finding_id=1)

    assert provider.evidence is not None
    return provider.evidence


def test_provider_receives_sanitized_json_body() -> None:
    evidence = analyze_response_body(
        json.dumps(
            {
                "username": "alice",
                "token": "secret-token",
                "password": "secret-password",
            }
        )
    )

    assert evidence.response_body is not None
    body = json.loads(evidence.response_body)
    assert body["username"] == "alice"
    assert body["token"] == "[REDACTED]"
    assert body["password"] == "[REDACTED]"
    assert "secret-token" not in evidence.response_body
    assert "secret-password" not in evidence.response_body


def test_provider_does_not_receive_plaintext_bearer_token() -> None:
    evidence = analyze_response_body(
        "request failed: Authorization: "
        "Bearer super-secret-token"
    )

    assert evidence.response_body == NON_JSON_RESPONSE_REDACTED
    assert "super-secret-token" not in evidence.response_body


def test_provider_does_not_receive_non_json_api_key() -> None:
    evidence = analyze_response_body(
        "api_key=very-secret-api-key"
    )

    assert evidence.response_body == NON_JSON_RESPONSE_REDACTED
    assert "very-secret-api-key" not in evidence.response_body


def test_provider_receives_placeholder_for_plain_text() -> None:
    evidence = analyze_response_body(
        "Internal Server Error"
    )

    assert evidence.response_body == NON_JSON_RESPONSE_REDACTED
