from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.target import Target
from app.db.models.test_identity import (
    TestIdentity,
)
from app.db.session import get_db
from app.schemas.test_identity import (
    BearerTokenUpdate,
    TestIdentityCreate,
    TestIdentityRead,
)


router = APIRouter(
    tags=["test-identities"],
)


@router.post(
    "/test-identities",
    response_model=TestIdentityRead,
    status_code=status.HTTP_201_CREATED,
)
def create_test_identity(
    payload: TestIdentityCreate,
    db: Session = Depends(get_db),
) -> TestIdentity:
    target = db.get(
        Target,
        payload.target_id,
    )

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found.",
        )

    existing = db.scalar(
        select(TestIdentity).where(
            TestIdentity.target_id
            == payload.target_id,
            TestIdentity.name
            == payload.name,
        )
    )

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Test identity with this name "
                "already exists for the target."
            ),
        )

    credentials = None

    if payload.auth_type == "bearer":
        assert (
            payload.access_token
            is not None
        )

        credentials = {
            "access_token": (
                payload.access_token
                .get_secret_value()
            )
        }

    identity = TestIdentity(
        target_id=payload.target_id,
        name=payload.name,
        role=payload.role,
        auth_type=payload.auth_type,
        credentials=credentials,
        is_active=True,
    )

    db.add(identity)
    db.commit()
    db.refresh(identity)

    return identity


@router.get(
    "/targets/{target_id}/test-identities",
    response_model=list[TestIdentityRead],
)
def list_test_identities(
    target_id: int,
    db: Session = Depends(get_db),
) -> list[TestIdentity]:
    target = db.get(
        Target,
        target_id,
    )

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found.",
        )

    identities = list(
        db.scalars(
            select(TestIdentity)
            .where(
                TestIdentity.target_id
                == target_id
            )
            .order_by(
                TestIdentity.id
            )
        ).all()
    )

    return identities


@router.put(
    "/test-identities/{identity_id}/token",
    response_model=TestIdentityRead,
)
def update_bearer_token(
    identity_id: int,
    payload: BearerTokenUpdate,
    db: Session = Depends(get_db),
) -> TestIdentity:
    identity = db.get(
        TestIdentity,
        identity_id,
    )

    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test identity not found.",
        )

    if identity.auth_type != "bearer":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Only bearer identities "
                "can receive a bearer token."
            ),
        )

    identity.credentials = {
        "access_token": (
            payload.access_token
            .get_secret_value()
        )
    }

    db.commit()
    db.refresh(identity)

    return identity