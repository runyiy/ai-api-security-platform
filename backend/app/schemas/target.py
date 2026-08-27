from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, HttpUrl


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
