from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError


EnrollmentReasonCode = Literal[
    "ownership_confirmed",
    "scope_confirmed",
    "out_of_scope",
    "dns_risk",
    "manual_review",
    "other",
]


_PROHIBITED_NOTE_AUTH_MATERIAL = re.compile(
    r"(?:"
    r"\bauthorization\s*[:=]|"
    r"\bbearer\s+\S+|"
    r"\b(?:set-)?cookie\s*[:=]|"
    r"\b(?:x[-_ ]?)?api[-_ ]?key\s*[:=]|"
    r"\b(?:access|refresh)[-_ ]?token\s*[:=]|"
    r"\b(?:credential|credentials|"
    r"(?:db[-_ ]?)?(?:password|passwd)|"
    r"(?:client[-_ ]?)?secret)\s*[:=]"
    r")",
    flags=re.IGNORECASE,
)


def validate_non_secret_enrollment_note(note: str) -> str:
    if _PROHIBITED_NOTE_AUTH_MATERIAL.search(note):
        raise PydanticCustomError(
            "asset_enrollment_note_auth_material",
            "Enrollment decision note contains prohibited authentication material.",
        )
    return note


class AssetEnrollmentDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    reason_code: EnrollmentReasonCode | None = None
    note: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("note")
    @classmethod
    def reject_authentication_material(cls, note: str | None) -> str | None:
        if note is None:
            return None
        return validate_non_secret_enrollment_note(note)


class AssetEnrollmentDecisionRead(BaseModel):
    id: int
    asset_candidate_dns_validation_id: int
    authorization_revision_id: int
    decision: str
    normalized_hostname: str
    reason_code: str | None
    note: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
