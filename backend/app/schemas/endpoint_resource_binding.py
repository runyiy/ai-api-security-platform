import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


PARAMETER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}\Z")
SENSITIVE_MATERIAL = re.compile(
    r"(?:"
    r"authorization\s*[:=]\s*(?:bearer|basic)\s+[^/\s]+"
    r"|(?:set-cookie|cookie)\s*[:=]\s*[^/\s]+"
    r"|(?:^|/)(?:set-cookie|cookie)/[^/]*=[^/\s]+"
    r"|(?:x-api-key|api_key|access_token|refresh_token|password|credential|secret)"
    r"\s*[:=]\s*[^/\s]+"
    r")",
    re.IGNORECASE,
)


def validate_json_pointer(value: str) -> None:
    if value == "" or value == "/" or not value.startswith("/"):
        raise ValueError("body selector must be a non-root RFC 6901 JSON Pointer")
    for token in value[1:].split("/"):
        if not token:
            raise ValueError("JSON Pointer tokens must be non-empty")
        if any(character in token for character in "$[]()*?"):
            raise ValueError("body selector cannot contain expression syntax")
        if re.search(r"[:=]\s*\S", token):
            raise ValueError("body selector cannot contain resource values")
        index = 0
        while index < len(token):
            if token[index] == "~":
                if index + 1 >= len(token) or token[index + 1] not in "01":
                    raise ValueError("JSON Pointer contains an invalid escape")
                index += 2
            else:
                index += 1


def validate_resource_binding_selector(location: str, value: str) -> None:
    if SENSITIVE_MATERIAL.search(value):
        raise PydanticCustomError(
            "resource_binding_selector_sensitive_material",
            "Resource binding selector contains prohibited sensitive material.",
        )
    if location in {"path", "query"}:
        if PARAMETER_NAME.fullmatch(value) is None:
            raise ValueError("path/query selector is not an exact parameter name")
    else:
        validate_json_pointer(value)


class EndpointResourceBindingCreate(BaseModel):
    location: Literal["path", "query", "body"]
    selector: str = Field(min_length=1, max_length=500)
    confidence: int = Field(ge=0, le=100, strict=True)
    review_state: Literal["candidate", "confirmed", "rejected"]

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_selector(self):
        validate_resource_binding_selector(self.location, self.selector)
        return self


class EndpointResourceBindingReviewUpdate(BaseModel):
    confidence: int | None = Field(default=None, ge=0, le=100, strict=True)
    review_state: Literal["candidate", "confirmed", "rejected"] | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def require_non_null_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one review field is required")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("review fields cannot be null")
        return self


class EndpointResourceBindingInferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EndpointResourceBindingInferenceRead(BaseModel):
    endpoint_id: int
    eligible_count: int
    created_count: int
    existing_inferred_count: int
    skipped_operator_count: int


class EndpointResourceBindingRead(BaseModel):
    id: int
    endpoint_id: int
    location: Literal["path", "query", "body"]
    selector: str
    provenance: Literal[
        "operator_supplied", "openapi_inferred", "heuristic_inferred"
    ]
    confidence: int
    review_state: Literal["candidate", "confirmed", "rejected"]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
