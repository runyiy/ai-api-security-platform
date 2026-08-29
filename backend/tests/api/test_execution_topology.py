import os
import subprocess
import sys
from unittest.mock import Mock

from fastapi import HTTPException
import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.routes import test_runs
from app.core.config import Settings, settings
from app.services.test_execution import TestExecutionService


def test_execution_topology_defaults_and_rejects_unknown_values() -> None:
    assert Settings(database_url="postgresql://unused").execution_topology == (
        "single_process"
    )
    assert Settings(
        database_url="postgresql://unused", execution_topology="multi_process"
    ).execution_topology == "multi_process"
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://unused", execution_topology="automatic")


def test_multi_process_topology_parses_in_independent_process() -> None:
    environment = os.environ.copy()
    environment["EXECUTION_TOPOLOGY"] = "multi_process"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.core.config import settings; print(settings.execution_topology)",
        ],
        cwd=".",
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert result.stdout.strip() == "multi_process"


def test_legacy_execute_is_blocked_before_service_or_database_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Mock(spec=Session)
    monkeypatch.setattr(settings, "execution_topology", "multi_process")
    monkeypatch.setattr(
        TestExecutionService,
        "__init__",
        lambda *args, **kwargs: pytest.fail("legacy service constructed"),
    )

    with pytest.raises(HTTPException) as raised:
        test_runs.execute_test_case(test_case_id=7, db=db)

    assert raised.value.status_code == 409
    assert raised.value.detail == {
        "code": "plan_bound_execution_required",
        "reason": "Execution requires an exact immutable ExecutionPlan.",
    }
    assert db.mock_calls == []


def test_legacy_execute_remains_available_in_single_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = object()
    monkeypatch.setattr(settings, "execution_topology", "single_process")
    monkeypatch.setattr(
        TestExecutionService, "execute", lambda self, **kwargs: expected
    )
    assert test_runs.execute_test_case(
        test_case_id=7, db=Mock(spec=Session)
    ) is expected


def test_exact_plan_execute_and_cancel_remain_available_in_multi_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_execution = object()
    expected_cancellation = object()
    monkeypatch.setattr(settings, "execution_topology", "multi_process")
    monkeypatch.setattr(
        test_runs.PlanExecutionService,
        "execute",
        lambda self, **kwargs: expected_execution,
    )
    monkeypatch.setattr(
        test_runs.ExecutionPlanCancellationService,
        "request_cancel",
        lambda self, *args, **kwargs: expected_cancellation,
    )
    db = Mock(spec=Session)
    db.get_bind.return_value = object()
    assert test_runs.execute_execution_plan(5, db) is expected_execution
    assert test_runs.cancel_execution_plan(5, db) is expected_cancellation
