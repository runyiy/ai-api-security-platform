from dataclasses import dataclass
import re

from app.db.models.endpoint import Endpoint
from app.db.models.resource import Resource
from app.db.models.test_identity import TestIdentity
from app.domain.ownership import (
    OwnershipRelation,
    determine_ownership_relation,
)


RESOURCE_ID_PARAMETER_PATTERN = re.compile(
    r"\{([A-Za-z][A-Za-z0-9_]*)_id\}"
)


SUPPORTED_BOLA_METHODS = frozenset(
    {
        "GET",
        "PATCH",
        "DELETE",
    }
)

@dataclass(frozen=True)
class EndpointResourceBinding:
    parameter_name: str
    resource_type: str


def detect_resource_binding(
    endpoint: Endpoint,
) -> EndpointResourceBinding | None:
    matches = list(
        RESOURCE_ID_PARAMETER_PATTERN.finditer(
            endpoint.path
        )
    )

    if len(matches) != 1:
        return None

    match = matches[0]

    parameter_base = match.group(1)

    return EndpointResourceBinding(
        parameter_name=f"{parameter_base}_id",
        resource_type=parameter_base.lower(),
    )


@dataclass(frozen=True)
class GeneratedTestCase:
    endpoint_id: int
    actor_identity_id: int
    resource_id: int

    test_type: str
    ownership_relation: str

    expected_statuses: tuple[int, ...]

OWNER_BASELINE = "owner_baseline"
BOLA_CROSS_OWNER = "bola_cross_owner"
ANONYMOUS_ACCESS = "anonymous_access"

OWNER_EXPECTED_STATUSES_BY_METHOD = {
    "GET": (
        200,
    ),
    "PATCH": (
        200,
        204,
    ),
    "DELETE": (
        200,
        204,
    ),
}


CROSS_OWNER_EXPECTED_STATUSES = (
    403,
    404,
)


ANONYMOUS_EXPECTED_STATUSES = (
    401,
    403,
    404,
)

def generate_bola_test_cases(
    *,
    endpoints: list[Endpoint],
    actors: list[TestIdentity],
    resources: list[Resource],
) -> list[GeneratedTestCase]:
    generated: list[GeneratedTestCase] = []

    for endpoint in endpoints:
        if (
            endpoint.method
            not in SUPPORTED_BOLA_METHODS
        ):
            continue

        binding = detect_resource_binding(
            endpoint
        )

        if binding is None:
            continue

        matching_resources = [
            resource
            for resource in resources
            if (
                resource.target_id
                == endpoint.target_id
                and resource.resource_type
                == binding.resource_type
            )
        ]

        if not matching_resources:
            continue

        matching_actors = [
            actor
            for actor in actors
            if (
                actor.target_id
                == endpoint.target_id
                and actor.is_active
            )
        ]

        for actor in matching_actors:
            for resource in matching_resources:
                relation = (
                    determine_ownership_relation(
                        actor=actor,
                        resource=resource,
                    )
                )

                if (
                    relation
                    == OwnershipRelation.OWNER
                ):
                    generated.append(
                        GeneratedTestCase(
                            endpoint_id=endpoint.id,
                            actor_identity_id=actor.id,
                            resource_id=resource.id,
                            test_type=OWNER_BASELINE,
                            ownership_relation=(
                                relation.value
                            ),
                            expected_statuses=(
                                OWNER_EXPECTED_STATUSES_BY_METHOD[
                                    endpoint.method
                                ]
                            ),
                        )
                    )

                    continue

                if (
                    relation
                    == OwnershipRelation.CROSS_OWNER
                ):
                    generated.append(
                        GeneratedTestCase(
                            endpoint_id=endpoint.id,
                            actor_identity_id=actor.id,
                            resource_id=resource.id,
                            test_type=(
                                BOLA_CROSS_OWNER
                            ),
                            ownership_relation=(
                                relation.value
                            ),
                            expected_statuses=(
                                CROSS_OWNER_EXPECTED_STATUSES
                            ),
                        )
                    )

                    continue

                if (
                    relation
                    == OwnershipRelation.ANONYMOUS
                ):
                    generated.append(
                        GeneratedTestCase(
                            endpoint_id=endpoint.id,
                            actor_identity_id=actor.id,
                            resource_id=resource.id,
                            test_type=(
                                ANONYMOUS_ACCESS
                            ),
                            ownership_relation=(
                                relation.value
                            ),
                            expected_statuses=(
                                ANONYMOUS_EXPECTED_STATUSES
                            ),
                        )
                    )

    return generated



