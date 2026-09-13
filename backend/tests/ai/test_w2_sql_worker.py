"""Client loopback restrictions and independently pinned server identity.

The server may report a different address/port behind the runner's local
forwarder. Every positive and identity-negative probe uses an explicitly owned
TEST PostgreSQL instance; malformed targets are rejected without connecting.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import selectors
import socket
import subprocess
import sys
from threading import Thread
import time

import pytest

from tests.ai.n1_postgres import OwnedPostgres
from tests.ai.w2_sql_worker import IDENTITY_SQL


WORKER = Path(__file__).with_name('w2_sql_worker.py')
PROBE = """
import json, runpy, sys
data = json.loads(sys.stdin.read(65536))
module = runpy.run_path(sys.argv[1])
attempts = []
if data.get('deny_connect'):
    import psycopg
    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError('unexpected connection attempt')
    psycopg.connect = forbidden
try:
    module['verify_database_identity'](data['url'], data['identity'])
    ok, error = True, None
except Exception as failure:
    ok, error = False, type(failure).__name__
print(json.dumps({'ok': ok, 'error': error, 'connect_attempts': len(attempts),
    'application_imports': sorted(name for name in sys.modules if name == 'app' or name.startswith('app.'))}))
"""


def probe(root, url, identity, *, deny_connect=False):
    # Only generated TEST credentials enter stdin; neither argv nor diagnostics
    # contains a DSN or an exception message. The owned cwd has no operator .env.
    result = subprocess.run([sys.executable, '-I', '-c', PROBE, str(WORKER)],
        cwd=root, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'},
        input=json.dumps(dict(url=url, identity=identity, deny_connect=deny_connect)),
        capture_output=True, text=True, timeout=10, close_fds=True)
    assert result.returncode == 0
    assert not result.stderr and len(result.stdout) <= 4096
    report = json.loads(result.stdout)
    assert report['application_imports'] == []
    return report


@pytest.fixture(scope='module')
def owned_forwarded_postgres(tmp_path_factory):
    root = tmp_path_factory.mktemp('w2-sql-target-pg')
    database = OwnedPostgres(root)
    original = database.command

    def command(*arguments, **kwargs):
        arguments = list(arguments)
        if arguments[0] == 'pg_ctl' and 'start' in arguments:
            index = arguments.index('-o')+1
            assert arguments[index].count('-h 127.0.0.1 ') == 1
            arguments[index] = arguments[index].replace('-h 127.0.0.1 ', '-h 127.0.0.1,127.0.0.2 ', 1)
        return original(*arguments, **kwargs)

    # This instance alone gains the second loopback listener. The shared N1
    # fixture and its ownership, HBA and bounded shutdown checks are unchanged.
    database.command = command
    try:
        with database.running():
            original_identity = database.verify(empty=True)
            identity = json.loads(database.command('psql', '-h', '127.0.0.2', '-p', database.port,
                '-U', database.admin, '-d', database.database, '-XAt', '-v', 'ON_ERROR_STOP=1', sql=IDENTITY_SQL))
            assert identity['host'] == '127.0.0.2' and identity['port'] == database.port
            assert identity['system_identifier'] == original_identity['system_identifier']
            assert identity['data_directory'] == str(database.data)
            assert identity['database'] == database.database and identity['user'] == database.admin
            yield database, identity
            database.verify(empty=True)
    finally:
        cluster, state, error = None, None, None
        try:
            if database.cluster is not None:
                cluster, state = database._cluster_identity()
        except Exception as failure:
            error = type(failure).__name__
        stopped = (cluster is not None and cluster == database.cluster and state == 'shut down'
            and not database.cleanup_failures and not (database.data/'postmaster.pid').exists())
        (root/'cleanup-evidence.json').write_text(json.dumps(dict(owned_directory=str(root),
            system_identifier=cluster[2] if cluster is not None else None, cluster_state=state,
            owned_postgres_stopped=stopped, cleanup_failures=database.cleanup_failures,
            verification_error=error), indent=2))
    assert stopped


@contextmanager
def loopback_forwarder(database):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(10)
    evidence = {'listener': list(listener.getsockname())}
    failures = []
    accepted = []

    def forward():
        try:
            with listener.accept()[0] as incoming, socket.create_connection(('127.0.0.2', database.port), timeout=3) as outgoing:
                accepted.append(True)
                evidence.update(accepted_peer=list(incoming.getpeername()), upstream_peer=list(outgoing.getpeername()))
                incoming.settimeout(3)
                outgoing.settimeout(3)
                with selectors.DefaultSelector() as selector:
                    selector.register(incoming, selectors.EVENT_READ, outgoing)
                    selector.register(outgoing, selectors.EVENT_READ, incoming)
                    end = time.monotonic()+10
                    while time.monotonic() < end:
                        for key, _ in selector.select(.25):
                            data = key.fileobj.recv(65536)
                            if not data:
                                return
                            key.data.sendall(data)
                    raise TimeoutError('owned loopback relay deadline')
        except BaseException as error:
            failures.append(type(error).__name__)

    thread = Thread(target=forward, daemon=True)
    thread.start()
    try:
        yield evidence
    finally:
        listener.close()
        thread.join(3)
        assert not thread.is_alive()
        assert not failures and len(accepted) == 1
        evidence.update(connection_count=len(accepted), relay_stopped=True)


def connection_url(database, host, port, *, password=None):
    return (f'postgresql+psycopg://{database.admin}:{password or database.admin_password}'
            f'@{host}:{port}/{database.database}')


def retain_forwarding_evidence(root, database, identity, expected, host, relay, report):
    (root/'forwarding-evidence.json').write_text(json.dumps(dict(
        configured_client_host=host, relay=relay, independently_verified_server_identity=identity,
        expected_server_identity=expected, worker_report=report,
        owned_postgres_cleanup_evidence=str(database.root/'cleanup-evidence.json')), indent=2))


@pytest.mark.parametrize('host', ['localhost', '127.0.0.1'])
def test_local_client_forwarding_preserves_full_server_identity(owned_forwarded_postgres, tmp_path, host):
    database, identity = owned_forwarded_postgres
    with loopback_forwarder(database) as relay:
        port = relay['listener'][1]
        assert port != identity['port'] and identity['host'] != host
        report = probe(tmp_path, connection_url(database, host, port), identity)
    retain_forwarding_evidence(tmp_path, database, identity, identity, host, relay, report)
    assert report['ok'] is True and report['error'] is None


@pytest.mark.parametrize('field', ['database', 'user', 'host', 'port', 'data_directory', 'system_identifier'])
def test_forwarding_never_relaxes_exact_server_identity(owned_forwarded_postgres, tmp_path, field):
    database, identity = owned_forwarded_postgres
    expected = dict(identity)
    expected[field] = identity[field]+1 if field == 'port' else 'mismatched_owned_test_identity'
    with loopback_forwarder(database) as relay:
        port = relay['listener'][1]
        report = probe(tmp_path, connection_url(database, '127.0.0.1', port), expected)
    retain_forwarding_evidence(tmp_path, database, identity, expected, '127.0.0.1', relay, report)
    assert report['ok'] is False and report['error'] == 'AssertionError'


def test_forwarding_does_not_bypass_database_authentication(owned_forwarded_postgres, tmp_path):
    database, identity = owned_forwarded_postgres
    wrong_password = ('0' if database.admin_password[0] != '0' else '1')+database.admin_password[1:]
    with loopback_forwarder(database) as relay:
        port = relay['listener'][1]
        report = probe(tmp_path, connection_url(database, '127.0.0.1', port, password=wrong_password), identity)
    retain_forwarding_evidence(tmp_path, database, identity, identity, '127.0.0.1', relay, report)
    assert report['ok'] is False and report['error'] == 'OperationalError'


@pytest.mark.parametrize('url', [
    '\x00postgresql+psycopg://test:test@localhost:5432/test',
    ' postgresql+psycopg://test:test@localhost:5432/test',
    'postgresql+psycopg://test:test@local\nhost:5432/test',
    'PostgreSQL+psycopg://test:test@localhost:5432/test',
    'postgresql+psycopg://test:test@192.0.2.1:5432/test',
    'postgresql+psycopg://test:test@localhost.example:5432/test',
    'postgresql+psycopg://test:test@localhost,192.0.2.1:5432/test',
    'postgresql+psycopg://test:test@localhost:5432/test?host=192.0.2.1',
    'postgresql+psycopg://test:test@localhost:5432/test?hostaddr=192.0.2.1',
    'postgresql+psycopg://test:test@localhost:5432/test?service=operator',
    'postgresql+psycopg://test:test@localhost:5432/test?passfile=/operator/passwords',
    'postgresql+psycopg://test:test@localhost:5432/test?host=localhost&host=192.0.2.1',
    'postgresql+psycopg://test:test@localhost:5432/test?%68ost=192.0.2.1',
    'postgresql+psycopg://test:test@localhost:5432/test#fragment',
    'postgresql://test:test@localhost:5432/test',
    'postgresql+psycopg://test:test@localhost/test',
    'postgresql+psycopg://test:test@/test',
    'postgresql+psycopg://localhost:5432/test',
    'postgresql+psycopg://test@localhost:5432/test',
    'postgresql+psycopg://test:test@localhost:5432/',
    'postgresql+psycopg://test:test@localhost:0/test',
    'postgresql+psycopg://test:test@localhost:65536/test',
    'postgresql+psycopg://test:test@localhost:bad/test',
])
def test_invalid_client_targets_and_dsn_overrides_reject_before_connect(tmp_path, url):
    report = probe(tmp_path, url, {}, deny_connect=True)
    assert report['ok'] is False and report['connect_attempts'] == 0


def test_operator_env_file_rejects_before_connect(tmp_path):
    (tmp_path/'.env').write_text('SYNTHETIC_OPERATOR_SETTING=must_not_be_read\n')
    report = probe(tmp_path, 'postgresql+psycopg://test:test@127.0.0.1:5432/test', {}, deny_connect=True)
    assert report['ok'] is False and report['connect_attempts'] == 0
