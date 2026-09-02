from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class TargetCreate(BaseModel):
    name: str
    base_url: HttpUrl
    environment: str = "development"
    network_mode: Literal[
        "private_local", "external_public_authorized"
    ] = "private_local"


class TargetNetworkModeUpdate(BaseModel):
    network_mode: Literal["private_local", "external_public_authorized"]


class TargetAuthorizationProfileUpdate(BaseModel):
    authorization_profile_id: int | None


class TargetAuthorizationRevisionUpdate(BaseModel):
    authorization_revision_id: int | None


class TargetRead(BaseModel):
    id: int
    asset_enrollment_decision_id: int | None
    authorization_profile_id: int | None
    authorization_revision_id: int | None
    name: str
    base_url: str
    environment: str
    network_mode: Literal["private_local", "external_public_authorized"]
    is_enabled: bool
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class ApprovedEnrollmentTargetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    environment: str = Field(default="development", min_length=1, max_length=50)
    scheme: Literal["http", "https"]
    port: int | None = Field(default=None, ge=1, le=65535)
    network_mode: Literal["private_local", "external_public_authorized"]
