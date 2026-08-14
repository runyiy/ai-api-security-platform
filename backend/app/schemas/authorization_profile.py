from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class AuthorizationProfileCreate(BaseModel):
    name: str = Field(max_length=120)
    program_name: str = Field(max_length=200)
    program_url: str | None = Field(default=None, max_length=500)
    authorization_type: str = Field(max_length=50)
    authorization_reference: str | None = Field(
        default=None,
        max_length=500,
    )
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    automation_allowed: bool = False
    max_requests_per_second: float = Field(default=1.0, gt=0)
    allow_get: bool = False
    allow_post: bool = False
    allow_patch: bool = False
    allow_put: bool = False
    allow_delete: bool = False
    require_human_execution_approval: bool = True
    notes: str | None = None

    @field_validator(
        "name",
        "program_name",
        "authorization_type",
    )
    @classmethod
    def validate_required_string(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "value cannot be empty or whitespace-only"
            )

        return normalized

    @model_validator(mode="after")
    def validate_validity_window(self):
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until <= self.valid_from
        ):
            raise ValueError(
                "valid_until must be later than valid_from"
            )

        return self


class AuthorizationProfileUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    program_name: str | None = Field(default=None, max_length=200)
    program_url: str | None = Field(default=None, max_length=500)
    authorization_type: str | None = Field(default=None, max_length=50)
    authorization_reference: str | None = Field(
        default=None,
        max_length=500,
    )
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    automation_allowed: bool | None = None
    max_requests_per_second: float | None = Field(default=None, gt=0)
    allow_get: bool | None = None
    allow_post: bool | None = None
    allow_patch: bool | None = None
    allow_put: bool | None = None
    allow_delete: bool | None = None
    require_human_execution_approval: bool | None = None
    notes: str | None = None

    @field_validator(
        "name",
        "program_name",
        "authorization_type",
    )
    @classmethod
    def validate_required_string(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "value cannot be empty or whitespace-only"
            )

        return normalized


class AuthorizationProfileRead(BaseModel):
    id: int
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
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )
