"""Bounded call-only Unix IPC. Administrative authority is never exported."""
from dataclasses import asdict
import socket
import struct
import threading
import time

from app.ai.proposals.adapter import Usage
from app.ai.proposals.bindings import (AuthorizationSnapshot, Configuration, Registry,
    SourceBinding, ReservationReceipt)
from app.ai.proposals.codec import canonical, bounded_json, ProposalRejected
from .records import decode, require, PortError, BRIDGE, stamp, utc


def receive_exact(connection, length):
    value = bytearray()
    while len(value) < length:
        chunk = connection.recv(min(4096, length-len(value)))
        require(bool(chunk), 'OBSERVER_UNAVAILABLE')
        value.extend(chunk)
    return bytes(value)


def receive_frame(connection):
    header = receive_exact(connection, 8)
    metadata_size, body_size = struct.unpack('!II', header)
    require(0 < metadata_size <= (4096 if body_size else 8192) and body_size <= 32768
            and 8+metadata_size+body_size <= 40960, 'LIMIT_EXCEEDED')
    meta = bounded_json(receive_exact(connection, metadata_size), maximum=8192, depth=8, nodes=4096)
    body = receive_exact(connection, body_size) if body_size else b''
    return meta, body


def send_frame(connection, metadata, body=b''):
    raw = canonical(metadata)
    require(len(raw) <= (4096 if body else 8192) and len(body) <= 32768
            and 8+len(raw)+len(body) <= 40960, 'LIMIT_EXCEEDED')
    connection.sendall(struct.pack('!II', len(raw), len(body))+raw+body)


def snapshot_document(value):
    registry = asdict(value.registry)
    registry['valid_from'] = value.registry.valid_from.isoformat()
    registry['expires_at'] = value.registry.expires_at.isoformat()
    registry['allowed_pairs'] = sorted(value.registry.allowed_pairs)
    for source in registry['sources']:
        source['valid_from'], source['expires_at'] = source['valid_from'].isoformat(), source['expires_at'].isoformat()
    return dict(registry=registry, config=asdict(value.config),
        permissions=list(value.permissions), valid_from=stamp(value.valid_from),
        expires_at=stamp(value.expires_at), state=value.state)


def registry_from(doc):
    from datetime import datetime
    # Only the trusted parent's exact snapshot is accepted; W1 independently
    # validates all digest, projection and interval semantics after decoding.
    require(set(doc) == {'request_ref', 'project_ref', 'context_ref', 'generation', 'sources',
                        'allowed_pairs', 'valid_from', 'expires_at', 'state'})
    sources = []
    for source in doc['sources']:
        value = {**source, 'decision_refs': tuple(source['decision_refs']),
                 'valid_from': datetime.fromisoformat(source['valid_from']),
                 'expires_at': datetime.fromisoformat(source['expires_at'])}
        sources.append(SourceBinding(**value))
    return Registry(**{**doc, 'sources': tuple(sources),
        'allowed_pairs': frozenset(tuple(p) for p in doc['allowed_pairs']),
        'valid_from': datetime.fromisoformat(doc['valid_from']), 'expires_at': datetime.fromisoformat(doc['expires_at'])})


