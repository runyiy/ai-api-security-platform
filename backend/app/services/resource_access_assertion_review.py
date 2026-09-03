from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.resource import Resource
from app.db.models.resource_access_assertion import ResourceAccessAssertion


class ResourceAccessAssertionReviewError(RuntimeError):
    def __init__(self, code: str, status_code: int):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def review_resource_access_assertion(
    db: Session,
    resource_id: int,
    assertion_id: int,
    decision: str,
    confidence: int,
) -> ResourceAccessAssertion:
    if db.get(Resource, resource_id) is None:
        raise ResourceAccessAssertionReviewError("resource_not_found", 404)
    source = db.scalar(
        select(ResourceAccessAssertion)
        .where(
            ResourceAccessAssertion.id == assertion_id,
            ResourceAccessAssertion.resource_id == resource_id,
        )
        .with_for_update()
    )
    if source is None:
        raise ResourceAccessAssertionReviewError(
            "resource_access_assertion_not_found", 404
        )
    if (
        source.verification_state != "candidate"
        or source.provenance not in {"observed_baseline", "inferred_candidate"}
    ):
        raise ResourceAccessAssertionReviewError(
            "resource_access_assertion_not_reviewable", 409
        )

    state = "verified" if decision == "verify" else "rejected"
    existing = db.scalar(select(ResourceAccessAssertion).where(
        ResourceAccessAssertion.reviewed_assertion_id == source.id
    ))
    if existing is not None:
        return _validate_existing(existing, source, state, confidence)

    created_id = db.scalar(
        insert(ResourceAccessAssertion)
        .values(
            resource_id=source.resource_id,
            test_identity_id=source.test_identity_id,
            relationship=source.relationship,
            expected_access=source.expected_access,
            provenance="human_verified",
            confidence=confidence,
            verification_state=state,
            observed_at=source.observed_at,
            valid_from=source.valid_from,
            valid_until=source.valid_until,
            source_test_run_id=None,
            reviewed_assertion_id=source.id,
        )
        .on_conflict_do_nothing(
            index_elements=[ResourceAccessAssertion.reviewed_assertion_id]
        )
        .returning(ResourceAccessAssertion.id)
    )
    review = (
        db.get(ResourceAccessAssertion, created_id)
        if created_id is not None
        else db.scalar(select(ResourceAccessAssertion).where(
            ResourceAccessAssertion.reviewed_assertion_id == source.id
        ))
    )
    if review is None:
        raise ResourceAccessAssertionReviewError(
            "resource_access_assertion_review_persistence_conflict", 409
        )
    return _validate_existing(review, source, state, confidence)


def _validate_existing(
    review: ResourceAccessAssertion,
    source: ResourceAccessAssertion,
    state: str,
    confidence: int,
) -> ResourceAccessAssertion:
    if not (
        review.resource_id == source.resource_id
        and review.test_identity_id == source.test_identity_id
        and review.relationship == source.relationship
        and review.expected_access == source.expected_access
        and review.provenance == "human_verified"
        and review.verification_state == state
        and review.confidence == confidence
        and review.observed_at == source.observed_at
        and review.valid_from == source.valid_from
        and review.valid_until == source.valid_until
        and review.source_test_run_id is None
        and review.reviewed_assertion_id == source.id
    ):
        raise ResourceAccessAssertionReviewError(
            "resource_access_assertion_already_reviewed", 409
        )
    return review
