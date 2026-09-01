from pydantic import BaseModel, ConfigDict, Field


class OpenAPIImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: int
    source_url: str = Field(min_length=1, max_length=2048)
    credential_binding_id: int | None = None


class OpenAPIImportResponse(BaseModel):
    target_id: int
    source_url: str
    import_record_id: int
    document_sha256: str
    content_encoding: str
    decoded_document_sha256: str

    discovered: int

    created: int
    updated: int
    unchanged: int
