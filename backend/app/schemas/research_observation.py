"""ra-observation/1: bounded JSON decoding and strict, authority-free values."""
import ipaddress
import json
import re

INTEGER_TOKEN = re.compile(r"-?(?:0|[1-9][0-9]*)")
from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator, model_validator
from app.schemas.research_context import StrictRecord, SyntheticReference, ID, Version

MAX_BYTES, MAX_ENTRY_BYTES, MAX_DEPTH, MAX_NODES = 262144, 4096, 5, 16384
Label = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$", max_length=64)]
Name = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$", max_length=64)]


class ObservationError(Exception):
    def __init__(self, code="observation_context_unavailable", status=409):
        self.code, self.status = code, status
        super().__init__(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def timestamp(value):
    if not isinstance(value, str) or not 20 <= len(value) <= 32 or not re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)", value
    ) or value.endswith("-00:00"):
        raise ValueError()
    if not value.endswith("Z") and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError()
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def origin(value):
    if not isinstance(value, str) or not value.isascii() or not 1 <= len(value) <= 256:
        raise ValueError()
    m = re.fullmatch(r"(https?)://(\[[0-9a-f:]+\]|[a-z0-9.-]+):([1-9][0-9]{0,4})", value)
    if not m or int(m[3]) > 65535:
        raise ValueError()
    host = m[2]
    if host.startswith("["):
        if str(ipaddress.IPv6Address(host[1:-1])) != host[1:-1]:
            raise ValueError()
    elif re.fullmatch(r"[0-9.]+", host):
        if str(ipaddress.IPv4Address(host)) != host:
            raise ValueError()
    elif any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in host.split(".")):
        raise ValueError()
    return value


def target_origin(value):
    p = urlsplit(value)
    if p.username or p.password or p.query or p.fragment or p.path not in ("", "/"):
        raise ValueError()
    host = p.hostname
    host = f"[{host}]" if host and ":" in host else host
    return origin(f"{p.scheme}://{host}:{p.port or (443 if p.scheme == 'https' else 80)}")


def template(value):
    if not value.isascii() or not 1 <= len(value) <= 512 or not value.startswith("/"):
        raise ValueError()
    slots = []
    if value != "/":
        for part in value[1:].split("/"):
            if re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_]{0,63}\}", part):
                slots.append(part)
            elif not re.fullmatch(r"[A-Za-z0-9_-]+", part):
                raise ValueError()
    if len(slots) > 8 or len(slots) != len(set(slots)):
        raise ValueError()
    return value


def unique(values):
    if len(values) != len(set(values)):
        raise ValueError()
    return values


class ResponseFacts(StrictRecord):
    status_code: Annotated[int, Field(ge=100, le=599)] | None
    media_kind: Literal["json_object", "json_array", "html", "text", "other", "unknown"]
    capture_state: Literal["complete", "truncated", "missing"]
    object_labels: Annotated[list[Label], Field(max_length=8)]
    session_state: Literal["reported_healthy", "expired", "login_page", "unknown"]

    @model_validator(mode="after")
    def coherent(self):
        unique(self.object_labels)
        if self.capture_state == "missing" and (self.status_code is not None or self.media_kind != "unknown" or self.object_labels):
            raise ValueError()
        if self.object_labels and (self.capture_state != "complete" or self.media_kind != "json_object" or self.status_code is None):
            raise ValueError()
        return self


class ObservationEntry(StrictRecord):
    entry_ref: Label
    source_entry_index: Annotated[int, Field(ge=0, le=999999)]
    observed_at: str
    method: Literal["GET"]
    origin: Annotated[str, Field(max_length=256)]
    path_template: Annotated[str, Field(max_length=512)]
    query_names: Annotated[list[Name], Field(max_length=16)]
    actor_ref: Label | None
    resource_labels: Annotated[list[Label], Field(min_length=1, max_length=8)]
    response: ResponseFacts

    _origin = field_validator("origin")(origin)
    _template = field_validator("path_template")(template)

    @model_validator(mode="after")
    def coherent(self):
        timestamp(self.observed_at)
        unique(self.query_names)
        unique(self.resource_labels)
        if not set(self.response.object_labels) <= set(self.resource_labels):
            raise ValueError()
        return self


