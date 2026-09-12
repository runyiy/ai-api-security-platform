"""Absolute operation bounds; completed reads cannot renew a call's allowance."""
from datetime import datetime, timezone
import math
import time

from .records import require, utc


class SystemClock:
    def monotonic_ns(self):
        return time.monotonic_ns()

    def utcnow(self):
        return datetime.now(timezone.utc)


class Deadline:
    def __init__(self, context, clock, cancelled, process_epoch):
        require(context.process_epoch == process_epoch, 'CONTEXT_CHANGED')
        self.context, self.clock, self.cancelled = context, clock, cancelled
        self.last_ns, self.last_wall = None, None
        self.valid_from, self.expires_at = None, None
        self.check()
        require(context.mono_deadline_ns - self.last_ns <= 30_000_000_000, 'DEADLINE_EXCEEDED')
        require((utc(context.deadline_at) - self.last_wall).total_seconds() <= 30, 'DEADLINE_EXCEEDED')

    def check(self, *windows, cleanup=False):
        ns, wall = self.clock.monotonic_ns(), self.clock.utcnow()
        require(type(ns) is int and ns >= 0 and type(wall) is datetime
                and wall.tzinfo is not None and wall.utcoffset() is not None, 'CONTEXT_CHANGED')
        require(self.last_ns is None or (ns >= self.last_ns and wall >= self.last_wall), 'CONTEXT_CHANGED')
        self.last_ns, self.last_wall = ns, wall
        if not cleanup:
            require(self.cancelled(self.context.cancellation_id) is False, 'CANCELLED')
            require(ns < self.context.mono_deadline_ns and wall < utc(self.context.deadline_at), 'DEADLINE_EXCEEDED')
            for start, end in windows:
                start, end = utc(start), utc(end)
                self.valid_from = max(self.valid_from, start) if self.valid_from is not None else start
                self.expires_at = min(self.expires_at, end) if self.expires_at is not None else end
            # Retain every qualified dependency window through later reads,
            # waits and final consumption. A completed lookup cannot discard a
            # shorter source/decision window in favor of the call deadline.
            require(self.valid_from is None or self.valid_from <= wall, 'SOURCE_UNAVAILABLE')
            require(self.expires_at is None or wall < self.expires_at, 'SOURCE_UNAVAILABLE')
        return wall

    def remaining(self, cap=30):
        self.check()
        require(type(cap) in (int, float) and math.isfinite(cap) and cap > 0)
        end = min(utc(self.context.deadline_at), self.expires_at or utc(self.context.deadline_at))
        return min(cap, (self.context.mono_deadline_ns - self.last_ns) / 1e9,
                   (end - self.last_wall).total_seconds())

    def sql_timeout(self, connection):
        from sqlalchemy import text
        timeout = max(1, math.ceil(self.remaining() * 1000))
        connection.execute(text("SELECT set_config('statement_timeout', :v, true), set_config('lock_timeout', :v, true)"), {'v': str(timeout)})
        self.check()
