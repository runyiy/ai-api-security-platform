import pytest

from app.db.models.endpoint import Endpoint
from app.db.models.resource import Resource
from app.db.models.target import Target
from app.services.test_execution import (
    TestExecutionError,
    build_test_case_url,
)


def build_target() -> Target:
    return Target(
        id=1,
        name="Lab",
        base_url="http://localhost:8001",
        environment="development",
        is_enabled=True,
    )


def test_builds_resource_url() -> None:
    endpoint = Endpoint(
        id=1,
        target_id=1,
        path="/api/projects/{project_id}",
        method="GET",
        operation_id=None,
        requires_auth=True,
        parameters=[],
        request_body=None,
        security=None,
    )

    resource = Resource(
        id=1,
        target_id=1,
        resource_type="project",
        external_id="2001",
        owner_identity_id=2,
    )

    url = build_test_case_url(
        target=build_target(),
        endpoint=endpoint,
        resource=resource,
    )

    assert (
        url
        == "http://localhost:8001"
        "/api/projects/2001"
    )


def test_encodes_external_id() -> None:
    endpoint = Endpoint(
        id=1,
        target_id=1,
        path="/api/documents/{document_id}",
        method="GET",
        operation_id=None,
        requires_auth=True,
        parameters=[],
        request_body=None,
        security=None,
    )

    resource = Resource(
        id=1,
        target_id=1,
        resource_type="document",
        external_id="abc 123",
        owner_identity_id=1,
    )

    url = build_test_case_url(
        target=build_target(),
        endpoint=endpoint,
        resource=resource,
    )

    assert url.endswith(
        "/api/documents/abc%20123"
    )


def test_rejects_unresolved_parameters() -> None:
    endpoint = Endpoint(
        id=1,
        target_id=1,
        path=(
            "/orgs/{organization_id}"
            "/projects/{project_id}"
        ),
        method="GET",
        operation_id=None,
        requires_auth=True,
        parameters=[],
        request_body=None,
        security=None,
    )

    resource = Resource(
        id=1,
        target_id=1,
        resource_type="project",
        external_id="2001",
        owner_identity_id=1,
    )

    with pytest.raises(
        TestExecutionError
    ):
        build_test_case_url(
            target=build_target(),
            endpoint=endpoint,
            resource=resource,
        )