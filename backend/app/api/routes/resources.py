from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.resource import Resource
from app.db.models.target import Target
from app.db.models.test_identity import (
    TestIdentity,
)
from app.db.session import get_db
from app.schemas.resource import (
    ResourceCreate,
    ResourceRead,
)


router = APIRouter(
    tags=["resources"],
)


@router.post(
    "/resources",
    response_model=ResourceRead,
    status_code=status.HTTP_201_CREATED,
)
def create_resource(
    payload: ResourceCreate,
    db: Session = Depends(get_db),
) -> Resource:
    target = db.get(
        Target,
        payload.target_id,
    )

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found.",
        )

    owner = db.get(
        TestIdentity,
        payload.owner_identity_id,
    )

    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Owner identity not found.",
        )

    if owner.target_id != target.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Owner identity belongs to "
                "a different target."
            ),
        )

    existing = db.scalar(
        select(Resource).where(
            Resource.target_id
            == payload.target_id,
            Resource.resource_type
            == payload.resource_type,
            Resource.external_id
            == payload.external_id,
        )
    )

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Resource already exists "
                "for this target."
            ),
        )

    resource = Resource(
        target_id=payload.target_id,
        resource_type=payload.resource_type,
        external_id=payload.external_id,
        owner_identity_id=(
            payload.owner_identity_id
        ),
    )

    db.add(resource)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        constraint_name = getattr(
            getattr(exc.orig, "diag", None),
            "constraint_name",
            None,
        )
        if (
            getattr(exc.orig, "sqlstate", None)
            == "23505"
            and constraint_name
            == "uq_resource_target_type_external_id"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Resource already exists "
                    "for this target."
                ),
            ) from exc
        raise
    db.refresh(resource)

    return resource


@router.get(
    "/targets/{target_id}/resources",
    response_model=list[ResourceRead],
)
def list_resources(
    target_id: int,
    db: Session = Depends(get_db),
) -> list[Resource]:
    target = db.get(
        Target,
        target_id,
    )

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found.",
        )

    resources = list(
        db.scalars(
            select(Resource)
            .where(
                Resource.target_id
                == target_id
            )
            .order_by(
                Resource.resource_type,
                Resource.external_id,
            )
        ).all()
    )

    return resources
