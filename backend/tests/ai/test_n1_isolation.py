"""Adversarial OS/SQL prerequisite evidence, separate from N1 protocol tests."""
import json
import os
from pathlib import Path
import socket
import subprocess
import threading

import pytest

from tests.ai.n1_postgres import OwnedPostgres
from tests.ai.n1_sandbox import IsolationUnavailable, Sandbox


def test_isolated_child_database_and_ownership_boundaries(tmp_path):
    private = tmp_path / 'parent-only'
    private.mkdir()
    journal = private / 'journal'
    witness = private / 'witness'
    journal.write_text('synthetic-parent-journal')
    witness.write_text('synthetic-witness')
    witness.chmod(0o400)
    code, call = tmp_path / 'code', tmp_path / 'call'
    code.mkdir()
    call.mkdir()
    database_root = tmp_path / 'postgres'
    database_root.mkdir()
    with OwnedPostgres(database_root).running() as database:
        privileged_socket = subprocess.run(
            [str(database.bin / 'psql'), '-XAt', '-h', str(database.socket),
             '-p', str(database.port), '-U', database.admin, '-d', database.database,
             '-c', 'SELECT current_user'], env=database.env,
            capture_output=True, text=True, timeout=3)
        assert privileged_socket.returncode != 0
        assert 'pg_hba.conf rejects connection' in privileged_socket.stderr
        database.sql('CREATE TABLE n1_probe (id integer PRIMARY KEY, n integer NOT NULL CHECK(n>=0));'
                     'INSERT INTO n1_probe VALUES (1,0);'
                     'GRANT SELECT, UPDATE ON n1_probe TO n1_adapter;')
        settings = {'db': database.child_config(), 'admin': database.admin,
                    'journal': str(journal), 'witness': str(witness),
                    'control': str(private / 'control.sock'),
                    'parent_pid': os.getpid(), 'host_port': database.port,
                    'parent_namespaces': {n: os.readlink('/proc/self/ns/' + n)
                                          for n in ('mnt', 'user', 'pid', 'net', 'ipc')}}
        (code / 'fixture.json').write_text(json.dumps(settings))
        (code / 'child.py').write_text(CHILD)
        inherited = os.open(journal, os.O_RDWR)
        os.set_inheritable(inherited, True)
        original = journal.read_bytes(), witness.read_bytes()
        # The control socket is intentionally outside every child mount.
        with socket.socket(socket.AF_UNIX) as control, socket.socket(socket.AF_UNIX) as endpoint:
            control.bind(str(private / 'control.sock'))
            control.listen(1)
            control.settimeout(.05)
            endpoint.bind(str(call / 'probe.sock'))
            endpoint.listen(1)
            endpoint.settimeout(5)
            observed = []

            def receive():
                connection, _ = endpoint.accept()
                with connection:
                    connection.settimeout(2)
                    for _ in range(5):
                        value = connection.recv(65)
                        observed.append(value)
                        connection.sendall(b'FIXTURE_ACK' if value == b'bounded_synthetic_probe' else b'DENIED')

            reader = threading.Thread(target=receive)
            reader.start()
            try:
                result = json.loads(Sandbox(code, database.socket, call).run(timeout=10))
            finally:
                os.close(inherited)
                reader.join(timeout=6)
            assert not reader.is_alive()
            assert observed == [b'mint_permit', b'WRITE_ACCEPTED', b'close_ack',
                                b'reopen', b'bounded_synthetic_probe']
            with pytest.raises(TimeoutError):
                control.accept()
        assert (journal.read_bytes(), witness.read_bytes()) == original
        assert result['db_positive'] == ['n1_adapter', 1]
        assert result['ipc_positive'] is True
        assert result['sql_rejected'] == 12
        assert result['privileged_reconnections_rejected'] == 3
        assert result['private_paths_absent'] is True
        assert result['parent_fd_absent'] is True
        assert result['parent_process_inaccessible'] is True
        assert result['private_access_attempts_denied'] == 8
        assert result['control_inaccessible'] is True
        assert result['unauthorized_ipc_rejected'] == 4
        assert result['nested_userns_denied'] is True
        assert result['namespaces_isolated'] is True
        assert result['privileges_empty'] is True
        assert result['network_isolated'] is True
        assert result['read_only_code'] is True
        assert result['empty_tmp'] is True
        assert set(result['environment']) <= {'LANG', 'PYTHONHOME', 'PYTHONDONTWRITEBYTECODE', 'PWD', 'LD_LIBRARY_PATH'}
        assert result['environment']['LD_LIBRARY_PATH'] == '/runtime/native'
        assert database.sql('SELECT n FROM n1_probe;').strip() == '1'
        assert database.sql("SELECT count(*) FROM pg_roles WHERE rolname='n1_forbidden';").strip() == '0'
        assert not (private / 'program-output').exists()


