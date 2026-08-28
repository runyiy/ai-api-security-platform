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
from app.executors.runtime import platform_rate_limiter
from app.network_safety.runtime import network_gateway
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
from app.services.plan_execution import (
    PlanExecutionError,
    PlanExecutionNotFoundError,
    PlanExecutionService,
)
from app.schemas.execution_plan_cancellation import ExecutionPlanCancellationRead
from app.services.execution_plan_cancellation import (
    ExecutionPlanAlreadyCompletedError,
    ExecutionPlanCancellationCoordinationError,
    ExecutionPlanCancellationInDoubtError,
    ExecutionPlanCancellationNotFoundError,
    ExecutionPlanCancellationService,
)


router = APIRouter(
    tags=["test-runs"],
)


policy_engine = ScopePolicyEngine(
    platform_allowed_hosts=(
        settings.allowed_target_host_set
    )
)


executor = PolicyEnforcedHTTPExecutor(
    policy_engine=policy_engine,
    rate_limiter=platform_rate_limiter,
    network_gateway=network_gateway,
)


@router.post(
    "/execution-plans/{execution_plan_id}/cancel",
    response_model=ExecutionPlanCancellationRead,
)
def cancel_execution_plan(
    execution_plan_id: int,
    db: Session = Depends(get_db),
):
    try:
        return ExecutionPlanCancellationService(
            bind=db.get_bind()
        ).request_cancel(execution_plan_id)
    except ExecutionPlanCancellationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "reason": "ExecutionPlan not found."},
        ) from exc
    except (ExecutionPlanAlreadyCompletedError, ExecutionPlanCancellationInDoubtError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc
    except ExecutionPlanCancellationCoordinationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": exc.code,
                "reason": "ExecutionPlan cancellation coordination failed.",
            },
        ) from exc


@router.post(
    "/execution-plans/{execution_plan_id}/execute",
    response_model=TestRunRead,
)
def execute_execution_plan(
    execution_plan_id: int,
    db: Session = Depends(get_db),
) -> TestRun:
    try:
        return PlanExecutionService(db=db, executor=executor).execute(
            execution_plan_id=execution_plan_id
        )
    except ExecutionBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code, "reason": exc.reason},
        ) from exc
    except PlanExecutionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except PlanExecutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


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
