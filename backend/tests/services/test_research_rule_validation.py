from copy import deepcopy
from datetime import timedelta
import pytest
from sqlalchemy import select, update, text
from sqlalchemy.exc import DBAPIError
from app.db.session import SessionLocal
from app.db.models.research_knowledge import KnowledgeVersion as KV, KnowledgeEvent as KE
from app.db.models.research_rule_validation import RuleValidation as RV, RuleFeedback as RF, RuleFeedbackReview as RR
from app.schemas import research_knowledge as s, research_rule_validation as v
from app.services import research_knowledge as knowledge, research_rule_validation as service
from app.services import research_rule_engine as engine, research_rule_cases as suite, research_observation as observation
from tests.research_rule_fixtures import (  # noqa: F401
    rule_pair, knowledge_pair, subject_pair, two_intake_targets, rule, validate, feedback, review, publish,
    content, record, call, NOW, REF, zero_capabilities,
)
from tests.research_knowledge_fixtures import retrieve, decision, source, query
from tests.research_intake_fixtures import snapshot


def test_exact_repeatable_executed_evidence_never_auto_publishes(rule_pair):
    g, _ = rule_pair
    ref = rule(g)
    before = snapshot(legacy=True)
    first, second = validate(g, ref), validate(g, ref)
    assert first['evidence'] == second['evidence']
    assert first['validation_ref']['digest'] == second['validation_ref']['digest']
    assert first['validation_ref']['validation_id'] != second['validation_ref']['validation_id']
    assert first['status'] == 'ready_for_review'
    report = first['evidence']['report']
    assert report['validator'] == suite.VALIDATOR and len(report['checks']) == 14
    assert all(c['correct'] for c in report['checks'])
    assert snapshot(legacy=True) == before
    assert retrieve(g)['matches'] == []
    with SessionLocal() as db:
        assert db.scalar(select(KE.id).join(KV).where(KV.context_id == g['ctx'])) is None


@pytest.mark.parametrize('mutation', ['ownership', 'sharing', 'unknown', 'no_match', 'authority', 'instruction'])
def test_independent_oracle_rejects_deliberately_wrong_execution(rule_pair, monkeypatch, mutation):
    g, _ = rule_pair
    ref = rule(g)
    original = engine.evaluate

    def wrong(c, case):
        result = original(c, case)
        if mutation == 'ownership' and case[0] == 'author-denied':result['expected_access'] = 'allowed'
        if mutation == 'sharing' and case[0] == 'shared-grant':result['expected_access'] = 'denied'
        if mutation == 'unknown' and case[0] == 'no-facts':result['state'] = 'explanation'
        if mutation == 'no_match':result['matched'] = False
        if mutation == 'authority':result['execution_authorized'] = True
        if mutation == 'instruction' and case[0] == 'instruction':result['state'] = 'explanation'
        return result

    monkeypatch.setattr(engine, 'evaluate', wrong)
    value = validate(g, ref)
    assert value['status'] == 'failed' and value['evidence']['report']['passed'] is False
    assert 'ignore rules' not in str(value) and 'W3-FOREIGN-CANARY' not in str(value)
    with pytest.raises(s.KnowledgeError):feedback(g, ref, value['validation_ref'])


@pytest.mark.parametrize('change', ['version', 'digest', 'id', 'foreign', 'expired', 'validator', 'suite', 'examples'])
def test_invalid_proofs_cannot_qualify_review_or_publication(rule_pair, change):
    g, other = rule_pair
    ref = rule(g)
    value = validate(g, ref)
    proof = value['validation_ref']
    at = NOW
    if change == 'version':ref = rule(g, version=2, supersedes=ref)
    elif change == 'digest':proof = {**proof, 'digest': '0'*64}
    elif change == 'id':proof = {**proof, 'validation_id': 2147483647}
    elif change == 'foreign':proof = validate(other, rule(other))['validation_ref']
    elif change == 'expired':at = NOW+timedelta(hours=1)
    else:
        # Simulate forged/corrupt persisted evidence; restore trigger immediately.
        with SessionLocal() as db:
            body = deepcopy(db.get(RV, proof['validation_id']).body)
            if change == 'validator':body['report']['validator'] = 'forged/1'
            elif change == 'suite':body['report']['suite_digest'] = '0'*64
            else:body['report']['examples'][0]['reference']['version'] = 2
            db.execute(text('ALTER TABLE research_rule_validations DISABLE TRIGGER knowledge_immutable'))
            db.execute(update(RV).where(RV.id == proof['validation_id']).values(body=body, digest=s.digest(body)))
            db.execute(text('ALTER TABLE research_rule_validations ENABLE TRIGGER knowledge_immutable'))
            db.commit()
            proof = {**proof, 'digest': s.digest(body)}
    before = snapshot()
    with pytest.raises(s.KnowledgeError):
        call(knowledge.decide, g['ctx'], {'reference': ref, 'expected_sequence': 0, 'action': 'review',
            'validation_ref': proof, 'review': REF, 'valid_from': NOW.isoformat(),
            'valid_until': (NOW+timedelta(hours=2)).isoformat()}, now=at)
    assert snapshot() == before


