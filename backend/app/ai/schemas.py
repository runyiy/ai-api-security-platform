from typing import Any, Literal

from pydantic import (
    BaseModel,
    Field,
)


class SanitizedFindingEvidence(BaseModel):
    finding_id: int

    category: str
    rule_confidence: float
    rule_severity: str

    endpoint_method: str
    endpoint_path: str

    actor_identity_id: int

    resource_type: str
    resource_external_id: str
    owner_identity_id: int

    expected_statuses: list[int]
    actual_status: int | None

    request: dict[str, Any]
    response_body: str | None

    rule_description: str


class AIAnalysisResult(BaseModel):
    category: Literal["BOLA"]

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    severity: Literal[
        "low",
        "medium",
        "high",
        "critical",
    ]

    false_positive_risk: Literal[
        "low",
        "medium",
        "high",
    ]

    reason: str

    recommended_review: str

    fix_recommendation: str