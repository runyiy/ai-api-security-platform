from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.security_report import (
    SecurityReport,
)
from app.db.session import get_db
from app.schemas.security_report import (
    SecurityReportMarkdownRead,
    SecurityReportRead,
)
from app.services.security_report import (
    SecurityReportError,
    SecurityReportNotFoundError,
    SecurityReportService,
)


router = APIRouter(
    tags=["security-reports"],
)


@router.post(
    "/findings/{finding_id}/reports",
    response_model=SecurityReportRead,
    status_code=status.HTTP_201_CREATED,
)
def generate_security_report(
    finding_id: int,
    db: Session = Depends(get_db),
) -> SecurityReport:
    service = SecurityReportService(
        db=db
    )

    try:
        return service.generate(
            finding_id=finding_id
        )

    except SecurityReportNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except SecurityReportError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get(
    "/findings/{finding_id}/reports",
    response_model=list[SecurityReportRead],
)
def list_security_reports(
    finding_id: int,
    db: Session = Depends(get_db),
) -> list[SecurityReport]:
    return list(
        db.scalars(
            select(SecurityReport)
            .where(
                SecurityReport.finding_id
                == finding_id
            )
            .order_by(
                SecurityReport.version.desc()
            )
        ).all()
    )


@router.get(
    "/reports/{report_id}",
    response_model=SecurityReportRead,
)
def get_security_report(
    report_id: int,
    db: Session = Depends(get_db),
) -> SecurityReport:
    report = db.get(
        SecurityReport,
        report_id,
    )

    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Security report not found.",
        )

    return report


@router.get(
    "/reports/{report_id}/markdown",
    response_model=SecurityReportMarkdownRead,
)
def get_security_report_markdown(
    report_id: int,
    db: Session = Depends(get_db),
) -> SecurityReportMarkdownRead:
    report = db.get(
        SecurityReport,
        report_id,
    )

    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Security report not found.",
        )

    return SecurityReportMarkdownRead(
        report_id=report.id,
        version=report.version,
        markdown=report.markdown_content,
    )
