from app.ai.schemas import (
    AIAnalysisResult,
    SanitizedFindingEvidence,
)


class MockAIProvider:
    provider_name = "mock"
    model_name = "mock-bola-analyzer-v1"

    def analyze(
        self,
        *,
        evidence: SanitizedFindingEvidence,
    ) -> AIAnalysisResult:
        if (
            evidence.actual_status is not None
            and 200 <= evidence.actual_status < 300
            and evidence.rule_confidence >= 0.9
        ):
            return AIAnalysisResult(
                category="BOLA",
                confidence=0.92,
                severity="high",
                false_positive_risk="low",
                reason=(
                    "The rule engine observed a "
                    "successful cross-owner response "
                    "with strong resource evidence."
                ),
                recommended_review=(
                    "Verify that the actor is not "
                    "intentionally authorized to "
                    "access the target resource."
                ),
                fix_recommendation=(
                    "Enforce object ownership or "
                    "equivalent authorization checks "
                    "in the data-access query before "
                    "returning the resource."
                ),
            )

        return AIAnalysisResult(
            category="BOLA",
            confidence=0.60,
            severity="medium",
            false_positive_risk="medium",
            reason=(
                "The available evidence requires "
                "additional business-context review."
            ),
            recommended_review=(
                "Review the resource sharing and "
                "authorization model manually."
            ),
            fix_recommendation=(
                "Ensure object-level authorization "
                "is validated for every resource "
                "access."
            ),
        )