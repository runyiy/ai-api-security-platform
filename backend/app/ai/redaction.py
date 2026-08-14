import json
from typing import Any


SENSITIVE_KEYS = {
    "authorization",
    "password",
    "passwd",
    "access_token",
    "refresh_token",
    "token",
    "api_key",
    "apikey",
    "secret",
    "client_secret",
    "cookie",
    "set-cookie",
}


REDACTED = "[REDACTED]"


def is_sensitive_key(
    key: str,
) -> bool:
    return (
        key.strip().lower()
        in SENSITIVE_KEYS
    )


def redact_json_value(
    value: Any,
) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}

        for key, child in value.items():
            key_string = str(key)

            if is_sensitive_key(
                key_string
            ):
                result[key_string] = REDACTED
            else:
                result[key_string] = (
                    redact_json_value(child)
                )

        return result

    if isinstance(value, list):
        return [
            redact_json_value(item)
            for item in value
        ]

    return value


def sanitize_response_body(
    body: str | None,
) -> str | None:
    if body is None:
        return None

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body[:16_000]

    sanitized = redact_json_value(
        parsed
    )

    return json.dumps(
        sanitized,
        ensure_ascii=False,
    )[:16_000]


def sanitize_request_data(
    request_data: dict[str, Any],
) -> dict[str, Any]:
    return redact_json_value(
        request_data
    )