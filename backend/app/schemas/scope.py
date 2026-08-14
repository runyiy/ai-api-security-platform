import ipaddress

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
)

from app.policies.scope_policy import (
    MVP_HTTP_METHODS,
    normalize_scope_path_pattern,
)


class ScopeCreate(BaseModel):
    target_id: int
    hostname: str
    path_pattern: str
    allowed_methods: list[str]
    is_active: bool = True

    @field_validator("hostname")
    @classmethod
    def validate_hostname(
        cls,
        value: str,
    ) -> str:
        hostname = (
            value
            .strip()
            .lower()
            .rstrip(".")
        )

        if not hostname:
            raise ValueError(
                "hostname cannot be empty"
            )

        if (
            "://" in hostname
            or "/" in hostname
            or "?" in hostname
            or "#" in hostname
            or "@" in hostname
        ):
            raise ValueError(
                "hostname must not contain "
                "scheme, path, query, fragment "
                "or userinfo"
            )

        try:
            return ipaddress.ip_address(
                hostname
            ).compressed
        except ValueError:
            pass

        if ":" in hostname:
            raise ValueError(
                "hostname must not contain a port"
            )

        return hostname

    @field_validator("path_pattern")
    @classmethod
    def validate_path_pattern(
        cls,
        value: str,
    ) -> str:
        try:
            return normalize_scope_path_pattern(
                value
            )
        except ValueError as exc:
            raise ValueError(
                str(exc)
            ) from exc

    @field_validator("allowed_methods")
    @classmethod
    def validate_allowed_methods(
        cls,
        values: list[str],
    ) -> list[str]:
        normalized = list(
            dict.fromkeys(
                value.strip().upper()
                for value in values
            )
        )

        if not normalized:
            raise ValueError(
                "at least one HTTP method "
                "is required"
            )

        unsupported = (
            set(normalized)
            - MVP_HTTP_METHODS
        )

        if unsupported:
            raise ValueError(
                "unsupported HTTP methods: "
                f"{sorted(unsupported)}"
            )

        return normalized


class ScopeRead(BaseModel):
    id: int
    target_id: int
    hostname: str
    path_pattern: str
    allowed_methods: list[str]
    is_active: bool

    model_config = ConfigDict(
        from_attributes=True,
    )