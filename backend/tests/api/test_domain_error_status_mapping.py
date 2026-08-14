from unittest.mock import Mock

from fastapi import HTTPException
import pytest
from sqlalchemy.orm import Session

from app.ai.mock_provider import MockAIProvider
from app.analyzers.bola import AnalysisOutcome, BOLAAnalysisResult
from app.api.routes.ai_analysis import analyze_finding_with_ai
from app.api.routes.findings import analyze_test_run
from app.api.routes.security_reports import generate_security_report
from app.api.routes.test_runs import execute_test_case
from app.services.ai_analysis import (
    AIAnalysisNotFoundError,
    AIAnalysisService,
    AIAnalysisServiceError,
)
from app.services.finding_analysis import (
    FindingAnalysisError,
    FindingAnalysisNotFoundError,
    FindingAnalysisOutcome,
    FindingAnalysisService,
)
from app.services.security_report import (
    SecurityReportError,
    SecurityReportNotFoundError,
    SecurityReportService,
)
from app.services.test_execution import (
    TestExecutionError as ExecutionError,
    TestExecutionNotFoundError as ExecutionNotFoundError,
    TestExecutionService as ExecutionService,
)


def raise_error(error: Exception):
    def raise_it(*args, **kwargs):
        raise error

    return raise_it


def test_execute_route_maps_not_found_conflict_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Mock(spec=Session)

    monkeypatch.setattr(
        ExecutionService,
        "execute",
        raise_error(ExecutionNotFoundError("TestCase not found.")),
    )
    with pytest.raises(HTTPException) as missing:
        execute_test_case(test_case_id=999, db=db)
    assert missing.value.status_code == 404

    monkeypatch.setattr(
        ExecutionService,
        "execute",
        raise_error(ExecutionError("TestCase is already running.")),
    )
    with pytest.raises(HTTPException) as conflict:
        execute_test_case(test_case_id=1, db=db)
    assert conflict.value.status_code == 409

    stored_run = object()
    monkeypatch.setattr(
        ExecutionService,
        "execute",
        lambda self, **kwargs: stored_run,
    )
    assert execute_test_case(test_case_id=1, db=db) is stored_run


def test_finding_analysis_route_maps_not_found_conflict_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Mock(spec=Session)

    monkeypatch.setattr(
        FindingAnalysisService,
        "analyze_test_run",
        raise_error(FindingAnalysisNotFoundError("TestRun not found.")),
    )
    with pytest.raises(HTTPException) as missing:
        analyze_test_run(test_run_id=999, db=db)
    assert missing.value.status_code == 404

    monkeypatch.setattr(
        FindingAnalysisService,
        "analyze_test_run",
        raise_error(
            FindingAnalysisError(
                "Endpoint and resource belong to different targets."
            )
        ),
    )
    with pytest.raises(HTTPException) as conflict:
        analyze_test_run(test_run_id=1, db=db)
    assert conflict.value.status_code == 409

    outcome = FindingAnalysisOutcome(
        analysis=BOLAAnalysisResult(
            outcome=AnalysisOutcome.INCONCLUSIVE,
            reason="No owner baseline run is available.",
        ),
        finding=None,
    )
    monkeypatch.setattr(
        FindingAnalysisService,
        "analyze_test_run",
        lambda self, **kwargs: outcome,
    )
    response = analyze_test_run(test_run_id=1, db=db)
    assert response.outcome == AnalysisOutcome.INCONCLUSIVE


def test_report_route_maps_not_found_conflict_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Mock(spec=Session)

    monkeypatch.setattr(
        SecurityReportService,
        "generate",
        raise_error(SecurityReportNotFoundError("Finding not found.")),
    )
    with pytest.raises(HTTPException) as missing:
        generate_security_report(finding_id=999, db=db)
    assert missing.value.status_code == 404

    monkeypatch.setattr(
        SecurityReportService,
        "generate",
        raise_error(
            SecurityReportError(
                "Only confirmed findings can generate reports."
            )
        ),
    )
    with pytest.raises(HTTPException) as conflict:
        generate_security_report(finding_id=1, db=db)
    assert conflict.value.status_code == 409

    report = object()
    monkeypatch.setattr(
        SecurityReportService,
        "generate",
        lambda self, **kwargs: report,
    )
    assert generate_security_report(finding_id=1, db=db) is report


def test_ai_route_maps_not_found_conflict_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Mock(spec=Session)

    monkeypatch.setattr(
        AIAnalysisService,
        "analyze_finding",
        raise_error(AIAnalysisNotFoundError("Finding not found.")),
    )
    with pytest.raises(HTTPException) as missing:
        analyze_finding_with_ai(finding_id=999, db=db)
    assert missing.value.status_code == 404

    monkeypatch.setattr(
        AIAnalysisService,
        "analyze_finding",
        raise_error(
            AIAnalysisServiceError("Finding is not eligible for analysis.")
        ),
    )
    with pytest.raises(HTTPException) as conflict:
        analyze_finding_with_ai(finding_id=1, db=db)
    assert conflict.value.status_code == 409

    analysis = object()
    monkeypatch.setattr(
        AIAnalysisService,
        "analyze_finding",
        lambda self, **kwargs: analysis,
    )
    assert analyze_finding_with_ai(finding_id=1, db=db) is analysis


def test_services_raise_not_found_subclasses_for_missing_primary_ids() -> None:
    db = Mock(spec=Session)
    db.get.return_value = None
    db.scalar.return_value = None

    with pytest.raises(ExecutionNotFoundError):
        ExecutionService(db=db, executor=Mock()).execute(test_case_id=999)

    with pytest.raises(FindingAnalysisNotFoundError):
        FindingAnalysisService(db=db).analyze_test_run(test_run_id=999)

    with pytest.raises(SecurityReportNotFoundError):
        SecurityReportService(db=db).generate(finding_id=999)

    with pytest.raises(AIAnalysisNotFoundError):
        AIAnalysisService(
            db=db,
            provider=MockAIProvider(),
        ).analyze_finding(finding_id=999)
