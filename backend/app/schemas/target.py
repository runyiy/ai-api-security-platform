from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl


class TargetCreate(BaseModel):
    name: str
    base_url: HttpUrl
    environment: str = "development"


class TargetAuthorizationProfileUpdate(BaseModel):
    authorization_profile_id: int | None


class TargetRead(BaseModel):
    id: int
    authorization_profile_id: int | None
    name: str
    base_url: str
    environment: str
    is_enabled: bool
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )
