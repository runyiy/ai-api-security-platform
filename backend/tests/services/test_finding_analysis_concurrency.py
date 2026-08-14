from collections.abc import Iterator
import threading
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql.dml import Insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.endpoint import Endpoint
from app.db.models.finding import Finding
from app.db.models.resource import Resource
from app.db.models.target import Target
from app.db.models.test_case import TestCase as StoredCase
from app.db.models.test_identity import TestIdentity as StoredIdentity
from app.db.models.test_run import TestRun as StoredRun
from app.db.session import engine
from app.generators.bola import BOLA_CROSS_OWNER, OWNER_BASELINE
from app.services.finding_analysis import FindingAnalysisService


@pytest.fixture
def analyzable_test_run_id() -> Iterator[int]:
    TestSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    unique_name = f"finding-concurrency-{uuid4()}"

    with TestSession() as db:
        target = Target(
            name=unique_name,
            base_url="https://example.test",
            environment="test",
            is_enabled=True,
        )
        db.add(target)
        db.flush()

        owner = StoredIdentity(
            target_id=target.id,
            name=f"{unique_name}-owner",
            role=None,
            auth_type="anonymous",
            credentials=None,
            is_active=True,
        )
        other_actor = StoredIdentity(
            target_id=target.id,
            name=f"{unique_name}-other",
            role=None,
            auth_type="anonymous",
            credentials=None,
            is_active=True,
        )
        endpoint = Endpoint(
            target_id=target.id,
            path="/projects/{project_id}",
            method="GET",
            operation_id="get_project",
            requires_auth=True,
            parameters=[],
            request_body=None,
            security=None,
        )
        db.add_all([owner, other_actor, endpoint])
        db.flush()

        resource = Resource(
            target_id=target.id,
            resource_type="project",
            external_id="2001",
            owner_identity_id=owner.id,
        )
        db.add(resource)
        db.flush()

        baseline_case = StoredCase(
            endpoint_id=endpoint.id,
            actor_identity_id=owner.id,
            resource_id=resource.id,
            test_type=OWNER_BASELINE,
            ownership_relation="owner",
            expected_statuses=[200],
            status="completed",
        )
        cross_owner_case = StoredCase(
            endpoint_id=endpoint.id,
            actor_identity_id=other_actor.id,
            resource_id=resource.id,
            test_type=BOLA_CROSS_OWNER,
            ownership_relation="cross_owner",
            expected_statuses=[403, 404],
            status="completed",
        )
        db.add_all([baseline_case, cross_owner_case])
        db.flush()

        response_body = '{"id": "2001", "name": "Project B"}'
        baseline_run = StoredRun(
            test_case_id=baseline_case.id,
            request_data={},
            response_status=200,
            response_body=response_body,
            duration_ms=1,
            error_message=None,
        )
        cross_owner_run = StoredRun(
            test_case_id=cross_owner_case.id,
            request_data={},
            response_status=200,
            response_body=response_body,
            duration_ms=1,
            error_message=None,
        )
        db.add_all([baseline_run, cross_owner_run])
        db.commit()
        test_run_id = cross_owner_run.id
        target_id = target.id

    try:
        yield test_run_id
    finally:
        with TestSession() as db:
            run_ids = select(StoredRun.id).join(
                StoredCase,
                StoredRun.test_case_id == StoredCase.id,
            ).join(
                Endpoint,
                StoredCase.endpoint_id == Endpoint.id,
            ).where(Endpoint.target_id == target_id)
            db.execute(
                delete(Finding).where(Finding.test_run_id.in_(run_ids))
            )
            db.execute(delete(StoredRun).where(StoredRun.id.in_(run_ids)))
            db.execute(
                delete(StoredCase).where(
                    StoredCase.endpoint_id.in_(
                        select(Endpoint.id).where(
                            Endpoint.target_id == target_id
                        )
                    )
                )
            )
            db.execute(
                delete(Resource).where(Resource.target_id == target_id)
            )
            db.execute(delete(Target).where(Target.id == target_id))
            db.commit()


def test_concurrent_analysis_returns_one_finding(
    analyzable_test_run_id: int,
) -> None:
    insert_ready = threading.Barrier(2)

    class RacingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            if isinstance(statement, Insert):
                insert_ready.wait(timeout=10)
            return super().scalar(statement, *args, **kwargs)

    ConcurrentSession = sessionmaker(
        bind=engine,
        class_=RacingSession,
        autoflush=False,
        expire_on_commit=False,
    )
    finding_ids: list[int] = []
    errors: list[Exception] = []

    def analyze() -> None:
        try:
            with ConcurrentSession() as db:
                outcome = FindingAnalysisService(db=db).analyze_test_run(
                    test_run_id=analyzable_test_run_id
                )
                assert outcome.finding is not None
                finding_ids.append(outcome.finding.id)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=analyze) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    with ConcurrentSession() as db:
        finding_count = db.scalar(
            select(func.count(Finding.id)).where(
                Finding.test_run_id == analyzable_test_run_id,
                Finding.category == "BOLA",
            )
        )

    assert errors == []
    assert len(finding_ids) == 2
    assert len(set(finding_ids)) == 1
    assert finding_count == 1


def test_reanalysis_updates_existing_finding_without_duplicate(
    analyzable_test_run_id: int,
) -> None:
    TestSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    with TestSession() as db:
        first = FindingAnalysisService(db=db).analyze_test_run(
            test_run_id=analyzable_test_run_id
        )
        assert first.finding is not None
        finding_id = first.finding.id

        first.finding.severity = "low"
        first.finding.confidence = 0.1
        first.finding.title = "stale title"
        first.finding.description = "stale description"
        first.finding.status = "confirmed"
        first.finding.review_notes = "Keep this review."
        db.commit()

    with TestSession() as db:
        second = FindingAnalysisService(db=db).analyze_test_run(
            test_run_id=analyzable_test_run_id
        )
        assert second.finding is not None

    with TestSession() as db:
        stored = db.get(Finding, finding_id)
        finding_count = db.scalar(
            select(func.count(Finding.id)).where(
                Finding.test_run_id == analyzable_test_run_id,
                Finding.category == "BOLA",
            )
        )

    assert second.finding.id == finding_id
    assert finding_count == 1
    assert stored is not None
    assert stored.severity == "high"
    assert stored.confidence == 0.99
    assert stored.title.startswith("Potential BOLA in GET")
    assert "Cross-owner access" in stored.description
    assert stored.status == "confirmed"
    assert stored.review_notes == "Keep this review."
