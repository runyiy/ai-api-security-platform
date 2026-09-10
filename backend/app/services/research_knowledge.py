"""Eligibility first, offline synthetic retrieval; ordinary publication stays closed."""
from datetime import timedelta
import re
from sqlalchemy import select, func, or_, text, delete
from sqlalchemy.exc import IntegrityError
from app.db.models.research_knowledge import KnowledgeVersion as KV, KnowledgeEvent as KE, KnowledgeAudit as KA
from app.db.models.research_observation import ObservationRecord, ObservationPreparation
from app.db.models.resource_access_assertion import ResourceAccessAssertion
from app.schemas.research_context import ResearchIntakeInput
from app.schemas import research_knowledge as s
from app.schemas.research_observation import timestamp, ObservationError
from app.schemas.research_subject import SubjectError
from app.services import research_context as intake, research_observation as observation, research_subject as subject
from app.services.resource_access_resolution import MAX_ASSERTIONS_SCANNED


# A bounded catalog mutex, always before W1 context locks; not a task scheduler.
# Transactional advisory key is specific to this new domain.
def _locked(db, project, context_id):
    intake._clean(db)
    db.execute(text('SELECT pg_advisory_xact_lock(73103, 2)'))
    try:
        return observation._locked(db, project, context_id)
    except (ObservationError, intake.ResearchContextError):
        raise s.KnowledgeError() from None


def _time(clock):
    try:
        return observation._time(clock)
    except ObservationError:
        raise s.KnowledgeError('knowledge_invalid', 422) from None


def _audit(db, context, code, at, review=None):
    if db.scalar(select(func.count()).select_from(KA).where(KA.context_id == context.id)) >= s.MAX_AUDIT:
        raise s.KnowledgeError('knowledge_audit_capacity')
    row = KA(context_id=context.id, code=code, recorded_at=at, review=review)
    db.add(row)
    db.flush()
    return row.id


def _ref(row):
    return dict(scope=row.scope, knowledge_id=row.knowledge_id, version=row.version, digest=row.digest)


def _exact(db, context, reference, *, owned=False):
    predicate = KV.context_id == context.id
    if not owned and reference.scope == 'reusable_synthetic':
        predicate = KV.scope == 'reusable_synthetic'
    row = db.scalar(select(KV).where(predicate, KV.scope == reference.scope,
        KV.knowledge_id == reference.knowledge_id, KV.version == reference.version,
        KV.digest == reference.digest).execution_options(populate_existing=True))
    if row is None:
        raise s.KnowledgeError()
    return row


def _sources(db, context, content, target_id, clock):
    deadlines = []
    for source in content.source_refs:
        if source.kind == 'synthetic_authored':
            continue  # Only fixed synthetic vocabulary; a separate exact review is still required.
        row = db.scalar(select(ObservationRecord).where(ObservationRecord.context_id == context.id,
                                                        ObservationRecord.id == source.observation_id))
        if row is None:
            raise s.KnowledgeError()
        prep = db.scalar(select(ObservationPreparation).where(ObservationPreparation.context_id == context.id,
            ObservationPreparation.id == row.preparation_id, ObservationPreparation.target_id == target_id))
        if prep is None:
            raise s.KnowledgeError()
        value = observation.read(db, context.project_number, context.id, source.observation_id, now=clock)
        if (value['availability'] != 'available' or 'payload' not in value
                or s.digest(value['payload']) != source.payload_digest
                or source.source_entry_index not in {e['source_entry_index'] for e in value['payload']['entries']}):
            raise s.KnowledgeError()
        deadlines.extend([row.expires_at, timestamp(prep.registry['valid_until'])])
    return deadlines


