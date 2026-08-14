from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.target import Target
from app.db.session import get_db
from app.schemas.target import (
    TargetAuthorizationProfileUpdate,
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
    target = db.get(Target, target_id)

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found.",
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
