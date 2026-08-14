from pydantic import BaseModel


class PolicyCheckRequest(BaseModel):
    target_id: int
    url: str
    method: str


class PolicyCheckResponse(BaseModel):
    allowed: bool
    code: str
    reason: str
    matched_scope_id: int | None