from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.asset_candidate_dns_validation import (
    AssetCandidateDNSValidation,
)
from app.db.models.asset_candidate_evaluation import AssetCandidateEvaluation
from app.db.models.asset_enrollment_decision import AssetEnrollmentDecision
from app.db.models.authorization_revision import AuthorizationRevision


class AssetEnrollmentDecisionNotFoundError(Exception):
    pass


class AssetEnrollmentDecisionProvenanceError(Exception):
    pass


def load_exact_dns_validation(
    db: Session,
    *,
    profile_id: int,
    revision_id: int,
    evaluation_id: int,
    validation_id: int,
) -> tuple[AssetCandidateEvaluation, AssetCandidateDNSValidation]:
    revision = db.scalar(select(AuthorizationRevision.id).where(
        AuthorizationRevision.id == revision_id,
        AuthorizationRevision.authorization_profile_id == profile_id,
    ))
    evaluation = db.scalar(select(AssetCandidateEvaluation).where(
        AssetCandidateEvaluation.id == evaluation_id,
        AssetCandidateEvaluation.authorization_revision_id == revision_id,
    ))
    validation = db.scalar(select(AssetCandidateDNSValidation).where(
        AssetCandidateDNSValidation.id == validation_id,
        AssetCandidateDNSValidation.asset_candidate_evaluation_id == evaluation_id,
        AssetCandidateDNSValidation.authorization_revision_id == revision_id,
    ))
    if revision is None or evaluation is None or validation is None:
        raise AssetEnrollmentDecisionNotFoundError
    if validation.normalized_hostname != evaluation.normalized_hostname:
        raise AssetEnrollmentDecisionProvenanceError
    return evaluation, validation


def create_asset_enrollment_decision(
    db: Session,
    *,
    profile_id: int,
    revision_id: int,
    evaluation_id: int,
    validation_id: int,
    decision: str,
    reason_code: str | None,
    note: str | None,
) -> AssetEnrollmentDecision:
    _, validation = load_exact_dns_validation(
        db,
        profile_id=profile_id,
        revision_id=revision_id,
        evaluation_id=evaluation_id,
        validation_id=validation_id,
    )
    enrollment = AssetEnrollmentDecision(
        asset_candidate_dns_validation_id=validation_id,
        authorization_revision_id=revision_id,
        decision=decision,
        normalized_hostname=validation.normalized_hostname,
        reason_code=reason_code,
        note=note,
    )
    db.add(enrollment)
    db.commit()
    db.refresh(enrollment)
    return enrollment
