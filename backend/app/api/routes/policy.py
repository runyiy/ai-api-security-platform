from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.db.session import get_db
from app.policies.scope_policy import (
    ScopePolicyEngine,
)
from app.schemas.policy import (
    PolicyCheckRequest,
    PolicyCheckResponse,
)


router = APIRouter(
    prefix="/policy",
    tags=["policy"],
)


policy_engine = ScopePolicyEngine(
    platform_allowed_hosts=(
        settings.allowed_target_host_set
    )
)


@router.post(
    "/check",
    response_model=PolicyCheckResponse,
)
def check_policy(
    payload: PolicyCheckRequest,
    db: Session = Depends(get_db),
) -> PolicyCheckResponse:
    target = db.get(
        Target,
        payload.target_id,
    )

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found.",
        )

    authorization_revision = None

    if target.authorization_revision_id is not None:
        authorization_revision = db.get(
            AuthorizationRevision,
            target.authorization_revision_id,
        )

    scopes = list(
        db.scalars(
            select(Scope).where(
                Scope.target_id
                == payload.target_id,
                Scope.is_active.is_(True),
            )
        ).all()
    )

    decision = policy_engine.evaluate(
        target=target,
        authorization_revision=authorization_revision,
        scopes=scopes,
        request_url=payload.url,
        method=payload.method,
    )

    return PolicyCheckResponse(
        allowed=decision.allowed,
        code=decision.code,
        reason=decision.reason,
        matched_scope_id=(
            decision.matched_scope_id
        ),
        authorization_profile_id=(
            decision.authorization_profile_id
        ),
        authorization_revision_id=(
            decision.authorization_revision_id
        ),
        evaluated_at=decision.evaluated_at,
    )
