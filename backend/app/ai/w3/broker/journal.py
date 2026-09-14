"""B-private J and append-only W records. Never repair/truncate ambiguous tails."""
import fcntl
import os
from pathlib import Path
import re
import stat
import struct

from app.ai.proposals.codec import bounded_json, canonical
from app.ai.w2.records import require
from .protocol import LIMIT, ZERO, acknowledgement, event, validate_event
from .recovery import amount, liability, merge

MAX_RECORD = 49152
MAX_ROWS = 512  # one call, 64 invalidations/dispositions, and local ACK copies


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        parent = self.path.parent.stat(follow_symlinks=False)
        require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid()
                and parent.st_mode & 0o077 == 0, 'OBSERVER_UNAVAILABLE')
        self.fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        self.rows = []
        self.failed = False
        try:
            self.identity = os.fstat(self.fd)
            require(stat.S_ISREG(self.identity.st_mode) and self.identity.st_uid == os.getuid()
                    and self.identity.st_nlink == 1 and self.identity.st_mode & 0o077 == 0,
                    'OBSERVER_UNAVAILABLE')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            directory = os.open(self.path.parent, os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(directory)
            finally: os.close(directory)
            require(self.identity.st_size <= 64 * 1024 * 1024, 'LIMIT_EXCEEDED')
            raw = os.read(self.fd, self.identity.st_size + 1)
            offset = 0
            while offset < len(raw):
                require(len(self.rows) < MAX_ROWS, 'LIMIT_EXCEEDED')
                require(len(raw) - offset >= 4, 'OBSERVER_UNAVAILABLE')
                length = struct.unpack('!I', raw[offset:offset + 4])[0]
                require(0 < length <= MAX_RECORD and offset + 4 + length <= len(raw), 'OBSERVER_UNAVAILABLE')
                self.rows.append(bounded_json(raw[offset + 4:offset + 4 + length],
                                              maximum=LIMIT, depth=16, nodes=8192))
                offset += 4 + length
            self.size = offset
        except BaseException:
            os.close(self.fd); self.fd = None
            raise

    def intact(self):
        if self.fd is None or self.failed:
            return False
        try: value = os.stat(self.path, follow_symlinks=False)
        except OSError: return False
        if (value.st_dev, value.st_ino, value.st_size) != (
                self.identity.st_dev, self.identity.st_ino, self.size):
            return False
        # Detect same-size replacement/rollback as well as missing tails. Bounds
        # are fixed; this small increment retains at most one call's metadata.
        raw = b''.join(struct.pack('!I', len(canonical(r))) + canonical(r) for r in self.rows)
        return os.pread(self.fd, self.size + 1, 0) == raw

    def append(self, value):
        require(self.intact() and len(self.rows) < MAX_ROWS, 'OBSERVER_UNAVAILABLE')
        raw = canonical(value)
        require(0 < len(raw) <= MAX_RECORD and self.size + len(raw) + 4 <= 64 * 1024 * 1024,
                'LIMIT_EXCEEDED')
        try:
            require(os.write(self.fd, struct.pack('!I', len(raw)) + raw) == len(raw) + 4,
                    'OBSERVER_UNAVAILABLE')
            os.fsync(self.fd)
            self.rows.append(value)
            self.size += len(raw) + 4
        except BaseException:
            self.failed = True
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd); self.fd = None


