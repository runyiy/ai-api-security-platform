from enum import StrEnum

from app.db.models.resource import Resource
from app.db.models.test_identity import (
    TestIdentity,
)


class OwnershipRelation(StrEnum):
    OWNER = "owner"
    CROSS_OWNER = "cross_owner"
    ANONYMOUS = "anonymous"


class OwnershipError(ValueError):
    pass


def determine_ownership_relation(
    *,
    actor: TestIdentity,
    resource: Resource,
) -> OwnershipRelation:
    if actor.target_id != resource.target_id:
        raise OwnershipError(
            "actor and resource belong "
            "to different targets"
        )

    if actor.auth_type == "anonymous":
        return OwnershipRelation.ANONYMOUS

    if actor.id == resource.owner_identity_id:
        return OwnershipRelation.OWNER

    return OwnershipRelation.CROSS_OWNER