def record(db, project, context_id, payload, *, now=None):
    data = s.validate(s.RecordInput, payload)
    context = _locked(db, project, context_id)
    try:
        with db.begin_nested():
            observation._eligible_context(db, context, data.context_version)
            observation._target(db, context, data.target_id)
            c = data.content
            if c.supersedes:
                old = _exact(db, context, c.supersedes, owned=True)
                before = s.validate(s.Content, old.content)
                if old.target_id != data.target_id or not {s.canonical(x.model_dump()) for x in before.source_refs} <= {s.canonical(x.model_dump()) for x in c.source_refs}:
                    raise s.KnowledgeError()
            _sources(db, context, c, data.target_id, now)
            if db.scalar(select(func.count()).select_from(KV).where(KV.context_id == context.id)) >= s.MAX_VERSIONS:
                raise s.KnowledgeError('knowledge_storage_limit')
            if c.scope == 'reusable_synthetic' and db.scalar(select(func.count()).select_from(KV).where(KV.scope == c.scope)) >= s.MAX_VERSIONS:
                raise s.KnowledgeError('knowledge_storage_limit')
            row = KV(context_id=context.id, context_version=data.context_version, target_id=data.target_id,
                     scope=c.scope, knowledge_id=c.knowledge_id, version=c.version, content=c.model_dump(),
                     digest=s.digest(c.model_dump()), review=data.review.model_dump(), recorded_at=_time(now))
            db.add(row)
            db.flush()
            audit = _audit(db, context, 'record', row.recorded_at, data.review.model_dump())
            return {'reference': _ref(row), 'state': 'candidate', 'audit_id': audit, 'ordinary_publication_allowed': False}
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None)
        if constraint in {'uq_knowledge_project_version', 'uq_knowledge_shared_version'}:
            raise s.KnowledgeError() from None
        raise s.KnowledgeError('knowledge_failed', 500) from None
    except (ObservationError, intake.ResearchContextError):
        raise s.KnowledgeError() from None


def decide(db, project, context_id, payload, *, now=None):
    data = s.validate(s.DecisionInput, payload)
    context = _locked(db, project, context_id)
    with db.begin_nested():
        row = _exact(db, context, data.reference, owned=True)
        if data.action == 'publish':
            raise s.KnowledgeError('knowledge_publication_closed')
        events = list(db.scalars(select(KE).where(KE.version_id == row.id).order_by(KE.sequence).limit(17)))
        if len(events) != data.expected_sequence or len(events) >= s.MAX_EVENTS:
            raise s.KnowledgeError('knowledge_decision_conflict')
        if any(e.action == 'disable' for e in events):
            raise s.KnowledgeError('knowledge_decision_conflict')
        if data.action in {'review', 'reuse'}:
            if len(events) >= s.MAX_EVENTS - 2 or any(e.action == 'withdraw' for e in events):
                raise s.KnowledgeError('knowledge_decision_conflict')
            try:
                observation._eligible_context(db, context, row.context_version)
                observation._target(db, context, row.target_id)
                _sources(db, context, s.validate(s.Content, row.content), row.target_id, now)
            except ObservationError:
                raise s.KnowledgeError() from None
        at = _time(now)
        body = s.EventBody(digest=row.digest, actor='local_operator', evidence='operator_recorded',
            review=data.review, context_version=row.context_version, valid_from=data.valid_from, valid_until=data.valid_until,
            review_event_id=None, reuse_event_id=None, validation_ref='NOT_RUN')
        event = KE(version_id=row.id, sequence=len(events)+1, action=data.action, body=body.model_dump(), recorded_at=at)
        db.add(event)
        db.flush()
        audit = _audit(db, context, 'decision', at, data.review.model_dump())
        return {'reference': _ref(row), 'event_id': event.id, 'sequence': event.sequence, 'action': event.action,
                'audit_id': audit, 'ordinary_publication_allowed': False}


def rotate_audit(db, project, context_id, payload, *, now=None):
    data = s.validate(s.AuditInput, payload)
    context = _locked(db, project, context_id)
    with db.begin_nested():
        at = _time(now)
        ids = list(db.scalars(select(KA.id).where(KA.context_id == context.id).order_by(KA.id).limit(s.MAX_AUDIT+1)))
        if len(ids) >= s.MAX_AUDIT:
            # Explicit reviewed retirement, never decision/version deletion or silent truncation.
            db.execute(delete(KA).where(KA.context_id == context.id, KA.id.in_(ids[:512])))
        else:
            db.execute(delete(KA).where(KA.context_id == context.id, KA.recorded_at <= at-timedelta(days=90)))
        return {'audit_id': _audit(db, context, 'rotate', at, data.review.model_dump()), 'ordinary_publication_allowed': False}


def _window(body, at):
    return body.valid_from is not None and body.valid_until is not None and timestamp(body.valid_from) <= at < timestamp(body.valid_until)


