from dataclasses import dataclass

from pydantic_core import PydanticCustomError
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.endpoint import Endpoint
from app.db.models.endpoint_resource_binding import EndpointResourceBinding
from app.schemas.endpoint_resource_binding import validate_resource_binding_selector
from app.services.openapi_binding_candidates import is_identifier_name_or_uuid


MAX_BODY_SCHEMA_DEPTH = 16
MAX_BODY_SCHEMA_NODES = 1024
MAX_PROPERTIES_PER_OBJECT = 128
MAX_NEW_INFERRED_BINDINGS = 64
BODY_CONFIDENCE = 30
UNSUPPORTED_SCHEMA_KEYS = {
    "$ref", "allOf", "oneOf", "anyOf", "not", "if", "then", "else",
    "discriminator",
}


class OpenAPIBodyBindingInferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAPIBodyBindingInferenceResult:
    endpoint_id: int
    eligible_count: int
    created_count: int
    existing_inferred_count: int
    skipped_operator_count: int


def escape_json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def request_body_schema(endpoint: Endpoint) -> dict | None:
    request_body = endpoint.request_body
    if not isinstance(request_body, dict):
        return None
    content = request_body.get("content")
    if not isinstance(content, dict):
        return None
    media_type = content.get("application/json")
    if not isinstance(media_type, dict):
        return None
    schema = media_type.get("schema")
    return schema if isinstance(schema, dict) else None


def derive_body_candidates(endpoint: Endpoint) -> list[tuple[str, str]]:
    root = request_body_schema(endpoint)
    if root is None:
        return []
    candidates: set[tuple[str, str]] = set()
    pending: list[tuple[dict, tuple[str, ...], int]] = [(root, (), 1)]
    nodes_seen = 0
    while pending:
        schema, path, depth = pending.pop()
        nodes_seen += 1
        if nodes_seen > MAX_BODY_SCHEMA_NODES:
            raise OpenAPIBodyBindingInferenceError(
                "openapi_body_schema_node_limit_exceeded"
            )
        if depth > MAX_BODY_SCHEMA_DEPTH:
            raise OpenAPIBodyBindingInferenceError(
                "openapi_body_schema_depth_limit_exceeded"
            )
        if UNSUPPORTED_SCHEMA_KEYS.intersection(schema):
            raise OpenAPIBodyBindingInferenceError(
                "openapi_body_schema_unsupported_construct"
            )
        if schema.get("type") == "array" or "items" in schema:
            continue
        properties = schema.get("properties")
        if properties is not None and not isinstance(properties, dict):
            raise OpenAPIBodyBindingInferenceError(
                "openapi_body_schema_malformed"
            )
        if isinstance(properties, dict):
            if schema.get("type") not in {None, "object"}:
                raise OpenAPIBodyBindingInferenceError(
                    "openapi_body_schema_malformed"
                )
            if len(properties) > MAX_PROPERTIES_PER_OBJECT:
                raise OpenAPIBodyBindingInferenceError(
                    "openapi_body_schema_property_limit_exceeded"
                )
            children: list[tuple[dict, tuple[str, ...], int]] = []
            for name in sorted(key for key in properties if isinstance(key, str)):
                child = properties[name]
                if not isinstance(child, dict):
                    continue
                children.append((child, (*path, name), depth + 1))
            pending.extend(reversed(children))
            continue
        if schema.get("type") == "object" or not path:
            continue
        name = path[-1]
        if not is_identifier_name_or_uuid(name, schema):
            continue
        selector = "/" + "/".join(
            escape_json_pointer_token(token) for token in path
        )
        try:
            validate_resource_binding_selector("body", selector)
        except (ValueError, TypeError, PydanticCustomError):
            continue
        candidates.add(("body", selector))
    return sorted(candidates)


def infer_openapi_body_binding_candidates(
    db: Session,
    endpoint: Endpoint,
) -> OpenAPIBodyBindingInferenceResult:
    candidates = derive_body_candidates(endpoint)
    existing_by_locator: dict[tuple[str, str], list[EndpointResourceBinding]] = {}
    if candidates:
        existing = db.scalars(select(EndpointResourceBinding).where(
            EndpointResourceBinding.endpoint_id == endpoint.id,
            tuple_(
                EndpointResourceBinding.location,
                EndpointResourceBinding.selector,
            ).in_(candidates),
        )).all()
        for binding in existing:
            existing_by_locator.setdefault(
                (binding.location, binding.selector), []
            ).append(binding)

    new_candidates: list[tuple[str, str]] = []
    existing_inferred_count = 0
    skipped_operator_count = 0
    for candidate in candidates:
        bindings = existing_by_locator.get(candidate, [])
        if any(item.provenance == "operator_supplied" for item in bindings):
            skipped_operator_count += 1
        elif any(item.provenance == "openapi_inferred" for item in bindings):
            existing_inferred_count += 1
        else:
            new_candidates.append(candidate)
    if len(new_candidates) > MAX_NEW_INFERRED_BINDINGS:
        raise OpenAPIBodyBindingInferenceError(
            "openapi_body_binding_creation_limit_exceeded"
        )

    created_count = 0
    for location, selector in new_candidates:
        created_id = db.scalar(
            insert(EndpointResourceBinding)
            .values(
                endpoint_id=endpoint.id,
                location=location,
                selector=selector,
                provenance="openapi_inferred",
                confidence=BODY_CONFIDENCE,
                review_state="candidate",
            )
            .on_conflict_do_nothing(
                constraint="uq_endpoint_resource_binding_exact"
            )
            .returning(EndpointResourceBinding.id)
        )
        if created_id is None:
            existing_inferred_count += 1
        else:
            created_count += 1
    return OpenAPIBodyBindingInferenceResult(
        endpoint_id=endpoint.id,
        eligible_count=len(candidates),
        created_count=created_count,
        existing_inferred_count=existing_inferred_count,
        skipped_operator_count=skipped_operator_count,
    )
