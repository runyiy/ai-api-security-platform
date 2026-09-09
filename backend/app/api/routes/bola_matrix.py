"""One bounded offline preview operation; POST never contacts a Target."""
import json
from typing import Final

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.generators.bola_matrix import BOLAMatrixPlanningError
from app.schemas.bola_matrix import (
    BOLAMatrixPreviewErrorResponse, BOLAMatrixPreviewRequest, BOLAMatrixPreviewResponse,
)
from app.services.bola_binding_matrix_preview import (
    BOLABindingMatrixPreviewError, BOLAResourceSlotAssignment, preview_bola_binding_matrix,
)
from app.services.bola_binding_selection import BOLABindingSelectionError
from app.services.bola_matrix_preview import BOLAMatrixPreviewError
from app.services.resource_access_resolution import ResourceAccessResolutionError


MAX_BOLA_MATRIX_REQUEST_BYTES: Final = 65536
MAX_BOLA_MATRIX_RESPONSE_BYTES: Final = 4194304
NO_STORE = {"Cache-Control": "no-store"}
INVALID_REQUEST = "bola_matrix_preview_invalid_request"
FAILED = "bola_matrix_preview_failed"
DOMAIN_STATUS = {
    **dict.fromkeys((
        "endpoint_not_found", "resource_not_found", "test_identity_not_found", "bola_binding_not_found",
    ), 404),
    **dict.fromkeys((
        "bola_binding_matrix_invalid_input", "bola_binding_matrix_assignment_limit_exceeded",
        "bola_binding_matrix_duplicate_binding", "bola_matrix_invalid_access_fact",
        "bola_matrix_duplicate_access_fact", "bola_matrix_fact_limit_exceeded",
        "bola_matrix_evaluation_time_invalid", "evaluation_time_timezone_required",
        "bola_binding_selection_invalid_input",
    ), 422),
    **dict.fromkeys((
        "bola_binding_matrix_duplicate_slot", "bola_binding_endpoint_mismatch",
        "bola_binding_not_confirmed", "bola_binding_location_unsupported", "bola_binding_selector_invalid",
        "bola_binding_endpoint_metadata_invalid", "bola_binding_selector_ambiguous",
        "bola_binding_selector_not_declared", "bola_binding_parameter_limit_exceeded",
        "bola_matrix_endpoint_resource_target_mismatch", "resource_identity_target_mismatch",
        "bola_matrix_identity_inactive", "resource_access_resolution_limit_exceeded",
    ), 409),
}
DOMAIN_ERRORS = (
    BOLABindingMatrixPreviewError, BOLABindingSelectionError, BOLAMatrixPreviewError,
    BOLAMatrixPlanningError, ResourceAccessResolutionError,
)


class _TransportError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": code}, headers=NO_STORE)


class _PreviewRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def scoped_handler(request: Request):
            try:
                return await handler(request)
            except _TransportError as exc:
                return _error(exc.status, exc.code)
            except DOMAIN_ERRORS as exc:
                status = DOMAIN_STATUS.get(exc.code) if type(exc.code) is str else None
                return _error(status, exc.code) if status else _error(500, FAILED)
            except Exception:
                # Includes dependency, validation-of-output and serialization failures.
                return _error(500, FAILED)
        return scoped_handler


router = APIRouter(prefix="/bola-matrix", tags=["bola-matrix"], route_class=_PreviewRoute)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key.")
        value[key] = item
    return value


def _reject_number(value):
    # No valid request field accepts a float, including non-finite numbers.
    raise ValueError("Invalid JSON number.")


def _parse_json(raw: bytearray):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                      parse_constant=_reject_number, parse_float=_reject_number)


async def _read_request(request: Request) -> BOLAMatrixPreviewRequest:
    content_types = request.headers.getlist("content-type")
    encodings = request.headers.getlist("content-encoding")
    parts = content_types[0].lower().split(";") if len(content_types) == 1 else []
    if (
        not parts or parts[0].strip() != "application/json" or len(parts) > 2
        or (len(parts) == 2 and parts[1].strip() not in ('charset=utf-8', 'charset="utf-8"'))
        or len(encodings) > 1 or (encodings and encodings[0].strip().lower() != "identity")
    ):
        raise _TransportError(415, "bola_matrix_preview_media_type_unsupported")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BOLA_MATRIX_REQUEST_BYTES:
            raise _TransportError(413, "bola_matrix_preview_request_limit_exceeded")
        raw.extend(chunk)
    if request.query_params:
        raise _TransportError(422, INVALID_REQUEST)
    try:
        return BOLAMatrixPreviewRequest.model_validate(_parse_json(raw))
    except (ValueError, RecursionError, OverflowError):
        raise _TransportError(422, INVALID_REQUEST) from None


