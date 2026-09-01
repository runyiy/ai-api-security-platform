from collections.abc import Iterator
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import threading
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql.dml import Insert
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import openapi as openapi_routes
from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.endpoint import Endpoint
from app.db.models.openapi_import_record import OpenAPIImportRecord
from app.db.models.scope import Scope
from app.db.models.safety_decision_record import SafetyDecisionRecord
from app.db.models.target import Target
from app.db.session import engine
from app.executors.rate_limit import InMemoryRateLimiter, RateLimitCoordinationError
from app.network_safety.controller import NetworkExecutionController
from app.network_safety.gateway import NetworkGateway
from app.policies.scope_policy import ScopePolicyEngine
from app.scanners.openapi import (
    MAX_OPENAPI_PARAMETERS_PER_ENDPOINT,
    MAX_OPENAPI_STRUCTURE_NODES,
    OpenAPIScanResult,
    OpenAPIScanner,
    ParsedEndpoint,
)
from app.schemas.openapi import OpenAPIImportRequest
from tests.network_gateway_fakes import HandlerNetworkGateway


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
                delete(OpenAPIImportRecord).where(
                    OpenAPIImportRecord.target_id == target_id
                )
            )
            db.execute(
                delete(Endpoint).where(Endpoint.target_id == target_id)
            )
            db.execute(delete(SafetyDecisionRecord).where(
                SafetyDecisionRecord.target_id == target_id
            ))
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


def create_import_target(
    *, base_url: str, hostname: str, path_pattern: str
) -> tuple[int, int]:
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    unique_name = f"openapi-import-{uuid4()}"
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
            base_url=base_url,
            environment="test",
            network_mode="private_local",
            is_enabled=True,
            authorization_profile_id=profile.id,
            authorization_revision_id=revision.id,
        )
        db.add(target)
        db.flush()
        db.add(Scope(
            target_id=target.id,
            hostname=hostname,
            path_pattern=path_pattern,
            allowed_methods=["GET"],
            is_active=True,
        ))
        db.commit()
        return target.id, profile.id


