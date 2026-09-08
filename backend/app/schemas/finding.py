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


class FindingEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    finding_id: int
    probe_test_run_id: int
    baseline_test_run_id: int
    evidence_type: str
    rule_id: str
    rule_version: str
    reason_code: str
    baseline_status_code: int
    probe_status_code: int
    baseline_resource_identifier_present: bool
    probe_resource_identifier_present: bool
    created_at: datetime


class FindingEvidenceExcerptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    finding_evidence_record_id: int
    extractor_id: str
    extractor_version: str
    baseline_excerpt: str
    probe_excerpt: str
    created_at: datetime


class FindingEvidenceFingerprintRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    finding_evidence_record_id: int
    algorithm: str
    fingerprint_version: str
    baseline_digest: str
    probe_digest: str
    baseline_body_bytes: int
    probe_body_bytes: int
    created_at: datetime