CHILD = r'''
import ctypes, errno, json, os, pathlib, socket, sys
sys.path.insert(0, '/packages')
import psycopg
p = json.loads(pathlib.Path('/code/fixture.json').read_text())
result = {}
result['environment'] = dict(os.environ)
status = dict(line.split(':', 1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line)
result['privileges_empty'] = all(int(status[k].strip(), 16) == 0 for k in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')) and status['NoNewPrivs'].strip() == '1'
result['namespaces_isolated'] = all(os.readlink('/proc/self/ns/' + n) != value for n, value in p['parent_namespaces'].items())
parent_proc = pathlib.Path('/proc/' + str(p['parent_pid']))
# PIDs can be equal across namespaces. A visible PID with that number must not
# resolve to the host parent, its namespace, root, memory or authority files.
result['parent_process_inaccessible'] = not parent_proc.exists() or (
    os.readlink(parent_proc / 'ns/pid') != p['parent_namespaces']['pid']
    and not (parent_proc / ('root' + p['journal'])).exists())
result['private_access_attempts_denied'] = 0
for filename in (p['journal'], p['witness'], '/proc/1/root' + p['journal'], '/proc/1/root' + p['witness']):
    for mode in ('rb', 'r+b'):
        try:
            with open(filename, mode) as f:
                if mode == 'r+b': f.write(b'forged-synthetic-evidence')
                else: f.read(64)
        except OSError:
            result['private_access_attempts_denied'] += 1
result['control_inaccessible'] = False
with socket.socket(socket.AF_UNIX) as s:
    try: s.connect(p['control'])
    except OSError: result['control_inaccessible'] = True
libc = ctypes.CDLL(None, use_errno=True)
result['nested_userns_denied'] = libc.unshare(0x10000000) == -1 and ctypes.get_errno() in (errno.EPERM, errno.ENOSPC)
result['private_paths_absent'] = all(not pathlib.Path(v).exists() for v in (p['journal'], p['witness'], '/home', '/root', '/run', '/etc'))
result['parent_fd_absent'] = True
for name in os.listdir('/proc/self/fd'):
    try:
        if os.readlink('/proc/self/fd/' + name) in (p['journal'], p['witness']):
            result['parent_fd_absent'] = False
    except FileNotFoundError:
        pass
result['read_only_code'] = False
try:
    pathlib.Path('/code/forged').write_text('synthetic')
except OSError:
    result['read_only_code'] = True
result['empty_tmp'] = list(pathlib.Path('/tmp').iterdir()) == []
pathlib.Path('/tmp/owned').write_text('synthetic')
result['network_isolated'] = False
with socket.socket() as s:
    s.settimeout(.2)
    try:
        s.connect(('127.0.0.1', p['host_port']))
    except OSError:
        result['network_isolated'] = True
with psycopg.connect(**p['db'], autocommit=True) as db:
    db.execute('UPDATE n1_probe SET n=n+1 WHERE id=1')
    result['db_positive'] = list(db.execute('SELECT current_user,n FROM n1_probe').fetchone())
    forbidden = [
        'SET ROLE n1_migration', 'SET SESSION AUTHORIZATION n1_migration',
        'CREATE ROLE n1_forbidden SUPERUSER', 'ALTER ROLE n1_adapter SUPERUSER',
        'GRANT pg_read_server_files TO n1_adapter',
        'GRANT pg_write_server_files TO n1_adapter',
        'GRANT pg_execute_server_program TO n1_adapter',
        "SELECT pg_read_file('" + p['journal'] + "')",
        "COPY n1_probe TO '" + p['journal'] + "'",
        "COPY n1_probe TO PROGRAM 'touch " + str(pathlib.Path(p['journal']).parent / 'program-output') + "'",
        'CREATE TEMP TABLE unauthorized (id int)', 'CREATE SCHEMA unauthorized',
    ]
    result['sql_rejected'] = 0
    for statement in forbidden:
        try:
            db.execute(statement)
        except psycopg.errors.InsufficientPrivilege:
            result['sql_rejected'] += 1
result['privileged_reconnections_rejected'] = 0
for identity in (p['admin'], 'postgres', 'n1_unknown'):
    args = dict(p['db'], user=identity)
    try:
        psycopg.connect(**args).close()
    except psycopg.OperationalError:
        result['privileged_reconnections_rejected'] += 1
with socket.socket(socket.AF_UNIX) as s:
    s.settimeout(2)
    s.connect('/call/probe.sock')
    result['unauthorized_ipc_rejected'] = 0
    for value in (b'mint_permit', b'WRITE_ACCEPTED', b'close_ack', b'reopen'):
        s.sendall(value)
        result['unauthorized_ipc_rejected'] += s.recv(32) == b'DENIED'
    s.sendall(b'bounded_synthetic_probe')
    result['ipc_positive'] = s.recv(32) == b'FIXTURE_ACK'
print(json.dumps(result))
'''


