"""Untrusted cache bytes and current eligibility never confer display authority."""
from datetime import timedelta
from unittest.mock import Mock
import json

import pytest
from sqlalchemy import select, text

from app.db.session import SessionLocal, engine
from app.db.models.research_context import ResearchContext
from app.ai.proposals.codec import canonical
from app.ai.w2 import schema as t
from app.ai.w2.execution import execute_fake
from app.ai.w2.records import PortError, utc
from app.ai.w3.cache import AnalysisCache, decode_analysis
from tests.ai.test_w2_execution import response
from tests.ai.test_w2_lifecycle import invalidate
from tests.ai.w2_fixtures import (w2, rule_pair, knowledge_pair, subject_pair,
    two_intake_targets, zero_capabilities, KNOWN)  # noqa: F401


@pytest.fixture
def cache_only_capabilities(monkeypatch):
    blocked = Mock(side_effect=AssertionError('cached analysis attempted model capability'))

    def arm():
        for path in (
            'app.ai.proposals.adapter.OpenAIProposalAdapter.propose_once',
            'app.ai.w2.w1_bridge.CallRuntime.reserve_v1',
            'app.ai.w2.w1_bridge.CallRuntime.admit',
            'app.ai.proposals.transport.MemorySecret.__call__',
            'app.ai.proposals.transport.MemoryResolver.__call__',
            'app.ai.proposals.transport.MemoryConnector.__call__',
            'app.ai.proposals.transport.MemoryWire.write',
        ):
            monkeypatch.setattr(path, blocked)

    yield arm
    blocked.assert_not_called()


def _ledger():
    """Fresh SQL evidence; cache reads may not allocate or alter a model call."""
    with SessionLocal() as db:
        return {
            table.name: list(db.execute(select(table).order_by(*table.primary_key.columns)).mappings())
            for table in (t.balance, t.reservation, t.admission, t.event,
                          t.settlement, t.posting, t.completion)
        }


def _remember(w2, tmp_path, cache_only_capabilities):
    w2.reopen()
    runtime = w2.runtime()
    completion, outcome = execute_fake(runtime, response(runtime), owned_directory=tmp_path)
    assert outcome.code is None and outcome.usage == KNOWN and outcome.display
    cache_only_capabilities()
    cache = AnalysisCache()
    key = cache.remember_v1(runtime, completion, outcome)
    read = w2.read()
    hit = cache.read_v1(runtime, read, w2.deadline(read))
    assert hit is not None and hit.display == outcome.display
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1
    return cache, key, runtime


def _no_new_call(w2, runtime, before):
    assert _ledger() == before
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1
    assert w2.authority.coverage()


@pytest.mark.parametrize('poison', ['secret', 'pii', 'authority', 'foreign_reference'])
def test_untrusted_cached_analysis_is_reparsed_evicted_and_not_leaked(
        w2, tmp_path, cache_only_capabilities, caplog, capsys, poison):
    cache, key, runtime = _remember(w2, tmp_path, cache_only_capabilities)
    doc = json.loads(cache._entries[key])
    suggestion = doc['proposal']['suggestions'][0]
    if poison == 'secret':
        canary = 'SYNTHETIC_CACHE_SECRET_MUST_NOT_ESCAPE'
        suggestion['reason_code'] = canary
    elif poison == 'pii':
        canary = 'synthetic.cache.person@example.invalid'
        suggestion['candidate_ref'] = canary
    elif poison == 'authority':
        canary = 'SYNTHETIC_CACHE_EXECUTION_AUTHORITY'
        suggestion['approved'] = canary
    else:
        prepared, _ = w2.prepare()
        other = w2.preparation.prepared[prepared.prepared_ref]
        canary = json.loads(other.prepared.payload)['candidates'][0]['ref']
        assert canary not in runtime.prepared.prepared.payload.decode()
        suggestion['candidate_ref'] = canary
    corrupted = canonical(doc)
    # The parser itself must reject, independently of the parent integrity seal.
    with pytest.raises(PortError) as parser_error:
        decode_analysis(corrupted, runtime, key)
    assert canary not in str(parser_error.value)
    before = _ledger()
    journal = w2.journal.path.read_bytes()
    witness = w2.witness.journal.path.read_bytes()
    cache._entries[key] = corrupted
    read = w2.read()
    with pytest.raises(PortError) as cache_error:
        cache.read_v1(runtime, read, w2.deadline(read))
    assert key not in cache._entries and key not in cache._seals
    output = capsys.readouterr()
    assert canary not in caplog.text + output.out + output.err + str(cache_error.value)
    assert canary.encode() not in w2.journal.path.read_bytes() + w2.witness.journal.path.read_bytes()
    assert w2.journal.path.read_bytes() == journal
    assert w2.witness.journal.path.read_bytes() == witness
    _no_new_call(w2, runtime, before)