class ObservationInput(StrictRecord):
    format: Literal["ra-observation"]
    version: Literal["1"]
    project_ref: Label
    batch_ref: Label
    preparation_ref: Label
    prepared_at: str
    entries: Annotated[list[ObservationEntry], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def coherent(self):
        prepared = timestamp(self.prepared_at)
        unique([e.entry_ref for e in self.entries])
        unique([e.source_entry_index for e in self.entries])
        if any(timestamp(e.observed_at) > prepared for e in self.entries):
            raise ValueError()
        return self


class BoundedJSON:
    """Budget before descent/allocation. Keys are strings, not value nodes.

    The stdlib string scanner handles escapes only after the complete byte cap;
    containers are decoded here so depth/node rejection happens during parsing.
    """
    def __init__(self, raw):
        if not isinstance(raw, bytes):
            raise ObservationError("observation_shape_invalid", 422)
        if len(raw) > MAX_BYTES:
            raise ObservationError("observation_input_limit", 413)
        try:
            self.s = raw.decode("utf-8")
        except UnicodeError:
            raise ObservationError("observation_encoding_invalid", 422) from None
        self.i, self.nodes = 0, 0

    def space(self):
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
            self.i += 1

    def string(self):
        if self.s[self.i:self.i+1] != '"':
            raise ValueError()
        value, self.i = json.decoder.scanstring(self.s, self.i + 1, True)
        value.encode("utf-8")  # Reject unpaired escaped surrogates, including keys.
        return value

    def value(self, depth):
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_NODES:
            raise ObservationError("observation_structure_limit", 413)
        self.space()
        ch = self.s[self.i:self.i+1]
        if ch == '"':
            return self.string()
        if ch in ("{", "["):
            self.i += 1
            result = {} if ch == "{" else []
            end = "}" if ch == "{" else "]"
            self.space()
            if self.s[self.i:self.i+1] == end:
                self.i += 1
                return result
            while True:
                self.space()
                if ch == "{":
                    key = self.string()
                    if key in result:
                        raise ObservationError("observation_duplicate_key", 422)
                    self.space()
                    if self.s[self.i:self.i+1] != ":":
                        raise ValueError()
                    self.i += 1
                    result[key] = self.value(depth + 1)
                else:
                    result.append(self.value(depth + 1))
                self.space()
                token = self.s[self.i:self.i+1]
                self.i += 1
                if token == end:
                    return result
                if token != ",":
                    raise ValueError()
        for literal, value in (("true", True), ("false", False), ("null", None)):
            if self.s.startswith(literal, self.i):
                self.i += len(literal)
                return value
        m = INTEGER_TOKEN.match(self.s, self.i)
        if not m or len(m[0]) > 19:
            raise ValueError()
        self.i += len(m[0])
        n = int(m[0])
        if not -(2**63) <= n < 2**63:
            raise ValueError()
        return n

    def parse(self):
        try:
            value = self.value(0)
            self.space()
            if self.i != len(self.s):
                raise ValueError()
            return value
        except (ValueError, IndexError, UnicodeError, RecursionError, OverflowError):
            raise ObservationError("observation_shape_invalid", 422) from None


def entry_size(entry):
    if len(canonical(entry)) > MAX_ENTRY_BYTES:
        raise ObservationError("observation_entry_limit", 413)


def validate(schema, value):
    try:
        return schema.model_validate(value.model_dump(warnings=False) if isinstance(value, schema) else value)
    except ValidationError as exc:
        code = "observation_field_not_allowed" if any(e["type"] == "extra_forbidden" for e in exc.errors(include_input=False, include_url=False)) else "observation_shape_invalid"
        raise ObservationError(code, 422) from None
    except (ValueError, TypeError, AttributeError):
        raise ObservationError("observation_shape_invalid", 422) from None


def parse_observation(raw, now):
    value = BoundedJSON(raw).parse()
    if isinstance(value, dict) and isinstance(value.get("entries"), list):
        for entry in value["entries"]:
            entry_size(entry)
            if isinstance(entry, dict) and "method" in entry and entry["method"] != "GET":
                raise ObservationError("observation_method_unsupported", 422)
    parsed = validate(ObservationInput, value)
    if timestamp(parsed.prepared_at) > now:
        raise ObservationError("observation_time_invalid", 422)
    return parsed


class PreparationInput(StrictRecord):
    preparation_ref: Label
    context_version: Version
    target_id: ID
    source: SyntheticReference
    review: SyntheticReference
    converter_version: Literal["synthetic_fixture_v1"]
    data_eligibility: Literal["synthetic"]
    retention_seconds: Annotated[int, Field(ge=1, le=2592000)]
    valid_until: str
    batch_refs: Annotated[list[Label], Field(min_length=1, max_length=128)]
    entry_refs: Annotated[list[Label], Field(min_length=1, max_length=128)]
    actor_refs: Annotated[list[Label], Field(max_length=128)]
    resource_labels: Annotated[list[Label], Field(min_length=1, max_length=128)]
    path_templates: Annotated[list[str], Field(min_length=1, max_length=16)]
    query_names: Annotated[list[Name], Field(max_length=16)]
    corrects_preparation: Label | None

    @model_validator(mode="after")
    def eligible(self):
        timestamp(self.valid_until)
        for name in ("batch_refs", "entry_refs", "actor_refs", "resource_labels", "path_templates", "query_names"):
            unique(getattr(self, name))
        # Closed synthetic vocabulary, never an arbitrary text 'sanitized' checkbox.
        for kind, values in (("preparation", [self.preparation_ref]), ("batch", self.batch_refs),
                             ("entry", self.entry_refs), ("actor", self.actor_refs), ("resource", self.resource_labels)):
            if any(not re.fullmatch(kind + r"_[1-9][0-9]{0,5}", v) for v in values):
                raise ValueError()
        for p in self.path_templates:
            template(p)
            if p == "/":
                continue
            if any(not re.fullmatch(r"(?:synthetic|folders|projects|tasks|items|\{(?:resource_id|project_id|task_id|item_id|slot_[1-8])\})", part)
                   for part in p[1:].split("/")):
                raise ValueError()
        if any(n not in {"page", "limit", "resource_id", "project_id", "task_id", "item_id"}
               and not re.fullmatch(r"query_(?:[1-9]|1[0-6])", n) for n in self.query_names):
            raise ValueError()
        return self


class ReviewInput(StrictRecord):
    review: SyntheticReference


class HoldInput(ReviewInput):
    reason: Literal["synthetic_review", "synthetic_incident"]
    until: str

    @model_validator(mode="after")
    def aware_until(self):
        timestamp(self.until)
        return self


class ControlInput(ReviewInput):
    action: Literal["suspend", "reconcile", "rotate_audit"]
    deleted_observation_ids: Annotated[list[ID], Field(max_length=1024)] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_replay(self):
        unique(self.deleted_observation_ids)
        if self.action != "reconcile" and self.deleted_observation_ids:
            raise ValueError()
        return self
