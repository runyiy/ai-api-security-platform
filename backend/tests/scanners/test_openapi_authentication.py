from collections.abc import Iterator
import base64
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from unittest.mock import Mock
from uuid import uuid4

import pytest
import httpx
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, event, select

from app.api.routes import openapi as openapi_routes
from app.auth.context import build_authentication_context
from app.credentials import bearer as bearer_credentials
from app.credentials.bearer import BearerCredentialService
from app.credentials.stored_secret import StoredSecretCipher, StoredSecretProvider
from app.core.config import Settings
from app.db.models.credential_binding import CredentialBinding
from app.db.models.credential_secret_version import CredentialSecretVersion
from app.db.models.endpoint import Endpoint
from app.db.models.openapi_import_record import OpenAPIImportRecord
from app.db.models.safety_decision_record import SafetyDecisionRecord
from app.db.models.target import Target
from app.db.models.test_identity import TestIdentity
from app.db.session import SessionLocal, engine
from app.executors.rate_limit import InMemoryRateLimiter
from app.network_safety.controller import NetworkExecutionController
from app.network_safety.gateway import NetworkGateway, NetworkGatewayError
from app.policies.scope_policy import ScopePolicyEngine
from app.scanners.openapi import (
    OpenAPIExecutionBlocked,
    OpenAPIPolicyDenied,
    OpenAPIScanError,
    OpenAPIScanner,
)
from app.schemas.openapi import OpenAPIImportRequest
from app.services.openapi_credentials import (
    OpenAPICredentialError,
    build_openapi_credential_refresh,
)
from tests.network_gateway_fakes import HandlerNetworkGateway, StaticJSONNetworkGateway
from tests.scanners.test_openapi import build_revision, build_scope, build_target
from tests.scanners.test_openapi_import_concurrency import (
    create_import_target,
    delete_import_target,
    openapi_target_id,
)


@pytest.fixture
def credential_graph() -> Iterator[tuple[int, int, int, int]]:
    with SessionLocal() as db:
        selected_target = Target(
            name=f"m9-04-selected-{uuid4()}",
            base_url="https://example.test",
            environment="test",
            is_enabled=True,
        )
        other_target = Target(
            name=f"m9-04-other-{uuid4()}",
            base_url="https://other.test",
            environment="test",
            is_enabled=True,
        )
        db.add_all([selected_target, other_target])
        db.flush()
        identity = TestIdentity(
            target_id=selected_target.id,
            name="documentation identity",
            role="docs",
            auth_type="bearer",
            credentials=None,
            is_active=True,
        )
        db.add(identity)
        db.flush()
        binding = CredentialBinding(
            test_identity_id=identity.id,
            auth_type="bearer",
            source_type="stored_secret",
            is_active=True,
        )
        db.add(binding)
        db.commit()
        values = selected_target.id, other_target.id, identity.id, binding.id
    try:
        yield values
    finally:
        with SessionLocal() as db:
            db.execute(delete(CredentialSecretVersion).where(
                CredentialSecretVersion.credential_binding_id == values[3]
            ))
            db.execute(delete(CredentialBinding).where(
                CredentialBinding.id == values[3]
            ))
            db.execute(delete(TestIdentity).where(TestIdentity.id == values[2]))
            db.execute(delete(Target).where(Target.id.in_(values[:2])))
            db.commit()


def test_openapi_request_is_anonymous_by_default_and_rejects_auth_material() -> None:
    assert OpenAPIImportRequest(
        target_id=1, source_url="https://example.test/openapi.json"
    ).credential_binding_id is None
    assert OpenAPIImportRequest(
        target_id=1,
        source_url="https://example.test/openapi.json",
        credential_binding_id=None,
    ).credential_binding_id is None
    for field, value in (
        ("authorization", "Bearer secret"),
        ("token", "secret"),
        ("cookie", "session=secret"),
        ("api_key", "secret"),
        ("headers", {"Authorization": "Bearer secret"}),
    ):
        with pytest.raises(ValidationError):
            OpenAPIImportRequest.model_validate({
                "target_id": 1,
                "source_url": "https://example.test/openapi.json",
                field: value,
            })