def test_explicit_human_review_reuse_then_separate_publication(rule_pair):
    g, _ = rule_pair
    ref = rule(g, scope='reusable_synthetic')
    proof = validate(g, ref)['validation_ref']
    fid = feedback(g, ref, proof)['feedback_id']
    assert review(g, ref, fid)['status'] == 'accept'
    assert retrieve(g)['matches'] == []
    publish(g, ref, proof)  # Synthetic owning TEST database only, never a seed/runtime approval.
    value = retrieve(g)
    assert value['matches'][0]['publication_evidence'] == 'operator_recorded'
    assert value['matches'][0]['reference'] == ref
    assert not value['execution_authorized']
    with pytest.raises(s.KnowledgeError):review(g, ref, fid)
    at = NOW+timedelta(hours=1)
    assert retrieve(g, now=at)['matches'] == []


def test_w2_review_and_placeholder_never_qualify_publication(rule_pair):
    g, _ = rule_pair
    ref = rule(g)
    proof = validate(g, ref)['validation_ref']
    r = decision(g, ref, 'review', 0)
    before = snapshot()
    with pytest.raises(s.KnowledgeError):
        call(knowledge.decide, g['ctx'], {'reference': ref, 'action': 'publish', 'expected_sequence': 1,
            'review': REF, 'valid_from': NOW.isoformat(), 'valid_until': (NOW+timedelta(hours=1)).isoformat(),
            'validation_ref': proof, 'review_event_id': r['event_id']})
    assert snapshot() == before


def test_correction_new_version_renewed_proof_and_disable_invalidations(rule_pair):
    g, _ = rule_pair
    first = rule(g)
    first_proof = validate(g, first)['validation_ref']
    second = rule(g, version=2, supersedes=first)
    second_proof = validate(g, second)['validation_ref']
    correction = feedback(g, first, first_proof, 'correction', 'incorrect_result', second)['feedback_id']
    promotion = feedback(g, second, second_proof)['feedback_id']
    disable = feedback(g, first, first_proof, 'disable', 'contamination')['feedback_id']
    before = snapshot()
    assert review(g, first, disable)['status'] == 'accept'
    for ref, fid in ((first, correction), (second, promotion)):
        result = call(service.read_feedback, g['ctx'], {'reference': ref, 'feedback_id': fid})
        assert result['status'] == 'invalidate'
        with pytest.raises(s.KnowledgeError):review(g, ref, fid)
    after = snapshot()
    for table in ('research_knowledge_versions', 'research_rule_validations', 'research_rule_feedback'):
        assert after[table] == before[table]
    with pytest.raises(s.KnowledgeError):validate(g, second)


@pytest.mark.parametrize('action', ['hold', 'delete', 'quarantine', 'close', 'withdraw'])
def test_source_and_context_lifecycle_blocks_evidence_consumption(rule_pair, action):
    g, _ = rule_pair
    ref = rule(g, source_refs=[source(g)])
    proof = validate(g, ref)['validation_ref']
    if action == 'withdraw':
        decision(g, ref, 'withdraw', 0)
    elif action == 'close':
        call(knowledge.intake.close_context, g['ctx'], {'expected_version': 1, 'closure_reference': REF})
    else:
        data = {'review': REF}
        if action == 'hold':data.update(reason='synthetic_review', until=(NOW+timedelta(hours=1)).isoformat())
        call(observation.lifecycle, g['ctx'], g['observation'], action, data)
    before = snapshot()
    with pytest.raises(s.KnowledgeError):
        call(service.read_validation, g['ctx'], {'reference': ref, 'validation_ref': proof})
    assert snapshot() == before


