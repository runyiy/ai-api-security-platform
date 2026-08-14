from pydantic import BaseModel


class OpenAPIImportRequest(BaseModel):
    target_id: int


class OpenAPIImportResponse(BaseModel):
    target_id: int
    openapi_url: str

    discovered: int

    created: int
    updated: int
    unchanged: int