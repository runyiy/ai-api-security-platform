from datetime import datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
)


class TestRunRead(BaseModel):
    id: int
    test_case_id: int

    request_data: dict[str, Any]

    response_status: int | None
    response_body: str | None

    duration_ms: int | None
    error_message: str | None

    executed_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )