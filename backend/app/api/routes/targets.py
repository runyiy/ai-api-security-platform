from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.target import Target
from app.db.session import get_db
from app.schemas.target import (
    TargetAuthorizationProfileUpdate,
    TargetAuthorizationRevisionUpdate,
    TargetCreate,
    TargetRead,
)


router = APIRouter(
    prefix="/targets",
    tags=["targets"],
)


@router.post(
    "",
    response_model=TargetRead,
    status_code=status.HTTP_201_CREATED,
)
def create_target(
    payload: TargetCreate,
    db: Session = Depends(get_db),
) -> Target:
    target = Target(
        name=payload.name,
        base_url=str(payload.base_url),
        environment=payload.environment,
    )

    db.add(target)
    db.commit()
    db.refresh(target)

    return target


@router.patch(
    "/{target_id}/authorization-profile",
    response_model=TargetRead,
)
def update_target_authorization_profile(
    target_id: int,
    payload: TargetAuthorizationProfileUpdate,
    db: Session = Depends(get_db),
) -> Target:
    target = db.scalar(
        select(Target).where(Target.id == target_id).with_for_update()
    )

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found.",
        )

    if (
        target.authorization_revision_id is not None
        and payload.authorization_profile_id != target.authorization_profile_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Unbind the AuthorizationRevision before changing "
                "the AuthorizationProfile."
            ),
        )

    if payload.authorization_profile_id is not None:
        profile = db.get(
            AuthorizationProfile,
            payload.authorization_profile_id,
        )

        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="AuthorizationProfile not found.",
            )

    target.authorization_profile_id = payload.authorization_profile_id
    db.commit()
    db.refresh(target)

    return target


@router.patch(
    "/{target_id}/authorization-revision",
    response_model=TargetRead,
)
def update_target_authorization_revision(
    target_id: int,
    payload: TargetAuthorizationRevisionUpdate,
    db: Session = Depends(get_db),
) -> Target:
    target = db.scalar(
        select(Target).where(Target.id == target_id).with_for_update()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Target not found.")

    revision_id = payload.authorization_revision_id
    if revision_id is not None:
        revision = db.get(AuthorizationRevision, revision_id)
        if revision is None:
            raise HTTPException(
                status_code=404,
                detail="AuthorizationRevision not found.",
            )
        if target.authorization_profile_id is None:
            raise HTTPException(
                status_code=409,
                detail="Target has no AuthorizationProfile.",
            )
        if revision.authorization_profile_id != target.authorization_profile_id:
            raise HTTPException(
                status_code=409,
                detail="Revision belongs to another profile.",
            )
        if revision.lifecycle_state != "active":
            raise HTTPException(
                status_code=409,
                detail="Only an active revision may be bound.",
            )

    target.authorization_revision_id = revision_id
    db.commit()
    db.refresh(target)
    return target
