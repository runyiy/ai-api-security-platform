from collections.abc import Iterator
import base64
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, select

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
from app.db.models.target import Target
from app.db.models.test_identity import TestIdentity
from app.db.session import SessionLocal, engine
from app.executors.rate_limit import InMemoryRateLimiter
from app.network_safety.controller import NetworkExecutionController
from app.network_safety.gateway import NetworkGateway
from app.policies.scope_policy import ScopePolicyEngine
from app.scanners.openapi import OpenAPIExecutionBlocked, OpenAPIScanner
from app.schemas.openapi import OpenAPIImportRequest
from app.services.openapi_credentials import (
    OpenAPICredentialError,
    build_openapi_credential_refresh,
)
from tests.network_gateway_fakes import StaticJSONNetworkGateway
from tests.scanners.test_openapi import build_revision, build_scope, build_target
from tests.scanners.test_openapi_import_concurrency import (
    create_import_target,
    delete_import_target,
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
    with SessionLocal() as db:
        if deactivate == "binding":
            db.get(CredentialBinding, binding_id).is_active = False
        else:
            db.get(TestIdentity, identity_id).is_active = False
        db.commit()

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
            assert record is not None
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
