from typing import Protocol

from app.ai.schemas import (
    AIAnalysisResult,
    SanitizedFindingEvidence,
)


class AIProvider(Protocol):
    provider_name: str
    model_name: str

    def analyze(
        self,
        *,
        evidence: SanitizedFindingEvidence,
    ) -> AIAnalysisResult:
        ...