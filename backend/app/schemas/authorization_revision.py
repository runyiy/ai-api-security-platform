from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuthorizationRevisionRead(BaseModel):
    id: int
    authorization_profile_id: int
    revision_number: int
    lifecycle_state: str
    name: str
    program_name: str
    program_url: str | None
    authorization_type: str
    authorization_reference: str | None
    valid_from: datetime | None
    valid_until: datetime | None
    automation_allowed: bool
    max_requests_per_second: float
    allow_get: bool
    allow_post: bool
    allow_patch: bool
    allow_put: bool
    allow_delete: bool
    require_human_execution_approval: bool
    notes: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
