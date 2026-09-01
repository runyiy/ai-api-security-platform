from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AssetHostnameRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_type: Literal["include", "exclude"]
    hostname_pattern: str = Field(min_length=1, max_length=255)


class AssetHostnameRuleRead(BaseModel):
    id: int
    authorization_revision_id: int
    rule_type: str
    hostname_pattern: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
