from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.ai.schemas import (
    AIAnalysisResult,
    SanitizedFindingEvidence,
)
from app.db.models.endpoint import Endpoint
from app.db.models.finding import Finding
from app.db.models.resource import Resource
from app.db.models.test_case import TestCase as StoredCase
from app.db.models.test_run import TestRun as StoredRun
from app.services.ai_analysis import AIAnalysisService


def build_objects() -> dict[tuple[type, int], object]:
    finding = Finding(
        id=1,
        target_id=2,
        endpoint_id=3,
        test_run_id=4,
        category="BOLA",
        severity="high",
        confidence=0.95,
        status="potential",
        title="Potential BOLA",
        description="Cross-owner access succeeded.",
    )
    test_run = StoredRun(
        id=4,
        test_case_id=5,
        request_data={},
        response_status=200,
        response_body='{"id": "2001"}',
        duration_ms=10,
        error_message=None,
    )
    test_case = StoredCase(
        id=5,
        endpoint_id=3,
        actor_identity_id=6,
        resource_id=7,
        test_type="bola_cross_owner",
        ownership_relation="cross_owner",
        expected_statuses=[403, 404],
        status="completed",
    )
    endpoint = Endpoint(
        id=3,
        target_id=2,
        path="/projects/{project_id}",
        method="GET",
        requires_auth=True,
        parameters=[],
    )
    resource = Resource(
        id=7,
        target_id=2,
        resource_type="project",
        external_id="2001",
        owner_identity_id=8,
    )
    return {
        (Finding, 1): finding,
        (StoredRun, 4): test_run,
        (StoredCase, 5): test_case,
        (Endpoint, 3): endpoint,
        (Resource, 7): resource,
    }


def build_session(
    events: list[str],
) -> Mock:
    objects = build_objects()
    transaction_active = {
        "value": False,
    }
    db = Mock(spec=Session)

    def get_object(model, object_id):
        transaction_active["value"] = True
        events.append("db-read")
        return objects.get((model, object_id))

    def add_analysis(analysis) -> None:
        transaction_active["value"] = True
        events.append("db-add")

    def commit() -> None:
        transaction_active["value"] = False
        events.append("commit")

    db.get.side_effect = get_object
    db.add.side_effect = add_analysis
    db.commit.side_effect = commit
    db.in_transaction.side_effect = (
        lambda: transaction_active["value"]
    )
    return db


class OrderingProvider:
    provider_name = "ordering"
    model_name = "test-model"

    def __init__(
        self,
        *,
        db: Mock,
        events: list[str],
        fail: bool = False,
    ) -> None:
        self.db = db
        self.events = events
        self.fail = fail

    def analyze(
        self,
        *,
        evidence: SanitizedFindingEvidence,
    ) -> AIAnalysisResult:
        self.events.append("provider-call")
        assert self.db.in_transaction() is False

        if self.fail:
            raise RuntimeError("provider failed")

        return AIAnalysisResult(
            category="BOLA",
            confidence=0.9,
            severity="high",
            false_positive_risk="low",
            reason="Analysis completed.",
            recommended_review="Review the finding.",
            fix_recommendation="Enforce ownership.",
        )


def test_analysis_ends_read_transaction_before_provider() -> None:
    events: list[str] = []
    db = build_session(events)
    provider = OrderingProvider(
        db=db,
        events=events,
    )

    analysis = AIAnalysisService(
        db=db,
        provider=provider,
    ).analyze_finding(finding_id=1)

    assert events == [
        "db-read",
        "db-read",
        "db-read",
        "db-read",
        "db-read",
        "commit",
        "provider-call",
        "db-add",
        "commit",
    ]
    assert analysis.provider == "ordering"
    assert analysis.model_name == "test-model"
    db.add.assert_called_once_with(analysis)
    assert db.commit.call_count == 2


def test_provider_failure_leaves_no_write_transaction() -> None:
    events: list[str] = []
    db = build_session(events)
    provider = OrderingProvider(
        db=db,
        events=events,
        fail=True,
    )

    with pytest.raises(
        RuntimeError,
        match="provider failed",
    ):
        AIAnalysisService(
            db=db,
            provider=provider,
        ).analyze_finding(finding_id=1)

    assert events[-2:] == [
        "commit",
        "provider-call",
    ]
    assert db.in_transaction() is False
    db.add.assert_not_called()
    assert db.commit.call_count == 1