def _publication(db, row, at):
    events = list(db.scalars(select(KE).where(KE.version_id == row.id).order_by(KE.sequence).limit(17)))
    if not events or len(events) > s.MAX_EVENTS or [e.sequence for e in events] != list(range(1,len(events)+1)):
        return None
    parsed = {}
    previous = row.recorded_at
    for event in events:
        b = s.validate(s.EventBody, event.body)
        if b.digest != row.digest or b.context_version != row.context_version or not previous <= event.recorded_at <= at:
            return None
        previous = event.recorded_at
        parsed[event.id] = (event, b)
    if any(e.action in {'withdraw','disable'} for e in events):
        return None
    pub = events[-1]
    b = parsed[pub.id][1]
    # There is NO ordinary publication implementation or test-mode switch.
    # Only explicitly marked fixture records inserted by tests can exercise this branch.
    if pub.action != 'publish' or b.evidence != 'synthetic_test_only' or b.validation_ref != 'synthetic_test_only' or not _window(b, at):
        return None
    deadlines = [timestamp(b.valid_until)]
    for ref, kind in ((b.review_event_id, 'review'), (b.reuse_event_id, 'reuse')):
        if kind == 'reuse' and row.scope == 'project' and ref is None:
            continue
        if ref not in parsed:
            return None
        e, q = parsed[ref]
        if e.action != kind or e.sequence >= pub.sequence or not _window(q, at):
            return None
        deadlines.append(timestamp(q.valid_until))
    return pub, b, min(deadlines), parsed[b.review_event_id][1].actor


def _lineage_eligible(content, catalog, disabled):
    seen = set()
    current = content
    while current.supersedes is not None:
        key = s.canonical(current.supersedes.model_dump())
        if key in seen or key not in catalog or len(seen) >= s.MAX_VERSIONS:
            return False
        seen.add(key)
        row = catalog[key]
        if row.id in disabled:
            return False
        current = s.validate(s.Content, row.content)
        if s.digest(current.model_dump()) != row.digest:
            return False
    return True


def _shape_gaps(db, context, value, clock):
    # Imported media/shape is only a qualified report, never verified object/access truth.
    p = value['proposal']
    if not p['sources'] or p['endpoint_id'] is None:
        return {'object_shape_missing'}
    from app.db.models.endpoint import Endpoint
    path = db.scalar(select(Endpoint.path).where(Endpoint.id == p['endpoint_id'], Endpoint.target_id == p['target_id']))
    gaps = {'observation_shape_unverified'}
    for source in p['sources']:
        result = observation.read(db, context.project_number, context.id, source['observation_id'], now=clock)
        if result['availability'] != 'available' or 'payload' not in result:
            raise s.KnowledgeError()
        entry = next((e for e in result['payload']['entries'] if e['source_entry_index'] == source['source_entry_index']), None)
        if entry is None:
            raise s.KnowledgeError()
        response = entry['response']
        if (entry['method'] != 'GET' or entry['path_template'] != path or entry['query_names']
                or len(entry['resource_labels']) != 1 or response['media_kind'] in {'json_array', 'html', 'text', 'other'}):
            gaps.add('preview_only_shape')
        if (response['media_kind'] != 'json_object' or response['capture_state'] != 'complete'
                or len(response['object_labels']) != 1):
            gaps.add('object_shape_missing')
    return gaps


def _applicable(content, value, gaps):
    p = value['proposal']
    if p['identity_choice'] not in content.applicability.actors:
        return None
    if content.category == 'mechanism':
        return 'general_explanation'
    if content.category != 'rule':
        return None
    blocked = {'facts_missing','facts_conflict','proposal_fact_conflict','identity_missing','resource_missing',
               'slot_missing','slot_unavailable','object_shape_missing','preview_only_shape','assertion_not_current_verified','permission_missing'}
    if blocked.intersection(gaps) or any(g.startswith('session_') or g == 'credential_changed' for g in gaps):
        return None
    # No membership/session/intent approval is inferred. This result only explains independent facts.
    return 'context_facts_present'


def _rank(content, query):
    words = set(re.findall('[a-z]+', content.claim.lower()))
    return len(words.intersection(query.keywords)) + 2 * len(set(content.tags).intersection(query.tags))


