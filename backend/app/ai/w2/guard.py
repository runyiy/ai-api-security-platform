"""Dedicated PostgreSQL G sessions and acknowledged close-before-commit writers."""
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import create_engine, select, insert, update, text, func
from sqlalchemy.pool import NullPool
from sqlalchemy.exc import SQLAlchemyError
from . import schema as t
from .records import make, require, PortError, stamp, utc


# Non-call recovery evidence has its own domain, never a ReservationKey.
WRITER_FENCE_KEY = sha256(b'ra-w2-writer-fence/1\n').hexdigest()


@dataclass
class GuardToken:
    connection: object
    epoch: str
    owner: object
    held: bool = True
    backend_pid: int | None = None

    def verify(self, deadline):
        require(self.held and not self.connection.closed and not self.connection.invalidated, 'OWNER_LOST')
        try:
            with self.connection.begin():
                deadline.sql_timeout(self.connection)
                held = self.connection.scalar(text("""SELECT EXISTS (SELECT FROM pg_locks
                  WHERE locktype='advisory' AND pid=pg_backend_pid() AND classid=73105
                    AND objid=2 AND objsubid=2 AND granted)"""))
                require(held is True, 'OWNER_LOST')
        except SQLAlchemyError:
            self.connection.invalidate()
            raise PortError('OWNER_LOST') from None
        deadline.check()


class Guard:
    def __init__(self, database_url):
        # Never return a session holding or ambiguously releasing G to a pool.
        self.engine = create_engine(database_url, poolclass=NullPool,
            connect_args={'connect_timeout': 3, 'options': '-c statement_timeout=3000 -c lock_timeout=3000'})

    @contextmanager
    def read(self, deadline, token=None):
        if token is not None:
            require(type(token) is GuardToken and token.owner is self and token.held, 'OWNER_LOST')
            token.verify(deadline)
            yield token
            return
        connection = self.engine.connect()
        owned = GuardToken(connection, uuid4().hex, self, False)
        try:
            with connection.begin():
                deadline.sql_timeout(connection)
                connection.execute(text('SELECT pg_advisory_lock(73105, 2)'))
                owned.backend_pid = connection.scalar(text('SELECT pg_backend_pid()'))
            owned.held = True
            deadline.check()
            yield owned
        finally:
            try:
                if connection.in_transaction():
                    connection.rollback()
                if owned.held and not connection.invalidated:
                    with connection.begin():
                        connection.execute(text("SET LOCAL statement_timeout='3s'"))
                        require(connection.scalar(text('SELECT pg_advisory_unlock(73105, 2)')) is True, 'OWNER_LOST')
            except SQLAlchemyError:
                connection.invalidate()
                raise PortError('OWNER_LOST') from None
            finally:
                owned.held = False
                connection.close()

    def close(self):
        self.engine.dispose()