# The custom reader has no FastAPI Body parameter. Inline its one nested request
# schema so OpenAPI describes the real typed input without dangling $defs refs.
_request_schema = BOLAMatrixPreviewRequest.model_json_schema()
_request_schema["properties"]["assignments"]["items"] = _request_schema.pop("$defs")["BOLASlotAssignmentRequest"]
_request_schema["description"] = (
    "At most 65536 UTF-8 bytes. Unique binding and identity IDs; at most 512 assignment × identity cells. "
    "Duplicate IDs and aggregate overflow are schema errors (422 bola_matrix_preview_invalid_request)."
)
_request_body = {
    "required": True, "content": {"application/json": {
        "schema": _request_schema,
        "example": {"endpoint_id": 1, "assignments": [{"binding_id": 2, "resource_id": 3}],
                    "test_identity_ids": [4], "evaluation_time": "2030-06-01T12:00:00Z"},
    }},
    "description": "Synthetic placeholder IDs only; select existing reviewed metadata in your local deployment.",
}
_responses = {
    status: {
        "model": BOLAMatrixPreviewErrorResponse,
        "description": "; ".join(code for code, mapped in DOMAIN_STATUS.items() if mapped == status),
    } for status in (404, 409, 422)
}
_responses[422]["description"] += "; bola_matrix_preview_invalid_request for invalid transport/schema."
_responses.update({
    413: {"model": BOLAMatrixPreviewErrorResponse,
          "description": "bola_matrix_preview_request_limit_exceeded: actual body exceeds 65536 bytes."},
    415: {"model": BOLAMatrixPreviewErrorResponse,
          "description": "bola_matrix_preview_media_type_unsupported: UTF-8 application/json and identity encoding only."},
    500: {"model": BOLAMatrixPreviewErrorResponse,
          "description": "bola_matrix_preview_response_limit_exceeded: serialized output exceeds 4194304 bytes; "
                         "bola_matrix_preview_failed: invalid dependency output or internal failure."},
})


@router.post(
    "/preview", response_model=BOLAMatrixPreviewResponse, responses=_responses,
    openapi_extra={"requestBody": _request_body},
    summary="Preview explicit BOLA slot proposals without execution",
    description=(
        "Read-only, transient, non-executable planning for one trusted operator's self-hosted platform. "
        "POST transports this request to the platform only; no request is sent to any Target. "
        "Select 1–32 confirmed path/query bindings and 0–512 explicit identities, with at most 512 cells. "
        "Assignments are proposals, not Resource-to-slot approval or parent-child membership proof. "
        "Relationship and expected access are independent. Conflict/insufficient facts remain visible and "
        "may have no candidates. Explicit time governs assertion eligibility, not historical binding or "
        "identity metadata. Results confer no connection permission and create no cases or execution plans. "
        "Actual request limit 65536 bytes; complete UTF-8 response limit 4194304 bytes. "
        "Unexpected query parameters, extras, duplicate JSON keys and non-finite numbers are rejected. "
        "All responses use Cache-Control: no-store. Public runtime remains blocked."
    ),
)
def preview_matrix(
    payload: BOLAMatrixPreviewRequest = Depends(_read_request),
    db: Session = Depends(get_db),
) -> Response:
    # FastAPI runs this synchronous handler in its threadpool, after async input
    # validation. The normal get_db dependency alone owns Session cleanup.
    preview = preview_bola_binding_matrix(
        db, endpoint_id=payload.endpoint_id,
        assignments=tuple(BOLAResourceSlotAssignment(a.binding_id, a.resource_id) for a in payload.assignments),
        test_identity_ids=tuple(payload.test_identity_ids), evaluation_time=payload.evaluation_time,
    )
    validated = BOLAMatrixPreviewResponse.from_preview(preview, payload)
    encoded = validated.model_dump_json().encode("utf-8")
    if len(encoded) > MAX_BOLA_MATRIX_RESPONSE_BYTES:
        raise _TransportError(500, "bola_matrix_preview_response_limit_exceeded")
    return Response(content=encoded, media_type="application/json", headers=NO_STORE)
