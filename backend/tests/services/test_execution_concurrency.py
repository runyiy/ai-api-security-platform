from collections.abc import Iterator
import threading
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.endpoint import Endpoint
from app.db.models.resource import Resource
from app.db.models.scope import Scope
from app.db.models.safety_decision_record import SafetyDecisionRecord
from app.db.models.target import Target
from app.db.models.test_case import TestCase as StoredCase
from app.db.models.test_identity import TestIdentity as StoredIdentity
from app.db.models.test_run import TestRun as StoredRun
from app.db.session import SessionLocal
from app.executors.http import HTTPExecutionError, HTTPExecutionResult
from app.services.test_execution import (
    TestExecutionError as ExecutionError,
    TestExecutionService as ExecutionService,
)


class BlockingExecutor:
    def __init__(self) -> None:
        self.call_count = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self._sessions: dict[int, object] = {}

    def register_session(self, session: object) -> None:
        with self._lock:
            self._sessions[threading.get_ident()] = session

    def execute(self, **kwargs) -> HTTPExecutionResult:
        with self._lock:
            self.call_count += 1
            session = self._sessions[threading.get_ident()]

        assert not session.in_transaction()

        self.entered.set()
        if not self.release.wait(timeout=10):
            raise RuntimeError("executor release timed out")

        return HTTPExecutionResult(
            status_code=200,
            body=b'{"id": "2001"}',
            duration_ms=1,
        )


class ImmediateExecutor:
    def __init__(self) -> None:
        self.call_count = 0

    def execute(self, **kwargs) -> HTTPExecutionResult:
        self.call_count += 1
        return HTTPExecutionResult(
            status_code=200,
            body=b'{"id": "2001"}',
            duration_ms=1,
        )


class FailingExecutor:
    def execute(self, **kwargs):
        raise HTTPExecutionError("synthetic HTTP failure")


@pytest.fixture
def executable_test_case_id() -> Iterator[int]:
    unique_name = f"concurrency-{uuid4()}"

    with SessionLocal() as db:
        profile = AuthorizationProfile(
            name=f"{unique_name}-authorization",
            program_name="Self-controlled test",
            authorization_type="self_owned",
            automation_allowed=True,
            allow_get=True,
            require_human_execution_approval=False,
        )
        db.add(profile)
        db.flush()
        revision = AuthorizationRevision(
            authorization_profile_id=profile.id,
            revision_number=1,
            lifecycle_state="active",
            name=profile.name,
            program_name=profile.program_name,
            authorization_type=profile.authorization_type,
            automation_allowed=True,
            max_requests_per_second=1000.0,
            allow_get=True,
            require_human_execution_approval=False,
        )
        db.add(revision)
        db.flush()
        target = Target(
            name=unique_name,
            base_url="https://example.test",
            environment="test",
            is_enabled=True,
            authorization_profile_id=profile.id,
            authorization_revision_id=revision.id,
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
        db.add_all([
            scope,
            actor,
            owner,
            endpoint,
        ])
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
            status="pending",
        )
        db.add(test_case)
        db.commit()
        test_case_id = test_case.id
        target_id = target.id
        profile_id = profile.id

    try:
        yield test_case_id
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(SafetyDecisionRecord).where(
                    SafetyDecisionRecord.test_case_id == test_case_id
                )
            )
            db.execute(
                delete(StoredRun).where(
                    StoredRun.test_case_id
                    == test_case_id
                )
            )
            db.execute(
                delete(StoredCase).where(
                    StoredCase.id == test_case_id
                )
            )
            db.execute(
                delete(Target).where(
                    Target.id == target_id
                )
            )
            db.execute(
                delete(AuthorizationRevision).where(
                    AuthorizationRevision.authorization_profile_id
                    == profile_id
                )
            )
            db.execute(
                delete(AuthorizationProfile).where(
                    AuthorizationProfile.id == profile_id
                )
            )
            db.commit()


def test_concurrent_execute_acquires_once(
    executable_test_case_id: int,
) -> None:
    executor = BlockingExecutor()
    start = threading.Barrier(3)
    one_finished = threading.Event()
    runs: list[StoredRun] = []
    errors: list[Exception] = []

    def execute() -> None:
        try:
            with SessionLocal() as db:
                executor.register_session(db)
                stale_case = db.get(
                    StoredCase,
                    executable_test_case_id,
                )
                assert stale_case is not None
                assert stale_case.status == "pending"
                start.wait(timeout=10)
                run = ExecutionService(
                    db=db,
                    executor=executor,
                ).execute(
                    test_case_id=(
                        executable_test_case_id
                    )
                )
                runs.append(run)
        except Exception as exc:
            errors.append(exc)
        finally:
            one_finished.set()

    threads = [
        threading.Thread(target=execute),
        threading.Thread(target=execute),
    ]
    for thread in threads:
        thread.start()

    start.wait(timeout=10)
    assert executor.entered.wait(timeout=10)
    try:
        assert one_finished.wait(timeout=10)
        assert executor.call_count == 1
    finally:
        executor.release.set()

    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert len(runs) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ExecutionError)

    with SessionLocal() as db:
        run_count = db.scalar(
            select(func.count(StoredRun.id)).where(
                StoredRun.test_case_id
                == executable_test_case_id
            )
        )
        test_case = db.get(
            StoredCase,
            executable_test_case_id,
        )

    assert run_count == 1
    assert test_case is not None
    assert test_case.status == "completed"
    assert runs[0].authorization_revision_id is not None


def test_completed_case_can_run_again_sequentially(
    executable_test_case_id: int,
) -> None:
    executor = ImmediateExecutor()

    for _ in range(2):
        with SessionLocal() as db:
            ExecutionService(
                db=db,
                executor=executor,
            ).execute(
                test_case_id=executable_test_case_id
            )

    with SessionLocal() as db:
        run_count = db.scalar(
            select(func.count(StoredRun.id)).where(
                StoredRun.test_case_id
                == executable_test_case_id
            )
        )

    assert executor.call_count == 2
    assert run_count == 2


def test_http_error_run_records_exact_revision_provenance(
    executable_test_case_id: int,
) -> None:
    with SessionLocal() as db:
        run = ExecutionService(
            db=db,
            executor=FailingExecutor(),
        ).execute(test_case_id=executable_test_case_id)
        target_revision_id = db.scalar(
            select(Target.authorization_revision_id)
            .join(Endpoint, Endpoint.target_id == Target.id)
            .join(StoredCase, StoredCase.endpoint_id == Endpoint.id)
            .where(StoredCase.id == executable_test_case_id)
        )
        audit = db.scalar(
            select(SafetyDecisionRecord).where(
                SafetyDecisionRecord.test_run_id == run.id
            )
        )

    assert run.error_message == "synthetic HTTP failure"
    assert run.authorization_revision_id == target_revision_id
    assert audit is not None
    assert audit.outcome == "failed"
    assert audit.code == "http_execution_failed"
    assert audit.reason == "HTTP execution failed."
    assert "synthetic" not in audit.reason
