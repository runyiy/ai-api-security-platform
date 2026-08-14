from datetime import datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
)


class EndpointRead(BaseModel):
    id: int
    target_id: int

    path: str
    method: str

    operation_id: str | None
    requires_auth: bool

    parameters: list[
        dict[str, Any]
    ]

    request_body: (
        dict[str, Any]
        | None
    )

    security: (
        list[dict[str, Any]]
        | None
    )

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )