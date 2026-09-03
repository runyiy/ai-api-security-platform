from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.schemas.resource_access_assertion import ExpectedAccess, Relationship


class ResourceAccessResolutionRead(BaseModel):
    resource_id: int
    test_identity_id: int
    evaluation_time: AwareDatetime
    state: Literal["resolved", "insufficient", "conflict"]
    relationship: Relationship
    expected_access: ExpectedAccess
    supporting_assertion_ids: tuple[int, ...]

    model_config = ConfigDict(frozen=True)