def test_explicit_authentication_is_refreshed_after_wait_and_policy_audit() -> None:
    events: list[str] = []
    gateway = StaticJSONNetworkGateway()
    original_request = gateway.request

    def request(**kwargs):
        events.append("network")
        assert kwargs["headers"] == {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Authorization": "Bearer local-doc-token",
        }
        return original_request(**kwargs)

    gateway.request = request
    limiter = Mock()
    limiter.wait.side_effect = lambda **kwargs: events.append("rate")
    scanner = OpenAPIScanner(Mock(), limiter, gateway)
    scanner.policy_engine.evaluate.return_value = Mock(
        allowed=True, authorization_revision_id=200
    )
    identity = Mock(
        id=10, name="docs", is_active=True, auth_type="bearer"
    )

    result = scanner.scan(
        target=build_target(),
        authorization_revision=build_revision(),
        scopes=[build_scope()],
        source_url="https://example.test/openapi.json",
        refresh_authorization=lambda: (
            events.append("authorization-refresh")
            or (build_target(), build_revision(), [build_scope()])
        ),
        policy_decision_observer=lambda decision: events.append("audit"),
        credential_binding_id=55,
        refresh_credential=lambda: (
            events.append("credential-refresh")
            or build_authentication_context(
                identity, bearer_token=SecretStr("local-doc-token")
            )
        ),
    )

    assert result.credential_binding_id == 55
    assert events == [
        "rate",
        "authorization-refresh",
        "audit",
        "credential-refresh",
        "network",
    ]


def test_selected_credential_refresh_failure_is_sanitized_before_network() -> None:
    gateway = StaticJSONNetworkGateway()
    scanner = OpenAPIScanner(Mock(), Mock(), gateway)
    scanner.policy_engine.evaluate.return_value = Mock(
        allowed=True, authorization_revision_id=200
    )

    with pytest.raises(OpenAPIExecutionBlocked) as raised:
        scanner.scan(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            source_url="https://example.test/openapi.json",
            refresh_authorization=lambda: (
                build_target(), build_revision(), [build_scope()]
            ),
            policy_decision_observer=lambda decision: None,
            credential_binding_id=55,
            refresh_credential=lambda: (_ for _ in ()).throw(
                RuntimeError("secret provider details")
            ),
        )

    assert raised.value.code == "openapi_credential_unavailable"
    assert "secret provider details" not in raised.value.reason
    assert raised.value.__cause__ is None
    assert gateway.calls == 0


def test_anonymous_scan_never_invokes_available_credential_refresh() -> None:
    gateway = StaticJSONNetworkGateway()
    scanner = OpenAPIScanner(Mock(), Mock(), gateway)
    scanner.policy_engine.evaluate.return_value = Mock(
        allowed=True, authorization_revision_id=200
    )
    scanner.scan(
        target=build_target(),
        authorization_revision=build_revision(),
        scopes=[build_scope()],
        source_url="https://example.test/openapi.json",
        refresh_authorization=lambda: (
            build_target(), build_revision(), [build_scope()]
        ),
        policy_decision_observer=lambda decision: None,
        refresh_credential=lambda: pytest.fail("anonymous credential lookup"),
    )
    assert gateway.calls == 1