def _context_deadlines(db, context, value, readiness):
    # Compare against the instants that produced facts/gaps, never a later clock
    # that could silently drop a dependency which expired during retrieval.
    deadlines = []
    permission_at = timestamp(readiness['evaluated_at'])
    for item in ResearchIntakeInput.model_validate(readiness['intake']).targets:
        _, revision, _, _ = intake._authorization_metadata(db, context, item)
        if revision:
            deadlines.extend(revision[key] for key in ('valid_from', 'valid_until')
                             if revision[key] is not None and revision[key] > permission_at)
    p = value['proposal']
    if p['resource_id'] is None or p['test_identity_id'] is None:
        return deadlines
    fact_at = timestamp(value['evaluated_at'])
    # Table SHARE locks already protect this qualified Resource/identity pair.
    # Include future verified assertions, even if not selected by the proposal:
    # their eligibility can introduce a conflict without a concurrent DB write.
    starts = func.greatest(ResourceAccessAssertion.asserted_at, ResourceAccessAssertion.valid_from)
    windows = list(db.execute(select(ResourceAccessAssertion.asserted_at,
        ResourceAccessAssertion.valid_from, ResourceAccessAssertion.valid_until).where(
        ResourceAccessAssertion.resource_id == p['resource_id'],
        ResourceAccessAssertion.test_identity_id == p['test_identity_id'],
        ResourceAccessAssertion.verification_state == 'verified',
        or_(ResourceAccessAssertion.valid_until.is_(None),
            ResourceAccessAssertion.valid_until > func.greatest(starts, fact_at)),
    ).order_by(ResourceAccessAssertion.id).limit(MAX_ASSERTIONS_SCANNED + 1)))
    if len(windows) > MAX_ASSERTIONS_SCANNED:
        raise s.KnowledgeError('knowledge_scan_limit')
    for asserted_at, valid_from, valid_until in windows:
        eligible_from = max(asserted_at, valid_from or asserted_at)
        if eligible_from > fact_at:
            deadlines.append(eligible_from)
        elif valid_until is not None:
            deadlines.append(valid_until)
    return deadlines


