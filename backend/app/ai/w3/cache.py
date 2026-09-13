"""Bounded volatile reuse of an exact W2 analysis, never a new model call.

The trusted parent seals validated fixed-template analysis. Cached bytes remain
untrusted and pass the W1 parser again on every read. Original call references,
expiry and accounting provenance are retained; there is no cross-call rebinding,
durable content, provider prompt cache, automatic fallback or replay.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from hashlib import sha256
from threading import RLock

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from pydantic import ValidationError

from app.schemas.research_observation import ObservationError
from app.schemas.research_knowledge import KnowledgeError
from app.services.research_context import ResearchContextError

from app.ai.proposals.adapter import ProposalOutcome, PROMPT, request_body
from app.ai.proposals.bindings import validate_registry
from app.ai.proposals.codec import bounded_json, canonical, ProposalRejected
from app.ai.proposals.contract import INPUT_VERSION, OUTPUT_VERSION, input_document, output_document, output_schema
from app.ai.w2 import schema as t
from app.ai.w2.accounting import actual, balance_id
from app.ai.w2.execution import _analysis_identity
from app.ai.w2.preparation import w1_registry_document
from app.ai.w2.records import decode, require, PortError, stamp, utc, usage_view
from app.ai.w2.w1_bridge import CallRuntime


MAX_ENTRIES = 64
MAX_ANALYSIS_BYTES = 12288
_REJECTION = (PortError, ProposalRejected, SQLAlchemyError, ValidationError,
              ObservationError, KnowledgeError, ResearchContextError)


@dataclass(frozen=True, repr=False)
class AnalysisRead:
    key_digest: str
    origin_call_ref: str
    origin_completion_id: str
    origin_settlement_id: str
    origin_usage: object
    display: tuple
    evaluated_at: str
    expires_at: str
    format: str = 'ra-w3-analysis-read/1'

    def encode(self):
        return canonical(dict(format=self.format, key_digest=self.key_digest,
            origin_call_ref=self.origin_call_ref, origin_completion_id=self.origin_completion_id,
            origin_settlement_id=self.origin_settlement_id, origin_usage=self.origin_usage.document(),
            display=[asdict(item) for item in self.display], evaluated_at=self.evaluated_at,
            expires_at=self.expires_at))


@dataclass(frozen=True, repr=False)
class _Seal:
    key: bytes
    digest: str
    completion: object
    settlement: object
    permit: object
    run: object


def _proposal(outcome, runtime):
    """Encode existing fixed display without changing any original W1 handle.

    This is a local projection, not a stored provider response or a new receipt.
    The W2 Q4 path has no model-visible input gaps and only one qualified rule.
    """
    require(type(outcome) is ProposalOutcome and outcome.code is None
            and outcome.delivery == 'responded' and outcome.refusal_code is None
            and outcome.usage.state == 'known' and type(outcome.display) is tuple
            and 1 <= len(outcome.display) <= 4, 'SOURCE_UNAVAILABLE')
    suggestions = []
    for index, display in enumerate(outcome.display, 1):
        require(display.suggestion_ref == 's_' + str(index)
                and display.uncertainty_codes == ('NOT_EXECUTED',), 'INVALID_RECORD')
        if display.text == 'Suggested for human review. Not executed.':
            require(len(display.references) == 2, 'INVALID_RECORD')
            candidate, rule = display.references
            kind, reason = 'REVIEW_CANDIDATE', 'CANDIDATE_REVIEW_ONLY'
        else:
            require(display.text == 'Review the referenced general rule. No project conclusion.'
                    and len(display.references) == 1, 'INVALID_RECORD')
            candidate, rule = None, display.references[0]
            kind, reason = 'EXPLAIN_RULE', 'GENERAL_RULE_ONLY'
        suggestions.append(dict(suggestion_ref=display.suggestion_ref, type=kind,
            candidate_ref=candidate, rule_refs=[rule], gap_refs=[], reason_code=reason,
            uncertainty_codes=['NOT_EXECUTED']))
    return dict(protocol=OUTPUT_VERSION, request_ref=runtime.key.call_ref,
                status='suggestions', suggestions=suggestions, refusal_code=None)


def decode_analysis(raw, runtime, key_digest):
    """Strictly decode untrusted cache bytes through the original W1 consumer."""
    try:
        doc = bounded_json(raw, maximum=MAX_ANALYSIS_BYTES, depth=8, nodes=2048)
        require(set(doc) == {'format', 'key_digest', 'completion_id', 'settlement_id', 'usage', 'proposal'})
        require(doc['format'] == 'ra-w3-analysis/1', 'VERSION_UNSUPPORTED')
        require(doc['key_digest'] == key_digest, 'CONTEXT_CHANGED')
        from app.ai.w2.records import _scalar
        require(_scalar('Id', doc['completion_id']) and _scalar('Id', doc['settlement_id']))
        usage = decode('UsageView', canonical(doc['usage']))
        require(usage.state == 'known' and usage.cached_input_tokens == usage.cache_write_tokens == 0)
        inp = input_document(runtime.prepared.prepared.payload)
        proposal, display = output_document(canonical(doc['proposal']), inp,
                                            runtime.prepared.prepared.registry.allowed_pairs)
        require(proposal['status'] == 'suggestions' and bool(display), 'SOURCE_UNAVAILABLE')
        return doc, display, usage
    except ProposalRejected:
        raise PortError('INVALID_RECORD') from None


class AnalysisCache:
    def __init__(self):
        # Separate parent seals bind integrity/provenance; the payload dictionary
        # is not an authority store. Neither dictionary is durable or global.
        self._entries, self._seals = {}, {}
        self._lock = RLock()
        self.hook = lambda stage, token: None  # Trusted fault schedule only.

    @contextmanager
    def _locked(self, deadline):
        require(self._lock.acquire(timeout=deadline.remaining()), 'DEADLINE_EXCEEDED')
        try:
            deadline.check()
            yield
        finally:
            self._lock.release()

    @staticmethod
    def key_digest(runtime):
        return sha256(AnalysisCache._key(runtime)).hexdigest()

    @staticmethod
    def _key(runtime):
        require(type(runtime) is CallRuntime, 'INVALID_RECORD')
        p = runtime.prepared
        core = decode('BindingCore', p.core.encode())
        require(runtime.key == p.key and runtime.run.scope == core.scope
                and runtime.run.call_ref == core.call_ref, 'CONTEXT_CHANGED')
        require(all(source.data_class == 'synthetic_authored' and source.scope == 'reusable_synthetic'
                    for source in p.prepared.registry.sources), 'SOURCE_UNAVAILABLE')
        # The full core carries source/context/rule/prompt/schema/model/settings/
        # usage/rate/secret versions and validity. The additional dimensions are
        # explicit, even though only this one synthetic privacy class is eligible.
        raw = canonical(dict(format='ra-w3-analysis-key/1', core=core.document(),
            run=runtime.run.document(), manifest_digest=p.manifest_digest,
            data_class='synthetic_authored', privacy_class='reusable_synthetic', provider='openai',
            input_protocol=INPUT_VERSION, output_protocol=OUTPUT_VERSION,
            counting_version=p.certificate.counting_version))
        require(len(raw) <= 16384, 'LIMIT_EXCEEDED')
        return raw

    def _qualify(self, runtime, deadline, token):
        p = runtime.prepared
        require(deadline.context.scope == runtime.key.scope
                and deadline.context.process_epoch == runtime.run.process_epoch
                and deadline.context.cancellation_id == runtime.run.cancellation_id, 'SOURCE_UNAVAILABLE')
        # A new read deadline cannot renew the original call or erase its clock
        # history. Both clocks remain checked through final consumption.
        runtime.deadline.check()
        now = deadline.check((p.core.valid_from, p.core.expires_at))
        view = runtime.preparation.requalify(p, deadline, token=token)
        _, manifest = runtime.preparation.registry.question(p.question, deadline)
        require(manifest.fingerprint() == p.manifest_digest, 'CONTEXT_CHANGED')
        runtime.preparation.registry.budget_decisions(runtime.key.scope, manifest, deadline)
        p.config.validate()
        inp = input_document(p.prepared.payload)
        validate_registry(p.prepared, inp, deadline.check())
        require(p.prepared.digest(p.config) == p.core.w1_binding_digest
                and sha256(p.prepared.payload).hexdigest() == p.core.payload_digest
                and request_body(inp) == p.body
                and sha256(p.body).hexdigest() == p.core.body_digest
                and sha256(canonical(w1_registry_document(p.prepared.registry))).hexdigest() == p.core.registry_digest
                and sha256(canonical(asdict(p.config))).hexdigest() == p.core.configuration_digest
                and sha256(PROMPT.encode()).hexdigest() == p.core.prompt_digest
                and sha256(canonical(output_schema())).hexdigest() == p.core.schema_digest,
                'CONTEXT_CHANGED')
        runtime.deadline.check()
        deadline.check((view.valid_from, view.expires_at))
        self.hook('after_qualification', token)
        deadline.check()
        return now

    def _origin(self, runtime, completion_id, deadline):
        # G precedes DB locks. Account -> task -> original call keeps W2 order.
        # All transactions end before querying the independent A authority.
        with runtime.store.transaction(deadline) as db:
            balances = runtime.store._balances(db, runtime.key.scope, active=True,
                                               at=deadline.check(), core=runtime.prepared.core)
            row, core = runtime.store._call(db, runtime.key, runtime.run)
            require(core == runtime.prepared.core and row['state'] == 'SETTLED'
                    and row['closed'] and not row['cancelled']
                    and row['held_tokens'] == row['held_microusd'] == 0, 'RESERVATION_UNAVAILABLE')
            saved = db.execute(select(t.completion).where(t.completion.c.completion_id == completion_id,
                t.completion.c.key_digest == runtime.key.fingerprint())).mappings().one_or_none()
            require(saved is not None, 'SOURCE_UNAVAILABLE')
            completion = decode('Completion', bytes(saved['record']))
            require(completion.fingerprint() == saved['digest']
                    and completion.key_digest == runtime.key.fingerprint()
                    and completion.completion_id == saved['completion_id']
                    and completion.expires_at == core.expires_at and completion.outcome_code is None
                    and completion.delivery == 'responded' and completion.display_state == 'SUPPRESSED'
                    and completion.usage.state == 'known', 'CONFLICT')
            saved_settlement = db.execute(select(t.settlement).where(t.settlement.c.key_digest == runtime.key.fingerprint())
                            .order_by(t.settlement.c.revision.desc()).limit(1)).mappings().one_or_none()
            require(saved_settlement is not None, 'CONFLICT')
            settlement = decode('Settlement', bytes(saved_settlement['record']))
            require(settlement.key_digest == runtime.key.fingerprint()
                    and all(getattr(settlement, field) == saved_settlement[field]
                            for field in ('settlement_id', 'final_event_id', 'revision'))
                    and settlement.revision == row['revision'] == 1
                    and settlement.state == 'KNOWN' and settlement.settlement_id == completion.settlement_ref
                    and settlement.held_tokens == settlement.held_microusd == 0
                    and settlement.overrun_tokens == settlement.overrun_microusd == 0
                    and actual(completion.usage) == (row['actual_tokens'], row['actual_microusd'])
                    == (settlement.settled_tokens, settlement.settled_microusd), 'CONFLICT')
            # Only the original ordinary settlement is reusable. Recovery may
            # correct liabilities, but never mints a replacement analysis origin.
            amounts = dict(tokens=settlement.settled_tokens, microusd=settlement.settled_microusd)
            reserved = dict(tokens=core.reserved_tokens, microusd=core.reserved_microusd)
            postings = list(db.execute(select(t.posting).where(
                t.posting.c.settlement_id == settlement.settlement_id).limit(5)).mappings())
            require(len(postings) == 4 and {(p['scope_kind'], p['dimension']) for p in postings}
                    == {(kind, dim) for kind in ('account', 'task') for dim in amounts}
                    and all(p['balance_id'] == balance_id(runtime.key.scope, p['scope_kind'])
                            and p['settled_delta'] == amounts[p['dimension']]
                            and p['held_delta'] == -reserved[p['dimension']] for p in postings), 'CONFLICT')
            totals = dict(key_digest=runtime.key.fingerprint(), final_event_id=settlement.final_event_id,
                revision=1, **amounts, settled_delta=list(amounts.values()),
                held_delta=[-value for value in reserved.values()])
            require(settlement.accounting_digest == sha256(canonical(totals)).hexdigest(), 'CONFLICT')
            for _, policy in balances:
                deadline.check((stamp(policy['valid_from']), stamp(policy['expires_at'])))
        deadline.check((runtime.prepared.core.valid_from, completion.expires_at))
        return completion, settlement

    def _observed(self, runtime, original, settlement, deadline, token):
        observed = runtime.observer.observations.get(settlement.final_event_id)
        require(observed is not None and observed.key_digest == runtime.key.fingerprint()
                and observed.kind == 'FINAL_USAGE' and observed.terminal == 'COMPLETED'
                and observed.usage.state == 'known' and observed.usage == original.usage
                and actual(observed.usage) == (settlement.settled_tokens, settlement.settled_microusd),
                'OBSERVER_UNAVAILABLE')
        token.verify(deadline)
        self.hook('before_consume', token)
        runtime.deadline.check()
        deadline.check()

    def _consume(self, runtime, deadline, *, hit):
        # G has finished releasing, including its database unlock/close. Nothing
        # below performs database work after the final independent A check.
        self.hook('after_release', None)
        runtime.deadline.check()
        deadline.check()
        # Existing accepted-call consumption check only: this never issues or
        # consumes a send permit, observes usage, or changes accounting history.
        if hit:
            deadline.check((runtime.prepared.core.valid_from, runtime.permit.expires_at))
            runtime.observer.qualify_consumption(runtime.permit, deadline.remaining(3))
        else:
            runtime.observer.qualify_admission(deadline.remaining(3))
        runtime.deadline.check()
        original = runtime.deadline
        now = deadline.check((runtime.prepared.core.valid_from, runtime.run.deadline_at),
            (stamp(original.valid_from or utc(runtime.prepared.core.valid_from)),
             stamp(original.expires_at or utc(runtime.run.deadline_at))))
        # The final read sample must also satisfy the original monotonic bound.
        # Sampling two Deadline objects cannot renew the earlier one's window.
        require(original.last_ns <= deadline.last_ns < runtime.run.mono_deadline_ns
                and original.last_wall <= now, 'DEADLINE_EXCEEDED')
        return now

    def remember_v1(self, runtime, completion, outcome):
        key = self._key(runtime)
        digest = sha256(key).hexdigest()
        try:
            require(completion.key_digest == runtime.key.fingerprint()
                    and completion.display_state == 'ELIGIBLE_NOW', 'SOURCE_UNAVAILABLE')
            raw = canonical(dict(format='ra-w3-analysis/1', key_digest=digest,
                completion_id=completion.completion_id, settlement_id=completion.settlement_ref,
                usage=usage_view(outcome.usage).document(), proposal=_proposal(outcome, runtime)))
            doc, display, usage = decode_analysis(raw, runtime, digest)
            require(display == outcome.display and usage == completion.usage, 'CONFLICT')
            require(getattr(runtime, '_analysis_receipt', None) is not None
                    and runtime._analysis_receipt == _analysis_identity(runtime, completion, outcome),
                    'CONFLICT')
            with self._locked(runtime.deadline):
                with runtime.guard.read(runtime.deadline) as token:
                    self._qualify(runtime, runtime.deadline, token)
                    original, settlement = self._origin(runtime, completion.completion_id, runtime.deadline)
                    require(original.usage == usage and original.settlement_ref == completion.settlement_ref, 'CONFLICT')
                    seal = _Seal(key, sha256(raw).hexdigest(), original, settlement, runtime.permit, runtime.run)
                    existing = self._seals.get(digest)
                    require(existing is None or (existing == seal and self._entries.get(digest) == raw), 'CONFLICT')
                    require(existing is not None or len(self._seals) < MAX_ENTRIES, 'LIMIT_EXCEEDED')
                    require(self._key(runtime) == key, 'CONTEXT_CHANGED')
                    self._observed(runtime, original, settlement, runtime.deadline, token)
                self._consume(runtime, runtime.deadline, hit=True)
                self._entries[digest], self._seals[digest] = raw, seal
            return digest
        except (SQLAlchemyError, ValidationError, ObservationError, KnowledgeError, ResearchContextError):
            raise PortError('SOURCE_UNAVAILABLE') from None
        except ProposalRejected:
            raise PortError('INVALID_RECORD') from None

    def read_v1(self, runtime, read, deadline):
        require(decode('ReadContext', read.encode()) == read and deadline.context == read, 'INVALID_RECORD')
        require(read.scope == runtime.key.scope, 'SOURCE_UNAVAILABLE')
        key = self._key(runtime)
        digest = sha256(key).hexdigest()
        try:
            with self._locked(deadline):
                with runtime.guard.read(deadline) as token:
                    self._qualify(runtime, deadline, token)
                    raw, seal = self._entries.get(digest), self._seals.get(digest)
                    self.hook('after_lookup', token)
                    deadline.check()
                    result = None
                    if raw is None and seal is None:
                        token.verify(deadline)
                    else:
                        require(raw is not None and seal is not None and seal.key == key
                                and seal.run == runtime.run and seal.permit == runtime.permit, 'CONFLICT')
                        doc, display, usage = decode_analysis(raw, runtime, digest)
                        require(sha256(raw).hexdigest() == seal.digest
                                and doc['completion_id'] == seal.completion.completion_id
                                and doc['settlement_id'] == seal.settlement.settlement_id
                                and usage == seal.completion.usage, 'CONFLICT')
                        original, settlement = self._origin(runtime, doc['completion_id'], deadline)
                        require((original, settlement) == (seal.completion, seal.settlement), 'CONFLICT')
                        self._qualify(runtime, deadline, token)
                        require(self._key(runtime) == key, 'CONTEXT_CHANGED')
                        result = AnalysisRead(digest, runtime.key.call_ref, original.completion_id,
                            settlement.settlement_id, usage, display, stamp(deadline.check()), original.expires_at)
                        require(len(result.encode()) <= MAX_ANALYSIS_BYTES, 'LIMIT_EXCEEDED')
                        self._observed(runtime, original, settlement, deadline, token)
                consumed_at = self._consume(runtime, deadline, hit=result is not None)
                if result is None:
                    return None
                # Derive display metadata from the completed final clock sample;
                # no further authority/database/clock read follows it.
                mono_end = min(runtime.run.mono_deadline_ns, read.mono_deadline_ns)
                ends = [utc(result.expires_at), utc(runtime.permit.expires_at),
                    utc(runtime.run.deadline_at), utc(read.deadline_at), deadline.expires_at,
                    consumed_at + timedelta(microseconds=(mono_end - deadline.last_ns) // 1000)]
                return replace(result, evaluated_at=stamp(consumed_at), expires_at=stamp(min(ends)))
        except _REJECTION:
            # Rejected material cannot later revive from this cache. This drops
            # only volatile analysis, never W2 liability, postings or evidence.
            # Do not wait beyond the failed operation merely to drop a payload.
            # If another lookup owns the cache lock, its own requalification and
            # seal checks still prevent this entry from being consumed unsafely.
            if self._lock.acquire(blocking=False):
                try:
                    self._entries.pop(digest, None)
                    self._seals.pop(digest, None)
                finally:
                    self._lock.release()
            raise PortError('SOURCE_UNAVAILABLE') from None
