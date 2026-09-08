"""Select one current reviewed parameter position without assigning a Resource."""
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.endpoint import Endpoint
from app.db.models.endpoint_resource_binding import EndpointResourceBinding
from app.schemas.endpoint_resource_binding import validate_resource_binding_selector


MAX_BOLA_BINDING_PARAMETERS: Final = 256


class BOLABindingSelectionError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class BOLAReviewedBindingSelection:
    endpoint_id: int
    binding_id: int
    location: Literal["path", "query"]
    selector: str
    review_state: Literal["confirmed"]


def _validate_path(path: object, selector: str) -> None:
    if not isinstance(path, str) or len(path) > 500:
        raise BOLABindingSelectionError("bola_binding_endpoint_metadata_invalid")
    names: set[str] = set()
    start: int | None = None
    for index, character in enumerate(path):
        if character == "{":
            if start is not None:
                raise BOLABindingSelectionError("bola_binding_endpoint_metadata_invalid")
            start = index + 1
        elif character == "}":
            if start is None:
                raise BOLABindingSelectionError("bola_binding_endpoint_metadata_invalid")
            name = path[start:index]
            try:
                validate_resource_binding_selector("path", name)
            except (TypeError, ValueError):
                raise BOLABindingSelectionError("bola_binding_endpoint_metadata_invalid") from None
            if name in names:
                raise BOLABindingSelectionError("bola_binding_selector_ambiguous")
            names.add(name)
            start = None
    if start is not None:
        raise BOLABindingSelectionError("bola_binding_endpoint_metadata_invalid")
    if selector not in names:
        raise BOLABindingSelectionError("bola_binding_selector_not_declared")


def _validate_query(parameters: object, selector: str) -> None:
    if not isinstance(parameters, list):
        raise BOLABindingSelectionError("bola_binding_endpoint_metadata_invalid")
    if len(parameters) > MAX_BOLA_BINDING_PARAMETERS:
        raise BOLABindingSelectionError("bola_binding_parameter_limit_exceeded")
    matches = 0
    for parameter in parameters:
        if not isinstance(parameter, dict):
            raise BOLABindingSelectionError("bola_binding_endpoint_metadata_invalid")
        name = parameter.get("name")
        location = parameter.get("in")
        if not isinstance(name, str) or not name or location not in ("path", "query", "header", "cookie"):
            raise BOLABindingSelectionError("bola_binding_endpoint_metadata_invalid")
        if location == "query" and name == selector:
            matches += 1
    if matches == 0:
        raise BOLABindingSelectionError("bola_binding_selector_not_declared")
    if matches > 1:
        raise BOLABindingSelectionError("bola_binding_selector_ambiguous")


def select_bola_binding(
    db: Session,
    *,
    endpoint_id: int,
    binding_id: int,
) -> BOLAReviewedBindingSelection:
    """Read current exact metadata in the caller's clean read transaction.

    The returned descriptor is transient; it supplies neither a durable review
    revision nor execution permission and cannot prevent later metadata changes.
    """
    if db.new or db.dirty or db.deleted:
        raise BOLABindingSelectionError("bola_binding_selection_session_not_clean")
    if any(type(value) is not int or value <= 0 for value in (endpoint_id, binding_id)):
        raise BOLABindingSelectionError("bola_binding_selection_invalid_input")

    with db.no_autoflush:
        endpoint = db.execute(select(Endpoint.id).where(Endpoint.id == endpoint_id)).scalar_one_or_none()
        if endpoint is None:
            raise BOLABindingSelectionError("endpoint_not_found")
        binding = db.execute(select(
            EndpointResourceBinding.endpoint_id,
            EndpointResourceBinding.location,
            EndpointResourceBinding.selector,
            EndpointResourceBinding.review_state,
        ).where(EndpointResourceBinding.id == binding_id)).one_or_none()
        if binding is None:
            raise BOLABindingSelectionError("bola_binding_not_found")
        if binding.endpoint_id != endpoint_id:
            raise BOLABindingSelectionError("bola_binding_endpoint_mismatch")
        if binding.review_state != "confirmed":
            raise BOLABindingSelectionError("bola_binding_not_confirmed")
        if binding.location not in ("path", "query"):
            raise BOLABindingSelectionError("bola_binding_location_unsupported")
        try:
            validate_resource_binding_selector(binding.location, binding.selector)
        except (TypeError, ValueError):
            raise BOLABindingSelectionError("bola_binding_selector_invalid") from None

        if binding.location == "path":
            path = db.execute(select(Endpoint.path).where(Endpoint.id == endpoint_id)).scalar_one()
            _validate_path(path, binding.selector)
        else:
            parameters = db.execute(select(Endpoint.parameters).where(Endpoint.id == endpoint_id)).scalar_one()
            _validate_query(parameters, binding.selector)
        return BOLAReviewedBindingSelection(endpoint_id, binding_id, binding.location, binding.selector, binding.review_state)
