from datetime import datetime, timezone
from unittest.mock import Mock

from sqlalchemy.orm import Session

from app.api.routes.security_reports import (
    get_security_report_markdown,
)
from app.db.models.endpoint import Endpoint
from app.db.models.finding import Finding
from app.db.models.resource import Resource
from app.db.models.security_report import SecurityReport
from app.db.models.test_case import TestCase as StoredCase
from app.db.models.test_run import TestRun as StoredRun
from app.schemas.security_report import SecurityReportRead
from app.services.security_report import SecurityReportService


def build_stored_report() -> SecurityReport:
    return SecurityReport(
        id=10,
        finding_id=20,
        target_id=30,
        version=1,
        report_format="markdown",
        report_data={
            "title": "Confirmed BOLA",
        },
        markdown_content="# Stored report",
        created_at=datetime.now(timezone.utc),
    )


def test_generates_and_stores_security_report() -> None:
    finding = Finding(
        id=20,
        target_id=30,
        endpoint_id=40,
        test_run_id=50,
        category="BOLA",
        severity="high",
        confidence=0.95,
        status="confirmed",
        title="Potential BOLA",
        description="Cross-owner access succeeded.",
        review_notes="Confirmed by reviewer.",
    )
    test_run = StoredRun(
        id=50,
        test_case_id=60,
        request_data={
            "url": "https://example.test/projects/2001",
            "headers": {
                "Authorization": "[REDACTED]",
            },
        },
        response_status=200,
        response_body='{"id": "2001"}',
        duration_ms=10,
        error_message=None,
    )
    test_case = StoredCase(
        id=60,
        endpoint_id=40,
        actor_identity_id=70,
        resource_id=80,
        test_type="bola_cross_owner",
        ownership_relation="cross_owner",
        expected_statuses=[403, 404],
        status="completed",
    )
    endpoint = Endpoint(
        id=40,
        target_id=30,
        path="/projects/{project_id}",
        method="GET",
        operation_id=None,
        requires_auth=True,
        parameters=[],
        request_body=None,
        security=None,
    )
    resource = Resource(
        id=80,
        target_id=30,
        resource_type="project",
        external_id="2001",
        owner_identity_id=90,
    )

    objects = {
        (StoredRun, 50): test_run,
        (StoredCase, 60): test_case,
        (Endpoint, 40): endpoint,
        (Resource, 80): resource,
    }
    db = Mock(spec=Session)
    db.get.side_effect = (
        lambda model, object_id: objects.get(
            (model, object_id)
        )
    )
    db.scalar.side_effect = [
        finding,
        None,
        None,
    ]

    def refresh(report: SecurityReport) -> None:
        report.id = 10
        report.created_at = datetime.now(timezone.utc)

    db.refresh.side_effect = refresh

    report = SecurityReportService(db=db).generate(
        finding_id=20
    )

    assert report.target_id == 30
    assert report.report_format == "markdown"
    assert report.report_data["title"].startswith(
        "Confirmed BOLA"
    )
    assert report.report_data["evidence"][
        "request"
    ]["headers"]["Authorization"] == "[REDACTED]"
    assert report.markdown_content.startswith(
        "# Confirmed BOLA"
    )
    db.add.assert_called_once_with(report)
    db.commit.assert_called_once_with()


def test_security_report_read_uses_persisted_fields() -> None:
    report = build_stored_report()

    payload = SecurityReportRead.model_validate(report)

    assert payload.finding_id == 20
    assert payload.target_id == 30
    assert payload.report_data == {
        "title": "Confirmed BOLA",
    }
    assert payload.markdown_content == "# Stored report"


def test_markdown_route_returns_stored_content() -> None:
    report = build_stored_report()
    db = Mock(spec=Session)
    db.get.return_value = report

    payload = get_security_report_markdown(
        report_id=report.id,
        db=db,
    )

    assert payload.report_id == report.id
    assert payload.version == report.version
    assert payload.markdown == "# Stored report"