@pytest.mark.parametrize('kind', ['context', 'publication'])
def test_cached_analysis_rechecks_real_lifecycle_writer(w2, tmp_path, cache_only_capabilities, kind):
    cache, key, runtime = _remember(w2, tmp_path, cache_only_capabilities)
    before = _ledger()
    witness_ids = set(w2.witness.acceptance_ids)
    old_operations = set(w2.authority.operations)
    invalidate(w2, kind)
    new_operations = set(w2.authority.operations) - old_operations
    assert len(new_operations) == 1
    operation_id = new_operations.pop()
    ack = w2.authority.operations[operation_id]
    assert ack.state == 'CLOSED' and ack.disposition == 'COMMITTED'
    with SessionLocal() as db:
        assert db.scalar(select(t.invalidation.c.operation_id).where(
            t.invalidation.c.operation_id == operation_id)) == operation_id
    read = w2.read()
    with pytest.raises(PortError):
        cache.read_v1(runtime, read, w2.deadline(read))
    assert key not in cache._entries
    assert w2.witness.acceptance_ids == witness_ids
    _no_new_call(w2, runtime, before)


@pytest.mark.parametrize('change', ['within', 'expiry_equal', 'wall_rollback', 'mono_rollback'])
def test_cached_analysis_checks_completed_lookup_time(w2, tmp_path, cache_only_capabilities, change):
    cache, key, runtime = _remember(w2, tmp_path, cache_only_capabilities)
    before = _ledger()
    journal = w2.journal.path.read_bytes()
    witness = w2.witness.journal.path.read_bytes()
    seen = []

    def hook(stage, token):
        if stage == 'after_lookup':
            seen.append(stage)
            if change == 'within':
                w2.clock.advance(.5)
            elif change == 'expiry_equal':
                w2.clock.advance((utc(runtime.prepared.core.expires_at) - w2.clock.utcnow()).total_seconds())
            elif change == 'wall_rollback':
                w2.clock.wall -= timedelta(microseconds=1)
            else:
                w2.clock.ns -= 1

    cache.hook = hook
    read = w2.read()
    deadline = w2.deadline(read)
    if change == 'within':
        assert cache.read_v1(runtime, read, deadline) is not None
        assert key in cache._entries
    else:
        with pytest.raises(PortError):
            cache.read_v1(runtime, read, deadline)
        assert key not in cache._entries
    assert seen == ['after_lookup']
    assert w2.journal.path.read_bytes() == journal
    assert w2.witness.journal.path.read_bytes() == witness
    _no_new_call(w2, runtime, before)


def test_g_session_loss_and_committed_invalidation_deny_stale_cache_hit(w2, tmp_path, cache_only_capabilities):
    cache, key, runtime = _remember(w2, tmp_path, cache_only_capabilities)
    before = _ledger()
    original_permit = runtime.permit
    original_operations = set(w2.authority.operations)
    witness_ids = set(w2.witness.acceptance_ids)
    checkpoints = []
    assert original_permit.owner_generation == runtime.run.owner_generation == 1
    assert w2.clock.utcnow() < utc(original_permit.expires_at)

    def hook(stage, token):
        if stage != 'before_consume':
            return
        assert token is not None and not token.connection.in_transaction()
        with engine.begin() as db:
            assert db.scalar(text('SELECT pg_terminate_backend(:pid)'), {'pid': token.backend_pid}) is True
        with engine.begin() as db:
            assert db.scalar(text("SELECT count(*) FROM pg_locks WHERE pid=:pid AND locktype='advisory'"),
                             {'pid': token.backend_pid}) == 0
        invalidate(w2, 'context')
        with SessionLocal() as db:
            assert db.get(ResearchContext, w2.g['ctx']).closed_at is not None
        checkpoints.append(stage)

    cache.hook = hook
    read = w2.read()
    with pytest.raises(PortError):
        cache.read_v1(runtime, read, w2.deadline(read))
    assert checkpoints == ['before_consume']
    assert key not in cache._entries
    assert runtime.permit == original_permit
    assert runtime.run.owner_generation == original_permit.owner_generation == 1
    assert w2.clock.utcnow() < utc(original_permit.expires_at)
    new_operations = set(w2.authority.operations) - original_operations
    assert len(new_operations) == 1
    operation_id = new_operations.pop()
    with SessionLocal() as db:
        assert db.scalar(select(t.invalidation.c.operation_id).where(
            t.invalidation.c.operation_id == operation_id)) == operation_id
        assert db.scalar(select(t.reservation.c.owner_generation)) == 1
        assert len(list(db.execute(select(t.admission)))) == 1
    durable = [row['payload'] for row in w2.journal.entries
               if row['payload'].get('operation_id') == operation_id]
    assert any(row.get('state') == 'CLOSED' and row.get('disposition') == 'PENDING' for row in durable)
    assert any(row.get('state') == 'CLOSED' and row.get('disposition') == 'COMMITTED' for row in durable)
    assert w2.witness.head_sequence == len(w2.journal.entries)
    assert w2.witness.head_digest == w2.journal.head
    assert w2.witness.acceptance_ids == witness_ids
    _no_new_call(w2, runtime, before)
