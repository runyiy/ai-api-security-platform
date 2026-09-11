"""Bounded JSON decoding before object construction; no payload-bearing errors."""
import json
import math
import re

CODES = frozenset('''PROVIDER_DISABLED CONFIG_UNAPPROVED DATA_INELIGIBLE
SOURCE_UNAVAILABLE INPUT_LIMIT OUTPUT_LIMIT MALFORMED_OUTPUT VERSION_UNSUPPORTED
REQUEST_MISMATCH REFERENCE_UNAVAILABLE SUGGESTION_UNSUPPORTED CONTEXT_CHANGED
PROVIDER_REFUSAL PROVIDER_INCOMPLETE TRANSPORT_DENIED PROVIDER_TIMEOUT
PROVIDER_FAILURE CREDENTIAL_UNAVAILABLE USAGE_UNKNOWN USAGE_INVALID
BUDGET_UNAVAILABLE CANCELLED AUDIT_UNAVAILABLE'''.split())


class ProposalRejected(Exception):
    def __init__(self, code: str):
        self.code = code if code in CODES else 'PROVIDER_FAILURE'
        super().__init__(self.code)


def require(condition, code='MALFORMED_OUTPUT'):
    if not condition:
        raise ProposalRejected(code)


def canonical(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError):
        raise ProposalRejected('MALFORMED_OUTPUT') from None


def bounded_json(raw: bytes, *, maximum: int, depth: int, nodes: int,
                 limit_code='OUTPUT_LIMIT') -> dict:
    require(type(raw) is bytes)
    require(len(raw) <= maximum, limit_code)
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeError:
        raise ProposalRejected('MALFORMED_OUTPUT') from None
    pos, count = 0, 0

    def whitespace():
        nonlocal pos
        while pos < len(text) and text[pos] in ' \t\r\n':
            pos += 1

    def string():
        nonlocal pos
        require(pos < len(text) and text[pos] == '"')
        try:
            value, pos = json.decoder.scanstring(text, pos + 1, True)
            value.encode('utf-8', errors='strict')
        except (ValueError, UnicodeError):
            raise ProposalRejected('MALFORMED_OUTPUT') from None
        return value

    def value(level):
        nonlocal pos, count
        count += 1
        require(level <= depth and count <= nodes, limit_code)
        whitespace()
        require(pos < len(text))
        char = text[pos]
        if char == '"':
            return string()
        if char in '{[':
            obj = {} if char == '{' else []
            end = '}' if char == '{' else ']'
            pos += 1
            whitespace()
            if pos < len(text) and text[pos] == end:
                pos += 1
                return obj
            while True:
                whitespace()
                if char == '{':
                    key = string()
                    require(key not in obj)
                    whitespace()
                    require(pos < len(text) and text[pos] == ':')
                    pos += 1
                    obj[key] = value(level + 1)
                else:
                    obj.append(value(level + 1))
                whitespace()
                require(pos < len(text))
                sep = text[pos]
                pos += 1
                if sep == end:
                    return obj
                require(sep == ',')
        for literal, result in [('true', True), ('false', False), ('null', None)]:
            if text.startswith(literal, pos):
                pos += len(literal)
                return result
        match = re.match(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?', text[pos:])
        require(match is not None)
        token = match.group()
        require(len(token) <= 64)
        pos += len(token)
        number = float(token) if any(c in token for c in '.eE') else int(token)
        require(not isinstance(number, float) or math.isfinite(number))
        return number

    result = value(0)
    whitespace()
    require(pos == len(text) and type(result) is dict)
    return result


def fields(value, names):
    require(type(value) is dict and set(value) == set(names.split()))


def choice(value, choices, code='MALFORMED_OUTPUT'):
    require(type(value) is str and value in choices, code)


def array(value, maximum, minimum=0):
    require(type(value) is list and minimum <= len(value) <= maximum)


def unique_strings(value, maximum, minimum=0):
    array(value, maximum, minimum)
    require(all(type(v) is str for v in value))
    require(len(set(value)) == len(value))