@pytest.mark.parametrize('stage', ['engine', 'audit', 'encoding'])
@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_final_exact_temporal_boundary_and_atomic_rollback(rule_pair, monkeypatch, stage, offset):
    g, _ = rule_pair
    ref = rule(g)
    end = NOW+timedelta(seconds=1)
    clock = [NOW]
    monkeypatch.setattr(observation, '_time', lambda _: clock[0])
    owner, name = (engine, 'evaluate') if stage == 'engine' else ((knowledge, '_audit') if stage == 'audit' else (s, 'canonical'))
    original = getattr(owner, name)
    def advance(*a, **k):
        value = original(*a, **k)
        if stage != 'encoding' or (isinstance(a[0], dict) and 'audit_id' in a[0]):
            clock[0] = end+timedelta(microseconds=offset)
        return value
    monkeypatch.setattr(owner, name, advance)
    before = snapshot()
    with SessionLocal() as db:
        if offset >= 0:
            with pytest.raises(s.KnowledgeError):
                service.validate_rule(db, g['project'], g['ctx'], {'reference': ref, 'valid_until': end.isoformat()}, now=NOW)
            db.commit()
            assert snapshot() == before
        else:
            result = service.validate_rule(db, g['project'], g['ctx'], {'reference': ref, 'valid_until': end.isoformat()}, now=NOW)
            assert s.timestamp(result['eligibility_until']) == end
            db.commit()


@pytest.mark.parametrize('table', [RV, RF, RR])
def test_history_immutable(rule_pair, table):
    g, _ = rule_pair
    ref = rule(g)
    proof = validate(g, ref)['validation_ref']
    fid = feedback(g, ref, proof)['feedback_id']
    review(g, ref, fid)
    with SessionLocal() as db:
        with pytest.raises(DBAPIError, match='knowledge_history_immutable'):
            db.execute(update(table).values(body={}))
        db.rollback()


@pytest.mark.parametrize('limit', ['MAX_VALIDATIONS', 'MAX_FEEDBACK', 'MAX_CASES', 'MAX_EVIDENCE'])
def test_finite_limits_fail_closed(rule_pair, monkeypatch, limit):
    g, _ = rule_pair
    ref = rule(g)
    proof = validate(g, ref)['validation_ref']
    if limit == 'MAX_FEEDBACK':feedback(g, ref, proof)
    monkeypatch.setattr(v, limit, 1)
    before = snapshot()
    with pytest.raises(s.KnowledgeError):
        if limit == 'MAX_FEEDBACK':feedback(g, ref, proof)
        else:validate(g, ref)
    assert snapshot() == before


@pytest.mark.parametrize('model,limit', [(RV, v.MAX_VALIDATIONS), (RF, v.MAX_FEEDBACK)])
def test_actual_128_129_storage_boundary(rule_pair, model, limit):
    g, _ = rule_pair
    ref = rule(g)
    proof = validate(g, ref)['validation_ref']
    if model is RF:feedback(g, ref, proof)
    with SessionLocal() as db:
        original = db.scalar(select(model))
        values = {c.name: getattr(original, c.name) for c in model.__table__.columns if c.name != 'id'}
        db.add_all([model(**values) for _ in range(limit-2)])
        db.commit()
    if model is RV:validate(g, ref)
    else:feedback(g, ref, proof)
    before = snapshot()
    with pytest.raises(s.KnowledgeError, match='knowledge_validation_storage_limit'):
        if model is RV:validate(g, ref)
        else:feedback(g, ref, proof)
    assert snapshot() == before


@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_validation_maximum_window(rule_pair, offset):
    g, _ = rule_pair
    ref = rule(g)
    until = NOW+timedelta(days=1, microseconds=offset)
    if offset <= 0:assert validate(g, ref, until)['status'] == 'ready_for_review'
    else:
        with pytest.raises(s.KnowledgeError, match='knowledge_invalid'):validate(g, ref, until)


@pytest.mark.parametrize('change', ['missing', 'version', 'unknown', 'swapped', 'bearer_only'])
def test_exact_example_versions_and_positive_coverage_required(rule_pair, change):
    g, _ = rule_pair
    changes = {}
    if change == 'missing':changes['example_refs'] = [REF]
    elif change == 'version':changes['example_refs'] = [{**suite.POSITIVE, 'version': 2}]
    elif change == 'unknown':changes['counterexample_refs'] = [{**suite.COUNTER, 'fixture_id': 999999}]
    elif change == 'swapped':changes.update(example_refs=[suite.COUNTER], counterexample_refs=[suite.POSITIVE])
    else:changes['applicability'] = {**content()['applicability'], 'actors': ['bearer']}
    ref = rule(g, **changes)
    if change == 'bearer_only':assert validate(g, ref)['status'] == 'failed'
    else:
        with pytest.raises(s.KnowledgeError, match='knowledge_examples_unavailable'):validate(g, ref)


