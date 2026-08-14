from app.db.models.endpoint import Endpoint
from app.db.models.resource import Resource
from app.db.models.test_identity import (
    TestIdentity,
)
from app.generators.bola import (
    ANONYMOUS_ACCESS,
    BOLA_CROSS_OWNER,
    OWNER_BASELINE,
    detect_resource_binding,
    generate_bola_test_cases,
)


def build_endpoint() -> Endpoint:
    return Endpoint(
        id=10,
        target_id=1,
        path="/api/projects/{project_id}",
        method="GET",
        operation_id="get_project",
        requires_auth=True,
        parameters=[],
        request_body=None,
        security=None,
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


def build_resource_a() -> Resource:
    return Resource(
        id=100,
        target_id=1,
        resource_type="project",
        external_id="1001",
        owner_identity_id=1,
    )


def build_resource_b() -> Resource:
    return Resource(
        id=200,
        target_id=1,
        resource_type="project",
        external_id="2001",
        owner_identity_id=2,
    )


def test_detects_project_binding() -> None:
    binding = detect_resource_binding(
        build_endpoint()
    )

    assert binding is not None

    assert (
        binding.parameter_name
        == "project_id"
    )

    assert (
        binding.resource_type
        == "project"
    )


def test_generates_expected_matrix() -> None:
    generated = (
        generate_bola_test_cases(
            endpoints=[
                build_endpoint(),
            ],
            actors=[
                build_user_a(),
                build_user_b(),
                build_anonymous(),
            ],
            resources=[
                build_resource_a(),
                build_resource_b(),
            ],
        )
    )

    assert len(generated) == 6


def test_generates_owner_baselines() -> None:
    generated = generate_bola_test_cases(
        endpoints=[
            build_endpoint(),
        ],
        actors=[
            build_user_a(),
            build_user_b(),
        ],
        resources=[
            build_resource_a(),
            build_resource_b(),
        ],
    )

    owner_cases = [
        case
        for case in generated
        if case.test_type
        == OWNER_BASELINE
    ]

    assert len(owner_cases) == 2


def test_generates_cross_owner_cases() -> None:
    generated = generate_bola_test_cases(
        endpoints=[
            build_endpoint(),
        ],
        actors=[
            build_user_a(),
            build_user_b(),
        ],
        resources=[
            build_resource_a(),
            build_resource_b(),
        ],
    )

    cross_owner_cases = [
        case
        for case in generated
        if case.test_type
        == BOLA_CROSS_OWNER
    ]

    assert len(cross_owner_cases) == 2

    assert all(
        case.expected_statuses
        == (403, 404)
        for case in cross_owner_cases
    )


def test_generates_anonymous_cases() -> None:
    generated = generate_bola_test_cases(
        endpoints=[
            build_endpoint(),
        ],
        actors=[
            build_anonymous(),
        ],
        resources=[
            build_resource_a(),
            build_resource_b(),
        ],
    )

    assert len(generated) == 2

    assert all(
        case.test_type
        == ANONYMOUS_ACCESS
        for case in generated
    )


def test_ignores_wrong_resource_type() -> None:
    invoice = Resource(
        id=300,
        target_id=1,
        resource_type="invoice",
        external_id="inv_001",
        owner_identity_id=1,
    )

    generated = generate_bola_test_cases(
        endpoints=[
            build_endpoint(),
        ],
        actors=[
            build_user_a(),
        ],
        resources=[
            invoice,
        ],
    )

    assert generated == []


def test_ignores_unsupported_method() -> None:
    endpoint = build_endpoint()

    endpoint.method = "POST"

    generated = generate_bola_test_cases(
        endpoints=[
            endpoint,
        ],
        actors=[
            build_user_a(),
        ],
        resources=[
            build_resource_a(),
        ],
    )

    assert generated == []