"""Clock transitions must preserve one consistent, qualified retrieval result."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.models.resource_access_assertion import ResourceAccessAssertion
from app.db.session import SessionLocal
from app.schemas import research_knowledge as s
from app.services import research_knowledge as service, research_observation as observation
from tests.research_intake_fixtures import snapshot
from tests.research_knowledge_fixtures import (  # noqa: F401
    knowledge_pair, permission_knowledge_pair, subject_pair, two_intake_targets,
    content, published, query, retrieve, future_assertion, NOW, zero_capabilities,
)


END = NOW + timedelta(seconds=1)


def advance_during(monkeypatch, stage, at):
    clock = [NOW]
    monkeypatch.setattr(observation, '_time', lambda now: clock[0])
    owner, name = (s.QueryRead, 'model_dump') if stage == 'serialization' else (
        service, '_rank' if stage == 'ranking' else '_audit')
    original = getattr(owner, name)

    def advancing(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] = at
        return result

    monkeypatch.setattr(owner, name, advancing)


def consume(g, at):
    before = snapshot()
    with SessionLocal() as db:
        if at >= END:
            with pytest.raises(s.KnowledgeError, match='knowledge_unavailable'):
                service.retrieve(db, g['project'], g['ctx'], query(), now=NOW)
            # Even a caller committing after rejection cannot retain query/source audits.
            db.commit()
            assert snapshot() == before
        else:
            value = service.retrieve(db, g['project'], g['ctx'], query(), now=NOW)
            assert value['matches']
            assert not {'permission_missing', 'facts_conflict'} & set(value['missing_inputs'])
            assert s.timestamp(value['eligibility_until']) == END
            db.commit()


@pytest.mark.parametrize('stage', ['ranking', 'audit', 'serialization'])
@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_permission_expiry_retained_through_consumption(permission_knowledge_pair, monkeypatch, stage, offset):
    g, _ = permission_knowledge_pair
    published(g)
    at = END + timedelta(microseconds=offset)
    advance_during(monkeypatch, stage, at)
    consume(g, at)


@pytest.mark.parametrize('field', ['valid_from', 'asserted_at'])
@pytest.mark.parametrize('stage', ['ranking', 'audit', 'serialization'])
@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_unselected_conflict_eligibility_retained_through_consumption(knowledge_pair, monkeypatch, field, stage, offset):
    g, _ = knowledge_pair
    future_assertion(g, END, field=field)
    published(g)
    at = END + timedelta(microseconds=offset)
    advance_during(monkeypatch, stage, at)
    consume(g, at)


def test_fresh_read_at_permission_expiry_recomputes_gaps(permission_knowledge_pair):
    g, _ = permission_knowledge_pair
    published(g)
    assert retrieve(g, now=END-timedelta(microseconds=1))['matches']
    value = retrieve(g, now=END)
    assert not value['matches'] and 'permission_missing' in value['missing_inputs']
    assert s.timestamp(value['eligibility_until']) > END


@pytest.mark.parametrize('field', ['valid_from', 'asserted_at'])
def test_fresh_read_at_fact_start_includes_unselected_conflict(knowledge_pair, field):
    g, _ = knowledge_pair
    future_assertion(g, END, field=field)
    published(g)
    before = retrieve(g, now=END-timedelta(microseconds=1))
    assert before['matches'] and s.timestamp(before['eligibility_until']) == END
    value = retrieve(g, now=END)
    assert not value['matches'] and 'facts_conflict' in value['missing_inputs']


def test_no_match_response_retains_future_fact_boundary(knowledge_pair, monkeypatch):
    g, _ = knowledge_pair
    future_assertion(g, END)
    value = retrieve(g)
    assert not value['matches'] and s.timestamp(value['eligibility_until']) == END
    before = snapshot()
    advance_during(monkeypatch, 'audit', END)
    with pytest.raises(s.KnowledgeError, match='knowledge_unavailable'):
        retrieve(g)
    assert snapshot() == before


def test_fact_expiry_deadline_and_fresh_boundary(knowledge_pair, monkeypatch):
    g, _ = knowledge_pair
    with SessionLocal() as db:
        row = db.scalar(select(ResourceAccessAssertion).where(ResourceAccessAssertion.resource_id == g['resource']))
        row.valid_from, row.valid_until = NOW-timedelta(seconds=1), END
        db.commit()
    published(g)
    before = retrieve(g, now=END-timedelta(microseconds=1))
    assert before['matches'] and s.timestamp(before['eligibility_until']) == END
    value = retrieve(g, now=END)
    assert not value['matches'] and 'facts_missing' in value['missing_inputs']
    advance_during(monkeypatch, 'audit', END)
    consume(g, END)


def test_assertion_transition_uses_later_start_and_ignores_empty_windows(knowledge_pair):
    g, _ = knowledge_pair
    later = END + timedelta(seconds=1)
    aid = future_assertion(g, END)
    with SessionLocal() as db:
        db.get(ResourceAccessAssertion, aid).asserted_at = later
        db.commit()
    published(g)
    assert s.timestamp(retrieve(g)['eligibility_until']) == later
    with SessionLocal() as db:
        db.get(ResourceAccessAssertion, aid).valid_until = later
        db.commit()
    assert s.timestamp(retrieve(g)['eligibility_until']) > later


def test_future_transition_scope_and_capacity(knowledge_pair):
    g, other = knowledge_pair
    published(g)
    baseline = retrieve(g)['eligibility_until']
    future_assertion(other, END)
    future_assertion(g, END, identity=g['bearer'])
    future_assertion(g, END, state='candidate')
    assert retrieve(g)['eligibility_until'] == baseline
    with SessionLocal() as db:
        db.add_all([ResourceAccessAssertion(resource_id=g['resource'], test_identity_id=g['anonymous'],
            relationship='non_owner', expected_access='denied', provenance='target_fixture', confidence=80,
            verification_state='verified', asserted_at=NOW, valid_from=END) for _ in range(255)])
        db.commit()
    assert s.timestamp(retrieve(g)['eligibility_until']) == END  # 256 current/future windows
    future_assertion(g, END)
    before = snapshot()
    with pytest.raises(s.KnowledgeError, match='knowledge_scan_limit'):
        retrieve(g)
    assert snapshot() == before


@pytest.mark.parametrize('category', ['mechanism', 'rule'])
def test_actor_excluded_before_ranking_topk(knowledge_pair, monkeypatch, category):
    g, _ = knowledge_pair
    denied = content(1, category=category)
    denied['applicability']['actors'] = ['bearer']
    published(g, denied)
    allowed = published(g, content(2, category=category))
    ranked = []
    original = service._rank

    def rank(c, q):
        ranked.append(c.knowledge_id)
        return original(c, q)

    monkeypatch.setattr(service, '_rank', rank)
    value = retrieve(g, query(top_k=1))
    assert [m['reference'] for m in value['matches']] == [allowed]
    assert ranked == ['knowledge-2']


def test_matching_actor_mechanism_remains_available_with_missing_facts(knowledge_pair):
    g, _ = knowledge_pair
    with SessionLocal() as db:
        row = db.scalar(select(ResourceAccessAssertion).where(ResourceAccessAssertion.resource_id == g['resource']))
        row.verification_state = 'candidate'
        db.commit()
    c = content(category='mechanism')
    c['applicability']['actors'] = ['anonymous']
    ref = published(g, c)
    value = retrieve(g)
    assert 'facts_missing' in value['missing_inputs']
    assert value['matches'][0]['reference'] == ref
    assert value['matches'][0]['applicability'] == 'general_explanation'
