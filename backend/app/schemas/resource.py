from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
)


class ResourceCreate(BaseModel):
    target_id: int

    resource_type: str
    external_id: str

    owner_identity_id: int

    @field_validator("resource_type")
    @classmethod
    def validate_resource_type(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip().lower()

        if not normalized:
            raise ValueError(
                "resource_type cannot be empty"
            )

        return normalized

    @field_validator("external_id")
    @classmethod
    def validate_external_id(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "external_id cannot be empty"
            )

        if "\r" in normalized or "\n" in normalized:
            raise ValueError(
                "external_id contains invalid "
                "newline characters"
            )

        return normalized


class ResourceRead(BaseModel):
    id: int

    target_id: int
    resource_type: str
    external_id: str

    owner_identity_id: int

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )