"""Adopted ra-json/1 and bounded local W1 commands. No caller health claims."""
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Annotated, Literal
from pydantic import Field, model_validator
from app.schemas.research_context import StrictRecord, SyntheticReference, ID, Version
from app.schemas.research_knowledge import ExactRef

MAX_INPUT = MAX_CORE = 32768
MAX_OUTPUT = 65536
MAX_VERSIONS = 1024
MAX_DECISIONS = 16
MAX_AUDIT = 4096
Number = Annotated[int, Field(strict=True, ge=1, le=1024)]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class IntentError(Exception):
    def __init__(self, code='intent_unavailable', status=409):
        self.code, self.status = code, status
        super().__init__(code)


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
        r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)', value
    ):
        raise IntentError('intent_invalid', 422)
    try:
        if value[-1] != 'Z' and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise ValueError('invalid offset')
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc)
    except ValueError:
        raise IntentError('intent_invalid', 422) from None


def stamp(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise IntentError('intent_invalid_clock', 422)
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _tree(value, depth=0, count=None):
    count = [0] if count is None else count
    count[0] += 1
    if count[0] > 8192 or depth > 8:
        raise IntentError('intent_structure_limit', 422)
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise IntentError('intent_invalid', 422)
            _tree(key, depth+1, count); _tree(child, depth+1, count)
    elif type(value) is list:
        for child in value:
            _tree(child, depth+1, count)
    elif type(value) is str:
        value.encode('utf-8', errors='strict')
    elif value is not None and type(value) not in (bool, int):
        raise IntentError('intent_invalid', 422)


def canonical(value):
    try:
        _tree(value)
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise IntentError('intent_invalid', 422) from None


def digest(protocol, value):
    return hashlib.sha256(protocol.encode('ascii')+b'\n'+canonical(value)).hexdigest()


def parse(raw):
    if len(raw) > MAX_INPUT:
        raise IntentError('intent_input_limit', 413)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise IntentError('intent_invalid', 422)
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
            parse_float=lambda _: (_ for _ in ()).throw(IntentError('intent_invalid', 422)),
            parse_constant=lambda _: (_ for _ in ()).throw(IntentError('intent_invalid', 422)))
        canonical(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise IntentError('intent_invalid', 422) from None


def validate(schema, value):
    try:
        value = value.model_dump(warnings=False) if isinstance(value, schema) else value
        return schema.model_validate(parse(canonical(value)))
    except (ValueError, TypeError, AttributeError, RecursionError):
        raise IntentError('intent_invalid', 422) from None


class Reference(StrictRecord):
    number: Number
    version: Number
    digest: Digest


class SubjectRef(StrictRecord):
    number: Number
    version: Number


class MappingInput(StrictRecord):
    context_version: Version
    target_id: ID
    endpoint_id: ID
    binding_id: ID
    resource_id: ID
    expected_version: Annotated[int, Field(strict=True, ge=0, le=1023)]
    decision: Literal['confirm', 'withdraw']
    evidence: SyntheticReference


class ManifestAction(StrictRecord):
    role: Literal['baseline', 'probe', 'health_baseline', 'health_probe']
    subject: SubjectRef
    mapping: Reference


class ManifestInput(StrictRecord):
    context_version: Version
    target_id: ID
    expected_version: Annotated[int, Field(strict=True, ge=0, le=1023)]
    actions: Annotated[list[ManifestAction], Field(min_length=2, max_length=4)]
    duration_seconds: Annotated[int, Field(strict=True, ge=1, le=300)]
    rate_millirequests_per_second: Annotated[int, Field(strict=True, ge=1, le=1000)]
    concurrency: Annotated[int, Field(strict=True, ge=1, le=1)]
    evidence: SyntheticReference

    @model_validator(mode='after')
    def roles(self):
        roles = [a.role for a in self.actions]
        if roles != [r for r in ('baseline', 'probe', 'health_baseline', 'health_probe') if r in roles] or not {'baseline','probe'} <= set(roles):
            raise ValueError('invalid')
        return self


class BudgetDecision(StrictRecord):
    manifest: Reference
    expected_sequence: Annotated[int, Field(strict=True, ge=0, le=15)]
    decision: Literal['approved', 'revoked']
    evidence: SyntheticReference


class ConvertInput(StrictRecord):
    manifest: Reference
    expected_version: Annotated[int, Field(strict=True, ge=0, le=1023)]
    purpose: Literal['business', 'health_baseline', 'health_probe']
    knowledge: ExactRef | None
    evidence: SyntheticReference


class Receipt(StrictRecord):
    protocol: Literal['ra-w1-receipt/1']
    reference: Reference
    kind: Literal['mapping', 'manifest', 'budget', 'intent']
    body: dict
    eligibility_until: str
    audit_id: ID
    execution_authorized: Literal[False]
    execution_status: Literal['w2_dependency_closed', 'requires_exact_dispatch']


def output(value):
    """Enforce the adopted depth/node limits on the complete typed response."""
    value=Receipt.model_validate(value).model_dump()
    raw=canonical(value)
    if len(raw)>MAX_OUTPUT:raise IntentError('intent_response_limit',500)
    return raw
