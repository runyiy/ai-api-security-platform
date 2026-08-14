import pytest

from app.db.models.resource import Resource
from app.db.models.test_identity import (
    TestIdentity,
)
from app.domain.ownership import (
    OwnershipError,
    OwnershipRelation,
    determine_ownership_relation,
)


def build_user_a() -> TestIdentity:
    return TestIdentity(
        id=1,
        target_id=1,
        name="User A",
        role="user",
        auth_type="bearer",
        credentials={
            "access_token": "token-a",
        },
        is_active=True,
    )


def build_user_b() -> TestIdentity:
    return TestIdentity(
        id=2,
        target_id=1,
        name="User B",
        role="user",
        auth_type="bearer",
        credentials={
            "access_token": "token-b",
        },
        is_active=True,
    )


def build_anonymous() -> TestIdentity:
    return TestIdentity(
        id=3,
        target_id=1,
        name="Anonymous",
        role=None,
        auth_type="anonymous",
        credentials=None,
        is_active=True,
    )


def build_user_a_resource() -> Resource:
    return Resource(
        id=10,
        target_id=1,
        resource_type="project",
        external_id="1001",
        owner_identity_id=1,
    )


def test_owner_relation() -> None:
    relation = determine_ownership_relation(
        actor=build_user_a(),
        resource=build_user_a_resource(),
    )

    assert (
        relation
        == OwnershipRelation.OWNER
    )


def test_cross_owner_relation() -> None:
    relation = determine_ownership_relation(
        actor=build_user_b(),
        resource=build_user_a_resource(),
    )

    assert (
        relation
        == OwnershipRelation.CROSS_OWNER
    )


def test_anonymous_relation() -> None:
    relation = determine_ownership_relation(
        actor=build_anonymous(),
        resource=build_user_a_resource(),
    )

    assert (
        relation
        == OwnershipRelation.ANONYMOUS
    )


def test_rejects_cross_target_relation() -> None:
    actor = build_user_a()

    actor.target_id = 2

    with pytest.raises(
        OwnershipError
    ):
        determine_ownership_relation(
            actor=actor,
            resource=build_user_a_resource(),
        )