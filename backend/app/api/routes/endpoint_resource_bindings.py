import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.endpoint import Endpoint
from app.db.models.endpoint_resource_binding import EndpointResourceBinding
from app.db.session import get_db
from app.schemas.endpoint_resource_binding import (
    EndpointResourceBindingCreate,
    EndpointResourceBindingInferenceRead,
    EndpointResourceBindingInferenceRequest,
    EndpointResourceBindingRead,
    EndpointResourceBindingReviewUpdate,
    validate_resource_binding_selector,
)
from app.services.openapi_binding_candidates import (
    OpenAPIBindingInferenceError,
    infer_openapi_binding_candidates,
)
from app.services.openapi_body_binding_candidates import (
    OpenAPIBodyBindingInferenceError,
    infer_openapi_body_binding_candidates,
)


router = APIRouter(tags=["endpoint-resource-bindings"])
MAX_PAGE_SIZE = 100
MAX_PAGE_OFFSET = 10_000


def get_endpoint_or_404(db: Session, endpoint_id: int) -> Endpoint:
    endpoint = db.get(Endpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Endpoint not found.")
    return endpoint


def get_endpoint_for_update_or_404(db: Session, endpoint_id: int) -> Endpoint:
    endpoint = db.scalar(
        select(Endpoint).where(Endpoint.id == endpoint_id).with_for_update()
    )
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Endpoint not found.")
    return endpoint


def validate_declared_parameter(
    endpoint: Endpoint, payload: EndpointResourceBindingCreate
) -> None:
    if payload.location == "body":
        return
    declared = any(
        isinstance(parameter, dict)
        and parameter.get("name") == payload.selector
        and parameter.get("in") == payload.location
        for parameter in endpoint.parameters
    )
    if payload.location == "path":
        template_names = re.findall(r"\{([A-Za-z_][A-Za-z0-9_.-]{0,127})\}", endpoint.path)
        declared = declared or payload.selector in template_names
    if not declared:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="resource_binding_selector_not_declared",
        )


@router.post(
    "/endpoints/{endpoint_id}/resource-bindings",
    response_model=EndpointResourceBindingRead,
    status_code=status.HTTP_201_CREATED,
)
def create_endpoint_resource_binding(
    endpoint_id: int,
    payload: EndpointResourceBindingCreate,
    db: Session = Depends(get_db),
) -> EndpointResourceBinding:
    endpoint = get_endpoint_for_update_or_404(db, endpoint_id)
    validate_declared_parameter(endpoint, payload)
    validate_resource_binding_selector(payload.location, payload.selector)
    binding = EndpointResourceBinding(
        endpoint_id=endpoint.id,
        location=payload.location,
        selector=payload.selector,
        provenance="operator_supplied",
        confidence=payload.confidence,
        review_state=payload.review_state,
    )
    db.add(binding)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == (
            "uq_endpoint_resource_binding_exact"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="resource_binding_already_exists",
            ) from exc
        raise
    except Exception:
        db.rollback()
        raise
    db.refresh(binding)
    return binding


@router.post(
    "/endpoints/{endpoint_id}/resource-bindings/infer-openapi-candidates",
    response_model=EndpointResourceBindingInferenceRead,
)
def infer_endpoint_openapi_binding_candidates(
    endpoint_id: int,
    payload: EndpointResourceBindingInferenceRequest,
    db: Session = Depends(get_db),
) -> EndpointResourceBindingInferenceRead:
    del payload
    endpoint = get_endpoint_for_update_or_404(db, endpoint_id)
    try:
        result = infer_openapi_binding_candidates(db, endpoint)
        db.commit()
    except OpenAPIBindingInferenceError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception:
        db.rollback()
        raise
    return EndpointResourceBindingInferenceRead(**result.__dict__)


@router.post(
    "/endpoints/{endpoint_id}/resource-bindings/"
    "infer-openapi-body-candidates",
    response_model=EndpointResourceBindingInferenceRead,
)
def infer_endpoint_openapi_body_binding_candidates(
    endpoint_id: int,
    payload: EndpointResourceBindingInferenceRequest,
    db: Session = Depends(get_db),
) -> EndpointResourceBindingInferenceRead:
    del payload
    endpoint = get_endpoint_for_update_or_404(db, endpoint_id)
    try:
        result = infer_openapi_body_binding_candidates(db, endpoint)
        db.commit()
    except OpenAPIBodyBindingInferenceError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception:
        db.rollback()
        raise
    return EndpointResourceBindingInferenceRead(**result.__dict__)


@router.patch(
    "/endpoints/{endpoint_id}/resource-bindings/{binding_id}/review",
    response_model=EndpointResourceBindingRead,
)
def update_endpoint_resource_binding_review(
    endpoint_id: int,
    binding_id: int,
    payload: EndpointResourceBindingReviewUpdate,
    db: Session = Depends(get_db),
) -> EndpointResourceBinding:
    binding = db.scalar(
        select(EndpointResourceBinding)
        .where(
            EndpointResourceBinding.id == binding_id,
            EndpointResourceBinding.endpoint_id == endpoint_id,
        )
        .with_for_update()
    )
    if binding is None:
        raise HTTPException(status_code=404, detail="Resource binding not found.")
    if "confidence" in payload.model_fields_set:
        binding.confidence = payload.confidence
    if "review_state" in payload.model_fields_set:
        binding.review_state = payload.review_state
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(binding)
    return binding


@router.get(
    "/endpoints/{endpoint_id}/resource-bindings",
    response_model=list[EndpointResourceBindingRead],
)
def list_endpoint_resource_bindings(
    endpoint_id: int,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0, le=MAX_PAGE_OFFSET),
    db: Session = Depends(get_db),
) -> list[EndpointResourceBinding]:
    get_endpoint_or_404(db, endpoint_id)
    return list(db.scalars(
        select(EndpointResourceBinding)
        .where(EndpointResourceBinding.endpoint_id == endpoint_id)
        .order_by(EndpointResourceBinding.id)
        .offset(offset)
        .limit(limit)
    ).all())


@router.get(
    "/endpoints/{endpoint_id}/resource-bindings/{binding_id}",
    response_model=EndpointResourceBindingRead,
)
def get_endpoint_resource_binding(
    endpoint_id: int,
    binding_id: int,
    db: Session = Depends(get_db),
) -> EndpointResourceBinding:
    get_endpoint_or_404(db, endpoint_id)
    binding = db.scalar(select(EndpointResourceBinding).where(
        EndpointResourceBinding.id == binding_id,
        EndpointResourceBinding.endpoint_id == endpoint_id,
    ))
    if binding is None:
        raise HTTPException(status_code=404, detail="Resource binding not found.")
    return binding
