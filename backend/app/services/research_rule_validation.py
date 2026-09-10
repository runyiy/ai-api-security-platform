"""Bounded offline execution, immutable evidence and explicit human proposal review."""
from datetime import timedelta, timezone
from sqlalchemy import select, func, or_
from app.db.models.research_knowledge import KnowledgeVersion as KV, KnowledgeEvent as KE
from app.db.models.research_rule_validation import RuleValidation as RV, RuleFeedback as RF, RuleFeedbackReview as RR
from app.schemas import research_knowledge as s, research_rule_validation as v
from app.schemas.research_observation import ObservationError, timestamp
from app.services import research_knowledge as knowledge
from app.services import research_rule_cases as suite, research_rule_engine as engine


def _content(row):
    content = s.validate(s.Content, row.content)
    if (s.digest(content.model_dump()) != row.digest or content.category != 'rule'
            or (content.scope, content.knowledge_id, content.version) != (row.scope, row.knowledge_id, row.version)):
        raise s.KnowledgeError()
    return content


def _live(db, context, row, now):
    try:
        knowledge.observation._eligible_context(db, context, row.context_version)
        knowledge.observation._target(db, context, row.target_id)
        content = _content(row)
        rows = list(db.scalars(select(KV).where(or_(KV.context_id == context.id,
            KV.scope == 'reusable_synthetic')).order_by(KV.id).limit(s.MAX_SCAN+1)))
        if len(rows) > s.MAX_SCAN:
            raise s.KnowledgeError('knowledge_scan_limit')
        disabled = set(db.scalars(select(KE.version_id).where(KE.version_id.in_([r.id for r in rows]),
            KE.action == 'disable').limit(s.MAX_SCAN*s.MAX_EVENTS+1)))
        catalog = {s.canonical(knowledge._ref(r)): r for r in rows}
        withdrawn = db.scalar(select(KE.id).where(KE.version_id == row.id, KE.action == 'withdraw').limit(1))
        if row.id in disabled or withdrawn is not None or not knowledge._lineage_eligible(content, catalog, disabled):
            raise s.KnowledgeError()
        return knowledge._sources(db, context, content, row.target_id, now)
    except ObservationError:
        raise s.KnowledgeError() from None


def run_checks(content):
    manifest = suite.manifest()
    if ([r.model_dump() for r in content.example_refs] != [suite.POSITIVE]
            or [r.model_dump() for r in content.counterexample_refs] != [suite.COUNTER]):
        raise s.KnowledgeError('knowledge_examples_unavailable')
    cases = suite.cases()
    if not 1 <= len(cases) <= v.MAX_CASES:
        raise s.KnowledgeError('knowledge_validation_limit')
    checks = []
    positive_exercised = False
    for index, case in enumerate(cases):
        # The engine sees inputs and exact immutable card only, never oracle labels.
        actual = engine.evaluate(content, case)
        expected = suite.expected(index, content.applicability.actors)
        valid = (isinstance(actual, dict) and set(actual) == set(expected)
            and actual.get('state') in {'explanation', 'no_match', 'needs_input', 'unsupported', 'rejected'}
            and type(actual.get('matched')) is bool and actual.get('execution_authorized') is False
            and actual.get('relationship') in {'owner', 'non_owner', 'shared', 'unspecified'}
            and actual.get('expected_access') in {'allowed', 'denied', 'unspecified'}
            and isinstance(actual.get('missing_inputs'), list)
            and len(actual['missing_inputs']) <= 8
            and all(g in {'facts_missing', 'facts_conflict', 'baseline_missing', 'identity_missing',
                'session_health_unverified', 'preview_only_shape', 'untrusted_material'} for g in actual['missing_inputs']))
        correct = valid and actual == expected
        positive_exercised |= index < 3 and correct and actual['matched']
        checks.append({'case_id': case[0], 'input_digest': s.digest(case),
            'expected_digest': s.digest(expected), 'actual': actual if valid else None, 'correct': correct})
    report = {**manifest, 'checks': checks, 'passed': all(c['correct'] for c in checks) and positive_exercised}
    if len(s.canonical(report)) > v.MAX_EVIDENCE:
        raise s.KnowledgeError('knowledge_validation_limit')
    return report


