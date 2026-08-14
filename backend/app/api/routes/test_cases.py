from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.endpoint import Endpoint
from app.db.models.resource import Resource
from app.db.models.target import Target
from app.db.models.test_case import TestCase
from app.db.models.test_identity import (
    TestIdentity,
)
from app.db.session import get_db
from app.generators.bola import (
    generate_bola_test_cases,
)
from app.schemas.test_case import (
    GenerateBOLATestCasesRequest,
    GenerateBOLATestCasesResponse,
    TestCaseRead,
)


router = APIRouter(
    tags=["test-cases"],
)


@router.post(
    "/test-cases/generate/bola",
    response_model=(
        GenerateBOLATestCasesResponse
    ),
)
def generate_bola_cases(
    payload: GenerateBOLATestCasesRequest,
    db: Session = Depends(get_db),
) -> GenerateBOLATestCasesResponse:
    target = db.get(
        Target,
        payload.target_id,
    )

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found.",
        )

    endpoints = list(
        db.scalars(
            select(Endpoint).where(
                Endpoint.target_id
                == target.id
            )
        ).all()
    )

    actors = list(
        db.scalars(
            select(TestIdentity).where(
                TestIdentity.target_id
                == target.id,
                TestIdentity.is_active.is_(True),
            )
        ).all()
    )

    resources = list(
        db.scalars(
            select(Resource).where(
                Resource.target_id
                == target.id
            )
        ).all()
    )

    generated = (
        generate_bola_test_cases(
            endpoints=endpoints,
            actors=actors,
            resources=resources,
        )
    )

    created = 0
    existing = 0

    for candidate in generated:
        test_case = db.scalar(
            select(TestCase).where(
                TestCase.endpoint_id
                == candidate.endpoint_id,
                TestCase.actor_identity_id
                == candidate.actor_identity_id,
                TestCase.resource_id
                == candidate.resource_id,
                TestCase.test_type
                == candidate.test_type,
            )
        )

        if test_case is not None:
            existing += 1
            continue

        db.add(
            TestCase(
                endpoint_id=(
                    candidate.endpoint_id
                ),
                actor_identity_id=(
                    candidate.actor_identity_id
                ),
                resource_id=(
                    candidate.resource_id
                ),
                test_type=(
                    candidate.test_type
                ),
                ownership_relation=(
                    candidate.ownership_relation
                ),
                expected_statuses=list(
                    candidate.expected_statuses
                ),
                status="pending",
            )
        )

        created += 1

    db.commit()

    return GenerateBOLATestCasesResponse(
        target_id=target.id,
        generated=len(generated),
        created=created,
        existing=existing,
    )


@router.get(
    "/targets/{target_id}/test-cases",
    response_model=list[TestCaseRead],
)
def list_test_cases(
    target_id: int,
    db: Session = Depends(get_db),
) -> list[TestCase]:
    target = db.get(
        Target,
        target_id,
    )

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found.",
        )

    test_cases = list(
        db.scalars(
            select(TestCase)
            .join(
                Endpoint,
                TestCase.endpoint_id
                == Endpoint.id,
            )
            .where(
                Endpoint.target_id
                == target_id
            )
            .order_by(
                TestCase.id
            )
        ).all()
    )

    return test_cases