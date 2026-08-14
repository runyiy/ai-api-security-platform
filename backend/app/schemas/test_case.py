from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
)


class GenerateBOLATestCasesRequest(
    BaseModel
):
    target_id: int


class GenerateBOLATestCasesResponse(
    BaseModel
):
    target_id: int

    generated: int
    created: int
    existing: int


class TestCaseRead(BaseModel):
    id: int

    endpoint_id: int
    actor_identity_id: int
    resource_id: int

    test_type: str
    ownership_relation: str

    expected_statuses: list[int]

    status: str

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )