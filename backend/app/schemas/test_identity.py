from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    SecretStr,
    model_validator,
)


AuthType = Literal[
    "anonymous",
    "bearer",
]


class TestIdentityCreate(BaseModel):
    target_id: int

    name: str
    role: str | None = None

    auth_type: AuthType

    access_token: SecretStr | None = None

    @model_validator(mode="after")
    def validate_credentials(self):
        if self.auth_type == "anonymous":
            if self.access_token is not None:
                raise ValueError(
                    "anonymous identity must not "
                    "contain an access token"
                )

        if self.auth_type == "bearer":
            if self.access_token is None:
                raise ValueError(
                    "bearer identity requires "
                    "an access token"
                )

            token = (
                self.access_token
                .get_secret_value()
            )

            if not token.strip():
                raise ValueError(
                    "access token cannot be empty"
                )

            if "\r" in token or "\n" in token:
                raise ValueError(
                    "access token contains "
                    "invalid newline characters"
                )

            if (
                token.lower()
                .startswith("bearer ")
            ):
                raise ValueError(
                    "store only the raw token; "
                    "do not include the "
                    "'Bearer ' prefix"
                )

        return self


class TestIdentityRead(BaseModel):
    id: int
    target_id: int

    name: str
    role: str | None

    auth_type: str
    is_active: bool

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class BearerTokenUpdate(BaseModel):
    access_token: SecretStr

    @model_validator(mode="after")
    def validate_token(self):
        token = (
            self.access_token
            .get_secret_value()
        )

        if not token.strip():
            raise ValueError(
                "access token cannot be empty"
            )

        if "\r" in token or "\n" in token:
            raise ValueError(
                "access token contains "
                "invalid newline characters"
            )

        if token.lower().startswith(
            "bearer "
        ):
            raise ValueError(
                "store only the raw token"
            )

        return self