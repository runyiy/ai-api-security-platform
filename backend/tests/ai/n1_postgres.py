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
                    'PGPASSWORD': self.admin_password}
        self.identity = None

    def command(self, *args, sql=None):
        result = subprocess.run([str(self.bin / args[0]), *map(str, args[1:])],
                                env=self.env, input=sql, capture_output=True,
                                text=True, timeout=15)
        if result.returncode:
            raise RuntimeError('N1_OWNED_DATABASE_COMMAND_FAILED')
        return result.stdout

    def sql(self, sql):
        return self.command('psql', '-h', '127.0.0.1', '-p', self.port,
                            '-U', self.admin, '-d', self.database,
                            '-XAt', '-v', 'ON_ERROR_STOP=1', sql=sql)

    def verify(self, *, empty=False):
        identity = json.loads(self.sql("""
          SELECT json_build_object('database',current_database(),
           'user',current_user,'host',inet_server_addr(),'port',inet_server_port(),
           'data_directory',current_setting('data_directory'),
           'encoding',current_setting('server_encoding'),
           'system_identifier',(SELECT system_identifier::text FROM pg_control_system()),
           'tables',(SELECT count(*) FROM information_schema.tables WHERE table_schema='public'),
           'other_clients',(SELECT count(*) FROM pg_stat_activity
              WHERE backend_type='client backend' AND pid<>pg_backend_pid()));
        """))
        assert identity['database'] == self.database and identity['user'] == self.admin
        assert identity['host'] == '127.0.0.1' and identity['port'] == self.port
        assert identity['data_directory'] == str(self.data)
        assert identity['encoding'] == 'UTF8' and identity['other_clients'] == 0
        if empty:
            assert identity['tables'] == 0
        assert identity['system_identifier'] in self.command('pg_controldata', self.data)
        if self.identity is not None:
            assert identity['system_identifier'] == self.identity['system_identifier']
        return identity

    @contextmanager
    def running(self):
        password = self.root / 'migration-password'
        password.write_text(self.admin_password)
        password.chmod(0o600)
        try:
            self.command('initdb', '-D', self.data, '-U', self.admin,
                         '--auth=scram-sha-256', '--pwfile', password)
        finally:
            password.unlink()
        # Authentication enforces identity, even if the child learns a migration
        # password. There is no trust/peer rule or alternate privileged socket role.
        (self.data / 'pg_hba.conf').write_text(
            f'local {self.database} {self.child} scram-sha-256\n'
            'local all all reject\n'
            f'host all {self.admin} 127.0.0.1/32 scram-sha-256\n'
            'host all all 0.0.0.0/0 reject\n'
            'host all all ::/0 reject\n')
        self.command('pg_ctl', '-D', self.data, '-l', self.root / 'server.log',
                     '-o', f'-h 127.0.0.1 -p {self.port} -k {self.socket}', '-w', 'start')
        try:
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
        finally:
            # Identity failure prevents a blind stop against a substituted server.
            self.verify()
            self.command('pg_ctl', '-D', self.data, '-m', 'fast', '-w', 'stop')
            assert not (self.data / 'postmaster.pid').exists()

    def child_config(self):
        return {'host': '/database', 'port': self.port, 'dbname': self.database,
                'user': self.child, 'password': self.child_password,
                'connect_timeout': 2}
