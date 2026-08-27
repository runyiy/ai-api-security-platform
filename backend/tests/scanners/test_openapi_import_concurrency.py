from collections.abc import Iterator
import threading
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql.dml import Insert
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import openapi as openapi_routes
from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.endpoint import Endpoint
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.db.session import engine
from app.scanners.openapi import ParsedEndpoint
from app.schemas.openapi import OpenAPIImportRequest


@pytest.fixture
def openapi_target_id() -> Iterator[int]:
    TestSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    unique_name = f"openapi-concurrency-{uuid4()}"

    with TestSession() as db:
        profile = AuthorizationProfile(
            name=f"{unique_name}-authorization",
            program_name="Self-controlled lab",
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
        db.add(
            Scope(
                target_id=target.id,
                hostname="example.test",
                path_pattern="/openapi.json",
                allowed_methods=["GET"],
                is_active=True,
            )
        )
        db.commit()
        target_id = target.id
        profile_id = profile.id

    try:
        yield target_id
    finally:
        with TestSession() as db:
            db.execute(
                delete(Endpoint).where(Endpoint.target_id == target_id)
            )
            db.execute(delete(Target).where(Target.id == target_id))
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


def parsed_endpoints(
    *,
    first_operation_id: str = "list_projects",
) -> list[ParsedEndpoint]:
    return [
        ParsedEndpoint(
            path="/projects",
            method="GET",
            operation_id=first_operation_id,
            requires_auth=True,
            parameters=[],
            request_body=None,
            security=[{"BearerAuth": []}],
        ),
        ParsedEndpoint(
            path="/projects/{project_id}",
            method="GET",
            operation_id="get_project",
            requires_auth=True,
            parameters=[
                {
                    "name": "project_id",
                    "in": "path",
                    "required": True,
                }
            ],
            request_body=None,
            security=[{"BearerAuth": []}],
        ),
    ]


def test_concurrent_import_is_endpoint_conflict_safe(
    openapi_target_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scan_ready = threading.Barrier(2)
    first_insert_ready = threading.Barrier(2)
    sessions: dict[int, Session] = {}
    sessions_lock = threading.Lock()

    class RacingSession(Session):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.first_insert_seen = False

        def scalar(self, statement, *args, **kwargs):
            if (
                isinstance(statement, Insert)
                and not self.first_insert_seen
            ):
                self.first_insert_seen = True
                first_insert_ready.wait(timeout=10)
            return super().scalar(statement, *args, **kwargs)

    ConcurrentSession = sessionmaker(
        bind=engine,
        class_=RacingSession,
        autoflush=False,
        expire_on_commit=False,
    )

    class BarrierScanner:
        def scan(
            self,
            *,
            target,
            authorization_revision,
            scopes,
            refresh_authorization,
            policy_decision_observer,
        ):
            with sessions_lock:
                session = sessions[threading.get_ident()]
            assert session.in_transaction() is False
            assert authorization_revision.id == target.authorization_revision_id
            scan_ready.wait(timeout=10)
            return (
                "https://example.test/openapi.json",
                parsed_endpoints(),
            )

    monkeypatch.setattr(
        openapi_routes,
        "scanner",
        BarrierScanner(),
    )
    payload = OpenAPIImportRequest(target_id=openapi_target_id)
    responses = []
    errors: list[Exception] = []

    def import_document() -> None:
        try:
            with ConcurrentSession() as db:
                with sessions_lock:
                    sessions[threading.get_ident()] = db
                responses.append(
                    openapi_routes.import_openapi(
                        payload=payload,
                        db=db,
                    )
                )
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=import_document)
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    with ConcurrentSession() as db:
        keys = list(
            db.execute(
                select(
                    Endpoint.target_id,
                    Endpoint.path,
                    Endpoint.method,
                ).where(Endpoint.target_id == openapi_target_id)
            ).all()
        )

    assert errors == []
    assert len(responses) == 2
    assert all(response.discovered == 2 for response in responses)
    assert sum(response.created for response in responses) == 2
    assert sum(response.updated for response in responses) == 0
    assert sum(response.unchanged for response in responses) == 2
    assert len(keys) == 2
    assert len(set(keys)) == 2


def test_sequential_import_preserves_create_unchanged_update_counts(
    openapi_target_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    TestSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    current_endpoints = parsed_endpoints()

    class MutableScanner:
        def scan(
            self,
            *,
            target,
            authorization_revision,
            scopes,
            refresh_authorization,
            policy_decision_observer,
        ):
            assert authorization_revision.id == target.authorization_revision_id
            return (
                "https://example.test/openapi.json",
                current_endpoints,
            )

    monkeypatch.setattr(
        openapi_routes,
        "scanner",
        MutableScanner(),
    )
    payload = OpenAPIImportRequest(target_id=openapi_target_id)

    with TestSession() as db:
        first = openapi_routes.import_openapi(payload=payload, db=db)
    with TestSession() as db:
        second = openapi_routes.import_openapi(payload=payload, db=db)

    current_endpoints = parsed_endpoints(
        first_operation_id="list_all_projects"
    )
    with TestSession() as db:
        changed = openapi_routes.import_openapi(payload=payload, db=db)

    assert (first.created, first.updated, first.unchanged) == (2, 0, 0)
    assert (second.created, second.updated, second.unchanged) == (0, 0, 2)
    assert (changed.created, changed.updated, changed.unchanged) == (0, 1, 1)
