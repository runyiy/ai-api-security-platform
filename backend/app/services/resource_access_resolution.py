from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models.resource import Resource
from app.db.models.resource_access_assertion import ResourceAccessAssertion
from app.db.models.test_identity import TestIdentity


MAX_ASSERTIONS_SCANNED = 256


class ResourceAccessResolutionError(RuntimeError):
    def __init__(self, code: str, status_code: int):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ResourceAccessResolution:
    resource_id: int
    test_identity_id: int
    evaluation_time: datetime
    state: Literal["resolved", "insufficient", "conflict"]
    relationship: str
    expected_access: str
    supporting_assertion_ids: tuple[int, ...]


def resolve_resource_access(
    db: Session,
    resource_id: int,
    test_identity_id: int,
    evaluation_time: datetime,
) -> ResourceAccessResolution:
    if evaluation_time.utcoffset() is None:
        raise ResourceAccessResolutionError(
            "evaluation_time_timezone_required", 422
        )
    resource = db.get(Resource, resource_id)
    if resource is None:
        raise ResourceAccessResolutionError("resource_not_found", 404)
    identity = db.get(TestIdentity, test_identity_id)
    if identity is None:
        raise ResourceAccessResolutionError("test_identity_not_found", 404)
    if resource.target_id != identity.target_id:
        raise ResourceAccessResolutionError(
            "resource_identity_target_mismatch", 409
        )

    assertions = list(db.scalars(
        select(ResourceAccessAssertion)
        .where(
            ResourceAccessAssertion.resource_id == resource.id,
            ResourceAccessAssertion.test_identity_id == identity.id,
            ResourceAccessAssertion.verification_state == "verified",
            ResourceAccessAssertion.asserted_at <= evaluation_time,
            or_(
                ResourceAccessAssertion.valid_from.is_(None),
                ResourceAccessAssertion.valid_from <= evaluation_time,
            ),
            or_(
                ResourceAccessAssertion.valid_until.is_(None),
                ResourceAccessAssertion.valid_until > evaluation_time,
            ),
        )
        .order_by(ResourceAccessAssertion.id)
        .limit(MAX_ASSERTIONS_SCANNED + 1)
    ))
    if len(assertions) > MAX_ASSERTIONS_SCANNED:
        raise ResourceAccessResolutionError(
            "resource_access_resolution_limit_exceeded", 409
        )

    relationship_values = {
        assertion.relationship for assertion in assertions
        if assertion.relationship != "unspecified"
    }
    access_values = {
        assertion.expected_access for assertion in assertions
        if assertion.expected_access != "unspecified"
    }
    relationship = (
        next(iter(relationship_values))
        if len(relationship_values) == 1 else "unspecified"
    )
    expected_access = (
        next(iter(access_values))
        if len(access_values) == 1 else "unspecified"
    )
    has_conflict = len(relationship_values) > 1 or len(access_values) > 1
    if has_conflict:
        state = "conflict"
    elif relationship == "unspecified" and expected_access == "unspecified":
        state = "insufficient"
    else:
        state = "resolved"
    supporting_ids = tuple(
        assertion.id for assertion in assertions
        if assertion.relationship != "unspecified"
        or assertion.expected_access != "unspecified"
    )
    return ResourceAccessResolution(
        resource_id=resource.id,
        test_identity_id=identity.id,
        evaluation_time=evaluation_time,
        state=state,
        relationship=relationship,
        expected_access=expected_access,
        supporting_assertion_ids=supporting_ids,
    )
