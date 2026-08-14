from app.ai.mock_provider import (
    MockAIProvider,
)
from app.ai.schemas import (
    SanitizedFindingEvidence,
)


def build_evidence():
    return SanitizedFindingEvidence(
        finding_id=1,
        category="BOLA",
        rule_confidence=0.95,
        rule_severity="high",
        endpoint_method="GET",
        endpoint_path=(
            "/api/projects/{project_id}"
        ),
        actor_identity_id=1,
        resource_type="project",
        resource_external_id="2001",
        owner_identity_id=2,
        expected_statuses=[
            403,
            404,
        ],
        actual_status=200,
        request={
            "headers": {
                "Authorization":
                    "[REDACTED]"
            }
        },
        response_body=(
            '{"id": 2001, '
            '"name": "Project B"}'
        ),
        rule_description=(
            "Cross-owner access returned "
            "resource data."
        ),
    )


def test_mock_provider_returns_structured_result():
    provider = MockAIProvider()

    result = provider.analyze(
        evidence=build_evidence()
    )

    assert result.category == "BOLA"

    assert 0 <= result.confidence <= 1

    assert result.severity in {
        "low",
        "medium",
        "high",
        "critical",
    }