def _finish(value, deadlines, now):
    value.update(execution_authorized=False, ordinary_publication_allowed=False,
        eligibility_until=min(deadlines).isoformat() if deadlines else None)
    if len(s.canonical(value)) > s.MAX_RESPONSE:
        raise s.KnowledgeError('knowledge_response_limit', 500)
    if deadlines and knowledge._time(now) >= min(deadlines):
        raise s.KnowledgeError()
    return value


def _capacity(db, model, context, limit):
    count = db.scalar(select(func.count()).select_from(model).join(KV, model.version_id == KV.id)
        .where(KV.context_id == context.id))
    if count >= limit:
        raise s.KnowledgeError('knowledge_validation_storage_limit')


def validate_rule(db, project, context_id, payload, *, now=None):
    data = s.validate(v.ValidateInput, payload)
    context = knowledge._locked(db, project, context_id)
    with db.begin_nested():
        row = knowledge._exact(db, context, data.reference, owned=True)
        deadlines = _live(db, context, row, now)
        at = knowledge._time(now)
        until = timestamp(data.valid_until)
        if not at < until <= at + timedelta(days=1):
            raise s.KnowledgeError('knowledge_invalid', 422)
        deadlines.append(until)
        _capacity(db, RV, context, v.MAX_VALIDATIONS)
        report = run_checks(_content(row))
        deadlines.extend(_live(db, context, row, now))
        body = {'reference': knowledge._ref(row), 'contract': row.content['contract'],
            'recorded_at': at.isoformat(), 'valid_until': min(deadlines).isoformat(), 'report': report}
        if len(s.canonical(body)) > v.MAX_EVIDENCE:
            raise s.KnowledgeError('knowledge_validation_limit')
        evidence = RV(version_id=row.id, body=body, digest=s.digest(body), recorded_at=at,
            valid_until=min(deadlines))
        db.add(evidence)
        db.flush()
        audit = knowledge._audit(db, context, 'record', at)
        return _finish({'validation_ref': {'validation_id': evidence.id, 'digest': evidence.digest},
            'evidence': body, 'audit_id': audit, 'status': 'ready_for_review' if report['passed'] else 'failed'}, deadlines, now)


def qualified(db, row, reference, at, *, passed=True):
    """Exact immutable proof, current validator/suite and half-open window.

    Source/lineage checks belong to the consuming transaction. Shared synthetic
    publication does not acquire the author's exited project/Target permission.
    """
    if not isinstance(reference, s.ValidationRef):
        raise s.KnowledgeError('knowledge_validation_unavailable')
    evidence = db.scalar(select(RV).where(RV.id == reference.validation_id,
        RV.version_id == row.id).execution_options(populate_existing=True))
    if evidence is None or not evidence.recorded_at <= at < evidence.valid_until:
        raise s.KnowledgeError('knowledge_validation_unavailable')
    expected_body = {'reference': knowledge._ref(row), 'contract': row.content['contract'],
        'recorded_at': evidence.recorded_at.astimezone(timezone.utc).isoformat(), 'valid_until': evidence.valid_until.astimezone(timezone.utc).isoformat(),
        'report': run_checks(_content(row))}
    if (evidence.digest != reference.digest or s.digest(evidence.body) != reference.digest
            or evidence.body != expected_body or (passed and evidence.body['report']['passed'] is not True)):
        raise s.KnowledgeError('knowledge_validation_unavailable')
    return evidence


