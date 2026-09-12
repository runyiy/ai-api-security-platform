"""Transaction-scoped integration for the adopted lifecycle writer inventory.

The injected Session token is explicit and shared by nested services. It owns G
until the outer transaction ends, including internal commits and read-side
invalidation. Without an installed controller the SQL backstop denies mutations
when a W2 deployment exists; ordinary default-disabled behavior is unchanged.
"""
from functools import wraps
from hashlib import sha256
import inspect

from sqlalchemy import event
from app.ai.proposals.codec import canonical
from .records import require, PortError


def attach(engine, controller, deadline_factory):
    # This is a trusted, local fake harness installation, not environment config
    # or a public route. Each call gets its own deadline and mutation context.
    require(not hasattr(engine, '_research_ai_lifecycle'), 'CONFLICT')
    engine._research_ai_lifecycle = controller, deadline_factory


def detach(engine):
    if hasattr(engine, '_research_ai_lifecycle'):
        del engine._research_ai_lifecycle


def _install_events(db):
    if db.info.get('w2_events_installed'):
        return
    db.info['w2_events_installed'] = True

    def committed(session):
        token = session.info.get('w2_lifecycle_guard')
        if token is not None and not session.in_nested_transaction():
            token.outcome = 'COMMITTED'

    def ended(session, transaction):
        if transaction.parent is not None:
            return
        token = session.info.pop('w2_lifecycle_guard', None)
        if token is not None:
            # Actual database completion precedes independent disposition lookup
            # and A. A is never requested from a flush/savepoint/SQL lock scope.
            token.__exit__(None if token.outcome == 'COMMITTED' else PortError,
                           None, None)

    event.listen(db, 'after_commit', committed)
    event.listen(db, 'after_transaction_end', ended)


def writer(function):
    signature = inspect.signature(function)
    writer_id = 'w_' + sha256((function.__module__ + '.' + function.__name__).encode()).hexdigest()[:32]

    @wraps(function)
    def wrapped(*args, **kwargs):
        arguments = signature.bind(*args, **kwargs).arguments
        db = arguments.get('db')
        if db is None:
            return function(*args, **kwargs)
        configured = getattr(db.get_bind(), '_research_ai_lifecycle', None)
        if configured is None:
            return function(*args, **kwargs)
        borrowed = db.info.get('w2_lifecycle_guard')
        if borrowed is not None:
            require(borrowed.token.held and borrowed.bound, 'OWNER_LOST')
            borrowed.deadline.check()
            return function(*args, **kwargs)
        transaction = db.get_transaction()
        # An explicit root begin without SQL has no DB locks. Once a connection
        # has been used, reject before A; caller must roll back and start fresh.
        require(transaction is None or not transaction._connections, 'CONTEXT_CHANGED')
        require(not db.new and not db.dirty and not db.deleted, 'CONTEXT_CHANGED')
        controller, deadline_factory = configured
        deadline = deadline_factory()
        def identity(value):
            if value is None or type(value) in (str, int, bool):
                return value
            if type(value) is bytes:
                return {'bytes_digest': sha256(value).hexdigest()}
            if hasattr(value, 'model_dump'):
                return value.model_dump(mode='json')
            if type(value) is dict:
                return {k: identity(v) for k, v in value.items()}
            if type(value) in (tuple, list):
                return [identity(v) for v in value]
            # Clocks/closures/ORM instances never have their repr or secret
            # properties serialized into operation evidence.
            raise PortError('INVALID_RECORD')
        params = {k: identity(v) for k, v in arguments.items() if k not in ('db', 'now', 'clock')}
        digest = sha256(canonical({'writer': writer_id, 'parameters': params})).hexdigest()
        token = controller.mutation('LIFECYCLE', writer_id, digest, deadline)
        token.__enter__()
        _install_events(db)
        db.info['w2_lifecycle_guard'] = token
        try:
            token.bind(db)
            result = function(*args, **kwargs)
            deadline.check()
            return result
        except BaseException:
            # End the failing root transaction before A. The independent
            # disposition lookup distinguishes rollback from a lost commit ack.
            if db.in_transaction():
                db.rollback()
            elif db.info.get('w2_lifecycle_guard') is token:
                db.info.pop('w2_lifecycle_guard')
                token.__exit__(PortError, None, None)
            raise

    return wrapped
