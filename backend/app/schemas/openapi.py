from pydantic import BaseModel, Field


class OpenAPIImportRequest(BaseModel):
    target_id: int
    source_url: str = Field(min_length=1, max_length=2048)


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
