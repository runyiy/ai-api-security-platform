"""Server UTC plus same-boot monotonic freshness; neither clock extends a deadline."""
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
        self.consumers = {}
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
        for mark, seconds, end in self.boundaries.values():
            _check(mark, self, seconds=seconds, end=end)
        return at

    def bind_intent(self, bind, project, context_id, reference):
        key=(context_id,reference['number'],reference['version'],reference['digest'])
        if key not in self.consumers and len(self.consumers)>=4:
            raise IntentError('verification_clock_limit')
        self.consumers[key]=(bind,project,context_id,dict(reference))

    def invalidate(self):
        self.poisoned=True
        from app.services.research_verification_fault import record
        for consumer in self.consumers.values():record(*consumer)

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
        if key not in clock.boundaries and len(clock.boundaries)>=64:
            raise IntentError('verification_clock_limit')
        clock.boundaries[key]=(dict(mark),seconds,end)
    return clock.last


def _check(mark, clock, *, seconds=None, end=None):
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
            raise IntentError('verification_expired')
    if seconds is not None:
        from datetime import timedelta
        if at >= start + timedelta(seconds=seconds) or clock.ns - mark['monotonic_ns'] >= seconds*1_000_000_000:
            raise IntentError('verification_expired')
    return at