class Mutation:
    def __init__(self, controller, reason, writer_id, operation_digest, deadline, operation_id):
        self.controller, self.deadline = controller, deadline
        self.id = operation_id or uuid4().hex
        self.context = make('InvalidationContext', deployment_ref=controller.deployment,
            writer_id=writer_id, process_epoch=deadline.context.process_epoch,
            deadline_at=deadline.context.deadline_at, mono_deadline_ns=deadline.context.mono_deadline_ns,
            cancellation_id=deadline.context.cancellation_id)
        self.request = make('InvalidationRequest', operation_id=self.id, operation_digest=operation_digest,
            deployment_ref=controller.deployment, writer_id=writer_id, action='INVALIDATE_DEPLOYMENT',
            reason=reason, deadline_at=self.context.deadline_at)
        self.ack, self.token = None, None
        self.transaction_id, self.backend_pid = None, None
        self.outcome, self.bound = 'UNKNOWN', False

    def __enter__(self):
        self.manager = self.controller.guard.read(self.deadline)
        self.token = self.manager.__enter__()
        try:
            self.ack = self.controller.authority.invalidate_v1(self.context, self.request, self.deadline.remaining())
            self.deadline.check()
            require(self.ack.operation_id == self.id and self.ack.operation_digest == self.request.operation_digest
                    and self.ack.disposition == 'PENDING', 'COMMIT_UNKNOWN')
            return self
        except BaseException:
            try:
                self.controller.pause_admission()
            finally:
                self.manager.__exit__(None, None, None)
            raise

    def bind(self, db):
        require(self.ack is not None and not self.bound, 'COMMIT_UNKNOWN')
        self.token.verify(self.deadline)
        self.deadline.sql_timeout(db)
        # G may disappear immediately after verify(). Hold this database-side
        # lock until the mutation ends. Recovery exclusively locks the same
        # deployment row and advances a durable fence before fresh qualification.
        # Thus a paused invocation cannot start SQL after recovery has reopened A.
        db.execute(select(t.deployment.c.deployment_ref)
            .where(t.deployment.c.deployment_ref == self.controller.deployment)
            .with_for_update(read=True)).all()
        fence = db.scalar(select(func.max(t.recovery.c.owner_generation)).where(
            t.recovery.c.deployment_ref == self.controller.deployment,
            t.recovery.c.key_digest == WRITER_FENCE_KEY))
        require(fence is None or self.ack.acceptance_generation > fence, 'OWNER_LOST')
        # A closed acknowledgement can bind exactly one database transaction.
        self.transaction_id, self.backend_pid = db.execute(text('SELECT txid_current(), pg_backend_pid()')).one()
        db.execute(insert(t.invalidation).values(operation_id=self.id, operation_digest=self.request.operation_digest,
            deployment_ref=self.controller.deployment, ack=self.ack.encode(), transaction_id=self.transaction_id,
            backend_pid=self.backend_pid))
        self.bound = True
        self.deadline.check()

    def committed(self):
        require(self.bound, 'COMMIT_UNKNOWN')
        self.outcome = 'COMMITTED'
        self.deadline.check()

    def _evidence(self):
        # All writer transactions have ended before this independent connection
        # is used, and before the subsequent call to A. A pending DB transaction
        # remains UNKNOWN even if its G connection has disappeared.
        with self.controller.sessions() as db, db.begin():
            db.execute(text("SET LOCAL statement_timeout='3s'"))
            row = db.execute(select(t.invalidation).where(t.invalidation.c.operation_id == self.id)).mappings().one_or_none()
            if row is not None:
                require(row['operation_digest'] == self.request.operation_digest and row['transaction_id'] == self.transaction_id,
                        'CONFLICT')
                return 'COMMITTED', self.id
            if self.transaction_id is None:
                return 'ROLLED_BACK', self.id
            status = db.scalar(text('SELECT txid_status(:txid)'), {'txid': self.transaction_id})
            if status == 'aborted':
                return 'ROLLED_BACK', self.id
            return 'UNKNOWN', None

    def __exit__(self, kind, value, traceback):
        try:
            outcome, ref = self._evidence()
            resolution = make('InvalidationResolution', operation_id=self.id,
                operation_digest=self.request.operation_digest, deployment_ref=self.controller.deployment,
                barrier_digest=self.ack.event_digest, database_outcome=outcome, transaction_evidence_ref=ref)
            self.controller.authority.resolve_invalidation_v1(self.context, resolution, (outcome, ref), 3)
            if kind is None:
                require(outcome == 'COMMITTED', 'COMMIT_UNKNOWN')
                self.deadline.check()
        except BaseException:
            self.controller.pause_admission()
            raise
        finally:
            try:
                self.manager.__exit__(kind, value, traceback)
            except BaseException:
                self.controller.pause_admission()
                raise


class WriterController:
    def __init__(self, deployment, guard, authority, sessions):
        self.deployment, self.guard, self.authority, self.sessions = deployment, guard, authority, sessions

    def mutation(self, reason, writer_id, digest, deadline, operation_id=None):
        return Mutation(self, reason, writer_id, digest, deadline, operation_id)

    def pause_admission(self):
        # An uncertain control reply cannot claim remote closure. Independently
        # stop new admissions in the accounting database; it does not revoke an
        # already issued permit (the Y3b distinction).
        with self.sessions() as db, db.begin():
            db.execute(text("SET LOCAL statement_timeout='3s'"))
            db.execute(text("SET LOCAL lock_timeout='3s'"))
            ids = select(t.policy.c.balance_id).where(t.policy.c.scope['deployment_ref'].astext == self.deployment)
            db.execute(update(t.balance).where(t.balance.c.balance_id.in_(ids), t.balance.c.state != 'CANCELLED')
                .values(state='PAUSED_UNKNOWN'))

    def register_deployment(self, deadline):
        """Explicit fake harness activation; retained rows keep SQL fail-closed.

        This implementation is one deployment per disposable database. Another
        controller cannot silently replace the authority for an existing epoch.
        """
        digest = sha256((self.deployment + '\n' + deadline.context.process_epoch).encode()).hexdigest()
        with self.mutation('CONFIGURATION', 'deployment_registration', digest, deadline) as operation:
            with self.sessions() as db, db.begin():
                operation.bind(db)
                rows = list(db.execute(select(t.deployment)).mappings())
                require(not rows, 'CONFLICT')
                db.execute(insert(t.deployment).values(deployment_ref=self.deployment,
                    process_epoch=deadline.context.process_epoch, state='FAKE_ACTIVE'))
            operation.committed()
