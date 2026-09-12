"""The adopted four-entry necessity table and complete literal projection."""
from dataclasses import asdict, dataclass
from hashlib import sha256
from uuid import uuid4

from app.ai.proposals.adapter import PROMPT, request_body
from app.ai.proposals.bindings import (Configuration, PreparedProposal, Registry as W1Registry,
    SourceBinding, RATE_CARD, USAGE_MAPPING, validate_registry)
from app.ai.proposals.codec import canonical
from app.ai.proposals.contract import INPUT_VERSION, input_document, output_schema
from .records import make, decode, require, PortError, stamp, utc
from .registry import ref_id
from .retrieval import CLAIM, CODES, PROJECTION_DIGEST

TEMPLATES = {
 'Q1': ('access-basis/1', 'Ownership alone does not establish access. Supply independent access facts before execution.'),
 'Q2': ('sharing-rule/1', 'Review the referenced sharing rule and its limitations. No project conclusion.'),
 'Q3': ('single-priority/1', 'One eligible synthetic candidate remains for human review. Not executed.'),
 'Q4': ('compare-priority/1', 'Review priority is unresolved between the declared synthetic candidates. Not executed.'),
}
PROMPT_DIGEST = sha256(PROMPT.encode()).hexdigest()
SCHEMA_DIGEST = sha256(canonical(output_schema())).hexdigest()


def synthetic_input_units(body):
    """Fixed fake fixture counter over the entire rendered request.

    Four UTF-8 bytes per unit, rounded once. This exercises the admission bound
    only; it is not a provider tokenizer or evidence about hidden overhead.
    """
    require(type(body) is bytes and len(body)<=32768,'LIMIT_EXCEEDED')
    return (len(body)+3)//4


def w1_registry_document(registry):
    doc = asdict(registry)
    doc['valid_from'], doc['expires_at'] = registry.valid_from.isoformat(), registry.expires_at.isoformat()
    doc['allowed_pairs'] = sorted(registry.allowed_pairs)
    for source in doc['sources']:
        source['valid_from'], source['expires_at'] = source['valid_from'].isoformat(), source['expires_at'].isoformat()
    return doc


@dataclass(frozen=True, repr=False)
class PreparedCall:
    question: object
    read: object
    prepared: PreparedProposal
    config: Configuration
    body: bytes
    core: object
    permission: object
    certificate: object
    manifest_digest: str

    @property
    def key(self):
        return make('ReservationKey', scope=self.core.scope, call_ref=self.core.call_ref,
                    attempt=1, core_digest=self.core.fingerprint())