def retrieve(db, project, context_id, payload, *, now=None):
    query = s.validate(s.QueryInput, payload, s.MAX_QUERY)
    context = _locked(db, project, context_id)
    with db.begin_nested():
        try:
            observation._eligible_context(db, context, query.context_version)
            # Prevent concurrent assertion insert/review phantoms while using M12 resolution.
            db.execute(text('LOCK TABLE resource_access_assertions, authorization_revisions, scopes IN SHARE MODE'))
            value = subject.read(db, project, context_id, query.subject_number, version=query.subject_version, now=now)
            if value['availability'] != 'available' or value['latest_version'] != query.subject_version:
                raise s.KnowledgeError()
            readiness = intake.read_context(db, project, context_id, version=query.context_version, now=_time(now))
        except (ObservationError, SubjectError, intake.ResearchContextError):
            raise s.KnowledgeError() from None
        # W1's generic facts_missing is superseded only by W3's independently resolved gaps.
        gaps = set(value['missing_inputs']) | (set(readiness['missing_inputs']) - {'facts_missing'})
        deadlines = _context_deadlines(db, context, value, readiness)
        try:
            gaps |= _shape_gaps(db, context, value, now)
        except ObservationError:
            raise s.KnowledgeError() from None
        predicate = or_(KV.context_id == context.id, KV.scope == 'reusable_synthetic')
        rows = list(db.scalars(select(KV).where(predicate).order_by(KV.id).limit(s.MAX_SCAN+1)))
        if len(rows) > s.MAX_SCAN:
            raise s.KnowledgeError('knowledge_scan_limit')
        catalog = {s.canonical(_ref(row)): row for row in rows}
        disabled = set(db.scalars(select(KE.version_id).where(KE.version_id.in_([r.id for r in rows]), KE.action == 'disable').limit(s.MAX_SCAN*s.MAX_EVENTS+1)))
        selected = {s.canonical(r.model_dump()) for r in query.selected}
        matches, source_reads, unavailable_source = [], 0, False
        for row in rows:
            if selected and s.canonical(_ref(row)) not in selected:
                continue
            try:
                content = s.validate(s.Content, row.content)
                if (s.digest(content.model_dump()) != row.digest or content.scope != row.scope or content.knowledge_id != row.knowledge_id
                        or content.version != row.version or content.purpose != query.purpose):
                    continue
                if not _lineage_eligible(content, catalog, disabled):
                    continue
                if row.scope == 'project' and (row.context_version != query.context_version or row.target_id != value['proposal']['target_id']):
                    continue
                publication = _publication(db, row, _time(now))
                if publication is None:
                    continue
                source_reads += sum(x.kind == 'observation' for x in content.source_refs)
                if source_reads > 64:
                    raise s.KnowledgeError('knowledge_scan_limit')
                with db.begin_nested():
                    _sources(db, context, content, value['proposal']['target_id'], now)
                applicable = _applicable(content, value, gaps)
                if not applicable:
                    continue
                # All data/decision/source/applicability checks precede ranking and top-k.
                score = _rank(content, query)
                if score:
                    pub, b, _, review_actor = publication
                    matches.append({'reference': _ref(row), 'content': content.model_dump(), 'score': score,
                        'review_event_id': b.review_event_id, 'reuse_event_id': b.reuse_event_id,
                        'publication_event_id': pub.id, 'publication_evidence': 'synthetic_test_only',
                        'review_actor': review_actor, 'publication_actor': b.actor, 'applicability': applicable})
            except (ValueError, ObservationError, s.KnowledgeError) as exc:
                if getattr(exc, 'code', '') in {'knowledge_scan_limit','observation_capacity_exceeded'}:
                    raise s.KnowledgeError('knowledge_scan_limit') from None
                if row.context_id == context.id and getattr(exc, 'code', '') in {'knowledge_unavailable','observation_context_unavailable'}:
                    unavailable_source = True
                continue  # No excluded identity/title/count/score diagnostics.
        matches.sort(key=lambda m: (-m['score'], m['reference']['scope'], m['reference']['knowledge_id'], m['reference']['version'], m['reference']['digest']))
        matches = matches[:query.top_k]
        # Revalidate selected sources/windows at the consumption boundary, after any waits/work.
        at = _time(now)
        for match in matches:
            row = _exact(db, context, s.ExactRef.model_validate(match['reference']))
            publication = _publication(db, row, at)
            if publication is None:
                raise s.KnowledgeError()
            deadlines.append(publication[2])
            try:
                deadlines.extend(_sources(db, context, s.Content.model_validate(match['content']), value['proposal']['target_id'], now))
            except ObservationError:
                raise s.KnowledgeError() from None
        current = subject.read(db, project, context_id, query.subject_number, version=query.subject_version, now=now)
        if current['availability'] != 'available' or current['facts'] != value['facts'] or current['missing_inputs'] != value['missing_inputs']:
            raise s.KnowledgeError()
        at = _time(now)
        if any(_publication(db, _exact(db, context, s.ExactRef.model_validate(m['reference'])), at) is None for m in matches):
            raise s.KnowledgeError()
        # Source and assertion expiry may advance while auditing/encoding. Capture only
        # already qualified, project-local dependencies, never an excluded record's metadata.
        for source in value['proposal']['sources']:
            row = db.scalar(select(ObservationRecord).where(ObservationRecord.context_id == context.id, ObservationRecord.id == source['observation_id']))
            prep = db.scalar(select(ObservationPreparation).where(ObservationPreparation.context_id == context.id, ObservationPreparation.id == row.preparation_id))
            deadlines.extend([row.expires_at, timestamp(prep.registry['valid_until'])])
        status = 'matched_synthetic' if matches else ('source_unavailable' if unavailable_source else 'no_match')
        if not matches and ('preview_only_shape' in gaps or 'slot_unavailable' in gaps):
            status = 'unsupported'
        elif not matches and {'facts_missing','facts_conflict','identity_missing','object_shape_missing'}.intersection(gaps):
            status = 'needs_input'
        audit = _audit(db, context, 'query', at)
        at = _time(now)
        if deadlines and at >= min(deadlines):
            raise s.KnowledgeError()
        result = s.QueryRead(format='ra-knowledge-retrieval/1', context_id=context.id, context_version=query.context_version,
            subject_number=query.subject_number, subject_version=query.subject_version, evaluated_at=at.isoformat(), audit_id=audit,
            status=status, missing_inputs=sorted(gaps | {'ordinary_publication_closed'}), matches=matches,
            eligibility_until=min(deadlines).isoformat() if deadlines else None,
            execution_authorized=False, ordinary_publication_allowed=False).model_dump()
        if deadlines and _time(now) >= min(deadlines):
            raise s.KnowledgeError()
        return result
