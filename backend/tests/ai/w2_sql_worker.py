"""Trusted SQL crash coordinator; never the sandboxed, untrusted adapter.

The executable starts with stdlib only, checks the exact disposable PostgreSQL
identity supplied by its already verified harness, then imports application
code. It receives no journal paths or descriptors. The independent parent owns
the real authority, endpoint and witness; its narrow test socket only serves the
one prepared call. SIGKILL prevents this process from running SQL cleanup.
"""
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit


IDENTITY_SQL = """SELECT json_build_object('database',current_database(),
 'user',current_user,'host',inet_server_addr(),'port',inet_server_port(),
 'data_directory',current_setting('data_directory'),
 'system_identifier',(SELECT system_identifier::text FROM pg_control_system()))"""


def verify_database_identity(database_url, expected):
    # No Settings import or operator .env lookup occurs before identity checks.
    # The caller also supplies an empty owned cwd and an explicit minimal env.
    assert not Path('.env').exists()
    # urlsplit normalizes leading controls/case; libpq consumes the original.
    assert (database_url.startswith('postgresql+psycopg://')
            and all(ord(c) > 32 and ord(c) != 127 for c in database_url))
    target = urlsplit(database_url)
    # CI publishes its service on localhost, but PostgreSQL reports its own
    # container address/port. Constrain the client target independently; URL
    # options must not override it with hostaddr, a service or multiple hosts.
    assert (target.scheme == 'postgresql+psycopg'
            and target.hostname in ('localhost', '127.0.0.1', '::1')
            and target.port is not None and 0 < target.port <= 65535
            and target.username and target.password and target.path not in ('', '/')
            and not target.query and not target.fragment)
    import psycopg
    with psycopg.connect(database_url.replace('postgresql+psycopg:', 'postgresql:', 1),
                          connect_timeout=3, options='-c statement_timeout=3000') as db:
        identity = db.execute(IDENTITY_SQL).fetchone()[0]
        assert identity == expected


def main():
    raw = sys.stdin.buffer.readline(65537)
    assert raw.endswith(b'\n') and len(raw) <= 65536
    data = json.loads(raw)
    database_url = os.environ['DATABASE_URL']
    verify_database_identity(database_url, data['database_identity'])
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from app.ai.proposals.codec import canonical
    from app.ai.w2.accounting import BudgetStore
    from app.ai.w2.guard import Guard
    from app.ai.w2.ipc import receive_frame, send_frame
    from app.ai.w2.records import decode, utc
    from app.ai.w2.time import Deadline

    def record(name, value):
        return decode(name, canonical(value))

    key, run = record('ReservationKey', data['key']), record('RunContext', data['run'])
    core = record('BindingCore', data['core'])
    prepared = SimpleNamespace(key=key, core=core, manifest_digest=data['manifest_digest'])
    clock = SimpleNamespace(utcnow=lambda: utc(run.started_at), monotonic_ns=lambda: run.mono_start_ns)
    deadline = Deadline(run, clock, lambda _: False, run.process_epoch)
    engine = create_engine(database_url, poolclass=NullPool,
        connect_args={'connect_timeout': 3, 'application_name': data['application_name']})
    sessions = sessionmaker(bind=engine)
    transaction = {}

    @event.listens_for(sessions, 'after_begin')
    def remember_transaction(session, sql_transaction, connection):
        pid, xid = connection.execute(text('SELECT pg_backend_pid(), txid_current()::text')).one()
        transaction.update(database_pid=pid, transaction_id=xid)

    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(3)
    connection.connect(data['socket'])

    def rpc(action, arguments=None):
        send_frame(connection, {'action': action, 'key_digest': key.fingerprint(), 'arguments': arguments or {}})
        reply, body = receive_frame(connection)
        assert not body and set(reply) == {'value'}
        return reply['value']

    class Observer:
        def events(self, requested):
            assert requested == key
            return tuple(record('Observation', e) for e in rpc('events'))

        def observe_v1(self, read, observation, timeout):
            return record('ObservationAck', rpc('observe', {'read': read.document(), 'event': observation.document()}))

        def coverage(self):
            return rpc('coverage')

        def proves_zero(self, requested):
            assert requested == key
            return rpc('proves_zero')

        def proves_final(self, requested, observation):
            assert requested == key and observation.key_digest == key.fingerprint()
            return rpc('proves_final', {'event': observation.document()})

        @property
        def witness(self):
            return SimpleNamespace(accepted={key.fingerprint(): rpc('accepted')})

        @property
        def calls(self):
            return {key.fingerprint(): {'closed': rpc('closed')}}

    store = BudgetStore(sessions, None)
    guard = None
    work_transaction = None

    def stop(stage):
        nonlocal work_transaction
        if stage == 'before_'+data['operation']+'_commit':
            # Reconciliation may read back the committed result in another
            # transaction before returning. Pin the transaction that writes
            # reservation/marker/settlement, never that later readback.
            assert work_transaction is None
            work_transaction = dict(transaction)
        if stage != data['boundary']:
            return
        assert work_transaction is not None
        print(json.dumps({'stage': stage, 'process_pid': os.getpid(), **work_transaction,
                          'guard_pid': guard.backend_pid if guard is not None else None}), flush=True)
        # The harness terminates us here. No callback in the surviving parent
        # performs this SQL transaction, and no exception unwinds its context.
        assert os.read(sys.stdin.fileno(), 1) == b'!'
        raise AssertionError('crash worker must never be released')

    store.hook = stop
    if data['operation'] == 'reserve':
        receipt = store.reserve_v1(run, key, prepared, deadline)
        rpc('reserved', {'receipt': receipt.document()})
        stop('acknowledged')
    elif data['operation'] == 'marker':
        admission = record('Admission', data['admission'])
        owned_guard = Guard(database_url)
        with owned_guard.read(deadline) as guard:
            ticket = record('AcceptanceTicket', rpc('ticket'))
            store.marker(run, admission, ticket, deadline)
            stop('acknowledged')
    else:
        assert data['operation'] == 'settlement'
        observer = Observer()
        ids = [e.event_id for e in observer.events(key)]
        assert len(ids) <= 8
        store.reconcile_v1(run, key, ids, observer)
        stop('acknowledged')
    raise AssertionError('requested crash boundary was not reached')


if __name__ == '__main__':
    main()