class PreparationService:
    def __init__(self, registry, retrieval):
        self.registry, self.retrieval = registry, retrieval
        self.prepared = {}

    def read_preparation_authority_v1(self, request, question, deadline, token=None):
        require(request.question_digest == question.fingerprint() and request.read.scope == question.scope, 'CONTEXT_CHANGED')
        question, _ = self.registry.question(question, deadline)
        cancel_generation = self.registry.task_cancellation(question.scope, deadline)
        with self.retrieval.guard.read(deadline, token), self.retrieval.sessions() as db, db.begin():
            deadline.sql_timeout(db)
            self.retrieval.context(db, request.read, deadline)
        deadline.check((question.valid_from, question.expires_at))
        missing = set(question.missing)
        policy_ref, projections, candidates = None, [], []
        if question.entry == 'Q1':
            if question.local_policy_ref is None:
                missing.add('POLICY_PROOF_MISSING')
            else:
                policy = self.registry.get(question.local_policy_ref, 'LocalPolicy', deadline)
                require(policy.policy_ref == question.local_policy_ref, 'SOURCE_UNAVAILABLE')
                policy_ref = policy.policy_ref
        else:
            if question.rule is None:
                missing.add('SOURCE_BINDING_MISSING')
            elif question.projection is None:
                missing.add('PROJECTION_APPROVAL_MISSING')
            else:
                binding = self.registry.projection(question.rule, deadline)
                require(question.projection == binding, 'SOURCE_UNAVAILABLE')
                projections.append(binding)
        for index, reference in enumerate(question.candidate_refs):
            if index >= len(question.synthetic_review_refs):
                missing.add('SYNTHETIC_REVIEW_MISSING')
                continue
            cert, gaps = self.registry.candidate(reference, question.synthetic_review_refs[index], deadline)
            candidates.append(cert)
            missing.update(gaps)
        now = deadline.check()
        until = min([utc(question.expires_at), utc(request.read.deadline_at),
                     deadline.expires_at or utc(request.read.deadline_at),
                     *[utc(p.expires_at) for p in projections], *[utc(c.expires_at) for c in candidates]])
        return make('PreparationAuthorityView', scope=question.scope, question_digest=question.fingerprint(),
            registry_revision=question.scope.context_generation, policy_binding_ref=policy_ref,
            projections=projections, candidates=candidates, missing=sorted(missing),
            valid_from=stamp(now), expires_at=stamp(until), observed_cancel_generation=cancel_generation)

    def _authority(self, read, question, stage, deadline, token=None):
        request = make('PreparationAuthorityRead', read=read, question_digest=question.fingerprint(), stage=stage)
        view = self.read_preparation_authority_v1(request, question, deadline, token)
        deadline.check((view.valid_from, view.expires_at))
        return view

    def prepare_v1(self, read, question, deadline):
        question = decode('QuestionManifest', question.encode() if hasattr(question, 'encode') else question)
        require(read.scope == question.scope, 'SOURCE_UNAVAILABLE')
        count = {'Q1': 0, 'Q2': 0, 'Q3': 1, 'Q4': 2}[question.entry]
        unsupported = question.template != TEMPLATES[question.entry][0] or len(question.candidate_refs) != count
        unsupported |= (question.entry == 'Q1' and (question.rule is not None or question.projection is not None))
        unsupported |= (question.entry != 'Q1' and question.local_policy_ref is not None)
        unsupported |= len(question.synthetic_review_refs) > count
        require(len({(c.source_id, c.source_version) for c in question.candidate_refs}) == len(question.candidate_refs), 'SOURCE_UNAVAILABLE')
        view = self._authority(read, question, 'before_rules', deadline)
        def result(state, *, missing=(), prepared_ref=None, core_digest=None):
            final = self._authority(read, question, 'after_render', deadline)
            require(final.projections == view.projections and final.candidates == view.candidates
                    and final.missing == view.missing, 'CONTEXT_CHANGED')
            if state in ('RULES_SUFFICIENT','RETRIEVAL_SUFFICIENT','PRIORITY_UNRESOLVED') and question.rule is not None:
                require(self.retrieval.qualified_selected(read,question,deadline)==question.projection,'CONTEXT_CHANGED')
            return make('Preparation', question_digest=question.fingerprint(), state=state, missing=list(missing),
                template=question.template if not unsupported else None,
                references=([question.rule.knowledge_id] if question.rule is not None else []) +
                           [c.source_id for c in question.candidate_refs],
                prepared_ref=prepared_ref, core_digest=core_digest, evaluated_at=stamp(deadline.check()),
                expires_at=final.expires_at)
        if unsupported:
            return result('UNSUPPORTED')
        if view.missing:
            return result('NEEDS_INPUT', missing=view.missing)
        if question.entry == 'Q1':
            return result('RULES_SUFFICIENT')
        if question.entry != 'Q4' and question.initial_rule:
            binding = self.retrieval.qualified_selected(read, question, deadline)
            require(binding == question.projection, 'SOURCE_UNAVAILABLE')
            return result('RULES_SUFFICIENT')
        self._authority(read, question, 'before_retrieval', deadline)
        request = make('RetrievalRequest', question_digest=question.fingerprint(), purpose='offline_context_explanation',
                       keywords=['sharing'], tags=['sharing'], selected=[question.rule], top_k=4)
        found = self.retrieval.retrieve_projected_v1(read, request, deadline)
        self._authority(read, question, 'after_retrieval', deadline)
        if not found.bindings:
            return result('NO_ELIGIBLE_RULE')
        require(found.bindings == (question.projection,), 'SOURCE_UNAVAILABLE')
        if question.entry != 'Q4':
            return result('RETRIEVAL_SUFFICIENT')
        try:
            permission, certificate = self.registry.model_permission(read.scope, deadline)
            _, manifest = self.registry.question(question, deadline)
            self.registry.budget_decisions(read.scope, manifest, deadline)
        except PortError as error:
            if error.code not in ('AUTHORITY_UNAVAILABLE', 'SOURCE_UNAVAILABLE'):
                raise
            return result('AWAITING_APPROVAL')
        require(len(self.prepared) < 64, 'LIMIT_EXCEEDED')
        prepared_ref = uuid4().hex
        prepared = self._render(read, question, view, permission, certificate, deadline)
        # Freeze before reserve. Neither the body nor source content is durable.
        completion = result('PRIORITY_UNRESOLVED', prepared_ref=prepared_ref, core_digest=prepared.core.fingerprint())
        self.prepared[prepared_ref] = prepared
        return completion

    def _render(self, read, question, view, permission, certificate, deadline):
        call_ref, rule_ref = 'q_' + uuid4().hex, 'k_' + uuid4().hex
        candidate_refs = ['c_' + uuid4().hex for _ in view.candidates]
        doc = {'protocol': INPUT_VERSION, 'request_ref': call_ref, 'task': 'prioritize_review',
            'candidates': [dict(ref=ref, shape=c.shape, actor_mode=c.actor_mode) for ref, c in zip(candidate_refs, view.candidates)],
            'rules': [dict(ref=rule_ref, claim=CLAIM, applicability_codes=list(CODES))], 'gaps': []}
        payload = canonical(doc)
        body = request_body(input_document(payload))
        require(synthetic_input_units(body)<=certificate.input_limit,'LIMIT_EXCEEDED')
        valid_from = max([utc(question.valid_from), utc(permission.valid_from), utc(certificate.valid_from),
                          *[utc(s.valid_from) for s in (*view.projections, *view.candidates)]])
        expires_at = min([utc(read.deadline_at), utc(view.expires_at), utc(permission.expires_at), utc(certificate.expires_at),
                          deadline.expires_at or utc(read.deadline_at)])
        projection = view.projections[0]
        sources = [SourceBinding(rule_ref, projection.source.knowledge_id, projection.source.version,
            projection.source.digest, projection.projection_digest, 'rule', 'synthetic_authored', 'reusable_synthetic',
            projection.decision_refs, utc(projection.valid_from), utc(projection.expires_at), 'active', 'independent_synthetic_reviewed')]
        for ref, cert in zip(candidate_refs, view.candidates):
            projection_hash = sha256(canonical({'shape': cert.shape, 'actor_mode': cert.actor_mode})).hexdigest()
            sources.append(SourceBinding(ref, cert.source_id, cert.source_version, cert.source_digest, projection_hash,
                'synthetic_catalog', 'synthetic_authored', 'reusable_synthetic', cert.decision_refs,
                utc(cert.valid_from), utc(cert.expires_at), 'active', 'independent_synthetic_reviewed'))
        registry = W1Registry(call_ref, read.scope.project_ref, read.scope.context_ref, read.scope.context_generation,
            tuple(sources), frozenset((c, rule_ref) for c in candidate_refs), valid_from, expires_at, 'active')
        config = Configuration(permission.config_revision, permission.account_ref, permission.secret_ref,
                               permission.secret_version, enabled=permission.enabled)
        config.validate()
        w1 = PreparedProposal(payload, registry)
        validate_registry(w1, doc, deadline.check())
        core = make('BindingCore', scope=read.scope, question_id=question.question_id, question_digest=question.fingerprint(),
            call_ref=call_ref, attempt=1, w1_binding_digest=w1.digest(config), payload_digest=sha256(payload).hexdigest(),
            body_digest=sha256(body).hexdigest(), registry_digest=sha256(canonical(w1_registry_document(registry))).hexdigest(),
            configuration_digest=sha256(canonical(asdict(config))).hexdigest(), config_revision=config.revision,
            secret_ref=config.secret_ref, secret_version=config.secret_version, necessity_version='ra-w2-necessity/1',
            retrieval_version='ra-w2-retrieval/1', projections=view.projections, candidates=view.candidates,
            prompt_digest=PROMPT_DIGEST, schema_digest=SCHEMA_DIGEST, model=config.model, profile=config.profile,
            destination='https://api.openai.com:443/v1/responses', method='POST', reasoning='low', input_limit=4096,
            output_limit=1024, reserved_tokens=5120, reserved_microusd=22528, rate_card=RATE_CARD,
            usage_mapping=USAGE_MAPPING, currency='USD', token_bound_evidence_ref=certificate.evidence_id,
            measurement_kind='synthetic', valid_from=stamp(valid_from), expires_at=stamp(expires_at))
        _, manifest = self.registry.question(question, deadline)
        return PreparedCall(question, read, w1, config, body, core, permission, certificate, manifest.fingerprint())

    def resolve(self, prepared_ref, key):
        value = self.prepared.get(prepared_ref)
        require(value is not None and value.key == key, 'SOURCE_UNAVAILABLE')
        return value

    def requalify(self, prepared, deadline, stage='after_render', token=None):
        view = self._authority(prepared.read, prepared.question, stage, deadline, token)
        require(not view.missing and view.projections == prepared.core.projections
                and view.candidates == prepared.core.candidates, 'CONTEXT_CHANGED')
        binding = self.retrieval.qualified_selected(prepared.read, prepared.question, deadline, token)
        require(binding == prepared.core.projections[0], 'CONTEXT_CHANGED')
        permission, certificate = self.registry.model_permission(prepared.read.scope, deadline)
        require((permission, certificate) == (prepared.permission, prepared.certificate), 'CONTEXT_CHANGED')
        deadline.check((prepared.core.valid_from, prepared.core.expires_at))
        return view
