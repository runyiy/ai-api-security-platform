from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EnrollmentReasonCode = Literal[
    "ownership_confirmed",
    "scope_confirmed",
    "out_of_scope",
    "dns_risk",
    "manual_review",
    "other",
]


class AssetEnrollmentDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    reason_code: EnrollmentReasonCode | None = None
    note: str | None = Field(default=None, min_length=1, max_length=500)


class AssetEnrollmentDecisionRead(BaseModel):
    id: int
    asset_candidate_dns_validation_id: int
    authorization_revision_id: int
    decision: str
    normalized_hostname: str
    reason_code: str | None
    note: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
