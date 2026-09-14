"""Bounded metadata protocol. Kernel peer identity and mTLS authenticate peers."""
from hashlib import sha256
import array
import os
import re
import socket
import stat
import struct
import time

from app.ai.proposals.codec import bounded_json, canonical
from app.ai.w2.records import require

LIMIT = 65536
KINDS = frozenset(('BOOTSTRAP', 'BINDING', 'PERMIT', 'INTENT', 'LOCAL_WRITE',
                  'PEER_RESPONSE', 'FINAL_USAGE', 'CLOSED', 'BARRIER', 'DISPOSITION'))
ZERO = '0' * 64


def digest(value):
    return sha256(canonical(value)).hexdigest()


def identifier(value):
    require(type(value) is str and re.fullmatch('[A-Za-z0-9_-]{1,64}', value))
    return value


def event(stream, sequence, previous, event_id, kind, data):
    identifier(stream); identifier(event_id)
    require(type(sequence) is int and 1 <= sequence <= 65536)
    require(type(previous) is str and re.fullmatch('[0-9a-f]{64}', previous))
    require(kind in KINDS and type(data) is dict)
    value = dict(format='ra-broker-event/1', stream=stream, sequence=sequence,
                 previous=previous, event_id=event_id, kind=kind, data=data)
    value['digest'] = digest(value)
    require(len(canonical(value)) <= LIMIT, 'LIMIT_EXCEEDED')
    return value


def validate_event(value):
    require(type(value) is dict and set(value) == {
        'format', 'stream', 'sequence', 'previous', 'event_id', 'kind', 'data', 'digest'})
    require(value['format'] == 'ra-broker-event/1', 'VERSION_UNSUPPORTED')
    require(value == event(*(value[k] for k in
        ('stream', 'sequence', 'previous', 'event_id', 'kind', 'data'))), 'CONFLICT')
    return value


def acknowledgement(value, witness):
    return dict(format='ra-broker-durable-ack/1', witness=witness, stream=value['stream'],
                sequence=value['sequence'], event_id=value['event_id'], event_digest=value['digest'])


def peer_identity(connection, uid, pid):
    actual_pid, actual_uid, _ = struct.unpack('3i', connection.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
    require((actual_uid, actual_pid) == (uid, pid), 'AUTHORITY_UNAVAILABLE')


def socket_identity(path):
    require(len(os.fsencode(path)) <= 107, 'LIMIT_EXCEEDED')
    value = os.stat(path, follow_symlinks=False)
    require(stat.S_ISSOCK(value.st_mode) and value.st_uid == os.getuid()
            and value.st_mode & 0o077 == 0, 'AUTHORITY_UNAVAILABLE')
    return value.st_dev, value.st_ino


def receive(connection, *, packet=False):
    if packet:
        raw, ancillary, flags, _ = connection.recvmsg(LIMIT + 1, socket.CMSG_SPACE(64 * 4),
                                                     socket.MSG_CMSG_CLOEXEC)
        # Received descriptors must be closed even when their message is denied.
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                fds = array.array('i'); fds.frombytes(data[:len(data) - len(data) % fds.itemsize])
                for fd in fds:
                    os.close(fd)
        require(not ancillary and not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC),
                'AUTHORITY_UNAVAILABLE')
    else:
        until = time.monotonic() + min(3, connection.gettimeout() or 3)
        def exact(size):
            result = bytearray()
            while len(result) < size:
                remaining = until - time.monotonic()
                require(remaining > 0, 'DEADLINE_EXCEEDED')
                connection.settimeout(remaining)
                chunk = connection.recv(size - len(result))
                require(bool(chunk), 'OBSERVER_UNAVAILABLE')
                result.extend(chunk)
            return bytes(result)
        size = struct.unpack('!I', exact(4))[0]
        require(0 < size <= LIMIT, 'LIMIT_EXCEEDED')
        raw = exact(size)
    require(0 < len(raw) <= LIMIT, 'LIMIT_EXCEEDED')
    return bounded_json(raw, maximum=LIMIT, depth=16, nodes=8192)


def send(connection, value, *, packet=False):
    raw = canonical(value)
    require(0 < len(raw) <= LIMIT, 'LIMIT_EXCEEDED')
    if packet:
        require(connection.send(raw) == len(raw), 'OBSERVER_UNAVAILABLE')
    else:
        # This is metadata IPC, not the provider-attempt write. Retrying a
        # partial provider request is never permitted.
        connection.sendall(struct.pack('!I', len(raw)) + raw)