def read_validation(db, project, context_id, payload, *, now=None):
    data = s.validate(v.ValidationReadInput, payload)
    context = knowledge._locked(db, project, context_id)
    with db.begin_nested():
        row = knowledge._exact(db, context, data.reference, owned=True)
        deadlines = _live(db, context, row, now)
        evidence = qualified(db, row, data.validation_ref, knowledge._time(now), passed=False)
        deadlines.append(evidence.valid_until)
        audit = knowledge._audit(db, context, 'query', knowledge._time(now))
        return _finish({'validation_ref': data.validation_ref.model_dump(), 'evidence': evidence.body,
            'audit_id': audit}, deadlines, now)


def submit_feedback(db, project, context_id, payload, *, now=None):
    data = s.validate(v.FeedbackInput, payload)
    context = knowledge._locked(db, project, context_id)
    with db.begin_nested():
        row = knowledge._exact(db, context, data.reference, owned=True)
        deadlines = _live(db, context, row, now)
        evidence = qualified(db, row, data.validation_ref, knowledge._time(now), passed=data.proposal == 'promotion')
        deadlines.append(evidence.valid_until)
        if data.correction:
            correction = knowledge._exact(db, context, data.correction, owned=True)
            if correction.content['supersedes'] != knowledge._ref(row):
                raise s.KnowledgeError()
            deadlines.extend(_live(db, context, correction, now))
        _capacity(db, RF, context, v.MAX_FEEDBACK)
        at = knowledge._time(now)
        feedback = RF(version_id=row.id, validation_id=evidence.id, body=data.model_dump(), recorded_at=at)
        db.add(feedback)
        db.flush()
        audit = knowledge._audit(db, context, 'record', at, data.review.model_dump())
        return _finish({'feedback_id': feedback.id, 'reference': data.reference.model_dump(),
            'status': 'pending', 'audit_id': audit}, deadlines, now)


def _feedback(db, row, number):
    feedback = db.scalar(select(RF).where(RF.id == number, RF.version_id == row.id))
    if feedback is None:
        raise s.KnowledgeError()
    data = s.validate(v.FeedbackInput, feedback.body)
    if data.reference.model_dump() != knowledge._ref(row) or data.validation_ref.validation_id != feedback.validation_id:
        raise s.KnowledgeError()
    return feedback, data


def read_feedback(db, project, context_id, payload, *, now=None):
    data = s.validate(v.FeedbackReadInput, payload)
    context = knowledge._locked(db, project, context_id)
    with db.begin_nested():
        row = knowledge._exact(db, context, data.reference, owned=True)
        feedback, proposal = _feedback(db, row, data.feedback_id)
        review = db.scalar(select(RR).where(RR.feedback_id == feedback.id))
        deadlines, eligible = [], False
        try:
            with db.begin_nested():
                deadlines = _live(db, context, row, now)
                evidence = qualified(db, row, proposal.validation_ref, knowledge._time(now),
                    passed=proposal.proposal == 'promotion')
                deadlines.append(evidence.valid_until)
                if proposal.correction:
                    correction = knowledge._exact(db, context, proposal.correction, owned=True)
                    deadlines.extend(_live(db, context, correction, now))
                eligible = True
        except s.KnowledgeError as exc:
            if exc.code not in {'knowledge_unavailable', 'knowledge_validation_unavailable', 'knowledge_examples_unavailable'}:
                raise
            deadlines = []
        review_body = None
        if eligible and review:
            parsed = s.validate(v.FeedbackReviewBody, review.body)
            if (not feedback.recorded_at <= review.recorded_at <= knowledge._time(now)
                    or (review.decision == 'invalidate') != (parsed.actor == 'system')
                    or (parsed.actor == 'local_operator' and parsed.validation_ref != proposal.validation_ref)):
                raise s.KnowledgeError()
            review_body = parsed.model_dump()
        # Show the bounded proposal for current human review. Unavailable history
        # retains only its receipt; no source/card/evidence body is copied here.
        audit = knowledge._audit(db, context, 'query', knowledge._time(now))
        return _finish({'feedback_id': feedback.id, 'reference': knowledge._ref(row),
            'status': review.decision if review else 'pending', 'review_id': review.id if review else None,
            'eligible_for_review': eligible and review is None,
            'proposal': proposal.model_dump() if eligible else None,
            'review': review_body, 'audit_id': audit}, deadlines, now)


