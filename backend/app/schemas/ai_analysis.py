from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
)


class FindingAIAnalysisRead(BaseModel):
    id: int
    finding_id: int

    provider: str
    model_name: str

    category: str
    confidence: float
    severity: str

    false_positive_risk: str

    reason: str
    recommended_review: str
    fix_recommendation: str

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )