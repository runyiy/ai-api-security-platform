"""Current posting provenance and the original call's shorter expiry."""
from datetime import timedelta

import pytest
from sqlalchemy import delete, select, text, update

from app.db.session import engine
from app.ai.w2 import schema as t
from app.ai.w2.execution import execute_fake
from app.ai.w2.records import PortError, make, stamp, utc
from app.ai.w3.cache import AnalysisCache
from tests.ai.test_w2_execution import response
from tests.ai.test_w3_cache import remembered, read, sql_state, independent_state
from tests.ai.test_w3_cache_security import cache_only_capabilities  # noqa: F401
from tests.ai.w2_fixtures import (w2, rule_pair, knowledge_pair, subject_pair,
    two_intake_targets, zero_capabilities, KNOWN)  # noqa: F401


@pytest.mark.parametrize('fault', ['missing', 'settled_delta', 'held_delta', 'balance_id'])
def test_restored_posting_omission_or_corruption_denies_cached_origin(
        w2, tmp_path, cache_only_capabilities, fault):
    cache, runtime, completion, outcome = remembered(w2, tmp_path)
    assert read(cache, w2, runtime).display == outcome.display
    cache_only_capabilities()
    key = AnalysisCache.key_digest(runtime)
    before = sql_state(), independent_state(w2)
    assert len(before[0][t.posting.name]) == 4
    predicate = ((t.posting.c.settlement_id == completion.settlement_ref)
                 & (t.posting.c.scope_kind == 'account')
                 & (t.posting.c.dimension == 'tokens'))
    # This administrative injector belongs only to the independently verified
    # disposable TEST server. Simulate a damaged restoration of one posting;
    # leave the settlement, call, balances and independent journals untouched.
    with engine.begin() as db:
        db.execute(text("SET LOCAL session_replication_role='replica'"))
        row = db.execute(select(t.posting).where(predicate)).mappings().one()
        if fault == 'missing':
            changed = db.execute(delete(t.posting).where(predicate))
        else:
            value = (db.scalar(select(t.posting.c.balance_id).where(
                t.posting.c.settlement_id == completion.settlement_ref,
                t.posting.c.scope_kind == 'task', t.posting.c.dimension == 'tokens'))
                if fault == 'balance_id' else row[fault] + 1)
            changed = db.execute(update(t.posting).where(predicate).values(**{fault: value}))
        assert changed.rowcount == 1
    damaged = sql_state(), independent_state(w2)
    assert damaged[0][t.posting.name] != before[0][t.posting.name]
    assert {name: rows for name, rows in damaged[0].items() if name != t.posting.name} == {
        name: rows for name, rows in before[0].items() if name != t.posting.name}
    assert damaged[1] == before[1]
    with pytest.raises(PortError):
        read(cache, w2, runtime)
    assert key not in cache._entries and key not in cache._seals
    # Cache rejection cannot repair a posting, settle/refund, or reopen W2.
    assert (sql_state(), independent_state(w2)) == damaged
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1


def test_cache_expiry_metadata_preserves_shorter_original_run_and_permit(
        w2, tmp_path, cache_only_capabilities):
    w2.reopen()
    runtime = w2.runtime(reserve=False)
    expires = stamp(w2.clock.utcnow() + timedelta(seconds=15))
    assert utc(expires) < utc(runtime.prepared.core.expires_at)
    runtime.run = make('RunContext', **{**runtime.run.document(),
        'deadline_at': expires, 'mono_deadline_ns': w2.clock.monotonic_ns() + 15_000_000_000})
    runtime.deadline = w2.deadline(runtime.run)
    runtime.clock.deadline = runtime.deadline
    runtime.reserve_v1()
    completion, outcome = execute_fake(runtime, response(runtime), owned_directory=tmp_path)
    assert outcome.code is None and outcome.usage == KNOWN
    assert completion.display_state == 'ELIGIBLE_NOW'
    assert runtime.permit.expires_at == expires
    cache_only_capabilities()
    before = sql_state(), independent_state(w2)
    cache = AnalysisCache()
    key = cache.remember_v1(runtime, completion, outcome)
    hit = read(cache, w2, runtime)
    assert hit is not None and hit.display == outcome.display
    assert hit.expires_at == expires
    assert utc(hit.expires_at) < utc(completion.expires_at)
    assert (sql_state(), independent_state(w2)) == before
    w2.clock.advance(15)
    assert w2.clock.utcnow() == utc(expires) < utc(runtime.prepared.core.expires_at)
    with pytest.raises(PortError):
        read(cache, w2, runtime)
    assert key not in cache._entries and key not in cache._seals
    assert (sql_state(), independent_state(w2)) == before
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1


@pytest.mark.parametrize('limit', ['wall', 'monotonic'])
def test_cache_expiry_metadata_preserves_shorter_fresh_read_deadline(
        w2, tmp_path, cache_only_capabilities, limit):
    cache, runtime, _, outcome = remembered(w2, tmp_path)
    cache_only_capabilities()
    original = w2.read()
    expires = stamp(w2.clock.utcnow() + timedelta(seconds=5))
    fields = ({'deadline_at': expires} if limit == 'wall' else
        {'mono_deadline_ns': w2.clock.monotonic_ns() + 5_000_000_000})
    context = make('ReadContext', **{**original.document(), **fields})
    deadline = w2.deadline(context)
    assert deadline.remaining() == 5
    assert utc(expires) < utc(runtime.run.deadline_at) <= utc(runtime.prepared.core.expires_at)
    before = sql_state(), independent_state(w2)
    def finish_wait(stage, token):
        if stage == 'after_release' and limit == 'monotonic':
            w2.clock.ns += 4_000_000_000
    cache.hook = finish_wait
    hit = cache.read_v1(runtime, context, deadline)
    assert hit is not None and hit.display == outcome.display
    if limit == 'monotonic':
        expires = stamp(w2.clock.utcnow() + timedelta(seconds=1))
    assert hit.expires_at == expires
    assert (sql_state(), independent_state(w2)) == before
    cache.hook = lambda stage, token: None
    w2.clock.advance(1 if limit == 'monotonic' else 5)
    assert w2.clock.utcnow() == utc(hit.expires_at) < utc(runtime.run.deadline_at)
    with pytest.raises(PortError):
        cache.read_v1(runtime, context, deadline)
    key = AnalysisCache.key_digest(runtime)
    assert key not in cache._entries and key not in cache._seals
    assert (sql_state(), independent_state(w2)) == before
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1
