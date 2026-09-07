from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


FindingReviewStatus = Literal[
    "reviewing",
    "confirmed",
    "false_positive",
]


class FindingRead(BaseModel):
    id: int

    target_id: int
    endpoint_id: int
    test_run_id: int
    baseline_test_run_id: int | None

    category: str
    severity: str
    confidence: float

    status: str

    title: str
    description: str

    review_notes: str | None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class AnalyzeTestRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_test_run_id: int = Field(strict=True, gt=0)


class AnalyzeTestRunResponse(BaseModel):
    outcome: str
    reason: str

    confidence: float | None
    severity: str | None

    finding: FindingRead | None


class FindingReviewRequest(BaseModel):
    status: FindingReviewStatus
    review_notes: str | None = None