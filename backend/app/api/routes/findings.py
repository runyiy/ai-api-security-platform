from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.finding import Finding
from app.db.models.finding_evidence_excerpt import FindingEvidenceExcerpt
from app.db.models.finding_evidence_fingerprint import FindingEvidenceFingerprint
from app.db.models.finding_evidence_similarity import FindingEvidenceSimilarity
from app.db.models.finding_evidence_retention_binding import FindingEvidenceRetentionBinding
from app.db.models.finding_evidence_record import FindingEvidenceRecord
from app.db.models.target import Target
from app.db.session import get_db
from app.schemas.finding import (
    AnalyzeTestRunRequest,
    AnalyzeTestRunResponse,
    FindingRead,
    FindingEvidenceRead,
    FindingEvidenceExcerptRead,
    FindingEvidenceFingerprintRead,
    FindingEvidenceSimilarityRead,
    FindingEvidenceRetentionRead,
    FindingReviewRequest,
)
from app.services.finding_analysis import (
    FindingAnalysisError,
    FindingAnalysisNotFoundError,
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
    payload: AnalyzeTestRunRequest,
    db: Session = Depends(get_db),
) -> AnalyzeTestRunResponse:
    service = FindingAnalysisService(
        db=db
    )

    try:
        outcome = service.analyze_test_run(
            test_run_id=test_run_id,
            baseline_test_run_id=payload.baseline_test_run_id,
        )

    except FindingAnalysisNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

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


@router.get("/findings/{finding_id}/evidence", response_model=FindingEvidenceRead)
def read_finding_evidence(
    finding_id: int,
    db: Session = Depends(get_db),
) -> FindingEvidenceRecord:
    if db.get(Finding, finding_id) is None:
        raise HTTPException(status_code=404, detail="Finding not found.")
    evidence = db.scalar(select(FindingEvidenceRecord).where(
        FindingEvidenceRecord.finding_id == finding_id,
    ))
    if evidence is None:
        raise HTTPException(status_code=404, detail="finding_structured_evidence_not_found")
    return evidence


@router.get("/findings/{finding_id}/evidence/excerpts", response_model=FindingEvidenceExcerptRead)
def read_finding_evidence_excerpt(
    finding_id: int,
    db: Session = Depends(get_db),
) -> FindingEvidenceExcerpt:
    evidence = read_finding_evidence(finding_id=finding_id, db=db)
    excerpt = db.scalar(select(FindingEvidenceExcerpt).where(
        FindingEvidenceExcerpt.finding_evidence_record_id == evidence.id,
    ))
    if excerpt is None:
        raise HTTPException(status_code=404, detail="finding_evidence_excerpt_not_found")
    return excerpt


@router.get("/findings/{finding_id}/evidence/fingerprints", response_model=FindingEvidenceFingerprintRead)
def read_finding_evidence_fingerprint(
    finding_id: int,
    db: Session = Depends(get_db),
) -> FindingEvidenceFingerprint:
    evidence = read_finding_evidence(finding_id=finding_id, db=db)
    fingerprint = db.scalar(select(FindingEvidenceFingerprint).where(
        FindingEvidenceFingerprint.finding_evidence_record_id == evidence.id,
    ))
    if fingerprint is None:
        raise HTTPException(status_code=404, detail="finding_evidence_fingerprint_not_found")
    return fingerprint


@router.get("/findings/{finding_id}/evidence/similarity", response_model=FindingEvidenceSimilarityRead)
def read_finding_evidence_similarity(
    finding_id: int,
    db: Session = Depends(get_db),
) -> FindingEvidenceSimilarity:
    fingerprint = read_finding_evidence_fingerprint(finding_id=finding_id, db=db)
    similarity = db.scalar(select(FindingEvidenceSimilarity).where(
        FindingEvidenceSimilarity.finding_evidence_fingerprint_id == fingerprint.id,
    ))
    if similarity is None:
        raise HTTPException(status_code=404, detail="finding_evidence_similarity_not_found")
    return similarity


@router.get("/findings/{finding_id}/evidence/retention", response_model=FindingEvidenceRetentionRead)
def read_finding_evidence_retention(
    finding_id: int,
    db: Session = Depends(get_db),
) -> FindingEvidenceRetentionBinding:
    evidence = read_finding_evidence(finding_id=finding_id, db=db)
    binding = db.scalar(select(FindingEvidenceRetentionBinding).where(
        FindingEvidenceRetentionBinding.finding_evidence_record_id == evidence.id,
    ))
    if binding is None:
        raise HTTPException(status_code=404, detail="finding_evidence_retention_binding_not_found")
    return binding