class CallServer:
    def __init__(self, path, runtime):
        self.path, self.runtime = str(path), runtime
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        self.listener.listen(1)
        self.listener.settimeout(1)
        self.stopping = threading.Event()
        self.ready = threading.Event()
        self.requests = []
        self.error = None
        self.connection = None
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self.thread.start()
        require(self.ready.wait(3), 'OBSERVER_UNAVAILABLE')
        return self

    def _dispatch(self, meta, body):
        require(set(meta) == {'action', 'key_digest', 'arguments'} and meta['key_digest'] == self.runtime.key.fingerprint())
        action, args = meta['action'], meta['arguments']
        require(type(args) is dict)
        r = self.runtime
        if action == 'current':
            require(not body and set(args) == {'project_ref', 'context_ref', 'stage'})
            return snapshot_document(r.current(**args))
        if action == 'admit':
            require(not body and set(args) == {'binding_digest', 'timeout'})
            r.admit(r.receipt, **args)
        elif action == 'begin_send':
            require(not args and not body)
            r.begin_send()
        elif action == 'end_send':
            require(not args and not body)
            try:
                r.end_send()
            finally:
                r.close()
        elif action == 'write':
            require(set(args) == {'timeout'} and bool(body))
            return r.consume(body, args['timeout']).document()
        elif action == 'close':
            require(not args and not body)
            r.close()
        elif action == 'terminal':
            require(not body and set(args) == {'terminal', 'usage'})
            usage = decode('UsageView', canonical(args['usage']))
            r.observe_terminal(args['terminal'], usage)
        elif action == 'record':
            require(not body and not args)
            r.reconcile()
        elif action == 'finish':
            require(not body and not args)
            r.finish(r.receipt)
        else:
            raise PortError('AUTHORITY_UNAVAILABLE')
        return None

    def _serve(self):
        self.ready.set()
        start = time.monotonic()
        try:
            while not self.stopping.is_set() and time.monotonic()-start < 30:
                try:
                    connection, _ = self.listener.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            self.connection = connection
            with connection:
                # One connection and one in-flight frame/call. It cannot select
                # another call or reconnect to revive an old stream.
                for _ in range(256):
                    if self.stopping.is_set():
                        break
                    connection.settimeout(max(.001, min(3, 30-(time.monotonic()-start))))
                    try:
                        meta, body = receive_frame(connection)
                    except (PortError, OSError):
                        break
                    action = meta.get('action')
                    self.requests.append(action if action in ('current','admit','begin_send','end_send','write','close','terminal','record','finish') else 'DENIED')
                    try:
                        value = self._dispatch(meta, body)
                        reply = dict(ok=True, value=value, wall=stamp(self.runtime.deadline.clock.utcnow()))
                    except PortError as error:
                        reply = dict(ok=False, code=BRIDGE[error.code], wall=stamp(self.runtime.deadline.clock.utcnow()))
                    except ProposalRejected as error:
                        reply = dict(ok=False, code=error.code, wall=stamp(self.runtime.deadline.clock.utcnow()))
                    except Exception:
                        reply = dict(ok=False, code='AUDIT_UNAVAILABLE', wall=stamp(self.runtime.deadline.clock.utcnow()))
                    send_frame(connection, reply)
        except Exception:
            self.error = 'W2_IPC_UNAVAILABLE'
        finally:
            try:
                self.runtime.end_send()
                self.runtime.close()
            except Exception:
                self.error = 'W2_IPC_CLEANUP_UNRESOLVED'

    def close(self):
        self.stopping.set()
        if self.connection is not None:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.listener.close()
        self.thread.join(3)
        require(not self.thread.is_alive(), 'OBSERVER_UNAVAILABLE')


class RemoteClock:
    def __init__(self, wall, remaining):
        self.wall = utc(wall)
        self.deadline = time.monotonic()+remaining

    def utcnow(self):
        return self.wall

    def monotonic(self):
        return time.monotonic()

    def remaining(self, maximum=30):
        remaining = self.deadline-time.monotonic()
        if remaining <= 0:
            raise ProposalRejected('PROVIDER_TIMEOUT')
        return min(maximum, remaining)


class CallClient:
    def __init__(self, path, key_digest, clock):
        self.key_digest, self.clock = key_digest, clock
        self.connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.connection.settimeout(clock.remaining(3))
        self.connection.connect(str(path))

    def call(self, action, arguments=None, body=b'', *, cleanup=False):
        self.connection.settimeout(3 if cleanup else self.clock.remaining(3))
        try:
            send_frame(self.connection, dict(action=action, key_digest=self.key_digest, arguments=arguments or {}), body)
            reply, extra = receive_frame(self.connection)
            require(not extra and reply.get('wall') is not None)
            wall = utc(reply['wall'])
            require(wall >= self.clock.wall, 'CONTEXT_CHANGED')
            self.clock.wall = wall
            if reply.get('ok') is not True:
                raise ProposalRejected(reply.get('code'))
            return reply['value']
        except (OSError, PortError):
            raise ProposalRejected('AUDIT_UNAVAILABLE') from None

    def current(self, project_ref, context_ref, stage):
        value = self.call('current', dict(project_ref=project_ref, context_ref=context_ref, stage=stage))
        return AuthorizationSnapshot(registry_from(value['registry']), Configuration(**value['config']),
            tuple(value['permissions']), utc(value['valid_from']), utc(value['expires_at']), value['state'])

    def admit(self, receipt, binding_digest, timeout):
        self.call('admit', dict(binding_digest=binding_digest, timeout=min(timeout, self.clock.remaining())))

    def sending(self, receipt, check):
        from contextlib import contextmanager
        @contextmanager
        def sending():
            self.call('begin_send')
            try:
                check()
                yield
            finally:
                self.call('end_send', cleanup=True)
        return sending()

    def consume(self, body, timeout):
        return self.call('write', {'timeout': timeout}, body)

    def close_stream(self):
        self.call('close', cleanup=True)

    def observe_terminal(self, terminal, usage):
        self.call('terminal', dict(terminal=terminal, usage=usage.document()), cleanup=True)

    def record(self, receipt, outcome):
        self.call('record', cleanup=True)

    def finish(self, receipt):
        self.call('finish', cleanup=True)

    def close(self):
        self.connection.close()