def test_required_isolation_has_no_fallback(tmp_path, monkeypatch):
    directories = [tmp_path / name for name in ('code', 'database', 'call')]
    for directory in directories:
        directory.mkdir()
    with pytest.raises(IsolationUnavailable, match='N1_MOUNT_OVERLAP'):
        Sandbox(directories[0], directories[0], directories[2]).run()
    nested = directories[0] / 'nested'
    nested.mkdir()
    with pytest.raises(IsolationUnavailable, match='N1_MOUNT_OVERLAP'):
        Sandbox(directories[0], nested, directories[2]).run()
    monkeypatch.setattr('tests.ai.n1_sandbox.shutil.which', lambda _: None)
    with pytest.raises(IsolationUnavailable, match='N1_BUBBLEWRAP_REQUIRED'):
        Sandbox(*directories).run()


@pytest.mark.parametrize('name', ['.env', '.env.test', '.git', '.codex', 'symlink'])
def test_code_projection_excludes_configuration_and_links(tmp_path, name):
    directories = [tmp_path / v for v in ('code', 'database', 'call')]
    for directory in directories:
        directory.mkdir()
    if name == 'symlink':
        (directories[0] / name).symlink_to(directories[1])
    else:
        (directories[0] / name).write_text('synthetic-excluded-canary')
    with pytest.raises(IsolationUnavailable, match='N1_CODE_PROJECTION_DENIED'):
        Sandbox(*directories).run()


@pytest.mark.parametrize('body, code', [
    ('while True: pass', 'N1_CHILD_TIMEOUT'),
    ('print("x" * 9000)', 'N1_CHILD_OUTPUT_LIMIT'),
    ('raise RuntimeError("synthetic-secret-must-not-escape")', 'N1_CHILD_FAILED'),
])
def test_child_failures_are_bounded_and_content_free(tmp_path, body, code):
    directories = [tmp_path / name for name in ('code', 'database', 'call')]
    for directory in directories:
        directory.mkdir()
    (directories[0] / 'child.py').write_text(body)
    with pytest.raises(IsolationUnavailable, match='^' + code + '$'):
        Sandbox(*directories).run(timeout=1)
    if code == 'N1_CHILD_TIMEOUT':
        # EOF is not process completion. This separately exercises the bounded
        # wait/reaping path after a child closes both captured output streams.
        (directories[0] / 'child.py').write_text('import os\nos.close(1)\nos.close(2)\nwhile True: pass\n')
        with pytest.raises(IsolationUnavailable, match='^N1_CHILD_TIMEOUT$'):
            Sandbox(*directories).run(timeout=1)
