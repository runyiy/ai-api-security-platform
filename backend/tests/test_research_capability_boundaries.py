"""Owned PostgreSQL reconnects without granting application capabilities.

Run this file against both the verified numeric and hostname TEST endpoints.
The outer harness owns/identifies the server before any application import.
"""
import importlib
import json
import socket
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, engine_from_config, text
from sqlalchemy.pool import NullPool

from app.db.session import engine
from app.ai.w2.guard import Guard
from tests import research_observation_fixtures as fixtures
from tests.trusted_database import IDENTITY_SQL, trusted_test_connections


def identity(connection):
    row = connection.execute(text(IDENTITY_SQL)).one()
    return {'identity': row[0], 'pid': row[1]}


def test_owned_database_reconnects_with_global_application_dns_denied(tmp_path):
    denied = Mock(side_effect=AssertionError('application DNS remains prohibited'))
    observations = []
    created = []
    guard = None
    try:
        with pytest.MonkeyPatch.context() as patch:
            with trusted_test_connections(engine) as trusted:
                patch.setattr(socket, 'getaddrinfo', denied)
                # Dispose the connection used to establish initial trust. Every
                # success below therefore needs a fresh physical connection.
                for phase in ('fresh', 'after_dispose'):
                    engine.dispose()
                    with engine.connect() as db:
                        observations.append(dict(phase=phase, **identity(db)))
                options = '-c statement_timeout=1750 -c lock_timeout=1250 -c application_name=capability_boundary_probe'
                separate = create_engine(engine.url, poolclass=NullPool,
                    connect_args={'connect_timeout': 3, 'options': options})
                created.append(separate)
                with separate.connect() as db:
                    observations.append(dict(phase='new_nullpool', **identity(db)))
                    assert db.scalar(text('SHOW statement_timeout')) == '1750ms'
                    assert db.scalar(text('SHOW lock_timeout')) == '1250ms'
                    assert db.scalar(text('SHOW application_name')) == 'capability_boundary_probe'
                # This is the factory used by alembic/env.py. The actual
                # migration roundtrip is also exercised by its existing test.
                migration_engine = engine_from_config(
                    {'sqlalchemy.url': engine.url.render_as_string(hide_password=False)},
                    prefix='sqlalchemy.', poolclass=NullPool)
                created.append(migration_engine)
                with migration_engine.connect() as db:
                    observations.append(dict(phase='migration_engine_factory', **identity(db)))
                guard = Guard(engine.url)
                with guard.engine.connect() as db:
                    observations.append(dict(phase='guard_engine', **identity(db)))
                assert all(item['identity'] == trusted.identity for item in observations)
                pids = {item['pid'] for item in observations}
                assert len(pids) == len(observations) == 5
                assert pids == {item['pid'] for item in trusted.connections}
                denied.assert_not_called()
            # Keep global DNS blocked while proving the scoped listener is gone.
            # Numeric access targets the same successfully observed local peer.
            outside = create_engine(engine.url.set(host=trusted.hostaddr), poolclass=NullPool)
            created.append(outside)
            calls = []
            original = outside.dialect.loaded_dbapi.connect

            def connect(*args, **kwargs):
                calls.append({'host': kwargs.get('host'), 'hostaddr_supplied': 'hostaddr' in kwargs})
                return original(*args, **kwargs)

            patch.setattr(outside.dialect.loaded_dbapi, 'connect', connect)
            with outside.connect() as db:
                after = identity(db)
            assert after['identity'] == trusted.identity and after['pid'] not in pids
            assert calls == [{'host': trusted.hostaddr, 'hostaddr_supplied': False}]
            assert len(trusted.connections) == 5
            denied.assert_not_called()
        (tmp_path/'database-capability-evidence.json').write_text(json.dumps(dict(
            configured_host=engine.url.host, configured_port=engine.url.port,
            pinned_hostaddr=trusted.hostaddr, server_identity=trusted.identity,
            physical_connections=observations, listener_records=trusted.connections,
            after_scope_connection=after, after_scope_connect_arguments=calls,
            application_dns_calls=denied.call_count), indent=2))
    finally:
        for item in created:
            item.dispose()
        if guard is not None:
            guard.close()
        engine.dispose()