def test_credential_refresh_enforces_exact_target_ownership(
    credential_graph: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, other_target_id, _, binding_id = credential_graph
    monkeypatch.setattr(
        BearerCredentialService,
        "resolve_binding",
        lambda self, **kwargs: SecretStr("synthetic-doc-token"),
    )
    refresh = build_openapi_credential_refresh(
        engine,
        target_id=other_target_id,
        credential_binding_id=binding_id,
    )
    gateway = StaticJSONNetworkGateway()
    scanner = OpenAPIScanner(Mock(), Mock(), gateway)
    scanner.policy_engine.evaluate.return_value = Mock(
        allowed=True, authorization_revision_id=200
    )
    with pytest.raises(OpenAPIExecutionBlocked) as raised:
        scanner.scan(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            source_url="https://example.test/openapi.json",
            refresh_authorization=lambda: (
                build_target(), build_revision(), [build_scope()]
            ),
            policy_decision_observer=lambda decision: None,
            credential_binding_id=binding_id,
            refresh_credential=refresh,
        )
    assert raised.value.code == "openapi_credential_unavailable"
    assert gateway.calls == 0


@pytest.mark.parametrize("invalid_state", ["auth_type", "source_type", "missing"])
def test_credential_refresh_rejects_unsupported_or_unresolvable_binding(
    credential_graph: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
    invalid_state: str,
) -> None:
    target_id, _, _, binding_id = credential_graph
    if invalid_state == "missing":
        from app.credentials.bearer import BearerCredentialError

        monkeypatch.setattr(
            BearerCredentialService,
            "resolve_binding",
            lambda self, **kwargs: (_ for _ in ()).throw(
                BearerCredentialError("Bearer credential is unavailable.")
            ),
        )
    else:
        with SessionLocal() as db:
            binding = db.get(CredentialBinding, binding_id)
            setattr(
                binding,
                invalid_state,
                "basic" if invalid_state == "auth_type" else "external_reference",
            )
            db.commit()
    refresh = build_openapi_credential_refresh(
        engine,
        target_id=target_id,
        credential_binding_id=binding_id,
    )
    with pytest.raises(OpenAPICredentialError) as raised:
        refresh()
    assert str(raised.value) == "The selected OpenAPI credential is unavailable."
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("deactivate", ["binding", "identity"])
def test_final_credential_refresh_catches_state_drift_before_network(
    credential_graph: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
    deactivate: str,
) -> None:
    target_id, _, identity_id, binding_id = credential_graph
    monkeypatch.setattr(
        BearerCredentialService,
        "resolve_binding",
        lambda self, **kwargs: SecretStr("synthetic-doc-token"),
    )
    refresh = build_openapi_credential_refresh(
        engine,
        target_id=target_id,
        credential_binding_id=binding_id,
    )
    gateway = StaticJSONNetworkGateway()
    limiter = Mock()

    def mutate_during_rate_wait(**kwargs) -> None:
        with SessionLocal() as db:
            if deactivate == "binding":
                db.get(CredentialBinding, binding_id).is_active = False
            else:
                db.get(TestIdentity, identity_id).is_active = False
            db.commit()

    limiter.wait.side_effect = mutate_during_rate_wait
    scanner = OpenAPIScanner(Mock(), limiter, gateway)
    scanner.policy_engine.evaluate.return_value = Mock(
        allowed=True, authorization_revision_id=200
    )
    with pytest.raises(OpenAPIExecutionBlocked) as raised:
        scanner.scan(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            source_url="https://example.test/openapi.json",
            refresh_authorization=lambda: (
                build_target(), build_revision(), [build_scope()]
            ),
            policy_decision_observer=lambda decision: None,
            credential_binding_id=binding_id,
            refresh_credential=refresh,
        )
    assert raised.value.code == "openapi_credential_unavailable"
    assert gateway.calls == 0


def test_credential_refresh_transaction_closes_before_network_request(
    credential_graph: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_id, _, _, binding_id = credential_graph
    monkeypatch.setattr(
        BearerCredentialService,
        "resolve_binding",
        lambda self, **kwargs: SecretStr("synthetic-doc-token"),
    )
    checked_out = 0

    def checkout(*args) -> None:
        nonlocal checked_out
        checked_out += 1

    def checkin(*args) -> None:
        nonlocal checked_out
        checked_out -= 1

    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)
    gateway = StaticJSONNetworkGateway()
    original_request = gateway.request

    def request(**kwargs):
        assert checked_out == 0
        return original_request(**kwargs)

    gateway.request = request
    scanner = OpenAPIScanner(Mock(), Mock(), gateway)
    scanner.policy_engine.evaluate.return_value = Mock(
        allowed=True, authorization_revision_id=200
    )
    try:
        scanner.scan(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            source_url="https://example.test/openapi.json",
            refresh_authorization=lambda: (
                build_target(), build_revision(), [build_scope()]
            ),
            policy_decision_observer=lambda decision: None,
            credential_binding_id=binding_id,
            refresh_credential=build_openapi_credential_refresh(
                engine,
                target_id=target_id,
                credential_binding_id=binding_id,
            ),
        )
    finally:
        event.remove(engine, "checkout", checkout)
        event.remove(engine, "checkin", checkin)
    assert checked_out == 0
    assert gateway.calls == 1


@pytest.mark.parametrize(
    ("source_url", "allowed_hosts"),
    [
        ("https://other.test/openapi.json", {"example.test", "other.test"}),
        ("https://example.test/out-of-scope.json", {"example.test"}),
    ],
)
def test_valid_credential_does_not_widen_origin_or_scope_policy(
    source_url: str,
    allowed_hosts: set[str],
) -> None:
    gateway = StaticJSONNetworkGateway()
    identity = Mock(id=10, name="docs", is_active=True, auth_type="bearer")
    credential_refresh = Mock(side_effect=lambda: build_authentication_context(
        identity, bearer_token=SecretStr("policy-doc-token")
    ))
    scanner = OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts=allowed_hosts),
        Mock(),
        gateway,
    )
    with pytest.raises(OpenAPIPolicyDenied):
        scanner.scan(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            source_url=source_url,
            refresh_authorization=lambda: (
                build_target(), build_revision(), [build_scope()]
            ),
            policy_decision_observer=lambda decision: None,
            credential_binding_id=55,
            refresh_credential=credential_refresh,
        )
    credential_refresh.assert_not_called()
    assert gateway.calls == 0


