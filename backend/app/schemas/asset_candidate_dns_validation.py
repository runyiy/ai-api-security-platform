from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AssetCandidateDNSValidationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetCandidateDNSCNAMEHopRead(BaseModel):
    ordinal: int
    hostname: str


class AssetCandidateDNSAddressRead(BaseModel):
    ordinal: int
    address: str
    category: str


class AssetCandidateDNSValidationRead(BaseModel):
    id: int
    asset_candidate_evaluation_id: int
    authorization_revision_id: int
    decision_code: str
    normalized_hostname: str
    terminal_hostname: str | None
    created_at: datetime
    cname_chain: list[AssetCandidateDNSCNAMEHopRead]
    addresses: list[AssetCandidateDNSAddressRead]


class AssetCandidateDNSValidationSummary(BaseModel):
    id: int
    asset_candidate_evaluation_id: int
    authorization_revision_id: int
    decision_code: str
    normalized_hostname: str
    terminal_hostname: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
