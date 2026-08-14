from datetime import datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
)


class SecurityReportRead(BaseModel):
    id: int

    finding_id: int
    target_id: int

    version: int
    report_format: str
    report_data: dict[str, Any]
    markdown_content: str

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
