import json

from app.ai.redaction import (
    sanitize_response_body,
)


def test_redacts_tokens() -> None:
    body = json.dumps(
        {
            "id": 2001,
            "access_token": "secret-token",
            "user": {
                "password": "secret-password",
            },
        }
    )

    sanitized = sanitize_response_body(
        body
    )

    assert sanitized is not None

    assert "secret-token" not in sanitized
    assert "secret-password" not in sanitized

    assert "[REDACTED]" in sanitized


def test_preserves_resource_data() -> None:
    body = json.dumps(
        {
            "id": 2001,
            "name": "Project B",
        }
    )

    sanitized = sanitize_response_body(
        body
    )

    assert sanitized is not None

    assert "2001" in sanitized
    assert "Project B" in sanitized


def test_nested_secret_is_redacted() -> None:
    body = json.dumps(
        {
            "data": {
                "credentials": {
                    "api_key": "abc123",
                }
            }
        }
    )

    sanitized = sanitize_response_body(
        body
    )

    assert sanitized is not None
    assert "abc123" not in sanitized