@pytest.mark.parametrize('field', ['host', 'port', 'dbname', 'user', 'password', 'hostaddr', 'service'])
def test_scoped_database_access_rejects_connection_overrides_before_connect(field):
    url = engine.url
    if field == 'host':
        changed = url.set(host='unrelated.invalid')
    elif field == 'port':
        changed = url.set(port=1 if url.port != 1 else 2)
    elif field == 'dbname':
        changed = url.set(database=url.database+'_mismatch')
    elif field == 'user':
        changed = url.set(username=url.username+'_mismatch')
    elif field == 'password':
        changed = url.set(password=url.password+'_mismatch')
    else:
        changed = url.update_query_dict({field: '192.0.2.1' if field == 'hostaddr' else 'unowned_service'})
    attempted = []

    def forbidden_connect(*args, **kwargs):
        attempted.append(True)
        raise AssertionError('DBAPI connection must not be attempted')

    with trusted_test_connections(engine):
        other = create_engine(changed, poolclass=NullPool)
        try:
            with pytest.MonkeyPatch.context() as patch:
                dns = Mock(side_effect=AssertionError('application DNS remains prohibited'))
                patch.setattr(socket, 'getaddrinfo', dns)
                patch.setattr(other.dialect.loaded_dbapi, 'connect', forbidden_connect)
                with pytest.raises(AssertionError, match='unqualified TEST database'):
                    other.connect()
                assert attempted == []
                dns.assert_not_called()
        finally:
            other.dispose()


def test_bootstrap_rejects_creator_that_overrides_declared_client_endpoint():
    # Both aliases refer to the same already verified disposable TEST server;
    # the creator connects only to the unchanged owned URL. Its physical
    # connection must not establish trust for a differently declared client.
    alias = '127.0.0.1' if engine.url.host == 'localhost' else 'localhost'
    args, kwargs = engine.dialect.create_connect_args(engine.url)
    shadow = create_engine(engine.url.set(host=alias), poolclass=NullPool,
        creator=lambda: engine.dialect.loaded_dbapi.connect(*args, **kwargs))
    try:
        with pytest.raises(AssertionError, match='TEST client endpoint changed'):
            with trusted_test_connections(shadow):
                pytest.fail('creator mismatch must not establish trusted database scope')
    finally:
        shadow.dispose()


DENIED_METHODS = (
    ('app.credentials.bearer', 'BearerCredentialService', 'resolve'),
    ('app.credentials.bearer', 'BearerCredentialService', 'resolve_binding'),
    ('app.ai.mock_provider', 'MockAIProvider', 'analyze'),
    ('app.services.plan_execution', 'PlanExecutionService', 'execute'),
    ('app.network_safety.gateway', 'NetworkGateway', 'request'),
)


@pytest.mark.parametrize('capability', ['dns_exact_database', 'dns_other_port', 'dns_unrelated', *range(len(DENIED_METHODS))])
def test_zero_capabilities_still_rejects_and_accounts_for_forbidden_attempts(capability):
    original_dns = socket.getaddrinfo
    with pytest.MonkeyPatch.context() as patch:
        # Importing this fixture into module globals would make it autouse and
        # intentionally failing calls would poison another teardown. Drive its
        # actual generator directly and expect both denial and teardown audit.
        fixture = fixtures.zero_capabilities.__wrapped__(patch)
        next(fixture)
        try:
            with pytest.raises(AssertionError, match='observation crossed capability boundary'):
                if capability == 'dns_exact_database':
                    socket.getaddrinfo(engine.url.host, engine.url.port, proto=socket.IPPROTO_TCP, type=socket.SOCK_STREAM)
                elif capability == 'dns_other_port':
                    socket.getaddrinfo(engine.url.host, 1 if engine.url.port != 1 else 2)
                elif capability == 'dns_unrelated':
                    socket.getaddrinfo('unrelated.invalid', 443)
                else:
                    module, owner, method = DENIED_METHODS[capability]
                    getattr(getattr(importlib.import_module(module), owner), method)(None)
            with pytest.raises(AssertionError, match='Expected .* to not have been called'):
                next(fixture)
        finally:
            fixture.close()
    assert socket.getaddrinfo is original_dns
