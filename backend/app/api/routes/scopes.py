from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from app.db.models.scope import Scope
from app.db.models.target import Target
from app.db.session import get_db
from app.schemas.scope import (
    ScopeCreate,
    ScopeRead,
)


router = APIRouter(
    prefix="/scopes",
    tags=["scopes"],
)


@router.post(
    "",
    response_model=ScopeRead,
    status_code=status.HTTP_201_CREATED,
)
def create_scope(
    payload: ScopeCreate,
    db: Session = Depends(get_db),
) -> Scope:
    target = db.get(
        Target,
        payload.target_id,
    )

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found.",
        )

    scope = Scope(
        target_id=payload.target_id,
        hostname=payload.hostname,
        path_pattern=payload.path_pattern,
        allowed_methods=payload.allowed_methods,
        is_active=payload.is_active,
    )

    db.add(scope)
    db.commit()
    db.refresh(scope)

    return scope