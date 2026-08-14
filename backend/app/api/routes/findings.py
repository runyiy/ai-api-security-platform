from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.finding import Finding
from app.db.models.target import Target
from app.db.session import get_db
from app.schemas.finding import (
    AnalyzeTestRunResponse,
    FindingRead,
    FindingReviewRequest,
)
from app.services.finding_analysis import (
    FindingAnalysisError,
    FindingAnalysisService,
)


router = APIRouter(
    tags=["findings"],
)


@router.post(
    "/test-runs/{test_run_id}/analyze",
    response_model=AnalyzeTestRunResponse,
)
def analyze_test_run(
    test_run_id: int,
    db: Session = Depends(get_db),
) -> AnalyzeTestRunResponse:
    service = FindingAnalysisService(
        db=db
    )

    try:
        outcome = service.analyze_test_run(
            test_run_id=test_run_id
        )

    except FindingAnalysisError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return AnalyzeTestRunResponse(
        outcome=outcome.analysis.outcome,
        reason=outcome.analysis.reason,
        confidence=(
            outcome.analysis.confidence
        ),
        severity=(
            outcome.analysis.severity
        ),
        finding=outcome.finding,
    )


@router.get(
    "/targets/{target_id}/findings",
    response_model=list[FindingRead],
)
def list_findings(
    target_id: int,
    db: Session = Depends(get_db),
) -> list[Finding]:
    target = db.get(
        Target,
        target_id,
    )

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found.",
        )

    return list(
        db.scalars(
            select(Finding)
            .where(
                Finding.target_id
                == target_id
            )
            .order_by(
                Finding.id.desc()
            )
        ).all()
    )


@router.patch(
    "/findings/{finding_id}/review",
    response_model=FindingRead,
)
def review_finding(
    finding_id: int,
    payload: FindingReviewRequest,
    db: Session = Depends(get_db),
) -> Finding:
    finding = db.get(
        Finding,
        finding_id,
    )

    if finding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Finding not found.",
        )

    allowed_transitions = {
        "potential": {
            "reviewing",
            "confirmed",
            "false_positive",
        },
        "reviewing": {
            "confirmed",
            "false_positive",
        },
    }

    allowed_next = allowed_transitions.get(
        finding.status,
        set(),
    )

    if payload.status not in allowed_next:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot change finding from "
                f"{finding.status!r} to "
                f"{payload.status!r}."
            ),
        )

    finding.status = payload.status
    finding.review_notes = (
        payload.review_notes
    )

    db.commit()
    db.refresh(finding)

    return finding