class Witness:
    """The server owns storage; its authenticated API is append or inspect only."""
    def __init__(self, journal, identity):
        self.journal, self.identity = journal, identity
        self.events = []
        self.conflict = False
        for value in journal.rows:
            if value.get('format') == 'ra-broker-witness-conflict/1':
                self.conflict = True
            else:
                self._validate_next(value)
                self.events.append(value)

    def _validate_next(self, value):
        validate_event(value)
        prior = self.events[-1] if self.events else None
        require(value['sequence'] == len(self.events) + 1
                and value['previous'] == (prior['digest'] if prior else ZERO)
                and (prior is None or value['stream'] == prior['stream'])
                and all(e['event_id'] != value['event_id'] for e in self.events), 'CONFLICT')

    def append(self, value):
        require(self.journal.intact() and not self.conflict, 'OBSERVER_UNAVAILABLE')
        validate_event(value)
        old = next((r for r in self.events if r['event_id'] == value['event_id']), None)
        try:
            if old is not None:
                require(old == value, 'CONFLICT')
            else:
                self._validate_next(value)
                self.journal.append(value)
                self.events.append(value)
        except BaseException:
            self.conflict = True
            if self.journal.intact():
                self.journal.append(dict(format='ra-broker-witness-conflict/1',
                    incoming=value['digest'], previous=self.events[-1]['digest'] if self.events else ZERO))
            raise
        return acknowledgement(value, self.identity)

    def status(self):
        require(self.journal.intact() and not self.conflict, 'OBSERVER_UNAVAILABLE')
        # Existing authenticated inspection exposes a bounded floor from W's
        # retained metadata, including when J was restored to an earlier tail.
        return dict(format='ra-broker-witness-status/2', witness=self.identity,
                    sequence=len(self.events), head=self.events[-1]['digest'] if self.events else ZERO,
                    stream=self.events[0]['stream'] if self.events else None,
                    recovery=liability(self.events))


class Observations:
    def __init__(self, journal, witness, stream, hook=lambda stage: None):
        self.journal, self.witness, self.stream, self.hook = journal, witness, stream, hook
        self.events, self.acks = [], []
        self.failed = False
        self.retained = amount()
        for row in journal.rows:
            if row.get('format') == 'ra-broker-event/1':
                validate_event(row)
                prior = self.events[-1] if self.events else None
                require(len(self.events) == len(self.acks)
                        and row['sequence'] == len(self.events) + 1
                        and row['previous'] == (prior['digest'] if prior else ZERO)
                        and row['stream'] == stream
                        and all(e['event_id'] != row['event_id'] for e in self.events), 'OBSERVER_UNAVAILABLE')
                self.events.append(row)
            else:
                require(len(self.events) == len(self.acks) + 1
                        and row == acknowledgement(self.events[-1], witness.identity), 'OBSERVER_UNAVAILABLE')
                self.acks.append(row)

    def coverage(self):
        local_complete = not self.failed and self.journal.intact() and len(self.events) == len(self.acks)
        try:
            status = self.witness.status()
            require(set(status) == {'format', 'witness', 'sequence', 'head', 'stream', 'recovery'}
                    and status['format'] == 'ra-broker-witness-status/2'
                    and status['witness'] == self.witness.identity
                    and type(status['sequence']) is int and 0 <= status['sequence'] <= MAX_ROWS
                    and type(status['head']) is str and re.fullmatch('[0-9a-f]{64}', status['head'])
                    and (status['stream'] == self.stream or status['stream'] is None and status['sequence'] == 0),
                    'OBSERVER_UNAVAILABLE')
            if status['sequence'] == 0:
                require(status['stream'] is None and status['head'] == ZERO and status['recovery'] == amount())
            self.retained = merge(self.retained, status['recovery'])
            return local_complete and status == dict(format='ra-broker-witness-status/2', witness=self.witness.identity,
                sequence=len(self.events), head=self.events[-1]['digest'] if self.events else ZERO,
                stream=self.stream if self.events else None, recovery=liability(self.events))
        except Exception:
            return False

    def recovery_liability(self):
        # Neither a failed fresh read nor a missing local file erases a floor
        # already obtained from authenticated W. This grants no coverage.
        self.retained = merge(self.retained, liability(self.events))
        return self.retained

    def append(self, event_id, kind, data):
        old = next((v for v in self.events if v['event_id'] == event_id), None)
        if old is not None:
            if old['kind'] != kind or old['data'] != data:
                self.failed = True
            require(not self.failed and self.coverage(), 'CONFLICT')
            return old
        require(self.coverage(), 'OBSERVER_UNAVAILABLE')
        value = event(
            self.stream, len(self.events) + 1, self.events[-1]['digest'] if self.events else ZERO,
            event_id, kind, data)
        try:
            self.hook('before_j_fsync')
            self.journal.append(value)
            self.events.append(value)
            self.hook('after_j_fsync')
            ack = self.witness.append(value)
            require(ack == acknowledgement(value, self.witness.identity), 'OBSERVER_UNAVAILABLE')
            self.hook('after_w_ack')
            self.journal.append(ack)
            self.acks.append(ack)
            self.hook('after_local_ack')
            return value
        except BaseException:
            self.failed = True
            raise
