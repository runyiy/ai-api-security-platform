"""Adapter and SQL-owner deaths, observed outside either interrupted process."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from threading import Event, Thread
import json
import select as ready_io
import signal
import socket
import subprocess
import sys
import tempfile
import time

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import NullPool
from app.db.session import SessionLocal, engine
from app.ai.proposals.adapter import Usage
from app.ai.proposals.codec import canonical
from app.ai.w2 import schema as t
from app.ai.w2.execution import child_projection
from app.ai.w2.ipc import CallServer, receive_frame, send_frame
from app.ai.w2.records import PortError, decode
from tests.ai.test_w2_execution import response, balances
from tests.ai.w2_sql_worker import IDENTITY_SQL
from tests.ai.w2_fixtures import (w2, rule_pair, knowledge_pair, subject_pair,
    two_intake_targets, zero_capabilities, KNOWN)  # noqa: F401


@pytest.mark.parametrize('boundary,accepted,known', [
    ('before_input',False,False),('before_marker_commit',False,False),('marker_ack',False,False),
    ('permit_ack',False,False),('after_endpoint_acceptance',True,False),
    ('after_acceptance_journal',True,False),('terminal_observation',True,True),
    ('before_record',True,True),('before_settlement_commit',True,True)])
def test_t7_adapter_kills_with_surviving_sql_coordinator(w2,tmp_path,boundary,accepted,known):
    w2.reopen();rt=w2.runtime()
    paused,release=Event(),Event()
    def stop(stage):
        if stage==boundary:
            paused.set();assert release.wait(10)
    def call_hook(stage):
        if stage=='admitted':w2.authority.script_final(rt.key,KNOWN if known else Usage())
        # The call-only accounting RPC does not accept outcome or display data.
        if boundary=='before_record' and stage=='qualified_before_return':stop(boundary)
        stop(stage)
    rt.hook=call_hook
    w2.store.hook=stop
    w2.authority.hook=stop
    sandbox=child_projection(tmp_path/'process',rt,response(rt,usage=KNOWN if known else Usage()))
    server=CallServer(sandbox.call_socket/'endpoint',rt).start()
    child=subprocess.Popen(sandbox.command('child.py'),stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,env={'PATH':'/usr/bin:/bin'})
    try:
        assert paused.wait(10)
        # An OS kill of the fresh bwrap child, with its namespace/descriptor
        # boundary intact. The parent, journal, witness and DB survive it.
        child.kill();child.wait(timeout=3)
        assert child.returncode != 0
        release.set()
        server.thread.join(5)
        assert not server.thread.is_alive()
        w2.authority.hook=lambda _:None
        w2.store.hook=lambda _:None
        rt.hook=lambda _:None
        rt.close();result=rt.reconcile()
        assert w2.witness.accepted[rt.key.fingerprint()]==int(accepted)
        assert w2.authority.coverage()
        if not accepted:
            assert result.state=='ZERO' and result.refund_tokens==5120 and result.refund_microusd==22528
        elif known:
            assert result.state=='KNOWN' and result.settled_tokens==2432 and result.settled_microusd==8704
        else:
            assert result.state=='UNKNOWN' and result.held_tokens==5120 and result.held_microusd==22528
        with SessionLocal() as db:
            assert len(list(db.execute(select(t.core))))==1
            assert len(list(db.execute(select(t.completion))))==0
        assert all(b['calls']==1 for b in balances(rt.key))
        if rt.permit:
            with pytest.raises(PortError):w2.authority.consume_write_v1(rt.permit,rt.prepared.body)
        assert w2.witness.accepted[rt.key.fingerprint()]==int(accepted)
    finally:
        release.set()
        if child.poll() is None:child.kill()
        child.wait(timeout=3)
        child.stdout.close();child.stderr.close()
        server.close()


class CoordinatorEvidenceServer:
    """Only the trusted SQL test worker sees this bounded, call-bound socket.

    SQL never runs here. The genuine N1 authority and its durable witness remain
    in the outer harness while the process that executes BudgetStore is killed.
    This socket is never mounted into the mandatory bwrap adapter sandbox.
    """
    def __init__(self, path, runtime):
        self.runtime = runtime
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path))
        self.listener.listen(1)
        self.listener.settimeout(10)
        self.error = None
        self.ticket = None
        self.thread = Thread(target=self.serve, daemon=True)

    def dispatch(self, action, arguments):
        rt, authority = self.runtime, self.runtime.observer
        if action == 'reserved':
            assert set(arguments) == {'receipt'}
            receipt = decode('Reservation', canonical(arguments['receipt']))
            assert receipt.key == rt.key
            authority.register_reserved(rt.prepared, rt.run, receipt)
            return None
        if action == 'observe':
            assert set(arguments) == {'read', 'event'}
            read = decode('ReadContext', canonical(arguments['read']))
            observation = decode('Observation', canonical(arguments['event']))
            assert read.scope == rt.key.scope and observation.key_digest == rt.key.fingerprint()
            return authority.observe_v1(read, observation).document()
        assert not arguments
        if action == 'ticket':
            self.ticket = authority.begin_acceptance_v1(rt.run, rt.admission, rt.prepared.core.body_digest)
            return self.ticket.document()
        if action == 'events':
            return [e.document() for e in authority.events(rt.key)]
        if action == 'coverage':
            return authority.coverage()
        if action == 'accepted':
            return authority.witness.accepted[rt.key.fingerprint()]
        if action == 'closed':
            return authority.calls[rt.key.fingerprint()]['closed']
        raise AssertionError('no other coordinator authority is exposed')

    def serve(self):
        try:
            connection, _ = self.listener.accept()
            with connection:
                connection.settimeout(10)
                for _ in range(64):
                    try:
                        metadata, body = receive_frame(connection)
                    except PortError:
                        return  # EOF after the owned coordinator is SIGKILLed.
                    assert not body and set(metadata) == {'action', 'key_digest', 'arguments'}
                    assert metadata['key_digest'] == self.runtime.key.fingerprint()
                    value = self.dispatch(metadata['action'], metadata['arguments'])
                    send_frame(connection, {'value': value})
                raise AssertionError('coordinator frame bound exceeded')
        except BaseException as error:
            self.error = error


def seed_independent_acceptance(w2, rt, root):
    """A real isolated adapter reaches N1, then dies before accounting RPC."""
    paused, release = Event(), Event()

    def hook(stage):
        if stage == 'admitted':
            w2.authority.script_final(rt.key, KNOWN)
        if stage == 'qualified_before_return':
            paused.set()
            assert release.wait(10)

    rt.hook = hook
    sandbox = child_projection(root/'adapter', rt, response(rt))
    server = CallServer(sandbox.call_socket/'endpoint', rt).start()
    child = subprocess.Popen(sandbox.command('child.py'), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, env={'PATH': '/usr/bin:/bin'})
    try:
        assert paused.wait(10)
        child.kill()
        child.wait(timeout=3)
        assert child.returncode == -signal.SIGKILL
        release.set()
        server.thread.join(5)
        assert not server.thread.is_alive()
        assert 'write' in server.requests and 'record' not in server.requests
        assert w2.witness.accepted[rt.key.fingerprint()] == 1
        assert any(e.kind == 'FINAL_USAGE' and e.usage.total_tokens == 2432 for e in w2.authority.events(rt.key))
        with SessionLocal() as db:
            assert not list(db.execute(select(t.settlement)))
            assert not list(db.execute(select(t.observation_copy)))
    finally:
        release.set()
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)
        child.stdout.close()
        child.stderr.close()
        server.close()
        rt.hook = lambda _: None


@contextmanager
def killed_sql_owner(w2, rt, operation, boundary, fresh):
    with tempfile.TemporaryDirectory(prefix='w2-sql-crash-', dir='/tmp') as directory:
        root = Path(directory)
        path = root/'evidence'
        server = CoordinatorEvidenceServer(path, rt)
        with fresh.connect() as db:
            identity = db.scalar(text(IDENTITY_SQL))
        payload = dict(operation=operation, boundary=boundary, database_identity=identity,
            application_name='w2_crash_'+operation, socket=str(path), key=rt.key.document(), run=rt.run.document(),
            core=rt.prepared.core.document(), manifest_digest=rt.prepared.manifest_digest,
            admission=rt.admission.document() if rt.admission is not None else None)
        # Explicit config only; the worker cwd is empty and .env is not copied.
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
            'DATABASE_URL': engine.url.render_as_string(hide_password=False)}
        server.thread.start()
        with (root/'stderr').open('wb') as errors:
            process = subprocess.Popen([sys.executable, '-I', str(Path(__file__).with_name('w2_sql_worker.py'))],
                cwd=root, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, close_fds=True)
            try:
                process.stdin.write(canonical(payload)+b'\n')
                process.stdin.flush()
                assert ready_io.select([process.stdout], [], [], 10)[0], 'SQL owner did not reach crash boundary'
                line = process.stdout.readline(4097)
                assert line and len(line) <= 4096, (root/'stderr').read_text()[-4000:]
                notice = json.loads(line)
                assert notice['process_pid'] == process.pid and notice['stage'] == boundary
                assert process.poll() is None
                with fresh.connect() as db:
                    state = db.execute(text('SELECT application_name, state FROM pg_stat_activity WHERE pid=:pid'),
                        {'pid': notice['database_pid']}).one_or_none()
                    disposition = db.scalar(text('SELECT txid_status(CAST(:xid AS bigint))'), {'xid': notice['transaction_id']})
                before_commit = boundary.startswith('before_')
                assert disposition == ('in progress' if before_commit else 'committed')
                if before_commit:
                    assert state == ('w2_crash_'+operation, 'idle in transaction')
                if operation == 'marker':
                    with fresh.connect() as db:
                        assert db.scalar(text('SELECT count(*) FROM pg_locks WHERE pid=:pid AND locktype=\'advisory\' '
                            'AND classid=73105 AND objid=2 AND granted'), {'pid': notice['guard_pid']}) == 1
                journal_before = w2.journal.path.read_bytes()
                witness_before = w2.witness.journal.path.read_bytes()
                process.kill()
                process.wait(timeout=3)
                assert process.returncode == -signal.SIGKILL
                assert w2.journal.path.read_bytes() == journal_before
                assert w2.witness.journal.path.read_bytes() == witness_before
                # Wait only for PostgreSQL to observe this process's descriptor
                # closure; inspect a new physical connection on every read.
                end = time.monotonic()+3
                while True:
                    with fresh.connect() as db:
                        remaining = db.scalar(text('SELECT count(*) FROM pg_stat_activity WHERE pid IN (:sql, :guard)'),
                            {'sql': notice['database_pid'], 'guard': notice['guard_pid'] or notice['database_pid']})
                    if not remaining:
                        break
                    assert time.monotonic() < end, 'killed SQL owner retained a database session'
                    time.sleep(.01)
                with fresh.connect() as db:
                    final_disposition = db.scalar(text('SELECT txid_status(CAST(:xid AS bigint))'),
                        {'xid': notice['transaction_id']})
                    assert final_disposition == ('aborted' if before_commit else 'committed')
                server.crash = dict(notice, sql_before=disposition, sql_after=final_disposition,
                    process_returncode=process.returncode, remaining_database_sessions=remaining,
                    journal_sha256=sha256(journal_before).hexdigest(), witness_sha256=sha256(witness_before).hexdigest())
                yield server
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)
                process.stdin.close()
                process.stdout.close()
                server.listener.close()
                server.thread.join(3)
                assert not server.thread.is_alive()
                assert server.error is None


@pytest.mark.parametrize('operation', ['reserve', 'marker', 'settlement'])
@pytest.mark.parametrize('moment', ['before_commit', 'committed_before_ack', 'acknowledged'])
def test_t7_sigkill_sql_owner_at_commit_and_ack_boundaries(w2, tmp_path, operation, moment):
    w2.reopen()
    rt = w2.runtime(reserve=operation != 'reserve')
    if operation == 'marker':
        rt.admit(rt.receipt, rt.prepared.core.w1_binding_digest, 30)
    elif operation == 'settlement':
        seed_independent_acceptance(w2, rt, tmp_path)
    boundary = 'before_'+operation+'_commit' if moment == 'before_commit' else (
        'after_commit' if moment == 'committed_before_ack' else 'acknowledged')
    fresh = create_engine(engine.url, poolclass=NullPool)
    try:
        with killed_sql_owner(w2, rt, operation, boundary, fresh) as evidence:
            with fresh.connect() as db:
                rows = list(db.execute(select(t.reservation)).mappings())
                budgets = list(db.execute(select(t.balance)).mappings())
                markers = list(db.execute(select(t.marker)).mappings())
                settlements = list(db.execute(select(t.settlement)).mappings())
                postings = list(db.execute(select(t.posting)).mappings())
                admissions = list(db.execute(select(t.admission)).mappings())
                assert not list(db.execute(select(t.completion)))
            committed = moment != 'before_commit'
            reserved = operation != 'reserve' or committed
            settled = operation == 'settlement' and committed
            assert len(rows) == int(reserved)
            assert len(settlements) == int(settled) and len(postings) == 4*int(settled)
            assert len(markers) == int(operation == 'settlement' or (operation == 'marker' and committed))
            assert len(admissions) == int(operation != 'reserve')
            assert all((b['calls'], b['wall_ns'], b['held_tokens'], b['held_microusd'],
                b['settled_tokens'], b['settled_microusd']) == (
                int(reserved), 30_000_000_000*int(reserved), 5120*int(reserved and not settled),
                22528*int(reserved and not settled), 2432*int(settled), 8704*int(settled)) for b in budgets)
            assert w2.authority.coverage()
            assert w2.witness.accepted[rt.key.fingerprint()] == int(operation == 'settlement')
            # Retain actual process/SQL/independent-journal observations for
            # inspection alongside the fixture's original journal and witness.
            (tmp_path/'sql-crash-evidence.json').write_text(json.dumps(dict(
                operation=operation, moment=moment, crash=evidence.crash,
                key_digest=rt.key.fingerprint(), endpoint_acceptances=w2.witness.accepted[rt.key.fingerprint()],
                journal_sequence=len(w2.journal.entries), witness_sequence=w2.witness.head_sequence,
                observations=[e.document() for e in w2.authority.events(rt.key)],
                budgets=[dict(b) for b in budgets], reservation_count=len(rows), admission_count=len(admissions),
                marker_count=len(markers), settlement_count=len(settlements), posting_count=len(postings)), indent=2))
            if not reserved:
                assert rt.key.fingerprint() not in w2.authority.calls
                assert not w2.authority.tickets and not w2.authority.permits
                return
            if operation == 'reserve' and moment == 'committed_before_ack':
                # SQL committed before the independent core registration ack.
                # Its absence is not fabricated zero evidence or permission to
                # replay reserve; explicit recovery remains blocked and held.
                assert rt.key.fingerprint() not in w2.authority.calls
                w2.store.mark_unknown(rt.run, rt.key)
                w2.authority.suspend()
                with pytest.raises(PortError, match='OBSERVER_UNAVAILABLE'):
                    w2.reopen()
                assert all(b['state'] == 'PAUSED_UNKNOWN' and b['held_tokens'] == 5120
                    and b['held_microusd'] == 22528 for b in balances(rt.key))
                assert w2.authority.state != 'OPEN'
                return
            # A separate, explicit recovery closes/fences the stream and takes
            # ownership. Only its independent evidence may release liability.
            ctx = w2.recovery.recover_call(w2.read(), rt.key, 'recover_sql_crash', 1, 'recovery_owner', w2.deadline())
            if not settled:
                assert all(b['held_tokens'] == 5120 and b['held_microusd'] == 22528 for b in balances(rt.key))
            observed = w2.authority.events(rt.key)
            result = w2.store.reconcile_v1(ctx, rt.key, [e.event_id for e in observed], w2.authority)
            assert result.state == ('KNOWN' if operation == 'settlement' else 'ZERO')
            assert w2.authority.state == 'CLOSED'
            assert w2.witness.accepted[rt.key.fingerprint()] == int(operation == 'settlement')
            with pytest.raises(PortError):
                w2.store.reconcile_v1(rt.run, rt.key, [observed[-1].event_id], w2.authority)
            if rt.permit is not None:
                with pytest.raises(PortError):
                    w2.authority.consume_write_v1(rt.permit, rt.prepared.body)
            if evidence.ticket is not None:
                with pytest.raises(PortError):
                    w2.authority.issue(rt.run, rt.admission, evidence.ticket, 'stale_marker', 'stale_guard')
            with fresh.connect() as db:
                assert len(list(db.execute(select(t.core)))) == 1
                assert len(list(db.execute(select(t.admission)))) == len(admissions)
                assert len(list(db.execute(select(t.settlement)))) == 1
                assert len(list(db.execute(select(t.posting)))) == 4
                assert not list(db.execute(select(t.completion)))
            assert all(b['calls'] == 1 and b['held_tokens'] == b['held_microusd'] == 0 for b in balances(rt.key))
    finally:
        fresh.dispose()