def test_valid_credential_does_not_enable_external_network_mode() -> None:
    gateway = StaticJSONNetworkGateway()
    identity = Mock(id=10, name="docs", is_active=True, auth_type="bearer")
    credential_refresh = Mock(side_effect=lambda: build_authentication_context(
        identity, bearer_token=SecretStr("external-doc-token")
    ))
    target = build_target()
    target.network_mode = "external_public_authorized"
    scanner = OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"example.test"}),
        Mock(),
        gateway,
    )
    with pytest.raises(OpenAPIExecutionBlocked) as raised:
        scanner.scan(
            target=target,
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            source_url="https://example.test/openapi.json",
            refresh_authorization=lambda: (
                target, build_revision(), [build_scope()]
            ),
            policy_decision_observer=lambda decision: None,
            credential_binding_id=55,
            refresh_credential=credential_refresh,
        )
    assert raised.value.code == "external_network_mode_not_ready"
    credential_refresh.assert_not_called()
    assert gateway.calls == 0


def test_authenticated_redirect_is_not_followed_or_forwarded() -> None:
    redirected_authorizations: list[str | None] = []

    class RedirectDestination(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            redirected_authorizations.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"paths":{}}')

        def log_message(self, format: str, *args) -> None:
            pass

    destination = ThreadingHTTPServer(("127.0.0.1", 0), RedirectDestination)
    destination_thread = threading.Thread(
        target=destination.serve_forever, daemon=True
    )
    destination_thread.start()
    source_authorizations: list[str | None] = []

    class RedirectSource(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            source_authorizations.append(self.headers.get("Authorization"))
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{destination.server_port}/redirected.json",
            )
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            pass

    source = ThreadingHTTPServer(("127.0.0.1", 0), RedirectSource)
    source_thread = threading.Thread(target=source.serve_forever, daemon=True)
    source_thread.start()
    target = build_target()
    target.base_url = f"http://127.0.0.1:{source.server_port}"
    scope = build_scope()
    scope.hostname = "127.0.0.1"
    scope.path_pattern = "/openapi.json"
    source_url = f"{target.base_url}/openapi.json"
    identity = Mock(id=10, name="docs", is_active=True, auth_type="bearer")
    scanner = OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"127.0.0.1"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        NetworkGateway(controller=NetworkExecutionController()),
    )
    try:
        with pytest.raises(OpenAPIScanError, match="not valid JSON"):
            scanner.scan(
                target=target,
                authorization_revision=build_revision(),
                scopes=[scope],
                source_url=source_url,
                refresh_authorization=lambda: (
                    target, build_revision(), [scope]
                ),
                policy_decision_observer=lambda decision: None,
                credential_binding_id=55,
                refresh_credential=lambda: build_authentication_context(
                    identity,
                    bearer_token=SecretStr("redirect-doc-token"),
                ),
            )
    finally:
        source.shutdown()
        source.server_close()
        source_thread.join(timeout=5)
        destination.shutdown()
        destination.server_close()
        destination_thread.join(timeout=5)
    assert source_authorizations == ["Bearer redirect-doc-token"]
    assert redirected_authorizations == []


