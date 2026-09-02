from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AssetCandidateEvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hostname: str = Field(min_length=1, max_length=255)


class AssetCandidateEvaluationRead(BaseModel):
    id: int
    authorization_revision_id: int
    normalized_hostname: str
    decision_code: str
    matched_include_rule_id: int | None
    matched_exclude_rule_id: int | None
    source_type: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