def review_feedback(db, project, context_id, payload, *, now=None):
    data = s.validate(v.FeedbackReviewInput, payload)
    context = knowledge._locked(db, project, context_id)
    with db.begin_nested():
        row = knowledge._exact(db, context, data.reference, owned=True)
        feedback, proposal = _feedback(db, row, data.feedback_id)
        if db.scalar(select(RR.id).where(RR.feedback_id == feedback.id)) is not None:
            raise s.KnowledgeError('knowledge_decision_conflict')
        deadlines = _live(db, context, row, now)
        evidence = qualified(db, row, proposal.validation_ref, knowledge._time(now),
            passed=proposal.proposal == 'promotion')
        deadlines.append(evidence.valid_until)
        if proposal.correction:
            correction = knowledge._exact(db, context, proposal.correction, owned=True)
            deadlines.extend(_live(db, context, correction, now))
        at = knowledge._time(now)
        review = RR(feedback_id=feedback.id, decision=data.decision,
            body={'actor': 'local_operator', 'review': data.review.model_dump(),
                  'reason': 'human_review', 'validation_ref': proposal.validation_ref.model_dump()}, recorded_at=at)
        db.add(review)
        db.flush()
        if data.decision == 'accept' and proposal.proposal == 'disable':
            count = db.scalar(select(func.count()).select_from(KE).where(KE.version_id == row.id))
            knowledge.decide(db, project, context_id, {'reference': knowledge._ref(row),
                'expected_sequence': count, 'action': 'disable', 'review': data.review.model_dump(),
                'valid_from': None, 'valid_until': None}, now=now)
        # Acceptance records the human proposal decision only. Separate version,
        # validation, review/reuse and publish operations remain required.
        audit = knowledge._audit(db, context, 'decision', at, data.review.model_dump())
        return _finish({'feedback_id': feedback.id, 'review_id': review.id, 'status': data.decision,
            'audit_id': audit}, deadlines, now)


def invalidate_pending(db, context, now):
    """Catalog lock held: append invalidations without rewriting original evidence."""
    rows = list(db.scalars(select(KV).where(or_(KV.context_id == context.id,
        KV.scope == 'reusable_synthetic')).order_by(KV.id).limit(s.MAX_SCAN+1)))
    if len(rows) > s.MAX_SCAN:
        raise s.KnowledgeError('knowledge_scan_limit')
    catalog = {s.canonical(knowledge._ref(r)): r for r in rows}
    disabled = set(db.scalars(select(KE.version_id).where(KE.version_id.in_([r.id for r in rows]),
        KE.action == 'disable').limit(s.MAX_SCAN*s.MAX_EVENTS+1)))
    try:
        affected = {r.id for r in rows if r.id in disabled or not knowledge._lineage_eligible(s.Content.model_validate(r.content), catalog, disabled)}
    except (ValueError, s.KnowledgeError):
        # An unreadable dependency must not prevent disable or leave an uncertain
        # pending proposal consumable. Conservatively invalidate this pending scope.
        affected = {r.id for r in rows}
    pending = list(db.scalars(select(RF).join(KV, RF.version_id == KV.id).where(KV.context_id == context.id,
        ~select(RR.id).where(RR.feedback_id == RF.id).exists()).order_by(RF.id).limit(v.MAX_FEEDBACK+1)))
    if len(pending) > v.MAX_FEEDBACK:
        raise s.KnowledgeError('knowledge_validation_storage_limit')
    affected_refs = {s.canonical(knowledge._ref(r)) for r in rows if r.id in affected}
    for feedback in pending:
        if feedback.version_id in affected or (feedback.body.get('correction') and s.canonical(feedback.body['correction']) in affected_refs):
            db.add(RR(feedback_id=feedback.id, decision='invalidate',
                body={'actor': 'system', 'reason': 'dependency_disabled'}, recorded_at=knowledge._time(now)))
    db.flush()
