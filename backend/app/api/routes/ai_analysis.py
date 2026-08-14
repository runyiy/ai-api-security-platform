from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.mock_provider import (
    MockAIProvider,
)
from app.db.models.finding_ai_analysis import (
    FindingAIAnalysis,
)
from app.db.session import get_db
from app.schemas.ai_analysis import (
    FindingAIAnalysisRead,
)
from app.services.ai_analysis import (
    AIAnalysisNotFoundError,
    AIAnalysisService,
    AIAnalysisServiceError,
)


router = APIRouter(
    tags=["ai-analysis"],
)


ai_provider = MockAIProvider()


@router.post(
    "/findings/{finding_id}/ai-analysis",
    response_model=FindingAIAnalysisRead,
)
def analyze_finding_with_ai(
    finding_id: int,
    db: Session = Depends(get_db),
) -> FindingAIAnalysis:
    service = AIAnalysisService(
        db=db,
        provider=ai_provider,
    )

    try:
        return service.analyze_finding(
            finding_id=finding_id
        )

    except AIAnalysisNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except AIAnalysisServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get(
    "/findings/{finding_id}/ai-analyses",
    response_model=list[
        FindingAIAnalysisRead
    ],
)
def list_ai_analyses(
    finding_id: int,
    db: Session = Depends(get_db),
) -> list[FindingAIAnalysis]:
    return list(
        db.scalars(
            select(FindingAIAnalysis)
            .where(
                FindingAIAnalysis.finding_id
                == finding_id
            )
            .order_by(
                FindingAIAnalysis.id.desc()
            )
        ).all()
    )
