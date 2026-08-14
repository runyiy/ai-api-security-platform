from datetime import datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
)


class SecurityReportRead(BaseModel):
    id: int

    finding_id: int
    source_ai_analysis_id: int | None

    version: int

    title: str
    summary: str

    affected_endpoint: str
    prerequisites: str

    steps_to_reproduce: list[str]

    expected_result: str
    actual_result: str

    security_impact: str

    evidence: dict[str, Any]

    suggested_fix: str

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class SecurityReportMarkdownRead(
    BaseModel
):
    report_id: int
    version: int
    markdown: str