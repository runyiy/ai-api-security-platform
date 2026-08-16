from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.credentials.bearer import (
    BearerCredentialError,
    BearerCredentialService,
    BearerIdentityNotFoundError,
    BearerIdentityTypeError,
)
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

    identity = TestIdentity(
        target_id=payload.target_id,
        name=payload.name,
        role=payload.role,
        auth_type=payload.auth_type,
        credentials=None,
        is_active=True,
    )

    db.add(identity)
    try:
        db.flush()
        if payload.auth_type == "bearer":
            assert payload.access_token is not None
            BearerCredentialService(db=db).provision(
                identity=identity,
                token=payload.access_token,
            )
        db.commit()
    except BearerCredentialError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
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
            == "uq_test_identity_target_name"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Test identity with this name "
                    "already exists for the target."
                ),
            ) from exc
        raise
    except Exception:
        db.rollback()
        raise
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
    try:
        identity = BearerCredentialService(db=db).update(
            identity_id=identity_id,
            token=payload.access_token,
        )
        db.commit()
    except BearerIdentityNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except (BearerIdentityTypeError, BearerCredentialError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(identity)

    return identity
