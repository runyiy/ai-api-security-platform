from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.services.asset_enrollment_note import (
    ASSET_ENROLLMENT_NOTE_AUTH_MATERIAL_CODE,
    ASSET_ENROLLMENT_NOTE_AUTH_MATERIAL_MESSAGE,
    AssetEnrollmentNoteAuthMaterialError,
    validate_non_secret_enrollment_note,
)


EnrollmentReasonCode = Literal[
    "ownership_confirmed",
    "scope_confirmed",
    "out_of_scope",
    "dns_risk",
    "manual_review",
    "other",
]


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
        try:
            return validate_non_secret_enrollment_note(note)
        except AssetEnrollmentNoteAuthMaterialError as exc:
            raise PydanticCustomError(
                ASSET_ENROLLMENT_NOTE_AUTH_MATERIAL_CODE,
                ASSET_ENROLLMENT_NOTE_AUTH_MATERIAL_MESSAGE,
            ) from exc


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
