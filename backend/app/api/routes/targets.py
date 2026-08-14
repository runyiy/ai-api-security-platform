from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.models.target import Target
from app.db.session import get_db
from app.schemas.target import TargetCreate, TargetRead


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