def delete_import_target(target_id: int, profile_id: int) -> None:
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with TestSession() as db:
        db.execute(delete(OpenAPIImportRecord).where(OpenAPIImportRecord.target_id == target_id))
        db.execute(delete(Endpoint).where(Endpoint.target_id == target_id))
        db.execute(delete(SafetyDecisionRecord).where(SafetyDecisionRecord.target_id == target_id))
        db.execute(delete(Target).where(Target.id == target_id))
        db.execute(delete(AuthorizationRevision).where(
            AuthorizationRevision.authorization_profile_id == profile_id
        ))
        db.execute(delete(AuthorizationProfile).where(AuthorizationProfile.id == profile_id))
        db.commit()


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
            source_url,
            refresh_authorization,
            policy_decision_observer,
        ):
            with sessions_lock:
                session = sessions[threading.get_ident()]
            assert session.in_transaction() is False
            assert authorization_revision.id == target.authorization_revision_id
            scan_ready.wait(timeout=10)
            body = b'{"paths":{"/projects":{"get":{}}}}'
            return OpenAPIScanResult(
                source_url=source_url,
                document_sha256=hashlib.sha256(body).hexdigest(),
                document_size_bytes=len(body),
                content_encoding="identity",
                decoded_document_sha256=hashlib.sha256(body).hexdigest(),
                decoded_document_size_bytes=len(body),
                endpoints=parsed_endpoints(),
            )

    monkeypatch.setattr(
        openapi_routes,
        "scanner",
        BarrierScanner(),
    )
    payload = OpenAPIImportRequest(
        target_id=openapi_target_id,
        source_url="https://example.test/openapi.json",
    )
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
        records = list(
            db.scalars(
                select(OpenAPIImportRecord)
                .where(OpenAPIImportRecord.target_id == openapi_target_id)
                .order_by(OpenAPIImportRecord.id)
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
    assert len(records) == 2
    assert {record.id for record in records} == {
        response.import_record_id for response in responses
    }


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
            source_url,
            refresh_authorization,
            policy_decision_observer,
        ):
            assert authorization_revision.id == target.authorization_revision_id
            body = b'{"paths":{"/projects":{"get":{}}}}'
            return OpenAPIScanResult(
                source_url=source_url,
                document_sha256=hashlib.sha256(body).hexdigest(),
                document_size_bytes=len(body),
                content_encoding="identity",
                decoded_document_sha256=hashlib.sha256(body).hexdigest(),
                decoded_document_size_bytes=len(body),
                endpoints=current_endpoints,
            )

    monkeypatch.setattr(
        openapi_routes,
        "scanner",
        MutableScanner(),
    )
    payload = OpenAPIImportRequest(
        target_id=openapi_target_id,
        source_url="https://example.test/openapi.json",
    )

    with TestSession() as db:
        first = openapi_routes.import_openapi(payload=payload, db=db)
    with TestSession() as db:
        second = openapi_routes.import_openapi(payload=payload, db=db)

    current_endpoints = parsed_endpoints(
        first_operation_id="list_all_projects"
    )
    with TestSession() as db:
        changed = openapi_routes.import_openapi(payload=payload, db=db)

    document = b'{"paths":{"/projects":{"get":{}}}}'
    with TestSession() as db:
        records = list(
            db.scalars(
                select(OpenAPIImportRecord)
                .where(OpenAPIImportRecord.target_id == openapi_target_id)
                .order_by(OpenAPIImportRecord.id)
            ).all()
        )

    assert (first.created, first.updated, first.unchanged) == (2, 0, 0)
    assert (second.created, second.updated, second.unchanged) == (0, 0, 2)
    assert (changed.created, changed.updated, changed.unchanged) == (0, 1, 1)
    assert len(records) == 3
    assert [record.id for record in records] == [
        first.import_record_id,
        second.import_record_id,
        changed.import_record_id,
    ]
    assert all(record.source_url == payload.source_url for record in records)
    assert all(
        record.document_sha256 == hashlib.sha256(document).hexdigest()
        for record in records
    )
    assert all(record.document_size_bytes == len(document) for record in records)
    assert all(record.content_encoding == "identity" for record in records)
    assert all(
        record.decoded_document_sha256 == record.document_sha256
        for record in records
    )
    assert all(
        record.decoded_document_size_bytes == record.document_size_bytes
        for record in records
    )
    assert all(record.discovered_endpoint_count == 2 for record in records)
    assert all(record.fetched_at.tzinfo is not None for record in records)


def test_real_provenance_failure_rolls_back_endpoint_insert(
    openapi_target_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProvenanceFailingSession(Session):
        def flush(self, objects=None) -> None:
            if any(isinstance(item, OpenAPIImportRecord) for item in self.new):
                raise RuntimeError("synthetic provenance failure")
            super().flush(objects)

    FailingSession = sessionmaker(
        bind=engine,
        class_=ProvenanceFailingSession,
        autoflush=False,
        expire_on_commit=False,
    )
    body = b'{"paths":{"/projects":{"get":{}}}}'

    class Scanner:
        def scan(self, *, source_url, **kwargs):
            return OpenAPIScanResult(
                source_url=source_url,
                document_sha256=hashlib.sha256(body).hexdigest(),
                document_size_bytes=len(body),
                content_encoding="identity",
                decoded_document_sha256=hashlib.sha256(body).hexdigest(),
                decoded_document_size_bytes=len(body),
                endpoints=parsed_endpoints(),
            )

    monkeypatch.setattr(openapi_routes, "scanner", Scanner())
    payload = OpenAPIImportRequest(
        target_id=openapi_target_id,
        source_url="https://example.test/openapi.json",
    )
    with FailingSession() as db:
        with pytest.raises(RuntimeError, match="synthetic provenance failure"):
            openapi_routes.import_openapi(payload=payload, db=db)

    with FailingSession() as db:
        assert db.scalar(
            select(Endpoint.id).where(Endpoint.target_id == openapi_target_id)
        ) is None
        assert db.scalar(
            select(OpenAPIImportRecord.id).where(
                OpenAPIImportRecord.target_id == openapi_target_id
            )
        ) is None


def test_real_localhost_gateway_import_persists_exact_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = (
        b'{"openapi":"3.1.0","paths":{"/local/{id}":'
        b'{"get":{"operationId":"get_local"}}}}\n'
    )
    observed: list[tuple[str, str | None, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed.append((
                self.path,
                self.headers.get("Authorization"),
                self.headers.get("Accept"),
            ))
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source_url = f"http://127.0.0.1:{server.server_port}/docs/spec.json"
    target_id, profile_id = create_import_target(
        base_url=f"http://127.0.0.1:{server.server_port}",
        hostname="127.0.0.1",
        path_pattern="/docs/spec.json",
    )
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(
        openapi_routes,
        "scanner",
        OpenAPIScanner(
            ScopePolicyEngine(platform_allowed_hosts={"127.0.0.1"}),
            InMemoryRateLimiter(requests_per_second=1000.0),
            NetworkGateway(controller=NetworkExecutionController()),
        ),
    )
    try:
        with TestSession() as db:
            response = openapi_routes.import_openapi(
                OpenAPIImportRequest(target_id=target_id, source_url=source_url), db
            )
        with TestSession() as db:
            endpoints = list(db.scalars(select(Endpoint).where(Endpoint.target_id == target_id)))
            records = list(db.scalars(select(OpenAPIImportRecord).where(
                OpenAPIImportRecord.target_id == target_id
            )))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        delete_import_target(target_id, profile_id)

    assert observed == [("/docs/spec.json", None, "application/json")]
    assert [(item.path, item.method, item.operation_id) for item in endpoints] == [
        ("/local/{id}", "GET", "get_local")
    ]
    assert len(records) == 1
    record = records[0]
    assert record.id == response.import_record_id
    assert record.source_url == source_url
    assert record.document_sha256 == hashlib.sha256(body).hexdigest()
    assert record.document_size_bytes == len(body)
    assert record.discovered_endpoint_count == 1


def test_malformed_json_import_is_atomic_in_postgresql(
    openapi_target_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with TestSession() as db:
        existing = Endpoint(
            target_id=openapi_target_id, path="/existing", method="GET",
            operation_id="unchanged", requires_auth=False, parameters=[],
            request_body=None, security=None,
        )
        db.add(existing)
        db.commit()
        existing_id = existing.id
    monkeypatch.setattr(
        openapi_routes,
        "scanner",
        OpenAPIScanner(
            ScopePolicyEngine(platform_allowed_hosts={"example.test"}),
            InMemoryRateLimiter(requests_per_second=1000.0),
            HandlerNetworkGateway(
                lambda request: Mock(status_code=200, content=b'{"paths":')
            ),
        ),
    )
    with TestSession() as db:
        with pytest.raises(HTTPException) as raised:
            openapi_routes.import_openapi(
                OpenAPIImportRequest(
                    target_id=openapi_target_id,
                    source_url="https://example.test/openapi.json",
                ), db
            )
    assert raised.value.status_code == 502
    with TestSession() as db:
        endpoints = list(db.scalars(select(Endpoint).where(Endpoint.target_id == openapi_target_id)))
        records = list(db.scalars(select(OpenAPIImportRecord).where(
            OpenAPIImportRecord.target_id == openapi_target_id
        )))
    assert [(item.id, item.operation_id) for item in endpoints] == [(existing_id, "unchanged")]
    assert records == []


def test_real_endpoint_persistence_failure_rolls_back_all_mutations(
    openapi_target_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutation_seen = False

    class EndpointFailingSession(Session):
        endpoint_inserts = 0

        def scalar(self, statement, *args, **kwargs):
            nonlocal mutation_seen
            if isinstance(statement, Insert):
                self.endpoint_inserts += 1
                if self.endpoint_inserts == 2:
                    mutation_seen = any(
                        isinstance(item, Endpoint) for item in self.dirty
                    )
                    raise RuntimeError("synthetic endpoint persistence failure")
            return super().scalar(statement, *args, **kwargs)

    FailingSession = sessionmaker(
        bind=engine, class_=EndpointFailingSession,
        autoflush=False, expire_on_commit=False,
    )
    with FailingSession() as db:
        db.add(Endpoint(
            target_id=openapi_target_id, path="/projects", method="GET",
            operation_id="original", requires_auth=False, parameters=[],
            request_body=None, security=None,
        ))
        db.commit()
    body = b'{"paths":{"/projects":{"get":{}},"/new":{"get":{}}}}'
    monkeypatch.setattr(openapi_routes, "scanner", Mock(scan=Mock(return_value=OpenAPIScanResult(
        source_url="https://example.test/openapi.json",
        document_sha256=hashlib.sha256(body).hexdigest(),
        document_size_bytes=len(body),
        content_encoding="identity",
        decoded_document_sha256=hashlib.sha256(body).hexdigest(),
        decoded_document_size_bytes=len(body),
        endpoints=[
            ParsedEndpoint("/projects", "GET", "changed", True, [], None, None),
            ParsedEndpoint("/new", "GET", "new", False, [], None, None),
        ],
    ))))
    with FailingSession() as db:
        with pytest.raises(RuntimeError, match="endpoint persistence failure"):
            openapi_routes.import_openapi(
                OpenAPIImportRequest(
                    target_id=openapi_target_id,
                    source_url="https://example.test/openapi.json",
                ), db
            )
    assert mutation_seen is True
    with FailingSession() as db:
        endpoints = list(db.scalars(select(Endpoint).where(Endpoint.target_id == openapi_target_id)))
        records = list(db.scalars(select(OpenAPIImportRecord).where(
            OpenAPIImportRecord.target_id == openapi_target_id
        )))
    assert [(item.path, item.operation_id, item.requires_auth) for item in endpoints] == [
        ("/projects", "original", False)
    ]
    assert records == []


@pytest.mark.parametrize(
    "failure",
    ["overflow", "network", "policy", "coordination"],
)
def test_import_failures_persist_no_provenance_or_endpoint_mutation(
    openapi_target_id: int,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    class FailingLimiter:
        def wait(self, **kwargs) -> None:
            raise RateLimitCoordinationError("synthetic coordination failure")

    if failure == "overflow":
        gateway = HandlerNetworkGateway(
            lambda request: Mock(status_code=200, content=b"x" * 1_000_001)
        )
        limiter = InMemoryRateLimiter(requests_per_second=1000.0)
        source_url = "https://example.test/openapi.json"
    elif failure == "network":
        gateway = HandlerNetworkGateway(
            lambda request: (_ for _ in ()).throw(httpx.ConnectError("failed"))
        )
        limiter = InMemoryRateLimiter(requests_per_second=1000.0)
        source_url = "https://example.test/openapi.json"
    elif failure == "policy":
        gateway = HandlerNetworkGateway(
            lambda request: pytest.fail("policy denial must be zero-connect")
        )
        limiter = InMemoryRateLimiter(requests_per_second=1000.0)
        source_url = "https://example.test/out-of-scope.json"
    else:
        gateway = HandlerNetworkGateway(
            lambda request: pytest.fail("coordination failure must be zero-connect")
        )
        limiter = FailingLimiter()
        source_url = "https://example.test/openapi.json"
    monkeypatch.setattr(openapi_routes, "scanner", OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"example.test"}), limiter, gateway
    ))
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with TestSession() as db:
        db.add(Endpoint(
            target_id=openapi_target_id, path="/existing", method="GET",
            operation_id="original", requires_auth=False, parameters=[],
            request_body=None, security=None,
        ))
        db.commit()
    with TestSession() as db:
        with pytest.raises(HTTPException):
            openapi_routes.import_openapi(
                OpenAPIImportRequest(target_id=openapi_target_id, source_url=source_url), db
            )
    with TestSession() as db:
        endpoints = list(db.scalars(select(Endpoint).where(Endpoint.target_id == openapi_target_id)))
        records = list(db.scalars(select(OpenAPIImportRecord).where(
            OpenAPIImportRecord.target_id == openapi_target_id
        )))
    assert [(item.path, item.operation_id) for item in endpoints] == [("/existing", "original")]
    assert records == []


def parser_rejection_cases() -> list[tuple[str, bytes, str]]:
    deep: object = 0
    for _ in range(31):
        deep = [deep]
    parameters = [
        {"name": f"p{index}", "in": "query"}
        for index in range(MAX_OPENAPI_PARAMETERS_PER_ENDPOINT + 1)
    ]
    endpoint_paths = {}
    for index in range(1001):
        methods = {method: {} for method in ("get", "post", "patch", "delete")}
        if index == 1000:
            methods = {"get": {}}
        endpoint_paths[f"/{index}"] = methods
    cases = [
        ("openapi_document_too_deep", {"paths": {}, "secret": deep}, "secret"),
        (
            "openapi_document_too_complex",
            {"paths": {}, "secret": [0] * (MAX_OPENAPI_STRUCTURE_NODES - 2)},
            "secret",
        ),
        (
            "openapi_too_many_paths",
            {"paths": {f"/{index}": {} for index in range(2001)}},
            "/2000",
        ),
        ("openapi_too_many_endpoints", {"paths": endpoint_paths}, "/1000"),
        (
            "openapi_too_many_parameters",
            {"paths": {"/items": {"get": {"parameters": parameters}}}},
            "p128",
        ),
        (
            "openapi_path_too_long",
            {"paths": {"/secret-" + "x" * 500: {"get": {}}}},
            "secret-",
        ),
        (
            "openapi_operation_id_too_long",
            {"paths": {"/items": {"get": {"operationId": "secret-" + "x" * 250}}}},
            "secret-",
        ),
        (
            "openapi_references_not_supported",
            {"paths": {}, "components": {"X": {"$ref": "https://secret/ref"}}},
            "https://secret/ref",
        ),
    ]
    return [(code, json.dumps(document).encode(), attacker) for code, document, attacker in cases]


@pytest.mark.parametrize("code,body,attacker_content", parser_rejection_cases())
def test_every_parser_rejection_is_sanitized_and_atomic_in_postgresql(
    openapi_target_id: int,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    body: bytes,
    attacker_content: str,
) -> None:
    gateway = HandlerNetworkGateway(
        lambda request: Mock(status_code=200, content=body)
    )
    monkeypatch.setattr(openapi_routes, "scanner", OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"example.test"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        gateway,
    ))
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with TestSession() as db:
        db.add(Endpoint(
            target_id=openapi_target_id, path="/existing", method="GET",
            operation_id="original", requires_auth=False, parameters=[],
            request_body=None, security=None,
        ))
        db.commit()
    with TestSession() as db:
        with pytest.raises(HTTPException) as raised:
            openapi_routes.import_openapi(
                OpenAPIImportRequest(
                    target_id=openapi_target_id,
                    source_url="https://example.test/openapi.json",
                ), db
            )
    assert raised.value.status_code == 502
    assert raised.value.detail == code
    assert attacker_content not in str(raised.value.detail)
    assert gateway.calls == 1
    with TestSession() as db:
        endpoints = list(db.scalars(select(Endpoint).where(Endpoint.target_id == openapi_target_id)))
        records = list(db.scalars(select(OpenAPIImportRecord).where(
            OpenAPIImportRecord.target_id == openapi_target_id
        )))
    assert [(item.path, item.operation_id) for item in endpoints] == [("/existing", "original")]
    assert records == []


def test_no_import_transaction_spans_json_or_structure_validation(
    openapi_target_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scanners import openapi as scanner_module

    body = b'{"paths":{"/safe":{"get":{}}}}'
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    real_loads = scanner_module.json.loads
    real_decode = scanner_module.decode_openapi_document
    real_validate = scanner_module.validate_openapi_structure
    active_session: Session | None = None

    def checked_loads(value):
        assert active_session is not None
        assert active_session.in_transaction() is False
        return real_loads(value)

    def checked_decode(value, content_encoding):
        assert active_session is not None
        assert active_session.in_transaction() is False
        return real_decode(value, content_encoding)

    def checked_validate(value):
        assert active_session is not None
        assert active_session.in_transaction() is False
        return real_validate(value)

    monkeypatch.setattr(scanner_module.json, "loads", checked_loads)
    monkeypatch.setattr(scanner_module, "decode_openapi_document", checked_decode)
    monkeypatch.setattr(scanner_module, "validate_openapi_structure", checked_validate)
    monkeypatch.setattr(openapi_routes, "scanner", OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"example.test"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        HandlerNetworkGateway(lambda request: Mock(status_code=200, content=body)),
    ))
    with TestSession() as db:
        active_session = db
        result = openapi_routes.import_openapi(
            OpenAPIImportRequest(
                target_id=openapi_target_id,
                source_url="https://example.test/openapi.json",
            ), db
        )
    assert result.created == 1


@pytest.mark.parametrize(
    "body,expected_exception,attacker_marker",
    [
        pytest.param(
            b'{"paths":{},"attacker":'
            + b"7" * (sys.get_int_max_str_digits() + 1)
            + b"}",
            ValueError,
            "777777",
            id="oversized-json-integer",
        ),
        pytest.param(
            b'{"paths":{},"attacker":"\xff"}',
            UnicodeDecodeError,
            "attacker",
            id="invalid-json-byte-encoding",
        ),
    ],
)
def test_decoder_failures_are_sanitized_and_atomic_in_postgresql(
    openapi_target_id: int,
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    expected_exception: type[Exception],
    attacker_marker: str,
) -> None:
    with pytest.raises(expected_exception) as decoder_raised:
        json.loads(body)
    assert type(decoder_raised.value) is expected_exception

    gateway = HandlerNetworkGateway(
        lambda request: Mock(status_code=200, content=body)
    )
    monkeypatch.setattr(openapi_routes, "scanner", OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"example.test"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        gateway,
    ))
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with TestSession() as db:
        db.add(Endpoint(
            target_id=openapi_target_id, path="/existing", method="GET",
            operation_id="original", requires_auth=False, parameters=[],
            request_body=None, security=None,
        ))
        db.commit()

    with TestSession() as db:
        with pytest.raises(HTTPException) as raised:
            openapi_routes.import_openapi(
                OpenAPIImportRequest(
                    target_id=openapi_target_id,
                    source_url="https://example.test/openapi.json",
                ),
                db,
            )

    assert raised.value.status_code == 502
    assert raised.value.detail == "OpenAPI response is not valid JSON"
    assert str(decoder_raised.value) not in str(raised.value.detail)
    assert attacker_marker not in str(raised.value.detail)
    assert gateway.calls == 1
    with TestSession() as db:
        endpoints = list(db.scalars(select(Endpoint).where(
            Endpoint.target_id == openapi_target_id
        )))
        records = list(db.scalars(select(OpenAPIImportRecord).where(
            OpenAPIImportRecord.target_id == openapi_target_id
        )))
    assert [(item.path, item.operation_id) for item in endpoints] == [
        ("/existing", "original")
    ]
    assert records == []


def test_real_localhost_gzip_import_persists_dual_provenance_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoded = b'{"paths":{"/gzip":{"post":{"operationId":"create_gzip"}}}}\n'
    wire = gzip.compress(decoded, mtime=0)
    observed: list[tuple[str, str | None, str | None, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed.append((
                self.path,
                self.headers.get("Authorization"),
                self.headers.get("Accept"),
                self.headers.get("Accept-Encoding"),
            ))
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

        def log_message(self, format: str, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source_url = f"http://127.0.0.1:{server.server_port}/docs/spec.json"
    target_id, profile_id = create_import_target(
        base_url=f"http://127.0.0.1:{server.server_port}",
        hostname="127.0.0.1",
        path_pattern="/docs/spec.json",
    )
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(openapi_routes, "scanner", OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"127.0.0.1"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        NetworkGateway(controller=NetworkExecutionController()),
    ))
    try:
        with TestSession() as db:
            first = openapi_routes.import_openapi(
                OpenAPIImportRequest(target_id=target_id, source_url=source_url), db
            )
        with TestSession() as db:
            second = openapi_routes.import_openapi(
                OpenAPIImportRequest(target_id=target_id, source_url=source_url), db
            )
        with TestSession() as db:
            endpoints = list(db.scalars(select(Endpoint).where(Endpoint.target_id == target_id)))
            records = list(db.scalars(select(OpenAPIImportRecord).where(
                OpenAPIImportRecord.target_id == target_id
            ).order_by(OpenAPIImportRecord.id)))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        delete_import_target(target_id, profile_id)

    assert observed == [
        ("/docs/spec.json", None, "application/json", "gzip"),
        ("/docs/spec.json", None, "application/json", "gzip"),
    ]
    assert [(item.path, item.method) for item in endpoints] == [("/gzip", "POST")]
    assert (first.created, second.unchanged) == (1, 1)
    assert len(records) == 2
    assert records[0].id != records[1].id
    assert all(record.document_sha256 == hashlib.sha256(wire).hexdigest() for record in records)
    assert all(record.document_size_bytes == len(wire) for record in records)
    assert all(record.content_encoding == "gzip" for record in records)
    assert all(
        record.decoded_document_sha256 == hashlib.sha256(decoded).hexdigest()
        for record in records
    )
    assert all(record.decoded_document_size_bytes == len(decoded) for record in records)
    assert all(record.discovered_endpoint_count == 1 for record in records)
    assert first.content_encoding == second.content_encoding == "gzip"
    assert first.decoded_document_sha256 == hashlib.sha256(decoded).hexdigest()


def test_explicit_identity_import_preserves_wire_semantics(
    openapi_target_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b'{"paths":{"/identity":{"get":{}}}}'
    gateway = HandlerNetworkGateway(
        lambda request: Mock(
            status_code=200,
            content=body,
            headers=httpx.Headers({"Content-Encoding": "identity"}),
        )
    )
    monkeypatch.setattr(openapi_routes, "scanner", OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"example.test"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        gateway,
    ))
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with TestSession() as db:
        response = openapi_routes.import_openapi(
            OpenAPIImportRequest(
                target_id=openapi_target_id,
                source_url="https://example.test/openapi.json",
            ), db
        )
    with TestSession() as db:
        record = db.scalar(select(OpenAPIImportRecord).where(
            OpenAPIImportRecord.target_id == openapi_target_id
        ))
    assert record is not None
    assert response.content_encoding == record.content_encoding == "identity"
    assert record.credential_binding_id is None
    assert record.document_sha256 == hashlib.sha256(body).hexdigest()
    assert record.decoded_document_sha256 == record.document_sha256
    assert record.decoded_document_size_bytes == record.document_size_bytes == len(body)


def gzip_rejection_cases() -> list[tuple[str, bytes, list[tuple[str, str]]]]:
    valid = gzip.compress(b'{"paths":{}}', mtime=0)
    ref = gzip.compress(b'{"paths":{},"nested":{"$ref":"#/secret"}}', mtime=0)
    deep: object = 0
    for _ in range(31):
        deep = [deep]
    deep_body = gzip.compress(json.dumps({"paths": {}, "deep": deep}).encode(), mtime=0)
    cases = [
        (
            "response_too_large: Network response exceeded the allowed size.",
            b"x" * 1_000_001,
            [("Content-Encoding", "gzip")],
        ),
        ("openapi_compressed_body_invalid", b"not-gzip", [("Content-Encoding", "gzip")]),
        ("openapi_compressed_body_invalid", valid[:-1], [("Content-Encoding", "gzip")]),
        ("openapi_compressed_body_invalid", valid + valid, [("Content-Encoding", "gzip")]),
        ("openapi_compressed_body_invalid", valid + b"tail", [("Content-Encoding", "gzip")]),
        (
            "openapi_decompressed_body_too_large",
            gzip.compress(b"x" * 1_000_001, mtime=0),
            [("Content-Encoding", "gzip")],
        ),
        ("openapi_content_encoding_not_supported", valid, [("Content-Encoding", "br")]),
        ("openapi_content_encoding_not_supported", valid, [("Content-Encoding", "deflate")]),
        ("openapi_content_encoding_not_supported", valid, [("Content-Encoding", "zstd")]),
        ("openapi_content_encoding_not_supported", valid, [("Content-Encoding", "gzip, br")]),
        (
            "openapi_content_encoding_not_supported",
            valid,
            [("Content-Encoding", "gzip"), ("Content-Encoding", "identity")],
        ),
        ("openapi_references_not_supported", ref, [("Content-Encoding", "gzip")]),
        ("openapi_document_too_deep", deep_body, [("Content-Encoding", "gzip")]),
    ]
    existing_codes = {code for code, _, _ in cases}
    for code, parser_body, _ in parser_rejection_cases():
        if code not in existing_codes:
            cases.append((
                code,
                gzip.compress(parser_body, mtime=0),
                [("Content-Encoding", "gzip")],
            ))
    return cases


@pytest.mark.parametrize("code,body,headers", gzip_rejection_cases())
def test_gzip_and_encoding_rejections_are_atomic_in_postgresql(
    openapi_target_id: int,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    body: bytes,
    headers: list[tuple[str, str]],
) -> None:
    gateway = HandlerNetworkGateway(
        lambda request: Mock(
            status_code=200,
            content=body,
            headers=httpx.Headers(headers),
        )
    )
    monkeypatch.setattr(openapi_routes, "scanner", OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"example.test"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        gateway,
    ))
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with TestSession() as db:
        db.add(Endpoint(
            target_id=openapi_target_id, path="/existing", method="GET",
            operation_id="original", requires_auth=False, parameters=[],
            request_body=None, security=None,
        ))
        db.commit()
    with TestSession() as db:
        with pytest.raises(HTTPException) as raised:
            openapi_routes.import_openapi(
                OpenAPIImportRequest(
                    target_id=openapi_target_id,
                    source_url="https://example.test/openapi.json",
                ), db
            )
    assert raised.value.status_code == 502
    assert raised.value.detail == code
    assert gateway.calls == 1
    with TestSession() as db:
        endpoints = list(db.scalars(select(Endpoint).where(Endpoint.target_id == openapi_target_id)))
        records = list(db.scalars(select(OpenAPIImportRecord).where(
            OpenAPIImportRecord.target_id == openapi_target_id
        )))
    assert [(item.path, item.operation_id) for item in endpoints] == [("/existing", "original")]
    assert records == []
