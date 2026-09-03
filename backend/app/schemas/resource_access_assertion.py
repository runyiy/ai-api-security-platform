from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, model_validator
from pydantic_core import PydanticCustomError


Relationship = Literal["owner", "shared", "non_owner", "unspecified"]
ExpectedAccess = Literal["allowed", "denied", "unspecified"]
StrictConfidence = Annotated[int, Strict(), Field(ge=0, le=100)]


class ResourceAccessAssertionCreate(BaseModel):
    test_identity_id: int
    relationship: Relationship
    expected_access: ExpectedAccess
    confidence: StrictConfidence
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_prohibited_fields(cls, value):
        allowed = {
            "test_identity_id", "relationship", "expected_access",
            "confidence", "valid_from", "valid_until",
        }
        if isinstance(value, dict) and set(value) - allowed:
            raise PydanticCustomError(
                "resource_access_assertion_prohibited_field",
                "Resource access assertion contains a prohibited field.",
            )
        return value

    @model_validator(mode="after")
    def validate_assertion(self):
        if self.relationship == "unspecified" and self.expected_access == "unspecified":
            raise ValueError("at least one assertion dimension must be specified")
        for value in (self.valid_from, self.valid_until):
            if value is not None and value.utcoffset() is None:
                raise ValueError("validity timestamps must be timezone-aware")
        if self.valid_until is not None:
            if self.valid_from is None:
                raise ValueError("valid_from is required when valid_until is supplied")
            if self.valid_until <= self.valid_from:
                raise ValueError("valid_until must be later than valid_from")
        return self


class ResourceAccessAssertionRead(BaseModel):
    id: int
    resource_id: int
    test_identity_id: int
    relationship: Relationship
    expected_access: ExpectedAccess
    provenance: Literal[
        "human_verified", "target_fixture", "observed_baseline",
        "inferred_candidate",
    ]
    confidence: int
    verification_state: Literal["candidate", "verified", "rejected"]
    asserted_at: datetime
    observed_at: datetime | None
    valid_from: datetime | None
    valid_until: datetime | None

    model_config = ConfigDict(from_attributes=True)
