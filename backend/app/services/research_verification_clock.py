"""Server UTC plus same-boot monotonic freshness; neither clock extends a deadline."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import time
from uuid import uuid4
from app.schemas.research_intent import IntentError, stamp, timestamp

try:
    _boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
except OSError:
    # Other hosts/processes cannot qualify this fallback clock domain.
    _boot = str(uuid4())
DOMAIN = hashlib.sha256(_boot.encode('ascii')).hexdigest()


def utcnow():
    return datetime.now(timezone.utc)


def monotonic_ns():
    return time.monotonic_ns()


class Clock:
    def __init__(self, wall=None):
        self.wall = wall
        self.last = None
        self.ns = None
        self.boundaries = {}
        self.windows = {}
        self.consumers = {}
        self.active = set()
        self.poisoned = False

    def __call__(self):
        if self.poisoned:raise IntentError('verification_clock_invalidated')
        at = self.wall() if callable(self.wall) else self.wall
        at = timestamp(stamp(at if at is not None else utcnow()))
        ns = monotonic_ns()
        if type(ns) is not int or ns < 0 or (self.last is not None and (at < self.last or ns < self.ns)):
            self.invalidate()
            raise IntentError('intent_clock_invalid')
        self.last, self.ns = at, ns
        for start, end, consumers in self.windows.values():
            if not start <= at < end:
                self.expire(consumers)
                raise IntentError('intent_expired')
        for mark, seconds, end, consumers in self.boundaries.values():
            _check(mark, self, seconds=seconds, end=end, consumers=consumers)
        return at

    def bind_intent(self, bind, project, context_id, reference):
        key=(context_id,reference['number'],reference['version'],reference['digest'])
        if key not in self.consumers and len(self.consumers)>=4:
            raise IntentError('verification_clock_limit')
        self.consumers[key]=(bind,project,context_id,dict(reference))
        self.active.add(key)

    def watch_window(self, start, end):
        # Intent cores have UTC bounds, not a persisted monotonic creation mark.
        # Retain those exact bounds through nested work and final serialization;
        # never invent monotonic provenance for an immutable W1 core.
        key=(start,end)
        if key not in self.windows and len(self.windows)+len(self.boundaries)>=64:
            raise IntentError('verification_clock_limit')
        affected=set(self.active)
        if key in self.windows:affected.update(self.windows[key][2])
        self.windows[key]=(start,end,frozenset(affected))

    @contextmanager
    def dependency(self):
        # A nested health intent is affected by its own checks, but a later
        # business-only deadline must not fence otherwise valid health work.
        previous = set(self.active)
        try:yield
        finally:self.active = previous

    def expire(self, consumers=None):
        self.invalidate(self.active if consumers is None else consumers)

    def invalidate(self, consumers=None):
        self.poisoned=True
        from app.services.research_verification_fault import record
        for key, consumer in self.consumers.items():
            if consumers is None or key in consumers:record(*consumer)

    def mark(self):
        return {'at': stamp(self()), 'monotonic_ns': self.ns, 'clock_domain': DOMAIN}


def current(clock):
    if isinstance(clock, Clock):return clock
    if isinstance(getattr(clock, 'source', None), Clock):return clock.source
    return Clock(clock)


def check_mark(mark, clock, *, seconds=None, end=None):
    clock = current(clock)
    clock()
    _check(mark, clock, seconds=seconds, end=end)
    if seconds is not None or end is not None:
        key=(mark['clock_domain'],mark['monotonic_ns'],mark['at'],seconds,end)
        if key not in clock.boundaries and len(clock.boundaries)+len(clock.windows)>=64:
            raise IntentError('verification_clock_limit')
        affected=set(clock.active)
        if key in clock.boundaries:affected.update(clock.boundaries[key][3])
        clock.boundaries[key]=(dict(mark),seconds,end,frozenset(affected))
    return clock.last


def _check(mark, clock, *, seconds=None, end=None, consumers=None):
    at = clock.last
    start = timestamp(mark['at'])
    if (mark['clock_domain'] != DOMAIN or type(mark['monotonic_ns']) is not int
            or mark['monotonic_ns'] < 0 or clock.ns < mark['monotonic_ns'] or at < start):
        clock.invalidate()
        raise IntentError('verification_clock_anomaly')
    if end is not None:
        # Exact microseconds, no floating-point rounding or deadline grace.
        delta = end - start
        window_ns = (delta.days*86400 + delta.seconds)*1_000_000_000 + delta.microseconds*1000
        if at >= end or clock.ns - mark['monotonic_ns'] >= window_ns:
            clock.expire(consumers)
            raise IntentError('verification_expired')
    if seconds is not None:
        from datetime import timedelta
        if at >= start + timedelta(seconds=seconds) or clock.ns - mark['monotonic_ns'] >= seconds*1_000_000_000:
            clock.expire(consumers)
            raise IntentError('verification_expired')
    return at
