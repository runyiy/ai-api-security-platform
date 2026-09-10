from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import timedelta
import pytest
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import research_rule_validation as service, research_knowledge as knowledge, research_observation as observation
from app.schemas.research_knowledge import KnowledgeError
from tests.services.test_research_context_isolation import wait_for_blocker
from tests.research_rule_fixtures import (  # noqa: F401
    rule_pair, knowledge_pair, subject_pair, two_intake_targets, rule, validate, feedback,
    NOW, REF, zero_capabilities,
)
from tests.research_knowledge_fixtures import source


@pytest.mark.parametrize('action', ['disable', 'hold', 'close'])
@pytest.mark.parametrize('read_first', [True, False])
def test_real_transactions_serialize_validation_and_eligibility(rule_pair, action, read_first):
    g, _ = rule_pair
    ref = rule(g, source_refs=[source(g)])
    ready, release, waiting = Event(), Event(), Event()
    pids = {}
    def worker(reading, leader):
        with SessionLocal() as db:
            pids[leader] = db.scalar(text('SELECT pg_backend_pid()'))
            if not leader:waiting.set()
            try:
                if reading:
                    result = service.validate_rule(db, g['project'], g['ctx'], {'reference': ref,
                        'valid_until': (NOW+timedelta(hours=1)).isoformat()}, now=NOW)
                elif action == 'disable':
                    result = knowledge.decide(db, g['project'], g['ctx'], {'reference': ref, 'expected_sequence': 0,
                        'action': 'disable', 'review': REF, 'valid_from': None, 'valid_until': None}, now=NOW)
                elif action == 'hold':
                    result = observation.lifecycle(db, g['project'], g['ctx'], g['observation'], 'hold',
                        {'review': REF, 'reason': 'synthetic_review', 'until': (NOW+timedelta(hours=1)).isoformat()}, now=NOW)
                else:
                    result = knowledge.intake.close_context(db, g['project'], g['ctx'],
                        {'expected_version': 1, 'closure_reference': REF}, now=NOW)
            except KnowledgeError as exc:result = exc.code
            if leader:
                ready.set()
                assert release.wait(10)
            db.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker, read_first, True)
        assert ready.wait(10)
        second = pool.submit(worker, not read_first, False)
        assert waiting.wait(10)
        try:wait_for_blocker(pids[False], pids[True])
        finally:release.set()
        a, b = first.result(15), second.result(15)
    if read_first:assert a['status'] == 'ready_for_review'
    else:assert b == 'knowledge_unavailable'


def test_concurrent_feedback_reviews_have_one_immutable_winner(rule_pair):
    g, _ = rule_pair
    ref = rule(g)
    proof = validate(g, ref)['validation_ref']
    fid = feedback(g, ref, proof)['feedback_id']
    def worker(decision):
        with SessionLocal() as db:
            try:
                result = service.review_feedback(db, g['project'], g['ctx'], {'reference': ref, 'feedback_id': fid,
                    'expected_sequence': 0, 'decision': decision, 'review': REF}, now=NOW)['status']
            except KnowledgeError as exc:result = exc.code
            db.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, decision) for decision in ('accept', 'reject')]
        results = [f.result(15) for f in futures]
    assert results.count('knowledge_decision_conflict') == 1
    assert sum(r in {'accept', 'reject'} for r in results) == 1
