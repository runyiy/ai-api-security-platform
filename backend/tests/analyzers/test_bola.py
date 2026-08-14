from app.analyzers.bola import (
    AnalysisOutcome,
    analyze_bola_run,
)
from app.db.models.resource import Resource
from app.db.models.test_case import TestCase
from app.db.models.test_run import TestRun
from app.generators.bola import (
    BOLA_CROSS_OWNER,
)


def build_resource() -> Resource:
    return Resource(
        id=2,
        target_id=1,
        resource_type="project",
        external_id="2001",
        owner_identity_id=2,
    )


def build_test_case() -> TestCase:
    return TestCase(
        id=10,
        endpoint_id=3,
        actor_identity_id=1,
        resource_id=2,
        test_type=BOLA_CROSS_OWNER,
        ownership_relation="cross_owner",
        expected_statuses=[
            403,
            404,
        ],
        status="completed",
    )


def build_baseline_run(
    *,
    status: int = 200,
    body: str = (
        '{"id": 2001, '
        '"name": "Project B"}'
    ),
) -> TestRun:
    return TestRun(
        id=100,
        test_case_id=11,
        request_data={},
        response_status=status,
        response_body=body,
        duration_ms=5,
        error_message=None,
    )


def build_cross_run(
    *,
    status: int = 200,
    body: str = (
        '{"id": 2001, '
        '"name": "Project B"}'
    ),
) -> TestRun:
    return TestRun(
        id=200,
        test_case_id=10,
        request_data={},
        response_status=status,
        response_body=body,
        duration_ms=5,
        error_message=None,
    )


def test_detects_potential_bola() -> None:
    result = analyze_bola_run(
        test_case=build_test_case(),
        cross_owner_run=build_cross_run(),
        owner_baseline_run=(
            build_baseline_run()
        ),
        resource=build_resource(),
    )

    assert (
        result.outcome
        == AnalysisOutcome.POTENTIAL_BOLA
    )

    assert result.confidence == 0.99
    assert result.severity == "high"


def test_passes_when_cross_owner_is_forbidden() -> None:
    result = analyze_bola_run(
        test_case=build_test_case(),
        cross_owner_run=build_cross_run(
            status=403,
            body='{"detail": "forbidden"}',
        ),
        owner_baseline_run=(
            build_baseline_run()
        ),
        resource=build_resource(),
    )

    assert (
        result.outcome
        == AnalysisOutcome.PASS
    )


def test_passes_when_cross_owner_is_not_found() -> None:
    result = analyze_bola_run(
        test_case=build_test_case(),
        cross_owner_run=build_cross_run(
            status=404,
            body='{"detail": "not found"}',
        ),
        owner_baseline_run=(
            build_baseline_run()
        ),
        resource=build_resource(),
    )

    assert (
        result.outcome
        == AnalysisOutcome.PASS
    )


def test_is_inconclusive_without_baseline() -> None:
    result = analyze_bola_run(
        test_case=build_test_case(),
        cross_owner_run=build_cross_run(),
        owner_baseline_run=None,
        resource=build_resource(),
    )

    assert (
        result.outcome
        == AnalysisOutcome.INCONCLUSIVE
    )


def test_is_inconclusive_when_baseline_fails() -> None:
    result = analyze_bola_run(
        test_case=build_test_case(),
        cross_owner_run=build_cross_run(),
        owner_baseline_run=(
            build_baseline_run(
                status=500,
            )
        ),
        resource=build_resource(),
    )

    assert (
        result.outcome
        == AnalysisOutcome.INCONCLUSIVE
    )


def test_success_without_resource_evidence_is_inconclusive() -> None:
    result = analyze_bola_run(
        test_case=build_test_case(),
        cross_owner_run=build_cross_run(
            body='{"message": "ok"}',
        ),
        owner_baseline_run=(
            build_baseline_run()
        ),
        resource=build_resource(),
    )

    assert (
        result.outcome
        == AnalysisOutcome.INCONCLUSIVE
    )


def test_server_error_is_not_pass() -> None:
    result = analyze_bola_run(
        test_case=build_test_case(),
        cross_owner_run=build_cross_run(
            status=500,
        ),
        owner_baseline_run=(
            build_baseline_run()
        ),
        resource=build_resource(),
    )

    assert (
        result.outcome
        == AnalysisOutcome.INCONCLUSIVE
    )