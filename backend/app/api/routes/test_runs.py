from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.test_run import TestRun
from app.db.session import get_db
from app.executors.http import (
    ExecutionBlockedError,
    PolicyEnforcedHTTPExecutor,
)
from app.executors.rate_limit import (
    InMemoryRateLimiter,
)
from app.policies.scope_policy import (
    ScopePolicyEngine,
)
from app.schemas.test_run import (
    TestRunRead,
)
from app.services.test_execution import (
    TestExecutionError,
    TestExecutionNotFoundError,
    TestExecutionService,
)


router = APIRouter(
    tags=["test-runs"],
)


policy_engine = ScopePolicyEngine(
    platform_allowed_hosts=(
        settings.allowed_target_host_set
    )
)


rate_limiter = InMemoryRateLimiter(
    requests_per_second=2.0
)


executor = PolicyEnforcedHTTPExecutor(
    policy_engine=policy_engine,
    rate_limiter=rate_limiter,
)


@router.post(
    "/test-cases/{test_case_id}/execute",
    response_model=TestRunRead,
)
def execute_test_case(
    test_case_id: int,
    db: Session = Depends(get_db),
) -> TestRun:
    service = TestExecutionService(
        db=db,
        executor=executor,
    )

    try:
        return service.execute(
            test_case_id=test_case_id
        )

    except ExecutionBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": exc.code,
                "reason": exc.reason,
            },
        ) from exc

    except TestExecutionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except TestExecutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get(
    "/test-cases/{test_case_id}/runs",
    response_model=list[TestRunRead],
)
def list_test_runs(
    test_case_id: int,
    db: Session = Depends(get_db),
) -> list[TestRun]:
    return list(
        db.scalars(
            select(TestRun)
            .where(
                TestRun.test_case_id
                == test_case_id
            )
            .order_by(
                TestRun.id.desc()
            )
        ).all()
    )
