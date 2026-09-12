"""Owned native PostgreSQL fixture for the isolated N1 child.

CI's TCP-only service is never forwarded into the sandbox. This fixture owns a
separate server, verifies it before imports/migrations, and exposes only its
restricted Unix socket. It neither starts nor changes an installed PG service.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import select
import signal
import socket
import subprocess


class OwnedPostgres:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.bin = Path('/usr/lib/postgresql/16/bin')
        if not (self.bin / 'initdb').is_file():
            raise RuntimeError('N1_POSTGRES16_REQUIRED')
        self.admin_password = secrets.token_hex(32)
        self.child_password = secrets.token_hex(32)
        self.admin = 'n1_migration'
        self.child = 'n1_adapter'
        self.database = 'n1_synthetic'
        self.data = self.root / 'data'
        self.socket = self.root / 'socket'
        self.socket.mkdir()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            self.port = listener.getsockname()[1]
        self.env = {'PATH': str(self.bin) + ':/usr/bin:/bin', 'LANG': 'C.UTF-8',
                    'PGPASSWORD': self.admin_password, 'PGCONNECT_TIMEOUT': '2',
                    'PGOPTIONS': '-c statement_timeout=3000'}
        self.identity = None
        self.cluster = None
        self.cleanup_failures = []

    def command(self, *args, sql=None):
        try:
            result = subprocess.run([str(self.bin / args[0]), *map(str, args[1:])],
                                    env=self.env, input=sql, capture_output=True,
                                    text=True, timeout=15)
        except subprocess.TimeoutExpired:
            raise RuntimeError('N1_OWNED_DATABASE_COMMAND_TIMEOUT') from None
        if result.returncode:
            raise RuntimeError('N1_OWNED_DATABASE_COMMAND_FAILED')
        return result.stdout

    def sql(self, sql, *, database=None):
        return self.command('psql', '-h', '127.0.0.1', '-p', self.port,
                            '-U', self.admin, '-d', database or self.database,
                            '-XAt', '-v', 'ON_ERROR_STOP=1', sql=sql)

    def verify(self, *, empty=False, maintenance=False):
        database = 'postgres' if maintenance else self.database
        identity = json.loads(self.sql("""
          SELECT json_build_object('database',current_database(),
           'user',current_user,'host',inet_server_addr(),'port',inet_server_port(),
           'data_directory',current_setting('data_directory'),
           'encoding',current_setting('server_encoding'),
           'system_identifier',(SELECT system_identifier::text FROM pg_control_system()),
           'tables',(SELECT count(*) FROM information_schema.tables WHERE table_schema='public'),
           'other_clients',(SELECT count(*) FROM pg_stat_activity
              WHERE backend_type='client backend' AND pid<>pg_backend_pid()));
        """, database=database))
        cluster, _ = self._cluster_identity()
        if (identity['database'] != database or identity['user'] != self.admin
                or identity['host'] != '127.0.0.1' or identity['port'] != self.port
                or identity['data_directory'] != str(self.data)
                or identity['encoding'] != 'UTF8' or self.cluster != cluster
                or identity['system_identifier'] != cluster[2]
                or (self.identity is not None and identity['system_identifier']
                    != self.identity['system_identifier'])):
            raise RuntimeError('N1_OWNED_DATABASE_IDENTITY_MISMATCH')
        if (not maintenance and identity['other_clients'] != 0) or (empty and identity['tables'] != 0):
            raise RuntimeError('N1_OWNED_DATABASE_NOT_EMPTY')
        return identity

    def _cluster_identity(self):
        # initdb's maintenance database exists even when createdb never ran.
        # Pin the owned directory inode and exact control-system identifier before
        # starting anything; neither a port nor a PID file establishes ownership.
        stat = self.data.stat(follow_symlinks=False)
        if self.data.is_symlink() or not self.data.is_dir():
            raise RuntimeError('N1_OWNED_DATABASE_IDENTITY_MISMATCH')
        control = dict(line.split(':', 1) for line in
                       self.command('pg_controldata', self.data).splitlines() if ':' in line)
        return ((stat.st_dev, stat.st_ino, control['Database system identifier'].strip()),
                control['Database cluster state'].strip())

    def cleanup(self):
        cluster, state = self._cluster_identity()
        if cluster != self.cluster:
            raise RuntimeError('N1_OWNED_DATABASE_IDENTITY_MISMATCH')
        pid_path = self.data / 'postmaster.pid'
        if not pid_path.exists():
            # A failed start is not proof of shutdown. Require the durable control
            # state as well. Any incomplete/uncertain startup remains unresolved.
            if state != 'shut down':
                raise RuntimeError('N1_OWNED_DATABASE_STATE_UNRESOLVED')
            return
        pid_record = pid_path.read_bytes()
        fields = pid_record.decode('ascii').splitlines()
        pid = int(fields[0])
        if fields[1] != str(self.data) or int(fields[3]) != self.port:
            raise RuntimeError('N1_OWNED_DATABASE_IDENTITY_MISMATCH')
        # Pin the process before verifying the SQL endpoint. pidfd signalling
        # cannot hit a reused PID or a subsequently substituted PID-file target.
        descriptor = os.pidfd_open(pid)
        try:
            if Path(f'/proc/{pid}/exe').resolve() != (self.bin / 'postgres').resolve():
                raise RuntimeError('N1_OWNED_DATABASE_IDENTITY_MISMATCH')
            command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
            if b'-D' not in command or command[command.index(b'-D') + 1] != os.fsencode(self.data):
                raise RuntimeError('N1_OWNED_DATABASE_IDENTITY_MISMATCH')
            self.verify(maintenance=True)
            if pid_path.read_bytes() != pid_record or self._cluster_identity()[0] != self.cluster:
                raise RuntimeError('N1_OWNED_DATABASE_IDENTITY_MISMATCH')
            signal.pidfd_send_signal(descriptor, signal.SIGINT)  # PostgreSQL fast stop.
            if not select.select([descriptor], [], [], 5)[0]:
                raise RuntimeError('N1_OWNED_DATABASE_STOP_TIMEOUT')
            if pid_path.exists() or self._cluster_identity()[1] != 'shut down':
                raise RuntimeError('N1_OWNED_DATABASE_STOP_UNCONFIRMED')
        finally:
            os.close(descriptor)

    @contextmanager
    def running(self):
        failure = None
        start_attempted = False
        try:
            password = self.root / 'migration-password'
            password.write_text(self.admin_password)
            password.chmod(0o600)
            try:
                self.command('initdb', '-D', self.data, '-U', self.admin,
                             '--auth=scram-sha-256', '--pwfile', password)
            finally:
                password.unlink(missing_ok=True)
            self.cluster, _ = self._cluster_identity()
            # No trust/peer rule or alternate privileged socket identity.
            (self.data / 'pg_hba.conf').write_text(
                f'local {self.database} {self.child} scram-sha-256\n'
                'local all all reject\n'
                f'host all {self.admin} 127.0.0.1/32 scram-sha-256\n'
                'host all all 0.0.0.0/0 reject\n'
                'host all all ::/0 reject\n')
            start_attempted = True  # Includes startup with a lost acknowledgement.
            self.command('pg_ctl', '-D', self.data, '-l', self.root / 'server.log',
                         '-o', f'-h 127.0.0.1 -p {self.port} -k {self.socket}',
                         '-t', '5', '-w', 'start')
            self.verify(maintenance=True)
            self.command('createdb', '-h', '127.0.0.1', '-p', self.port,
                         '-U', self.admin, self.database)
            self.identity = self.verify(empty=True)
            (self.root / 'identity.json').write_text(json.dumps(self.identity, indent=2))
            # Only fixed identifiers and generated hex passwords enter this SQL.
            self.sql(f"""
              CREATE ROLE {self.child} LOGIN PASSWORD '{self.child_password}'
                NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
              REVOKE ALL ON DATABASE {self.database} FROM PUBLIC;
              GRANT CONNECT ON DATABASE {self.database} TO {self.child};
              REVOKE CREATE ON SCHEMA public FROM PUBLIC;
              GRANT USAGE ON SCHEMA public TO {self.child};
            """)
            yield self
        except BaseException as error:
            failure = error
            raise
        finally:
            if start_attempted:
                try:
                    self.cleanup()
                except Exception:
                    # Never mask the primary failure or expose raw SQL/errors,
                    # passwords or connection strings. Retain an actionable owned
                    # resource location; unverifiable instances are not stopped.
                    report = 'N1_OWNED_DATABASE_CLEANUP_UNRESOLVED: ' + str(self.root)
                    self.cleanup_failures.append(report)
                    if failure is not None:
                        failure.add_note(report)
                    else:
                        raise RuntimeError(report) from None

    def child_config(self):
        return {'host': '/database', 'port': self.port, 'dbname': self.database,
                'user': self.child, 'password': self.child_password,
                'connect_timeout': 2}
