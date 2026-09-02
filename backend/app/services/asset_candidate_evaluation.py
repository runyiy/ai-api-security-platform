from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.asset_candidate_evaluation import AssetCandidateEvaluation
from app.db.models.asset_hostname_rule import AssetHostnameRule
from app.db.models.authorization_revision import AuthorizationRevision
from app.services.asset_hostname_rule import match_asset_candidate
from app.services.authorization_revision import lock_profile


class AssetCandidateEvaluationError(Exception):
    pass


class AssetCandidateEvaluationNotFoundError(AssetCandidateEvaluationError):
    pass


class AssetCandidateEvaluationInactiveError(AssetCandidateEvaluationError):
    pass


class AssetCandidateEvaluationInvalidError(AssetCandidateEvaluationError):
    pass


def create_asset_candidate_evaluation(
    db: Session, *, profile_id: int, revision_id: int, hostname: str
) -> AssetCandidateEvaluation:
    # Match lifecycle transition lock order so active-state checking and event
    # persistence serialize with activate/revoke/supersede.
    if lock_profile(db, profile_id) is None:
        raise AssetCandidateEvaluationNotFoundError
    revision = db.scalar(
        select(AuthorizationRevision)
        .where(
            AuthorizationRevision.id == revision_id,
            AuthorizationRevision.authorization_profile_id == profile_id,
        )
        .with_for_update()
    )
    if revision is None:
        raise AssetCandidateEvaluationNotFoundError
    if revision.lifecycle_state != "active":
        raise AssetCandidateEvaluationInactiveError

    rules = list(db.scalars(
        select(AssetHostnameRule)
        .where(AssetHostnameRule.authorization_revision_id == revision_id)
        .order_by(AssetHostnameRule.id)
    ).all())
    decision = match_asset_candidate(
        authorization_revision_id=revision_id,
        candidate_hostname=hostname,
        rules=rules,
    )
    if decision.code == "asset_candidate_invalid":
        raise AssetCandidateEvaluationInvalidError
    rule_ids = {rule.id for rule in rules}
    if (
        decision.matched_include_rule_id is not None
        and decision.matched_include_rule_id not in rule_ids
    ) or (
        decision.matched_exclude_rule_id is not None
        and decision.matched_exclude_rule_id not in rule_ids
    ):
        raise AssetCandidateEvaluationInvalidError

    evaluation = AssetCandidateEvaluation(
        authorization_revision_id=revision_id,
        normalized_hostname=decision.normalized_hostname,
        decision_code=decision.code,
        matched_include_rule_id=decision.matched_include_rule_id,
        matched_exclude_rule_id=decision.matched_exclude_rule_id,
        source_type="operator_supplied",
    )
    db.add(evaluation)
    db.commit()
    db.refresh(evaluation)
    return evaluation
