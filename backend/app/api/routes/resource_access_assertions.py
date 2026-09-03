from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.resource import Resource
from app.db.models.resource_access_assertion import ResourceAccessAssertion
from app.db.models.test_identity import TestIdentity
from app.db.session import get_db
from app.schemas.resource_access_assertion import (
    ResourceAccessAssertionCreate,
    ResourceAccessAssertionRead,
)


router = APIRouter(tags=["resource-access-assertions"])


def get_resource_or_404(db: Session, resource_id: int) -> Resource:
    resource = db.get(Resource, resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Resource not found.")
    return resource


@router.post(
    "/resources/{resource_id}/access-assertions",
    response_model=ResourceAccessAssertionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_resource_access_assertion(
    resource_id: int,
    payload: ResourceAccessAssertionCreate,
    db: Session = Depends(get_db),
) -> ResourceAccessAssertion:
    resource = get_resource_or_404(db, resource_id)
    identity = db.get(TestIdentity, payload.test_identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="Test identity not found.")
    if identity.target_id != resource.target_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Test identity belongs to a different target.",
        )
    assertion = ResourceAccessAssertion(
        resource_id=resource.id,
        test_identity_id=identity.id,
        relationship=payload.relationship,
        expected_access=payload.expected_access,
        provenance="human_verified",
        confidence=payload.confidence,
        verification_state="verified",
        observed_at=None,
        valid_from=payload.valid_from,
        valid_until=payload.valid_until,
    )
    db.add(assertion)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(assertion)
    return assertion


@router.get(
    "/resources/{resource_id}/access-assertions",
    response_model=list[ResourceAccessAssertionRead],
)
def list_resource_access_assertions(
    resource_id: int,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[ResourceAccessAssertion]:
    get_resource_or_404(db, resource_id)
    return list(db.scalars(
        select(ResourceAccessAssertion)
        .where(
            ResourceAccessAssertion.resource_id == resource_id,
            ResourceAccessAssertion.id > after_id,
        )
        .order_by(ResourceAccessAssertion.id)
        .limit(limit)
    ))


@router.get(
    "/resources/{resource_id}/access-assertions/{assertion_id}",
    response_model=ResourceAccessAssertionRead,
)
def get_resource_access_assertion(
    resource_id: int,
    assertion_id: int,
    db: Session = Depends(get_db),
) -> ResourceAccessAssertion:
    get_resource_or_404(db, resource_id)
    assertion = db.scalar(select(ResourceAccessAssertion).where(
        ResourceAccessAssertion.id == assertion_id,
        ResourceAccessAssertion.resource_id == resource_id,
    ))
    if assertion is None:
        raise HTTPException(status_code=404, detail="Access assertion not found.")
    return assertion
