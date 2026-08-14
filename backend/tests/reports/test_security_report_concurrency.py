from collections.abc import Iterator
import threading
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.endpoint import Endpoint
from app.db.models.finding import Finding
from app.db.models.resource import Resource
from app.db.models.scope import Scope
from app.db.models.security_report import SecurityReport
from app.db.models.target import Target
from app.db.models.test_case import TestCase as StoredCase
from app.db.models.test_identity import TestIdentity as StoredIdentity
from app.db.models.test_run import TestRun as StoredRun
from app.db.session import engine
from app.services.security_report import SecurityReportService
import app.services.security_report as report_service_module


@pytest.fixture
def confirmed_finding_id() -> Iterator[int]:
    unique_name = f"report-concurrency-{uuid4()}"
    TestSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    with TestSession() as db:
        target = Target(
            name=unique_name,
            base_url="https://example.test",
            environment="test",
            is_enabled=True,
        )
        db.add(target)
        db.flush()

        scope = Scope(
            target_id=target.id,
            hostname="example.test",
            path_pattern="/projects/*",
            allowed_methods=["GET"],
            is_active=True,
        )
        actor = StoredIdentity(
            target_id=target.id,
            name=f"{unique_name}-actor",
            role=None,
            auth_type="anonymous",
            credentials=None,
            is_active=True,
        )
        owner = StoredIdentity(
            target_id=target.id,
            name=f"{unique_name}-owner",
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
        db.add_all([scope, actor, owner, endpoint])
        db.flush()

        resource = Resource(
            target_id=target.id,
            resource_type="project",
            external_id="2001",
            owner_identity_id=owner.id,
        )
        db.add(resource)
        db.flush()

        test_case = StoredCase(
            endpoint_id=endpoint.id,
            actor_identity_id=actor.id,
            resource_id=resource.id,
            test_type="bola_cross_owner",
            ownership_relation="cross_owner",
            expected_statuses=[403, 404],
            status="completed",
        )
        db.add(test_case)
        db.flush()

        test_run = StoredRun(
            test_case_id=test_case.id,
            request_data={
                "url": "https://example.test/projects/2001",
                "headers": {},
            },
            response_status=200,
            response_body='{"id": "2001"}',
            duration_ms=1,
            error_message=None,
        )
        db.add(test_run)
        db.flush()

        finding = Finding(
            target_id=target.id,
            endpoint_id=endpoint.id,
            test_run_id=test_run.id,
            category="BOLA",
            severity="high",
            confidence=0.95,
            status="confirmed",
            title="Confirmed BOLA",
            description="Cross-owner access succeeded.",
            review_notes="Confirmed by reviewer.",
        )
        db.add(finding)
        db.commit()
        finding_id = finding.id
        test_run_id = test_run.id
        test_case_id = test_case.id
        target_id = target.id

    try:
        yield finding_id
    finally:
        with TestSession() as db:
            db.execute(
                delete(SecurityReport).where(
                    SecurityReport.finding_id == finding_id
                )
            )
            db.execute(
                delete(Finding).where(Finding.id == finding_id)
            )
            db.execute(
                delete(StoredRun).where(StoredRun.id == test_run_id)
            )
            db.execute(
                delete(StoredCase).where(StoredCase.id == test_case_id)
            )
            db.execute(delete(Target).where(Target.id == target_id))
            db.commit()


def test_concurrent_generation_allocates_sequential_versions(
    confirmed_finding_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_attempts = [threading.Event(), threading.Event()]
    thread_indexes: dict[int, int] = {}
    index_lock = threading.Lock()

    class TrackingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            if getattr(statement, "_for_update_arg", None) is not None:
                with index_lock:
                    index = thread_indexes[threading.get_ident()]
                lock_attempts[index].set()
            return super().scalar(statement, *args, **kwargs)

    ConcurrentSession = sessionmaker(
        bind=engine,
        class_=TrackingSession,
        autoflush=False,
        expire_on_commit=False,
    )

    original_renderer = (
        report_service_module.render_security_report_markdown
    )
    first_renderer_entered = threading.Event()
    release_first_renderer = threading.Event()
    render_count = 0
    render_lock = threading.Lock()

    def blocking_renderer(report: SecurityReport) -> str:
        nonlocal render_count
        with render_lock:
            render_count += 1
            is_first = render_count == 1
        if is_first:
            first_renderer_entered.set()
            if not release_first_renderer.wait(timeout=10):
                raise RuntimeError("renderer release timed out")
        return original_renderer(report)

    monkeypatch.setattr(
        report_service_module,
        "render_security_report_markdown",
        blocking_renderer,
    )

    start = threading.Barrier(3)
    versions: list[int] = []
    errors: list[Exception] = []

    def generate(index: int) -> None:
        with index_lock:
            thread_indexes[threading.get_ident()] = index
        try:
            start.wait(timeout=10)
            with ConcurrentSession() as db:
                report = SecurityReportService(db=db).generate(
                    finding_id=confirmed_finding_id
                )
                versions.append(report.version)
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=generate, args=(0,)),
        threading.Thread(target=generate, args=(1,)),
    ]
    for thread in threads:
        thread.start()

    start.wait(timeout=10)
    try:
        assert lock_attempts[0].wait(timeout=10)
        assert lock_attempts[1].wait(timeout=10)
        assert first_renderer_entered.wait(timeout=10)
    finally:
        release_first_renderer.set()

    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert errors == []
    assert sorted(versions) == [1, 2]

    with ConcurrentSession() as db:
        persisted_versions = list(
            db.scalars(
                select(SecurityReport.version)
                .where(
                    SecurityReport.finding_id
                    == confirmed_finding_id
                )
                .order_by(SecurityReport.version)
            )
        )

    assert persisted_versions == [1, 2]