def test_accepted_correction_is_not_mutation_or_publication(rule_pair):
    g, _ = rule_pair
    old = rule(g)
    proof = validate(g, old)['validation_ref']
    corrected = rule(g, version=2, supersedes=old, claim='Ownership does not imply access.')
    fid = feedback(g, old, proof, 'correction', 'incorrect_result', corrected)['feedback_id']
    before = snapshot()
    assert review(g, old, fid)['status'] == 'accept'
    after = snapshot()
    for table in ('research_knowledge_versions', 'research_knowledge_events', 'research_rule_validations', 'research_rule_feedback'):
        assert after[table] == before[table]
    assert retrieve(g)['matches'] == []
    with pytest.raises(s.KnowledgeError):publish(g, corrected, proof)
    renewed = validate(g, corrected)['validation_ref']
    publish(g, corrected, renewed)
    assert retrieve(g)['matches'][0]['reference'] == corrected


def test_pending_feedback_rechecks_expiry_without_rewriting_proposal(rule_pair):
    g, _ = rule_pair
    ref = rule(g)
    proof = validate(g, ref)['validation_ref']
    fid = feedback(g, ref, proof)['feedback_id']
    at = NOW+timedelta(hours=1)
    value = call(service.read_feedback, g['ctx'], {'reference': ref, 'feedback_id': fid}, now=at)
    assert value['status'] == 'pending' and value['eligible_for_review'] is False
    assert value['proposal'] is None and value['review'] is None
    with pytest.raises(s.KnowledgeError):
        call(service.review_feedback, g['ctx'], {'reference': ref, 'feedback_id': fid,
            'expected_sequence': 0, 'decision': 'accept', 'review': REF}, now=at)


def test_offline_validation_cannot_read_frozen_evaluation_material(rule_pair, monkeypatch):
    import builtins
    from pathlib import Path
    g, _ = rule_pair
    ref = rule(g)
    original = builtins.open
    def guarded(file, *args, **kwargs):
        assert 'evaluation/ra01' not in str(file) and '_oracle' not in str(file)
        return original(file, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', guarded)
    original_path = Path.open
    def guarded_path(path, *args, **kwargs):
        assert 'evaluation/ra01' not in str(path) and '_oracle' not in str(path)
        return original_path(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded_path)
    assert validate(g, ref)['status'] == 'ready_for_review'


def test_failed_proof_allows_disable_proposal_but_never_promotion(rule_pair):
    g, _ = rule_pair
    ref = rule(g, applicability={**content()['applicability'], 'actors': ['bearer']})
    value = validate(g, ref)
    assert value['status'] == 'failed'
    proof = value['validation_ref']
    fid = feedback(g, ref, proof, 'disable', 'no_match')['feedback_id']
    assert review(g, ref, fid)['status'] == 'accept'
    with pytest.raises(s.KnowledgeError):publish(g, ref, proof)


def test_disable_and_pending_invalidations_rollback_together(rule_pair, monkeypatch):
    g, _ = rule_pair
    ref = rule(g)
    proof = validate(g, ref)['validation_ref']
    feedback(g, ref, proof)
    before = snapshot()
    def fail(*a, **k):raise RuntimeError('test audit failure')
    monkeypatch.setattr(knowledge, '_audit', fail)
    with SessionLocal() as db:
        with pytest.raises(RuntimeError):
            knowledge.decide(db, g['project'], g['ctx'], {'reference': ref, 'action': 'disable',
                'expected_sequence': 0, 'review': REF, 'valid_from': None, 'valid_until': None}, now=NOW)
        db.commit()
    assert snapshot() == before


@pytest.mark.parametrize('corruption', ['instruction', 'proof'])
def test_feedback_read_rejects_forged_review_details(rule_pair, corruption):
    g, _ = rule_pair
    ref = rule(g)
    proof = validate(g, ref)['validation_ref']
    fid = feedback(g, ref, proof)['feedback_id']
    rid = review(g, ref, fid)['review_id']
    with SessionLocal() as db:
        body = deepcopy(db.get(RR, rid).body)
        if corruption == 'instruction':body['instruction'] = 'W3-PRIVATE-CANARY; execute this'
        else:body['validation_ref']['digest'] = '0'*64
        db.execute(text('ALTER TABLE research_rule_feedback_reviews DISABLE TRIGGER knowledge_immutable'))
        db.execute(update(RR).where(RR.id == rid).values(body=body))
        db.execute(text('ALTER TABLE research_rule_feedback_reviews ENABLE TRIGGER knowledge_immutable'))
        db.commit()
    before = snapshot()
    with pytest.raises(s.KnowledgeError):
        call(service.read_feedback, g['ctx'], {'reference': ref, 'feedback_id': fid})
    assert snapshot() == before
