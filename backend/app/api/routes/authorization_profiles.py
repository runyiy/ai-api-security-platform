from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.session import get_db
from app.schemas.authorization_profile import (
    AuthorizationProfileCreate,
    AuthorizationProfileRead,
    AuthorizationProfileUpdate,
)


router = APIRouter(
    prefix="/authorization-profiles",
    tags=["authorization-profiles"],
)


WRITABLE_PROFILE_FIELDS = tuple(
    AuthorizationProfileCreate.model_fields
)


def get_profile_or_404(
    *,
    db: Session,
    profile_id: int,
) -> AuthorizationProfile:
    profile = db.get(
        AuthorizationProfile,
        profile_id,
    )

    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AuthorizationProfile not found.",
        )

    return profile


@router.post(
    "",
    response_model=AuthorizationProfileRead,
    status_code=status.HTTP_201_CREATED,
)
def create_authorization_profile(
    payload: AuthorizationProfileCreate,
    db: Session = Depends(get_db),
) -> AuthorizationProfile:
    profile = AuthorizationProfile(
        **payload.model_dump()
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    return profile


@router.get(
    "",
    response_model=list[AuthorizationProfileRead],
)
def list_authorization_profiles(
    db: Session = Depends(get_db),
) -> list[AuthorizationProfile]:
    return list(
        db.scalars(
            select(AuthorizationProfile).order_by(
                AuthorizationProfile.id
            )
        ).all()
    )


@router.get(
    "/{profile_id}",
    response_model=AuthorizationProfileRead,
)
def get_authorization_profile(
    profile_id: int,
    db: Session = Depends(get_db),
) -> AuthorizationProfile:
    return get_profile_or_404(
        db=db,
        profile_id=profile_id,
    )


@router.patch(
    "/{profile_id}",
    response_model=AuthorizationProfileRead,
)
def update_authorization_profile(
    profile_id: int,
    payload: AuthorizationProfileUpdate,
    db: Session = Depends(get_db),
) -> AuthorizationProfile:
    profile = get_profile_or_404(
        db=db,
        profile_id=profile_id,
    )
    supplied_values = payload.model_dump(
        exclude_unset=True
    )
    merged_values = {
        field_name: getattr(profile, field_name)
        for field_name in WRITABLE_PROFILE_FIELDS
    }
    merged_values.update(supplied_values)

    try:
        validated_state = (
            AuthorizationProfileCreate.model_validate(
                merged_values
            )
        )
    except ValidationError as exc:
        raise RequestValidationError(
            exc.errors()
        ) from exc

    normalized_values = validated_state.model_dump()

    for field_name in supplied_values:
        setattr(
            profile,
            field_name,
            normalized_values[field_name],
        )

    db.commit()
    db.refresh(profile)

    return profile


@router.delete(
    "/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_authorization_profile(
    profile_id: int,
    db: Session = Depends(get_db),
) -> Response:
    profile = get_profile_or_404(
        db=db,
        profile_id=profile_id,
    )
    db.delete(profile)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "AuthorizationProfile is referenced "
                "by one or more Targets."
            ),
        ) from exc

    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )
