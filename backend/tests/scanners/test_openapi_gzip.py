import gzip
import hashlib
import json

import pytest

from app.scanners.openapi import (
    MAX_OPENAPI_DECOMPRESSED_BYTES,
    OpenAPIParseError,
    decode_openapi_document,
    validate_openapi_structure,
)


def assert_decode_code(raw: bytes, encoding: str | None, code: str) -> None:
    with pytest.raises(OpenAPIParseError) as raised:
        decode_openapi_document(raw, encoding)
    assert raised.value.code == code
    assert str(raised.value) == code


def test_decompressed_budget_is_exactly_inclusive() -> None:
    accepted = b"a" * MAX_OPENAPI_DECOMPRESSED_BYTES
    encoding, decoded = decode_openapi_document(gzip.compress(accepted), "gzip")
    assert encoding == "gzip"
    assert decoded == accepted

    rejected = b"a" * (MAX_OPENAPI_DECOMPRESSED_BYTES + 1)
    assert_decode_code(
        gzip.compress(rejected),
        "gzip",
        "openapi_decompressed_body_too_large",
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"not gzip",
        gzip.compress(b'{"paths":{}}')[:-1],
        gzip.compress(b'{"paths":{}}') + gzip.compress(b'{"paths":{}}'),
        gzip.compress(b'{"paths":{}}') + b"trailing",
    ],
    ids=["malformed", "truncated", "concatenated", "trailing-data"],
)
def test_invalid_gzip_forms_fail_sanitized(raw: bytes) -> None:
    assert_decode_code(raw, "gzip", "openapi_compressed_body_invalid")


@pytest.mark.parametrize(
    "encoding",
    ["deflate", "br", "zstd", "compress", "gzip, br", "gzip, gzip", "gzip;bad"],
)
def test_unsupported_or_ambiguous_encodings_fail_closed(encoding: str) -> None:
    assert_decode_code(
        b'{"paths":{}}', encoding, "openapi_content_encoding_not_supported"
    )


@pytest.mark.parametrize(
    "header,expected",
    [(None, "identity"), ("identity", "identity"), (" IDENTITY \t", "identity"),
     ("gzip", "gzip"), (" GZip \t", "gzip")],
)
def test_identity_and_gzip_tokens_are_normalized(
    header: str | None, expected: str
) -> None:
    document = b'{"paths":{}}'
    raw = gzip.compress(document) if expected == "gzip" else document
    encoding, decoded = decode_openapi_document(raw, header)
    assert encoding == expected
    assert decoded == document


def test_gzip_magic_is_not_sniffed_without_header() -> None:
    raw = gzip.compress(b'{"paths":{}}')
    encoding, decoded = decode_openapi_document(raw, None)
    assert encoding == "identity"
    assert decoded == raw
    assert hashlib.sha256(decoded).hexdigest() == hashlib.sha256(raw).hexdigest()


def test_gzip_ref_reaches_existing_parser_protection() -> None:
    document = json.dumps({
        "paths": {}, "nested": {"$ref": "https://attacker.invalid/ref"}
    }).encode()
    encoding, decoded = decode_openapi_document(gzip.compress(document), "gzip")
    assert encoding == "gzip"
    decoded_document = json.loads(decoded)
    with pytest.raises(OpenAPIParseError) as raised:
        validate_openapi_structure(decoded_document)
    assert raised.value.code == "openapi_references_not_supported"
