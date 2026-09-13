"""Scoped TEST database connections while application DNS remains prohibited.

The caller's harness must independently verify ownership before importing the
application. This fixture pins that configured endpoint and its server identity;
it is not permission to use an arbitrary database or resolve application hosts.
"""
from contextlib import contextmanager
from ipaddress import ip_address
from types import SimpleNamespace

from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import event
from sqlalchemy.engine import Engine


IDENTITY_SQL = """SELECT json_build_object('database',current_database(),
 'user',current_user,'host',inet_server_addr(),'port',inet_server_port(),
 'data_directory',current_setting('data_directory'),
 'system_identifier',(SELECT system_identifier::text FROM pg_control_system())), pg_backend_pid()"""
ENDPOINT = ('host', 'port', 'dbname', 'user', 'password')


def _parameters(args, kwargs):
    assert len(args) <= 1, 'unqualified TEST database connection'
    params = conninfo_to_dict(args[0] if args else '', **kwargs)
    assert all(params.get(key) for key in ENDPOINT), 'implicit TEST database connection'
    assert not params.get('service'), 'unqualified TEST database service'
    return params


def _identity(connection):
    with connection.cursor() as cursor:
        cursor.execute(IDENTITY_SQL)
        return cursor.fetchone()


@contextmanager
def trusted_test_connections(engine):
    assert (engine.dialect.name, engine.dialect.driver) == ('postgresql', 'psycopg')
    args, kwargs = engine.dialect.create_connect_args(engine.url)
    configured = _parameters(args, kwargs)
    assert configured['host'] in ('localhost', '127.0.0.1', '::1'), 'nonlocal TEST database target'
    # This occurs before the capability fixture installs its global DNS denial.
    # Server-side container address/port are distinct from the client endpoint.
    with engine.connect() as sql:
        connection = sql.connection.driver_connection
        identity, _ = _identity(connection)
        address = connection.info.hostaddr
        assert (connection.info.host == configured['host']
                and str(connection.info.port) == str(configured['port'])), 'TEST client endpoint changed'
        assert address and ip_address(address).is_loopback, 'nonlocal TEST database connection'
        assert (identity['database'], identity['user']) == (configured['dbname'], configured['user'])
        assert not configured.get('hostaddr') or configured['hostaddr'] == address
    evidence = SimpleNamespace(identity=dict(identity), hostaddr=address, connections=[])

    def connect(dialect, record, args, kwargs):
        if (dialect.name, dialect.driver) != ('postgresql', 'psycopg'):
            return None
        params = _parameters(args, kwargs)
        assert all(params[key] == configured[key] for key in ENDPOINT), 'unqualified TEST database connection'
        assert not params.get('hostaddr') or params['hostaddr'] == address, 'unqualified TEST database address'
        # No global resolver restoration, hostname exemption or call-stack
        # inference. Only this exact DBAPI connection gets the pinned address.
        connection = dialect.loaded_dbapi.connect(*args, **{**kwargs, 'hostaddr': address})
        try:
            actual, pid = _identity(connection)
            assert actual == identity, 'TEST database identity changed'
            connection.rollback()
        except BaseException:
            connection.close()
            raise
        evidence.connections.append({'identity': actual, 'pid': pid})
        return connection

    # Class scope includes already-created app engines, Guard/NullPool engines
    # and engines Alembic creates later while the capability denial is active.
    event.listen(Engine, 'do_connect', connect)
    try:
        yield evidence
    finally:
        event.remove(Engine, 'do_connect', connect)
