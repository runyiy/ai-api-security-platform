from sqlalchemy.orm import Session

from app.ai.provider import AIProvider
from app.ai.redaction import (
    sanitize_request_data,
    sanitize_response_body,
)
from app.ai.schemas import (
    SanitizedFindingEvidence,
)
from app.db.models.endpoint import Endpoint
from app.db.models.finding import Finding
from app.db.models.finding_ai_analysis import (
    FindingAIAnalysis,
)
from app.db.models.resource import Resource
from app.db.models.test_case import TestCase
from app.db.models.test_run import TestRun


class AIAnalysisServiceError(
    RuntimeError
):
    pass


class AIAnalysisService:
    def __init__(
        self,
        *,
        db: Session,
        provider: AIProvider,
    ) -> None:
        self.db = db
        self.provider = provider

    def analyze_finding(
        self,
        *,
        finding_id: int,
    ) -> FindingAIAnalysis:
        finding = self.db.get(
            Finding,
            finding_id,
        )

        if finding is None:
            raise AIAnalysisServiceError(
                "Finding not found."
            )

        if finding.status not in {
            "potential",
            "reviewing",
            "confirmed",
        }:
            raise AIAnalysisServiceError(
                "Finding is not eligible "
                "for AI analysis."
            )

        test_run = self.db.get(
            TestRun,
            finding.test_run_id,
        )

        if test_run is None:
            raise AIAnalysisServiceError(
                "TestRun not found."
            )

        test_case = self.db.get(
            TestCase,
            test_run.test_case_id,
        )

        if test_case is None:
            raise AIAnalysisServiceError(
                "TestCase not found."
            )

        endpoint = self.db.get(
            Endpoint,
            test_case.endpoint_id,
        )

        resource = self.db.get(
            Resource,
            test_case.resource_id,
        )

        if endpoint is None:
            raise AIAnalysisServiceError(
                "Endpoint not found."
            )

        if resource is None:
            raise AIAnalysisServiceError(
                "Resource not found."
            )

        evidence = SanitizedFindingEvidence(
            finding_id=finding.id,
            category=finding.category,
            rule_confidence=finding.confidence,
            rule_severity=finding.severity,
            endpoint_method=endpoint.method,
            endpoint_path=endpoint.path,
            actor_identity_id=(
                test_case.actor_identity_id
            ),
            resource_type=(
                resource.resource_type
            ),
            resource_external_id=(
                resource.external_id
            ),
            owner_identity_id=(
                resource.owner_identity_id
            ),
            expected_statuses=(
                test_case.expected_statuses
            ),
            actual_status=(
                test_run.response_status
            ),
            request=sanitize_request_data(
                test_run.request_data
            ),
            response_body=(
                sanitize_response_body(
                    test_run.response_body
                )
            ),
            rule_description=(
                finding.description
            ),
        )

        result = self.provider.analyze(
            evidence=evidence
        )

        analysis = FindingAIAnalysis(
            finding_id=finding.id,
            provider=(
                self.provider.provider_name
            ),
            model_name=(
                self.provider.model_name
            ),
            category=result.category,
            confidence=result.confidence,
            severity=result.severity,
            false_positive_risk=(
                result.false_positive_risk
            ),
            reason=result.reason,
            recommended_review=(
                result.recommended_review
            ),
            fix_recommendation=(
                result.fix_recommendation
            ),
        )

        self.db.add(analysis)
        self.db.commit()
        self.db.refresh(analysis)

        return analysis