def test_wrong_key_stored_secret_failure_is_sanitized_before_network(
    credential_graph: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    target_id, _, identity_id, binding_id = credential_graph
    token = "wrong-key-secret-token"

    def provider(key_byte: bytes, version: str) -> StoredSecretProvider:
        configured = Settings(
            database_url="postgresql://unused.test/database",
            credential_encryption_key=SecretStr(
                base64.urlsafe_b64encode(key_byte * 32).decode("ascii")
            ),
            credential_encryption_key_version=version,
        )
        return StoredSecretProvider(StoredSecretCipher.from_settings(configured))

    with SessionLocal() as db:
        binding = db.get(CredentialBinding, binding_id)
        provider(b"a", "stored-key").store_secret(
            db, binding, SecretStr(token)
        )
        db.commit()
    monkeypatch.setattr(
        bearer_credentials,
        "build_stored_secret_provider",
        lambda: provider(b"b", "wrong-key"),
    )
    gateway = StaticJSONNetworkGateway()
    scanner = OpenAPIScanner(Mock(), Mock(), gateway)
    scanner.policy_engine.evaluate.return_value = Mock(
        allowed=True, authorization_revision_id=200
    )
    with pytest.raises(OpenAPIExecutionBlocked) as raised:
        scanner.scan(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            source_url="https://example.test/openapi.json",
            refresh_authorization=lambda: (
                build_target(), build_revision(), [build_scope()]
            ),
            policy_decision_observer=lambda decision: None,
            credential_binding_id=binding_id,
            refresh_credential=build_openapi_credential_refresh(
                engine,
                target_id=target_id,
                credential_binding_id=binding_id,
            ),
        )
    captured = caplog.text + str(raised.value)
    assert raised.value.code == "openapi_credential_unavailable"
    assert raised.value.reason == "The selected OpenAPI credential is unavailable."
    assert raised.value.__cause__ is None
    assert token not in captured
    assert "wrong-key" not in captured
    assert "cipher" not in captured.lower()
    assert gateway.calls == 0


def test_authenticated_network_failure_output_does_not_disclose_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "network-failure-secret-token"

    class FailingGateway:
        calls = 0

        def request(self, **kwargs):
            self.calls += 1
            assert kwargs["headers"]["Authorization"] == f"Bearer {token}"
            raise NetworkGatewayError(
                code="network_request_failed",
                reason="Network request failed.",
            )

    gateway = FailingGateway()
    scanner = OpenAPIScanner(Mock(), Mock(), gateway)
    scanner.policy_engine.evaluate.return_value = Mock(
        allowed=True, authorization_revision_id=200
    )
    identity = Mock(id=10, name="docs", is_active=True, auth_type="bearer")
    with pytest.raises(OpenAPIScanError) as raised:
        scanner.scan(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            source_url="https://example.test/openapi.json",
            refresh_authorization=lambda: (
                build_target(), build_revision(), [build_scope()]
            ),
            policy_decision_observer=lambda decision: None,
            credential_binding_id=55,
            refresh_credential=lambda: build_authentication_context(
                identity, bearer_token=SecretStr(token)
            ),
        )
    captured = caplog.text + str(raised.value)
    assert captured == "network_request_failed: Network request failed."
    assert token not in captured
    assert gateway.calls == 1


def test_authenticated_parser_failure_is_atomic(
    openapi_target_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "atomic-auth-token"
    with SessionLocal() as db:
        identity = TestIdentity(
            target_id=openapi_target_id,
            name=f"atomic-docs-{uuid4()}",
            role="docs",
            auth_type="bearer",
            credentials=None,
            is_active=True,
        )
        db.add(identity)
        db.flush()
        binding = CredentialBinding(
            test_identity_id=identity.id,
            auth_type="bearer",
            source_type="stored_secret",
            is_active=True,
        )
        db.add(binding)
        db.add(Endpoint(
            target_id=openapi_target_id,
            path="/existing",
            method="GET",
            operation_id="existing",
            requires_auth=False,
            parameters=[],
            request_body=None,
            security=None,
        ))
        db.commit()
        identity_id = identity.id
        binding_id = binding.id
    monkeypatch.setattr(
        BearerCredentialService,
        "resolve_binding",
        lambda self, **kwargs: SecretStr(token),
    )
    invalid_document = gzip.compress(
        b'{"paths":{},"schema":{"$ref":"#/components/secret"}}',
        mtime=0,
    )
    gateway = HandlerNetworkGateway(lambda request: (
        pytest.fail("AuthenticationContext header missing")
        if request.headers.get("Authorization") != f"Bearer {token}"
        else Mock(
            status_code=200,
            content=invalid_document,
            headers=httpx.Headers({"Content-Encoding": "gzip"}),
        )
    ))
    monkeypatch.setattr(openapi_routes, "scanner", OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"example.test"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        gateway,
    ))
    try:
        with SessionLocal() as db, pytest.raises(HTTPException) as raised:
            openapi_routes.import_openapi(
                OpenAPIImportRequest(
                    target_id=openapi_target_id,
                    source_url="https://example.test/openapi.json",
                    credential_binding_id=binding_id,
                ),
                db,
            )
        assert raised.value.status_code == 502
        assert raised.value.detail == "openapi_references_not_supported"
        with SessionLocal() as db:
            endpoints = list(db.scalars(select(Endpoint).where(
                Endpoint.target_id == openapi_target_id
            )))
            records = list(db.scalars(select(OpenAPIImportRecord).where(
                OpenAPIImportRecord.target_id == openapi_target_id
            )))
        assert [(item.path, item.operation_id) for item in endpoints] == [
            ("/existing", "existing")
        ]
        assert records == []
    finally:
        with SessionLocal() as db:
            db.execute(delete(CredentialBinding).where(
                CredentialBinding.id == binding_id
            ))
            db.execute(delete(TestIdentity).where(TestIdentity.id == identity_id))
            db.commit()


def test_real_localhost_authenticated_gzip_import_records_binding_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "synthetic-localhost-doc-token"
    decoded = b'{"openapi":"3.1.0","paths":{"/docs":{"get":{}}}}'
    wire = gzip.compress(decoded, mtime=0)
    observed: list[tuple[str | None, str | None, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed.append((
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
    test_settings = Settings(
        database_url="postgresql://unused.test/database",
        credential_encryption_key=SecretStr(
            base64.urlsafe_b64encode(b"m" * 32).decode("ascii")
        ),
        credential_encryption_key_version="m9-04-test-key",
    )
    provider = StoredSecretProvider(
        StoredSecretCipher.from_settings(test_settings)
    )
    monkeypatch.setattr(
        bearer_credentials,
        "build_stored_secret_provider",
        lambda: provider,
    )
    with SessionLocal() as db:
        identity = TestIdentity(
            target_id=target_id,
            name="authenticated docs",
            role="docs",
            auth_type="bearer",
            credentials=None,
            is_active=True,
        )
        db.add(identity)
        db.flush()
        binding = BearerCredentialService(db=db).provision(
            identity=identity,
            token=SecretStr(token),
        )
        db.commit()
        identity_id = identity.id
        binding_id = binding.id

    monkeypatch.setattr(openapi_routes, "scanner", OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"127.0.0.1"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        NetworkGateway(controller=NetworkExecutionController()),
    ))
    try:
        with SessionLocal() as db:
            response = openapi_routes.import_openapi(
                OpenAPIImportRequest(
                    target_id=target_id,
                    source_url=source_url,
                    credential_binding_id=binding_id,
                ),
                db,
            )
        with SessionLocal() as db:
            record = db.scalar(select(OpenAPIImportRecord).where(
                OpenAPIImportRecord.id == response.import_record_id
            ))
            endpoint = db.scalar(select(Endpoint).where(
                Endpoint.target_id == target_id
            ))
            audit_records = list(db.scalars(select(SafetyDecisionRecord).where(
                SafetyDecisionRecord.target_id == target_id,
                SafetyDecisionRecord.operation == "openapi_import",
            )))
            assert record is not None
            assert audit_records
            assert record.credential_binding_id == binding_id
            assert record.document_sha256 == hashlib.sha256(wire).hexdigest()
            assert record.document_size_bytes == len(wire)
            assert record.decoded_document_sha256 == hashlib.sha256(
                decoded
            ).hexdigest()
            assert record.decoded_document_size_bytes == len(decoded)
            assert token not in repr(record)
            assert token not in repr(endpoint)
            assert token not in repr(response)
            persisted_audit_text = " ".join(
                str(value)
                for audit in audit_records
                for value in (
                    audit.stage,
                    audit.operation,
                    audit.outcome,
                    audit.code,
                    audit.reason,
                )
            )
            persisted_provenance_text = " ".join(str(value) for value in (
                record.source_url,
                record.document_sha256,
                record.content_encoding,
                record.decoded_document_sha256,
                record.credential_binding_id,
            ))
            persisted_endpoint_text = " ".join(str(value) for value in (
                endpoint.path,
                endpoint.method,
                endpoint.operation_id,
                endpoint.parameters,
                endpoint.request_body,
                endpoint.security,
            ))
            assert token not in persisted_audit_text
            assert token not in persisted_provenance_text
            assert token not in persisted_endpoint_text
            assert f"Bearer {token}" not in persisted_audit_text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        with SessionLocal() as db:
            db.execute(delete(OpenAPIImportRecord).where(
                OpenAPIImportRecord.target_id == target_id
            ))
            db.execute(delete(CredentialSecretVersion).where(
                CredentialSecretVersion.credential_binding_id == binding_id
            ))
            db.execute(delete(CredentialBinding).where(
                CredentialBinding.id == binding_id
            ))
            db.execute(delete(TestIdentity).where(TestIdentity.id == identity_id))
            db.commit()
        delete_import_target(target_id, profile_id)

    assert observed == [(
        f"Bearer {token}",
        "application/json",
        "gzip",
    )]
