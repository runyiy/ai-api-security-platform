import re
from dataclasses import dataclass

from pydantic_core import PydanticCustomError
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.endpoint import Endpoint
from app.db.models.endpoint_resource_binding import EndpointResourceBinding
from app.schemas.endpoint_resource_binding import validate_resource_binding_selector


MAX_PARAMETERS_INSPECTED = 128
MAX_NEW_INFERRED_BINDINGS = 64
PATH_CONFIDENCE = 60
QUERY_CONFIDENCE = 40


class OpenAPIBindingInferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAPIBindingInferenceResult:
    endpoint_id: int
    eligible_count: int
    created_count: int
    existing_inferred_count: int
    skipped_operator_count: int


def is_identifier_parameter(parameter: dict, name: str) -> bool:
    lowered = name.lower()
    name_signal = (
        lowered == "id"
        or lowered.endswith(("_id", "-id", ".id"))
        or (len(name) > 2 and name.endswith("Id"))
    )
    schema = parameter.get("schema")
    uuid_signal = isinstance(schema, dict) and schema.get("format") == "uuid"
    return name_signal or uuid_signal


def derive_candidates(endpoint: Endpoint) -> list[tuple[str, str]]:
    parameters = endpoint.parameters
    if not isinstance(parameters, list):
        return []
    if len(parameters) > MAX_PARAMETERS_INSPECTED:
        raise OpenAPIBindingInferenceError(
            "openapi_binding_parameter_limit_exceeded"
        )
    path_variables = set(re.findall(
        r"\{([A-Za-z_][A-Za-z0-9_.-]{0,127})\}", endpoint.path
    ))
    candidates: set[tuple[str, str]] = set()
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("name")
        location = parameter.get("in")
        if not isinstance(name, str) or location not in {"path", "query"}:
            continue
        try:
            validate_resource_binding_selector(location, name)
        except (ValueError, TypeError, PydanticCustomError):
            continue
        if location == "path" and name not in path_variables:
            continue
        if is_identifier_parameter(parameter, name):
            candidates.add((location, name))
    return sorted(candidates)


def infer_openapi_binding_candidates(
    db: Session,
    endpoint: Endpoint,
) -> OpenAPIBindingInferenceResult:
    candidates = derive_candidates(endpoint)
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
        raise OpenAPIBindingInferenceError(
            "openapi_binding_creation_limit_exceeded"
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
                confidence=(
                    PATH_CONFIDENCE if location == "path" else QUERY_CONFIDENCE
                ),
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
    return OpenAPIBindingInferenceResult(
        endpoint_id=endpoint.id,
        eligible_count=len(candidates),
        created_count=created_count,
        existing_inferred_count=existing_inferred_count,
        skipped_operator_count=skipped_operator_count,
    )
