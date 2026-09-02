import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PARAMETER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}\Z")


def validate_json_pointer(value: str) -> None:
    if value == "" or value == "/" or not value.startswith("/"):
        raise ValueError("body selector must be a non-root RFC 6901 JSON Pointer")
    for token in value[1:].split("/"):
        if not token:
            raise ValueError("JSON Pointer tokens must be non-empty")
        if any(character in token for character in "$[]()*?"):
            raise ValueError("body selector cannot contain expression syntax")
        index = 0
        while index < len(token):
            if token[index] == "~":
                if index + 1 >= len(token) or token[index + 1] not in "01":
                    raise ValueError("JSON Pointer contains an invalid escape")
                index += 2
            else:
                index += 1


class EndpointResourceBindingCreate(BaseModel):
    location: Literal["path", "query", "body"]
    selector: str = Field(min_length=1, max_length=500)
    confidence: int = Field(ge=0, le=100, strict=True)
    review_state: Literal["candidate", "confirmed", "rejected"]

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_selector(self):
        if self.location in {"path", "query"}:
            if PARAMETER_NAME.fullmatch(self.selector) is None:
                raise ValueError("path/query selector is not an exact parameter name")
        else:
            validate_json_pointer(self.selector)
        return self


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
