"""New, independently authored W3 synthetic fixtures; never runtime seed approvals."""
from datetime import timedelta
import pytest
from sqlalchemy import delete, select
from app.db.session import SessionLocal
from app.db.models.research_rule_validation import RuleValidation as RV, RuleFeedback as RF, RuleFeedbackReview as RR
from app.db.models.research_knowledge import KnowledgeVersion as KV
from app.services import research_rule_validation as service, research_rule_cases as suite
from tests.research_knowledge_fixtures import (  # noqa: F401
    knowledge_pair, subject_pair, two_intake_targets, content, record, call, NOW, REF, zero_capabilities,
)


@pytest.fixture
def rule_pair(knowledge_pair):
    try:
        yield knowledge_pair
    finally:
        with SessionLocal() as db:
            versions = select(KV.id).where(KV.context_id.in_([g['ctx'] for g in knowledge_pair]))
            feedback = select(RF.id).where(RF.version_id.in_(versions))
            db.execute(delete(RR).where(RR.feedback_id.in_(feedback)))
            db.execute(delete(RF).where(RF.version_id.in_(versions)))
            db.execute(delete(RV).where(RV.version_id.in_(versions)))
            db.commit()


def rule(g, number=1, **changes):
    c = content(number)
    c.update({'example_refs': [suite.POSITIVE], 'counterexample_refs': [suite.COUNTER], **changes})
    return record(g, c)['reference']


def validate(g, ref, until=None):
    return call(service.validate_rule, g['ctx'], {'reference': ref,
        'valid_until': (until or NOW+timedelta(hours=1)).isoformat()}, project=g['project'])


def feedback(g, ref, proof, proposal='promotion', reason='validation_passed', correction=None):
    return call(service.submit_feedback, g['ctx'], {'reference': ref, 'validation_ref': proof,
        'proposal': proposal, 'reason': reason, 'correction': correction, 'review': REF}, project=g['project'])


def review(g, ref, fid, decision='accept'):
    return call(service.review_feedback, g['ctx'], {'reference': ref, 'feedback_id': fid,
        'expected_sequence': 0, 'decision': decision, 'review': REF}, project=g['project'])


def publish(g, ref, proof):
    from app.services import research_knowledge as knowledge
    values = {'reference': ref, 'review': REF, 'valid_from': NOW.isoformat(),
              'valid_until': (NOW+timedelta(hours=1)).isoformat()}
    r = call(knowledge.decide, g['ctx'], {**values, 'expected_sequence': 0,
        'action': 'review', 'validation_ref': proof}, project=g['project'])
    u = call(knowledge.decide, g['ctx'], {**values, 'expected_sequence': 1, 'action': 'reuse'}, project=g['project'])
    return call(knowledge.decide, g['ctx'], {**values, 'expected_sequence': 2, 'action': 'publish',
        'validation_ref': proof, 'review_event_id': r['event_id'], 'reuse_event_id': u['event_id']}, project=g['project'])
