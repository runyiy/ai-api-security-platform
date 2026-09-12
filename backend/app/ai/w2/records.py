"""Immutable, bounded ra-w2-ports/1 records; no coercion or implicit upgrades.

The declarative layouts are the wire schema, including required nullable fields.
Validation happens on bounded bytes before constructing immutable records. Direct
construction takes the same path; no trusted/model_construct bypass is provided.
"""
from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
import re
from types import MappingProxyType

from app.ai.proposals.bindings import RATE_CARD, USAGE_MAPPING
from app.ai.proposals.codec import ProposalRejected, bounded_json, canonical

MAX_N = 2**63 - 1
MISSING = ('POLICY_PROOF_MISSING', 'SOURCE_BINDING_MISSING', 'PROJECTION_APPROVAL_MISSING',
           'SYNTHETIC_REVIEW_MISSING', 'ACTOR_MODE_MISSING', 'INDEPENDENT_FACTS_MISSING',
           'INDEPENDENT_EXPECTATION_MISSING', 'SHAPE_MISSING')
STATES = ('RULES_SUFFICIENT', 'RETRIEVAL_SUFFICIENT', 'PRIORITY_UNRESOLVED', 'NEEDS_INPUT',
          'NO_ELIGIBLE_RULE', 'AWAITING_APPROVAL', 'UNSUPPORTED', 'LIMIT_EXCEEDED')
STAGES = ('before_input', 'after_input', 'after_wait', 'before_secret', 'after_secret',
          'after_dns', 'after_connect', 'after_tls', 'final_send', 'sending',
          'before_write', 'write_ready', 'after_send', 'before_consume', 'before_return',
          'final_return', 'complete')
KINDS = ('PERMIT_ISSUED', 'WRITE_ACCEPTED', 'STREAM_CLOSED', 'FINAL_USAGE', 'ZERO_PROVEN',
         'COVERAGE_GAP', 'CONFLICT')
TERMINALS = ('NONE', 'COMPLETED', 'INCOMPLETE', 'FAILED', 'CANCELLED', 'UNKNOWN')
PORT_CODES = ('VERSION_UNSUPPORTED', 'INVALID_RECORD', 'LIMIT_EXCEEDED', 'SOURCE_UNAVAILABLE',
              'CONTEXT_CHANGED', 'CANCELLED', 'DEADLINE_EXCEEDED', 'AUTHORITY_UNAVAILABLE',
              'RESERVATION_UNAVAILABLE', 'OWNER_LOST', 'OBSERVER_UNAVAILABLE', 'COMMIT_UNKNOWN', 'CONFLICT')
BRIDGE = dict(zip(PORT_CODES, ('VERSION_UNSUPPORTED', 'MALFORMED_OUTPUT', 'INPUT_LIMIT',
    'SOURCE_UNAVAILABLE', 'CONTEXT_CHANGED', 'CANCELLED', 'PROVIDER_TIMEOUT', 'CONFIG_UNAPPROVED',
    'BUDGET_UNAVAILABLE', 'BUDGET_UNAVAILABLE', 'AUDIT_UNAVAILABLE', 'AUDIT_UNAVAILABLE', 'AUDIT_UNAVAILABLE')))


class PortError(Exception):
    def __init__(self, code='INVALID_RECORD', key_digest=None):
        if code not in PORT_CODES:
            code = 'INVALID_RECORD'
        self.code, self.key_digest = code, key_digest
        super().__init__(code)

    def document(self):
        return {'format': 'ra-w2-error/1', 'code': self.code, 'key_digest': self.key_digest}


def require(condition, code='INVALID_RECORD'):
    if not condition:
        raise PortError(code)


def utc(value):
    require(type(value) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z', value))
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
    except ValueError:
        raise PortError() from None


def stamp(value):
    require(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
    value = value.astimezone(timezone.utc)
    return value.isoformat(timespec='microseconds').replace('+00:00', 'Z')


# ? means a required nullable value; [min:max] is an ordered unique array.
LAYOUTS = {
 'Scope': 'task_id:Id case_id:Id project_ref:Id context_ref:Id context_version:Ver context_generation:Gen account_ref:Id deployment_ref:Id policy_id:Id policy_version:Ver',
 'ReadContext': 'scope:Scope process_epoch:Id deadline_at:Time mono_deadline_ns:N cancellation_id:Id',
 'RunContext': 'scope:Scope call_ref:Call attempt:=1 owner_id:Id owner_generation:Gen process_epoch:Id started_at:Time deadline_at:Time mono_start_ns:N mono_deadline_ns:N cancellation_id:Id',
 'RecoveryContext': 'read:ReadContext key:ReservationKey recovery_decision_id:Id owner_id:Id owner_generation:Gen',
 'ExactSource': 'scope:=reusable_synthetic knowledge_id:Knowledge version:Ver digest:Hash',
 'ProjectionBinding': 'source:ExactSource projection_digest:Hash template_digest:Hash template:=sharing-complete/1 decision_refs:Id[7:7] validation_id:Positive validation_digest:Hash review_event_id:Positive reuse_event_id:Positive publication_event_id:Positive valid_from:Time expires_at:Time',
 'CandidateRef': 'source_id:Id source_version:Ver source_digest:Hash',
 'SyntheticCandidateCertificate': 'source_id:Id source_version:Ver source_digest:Hash shape:=single_resource_path_get_json_object actor_mode:Actor scenario_manifest_digest:Hash missing:Missing[0:8] decision_refs:Id[7:7] valid_from:Time expires_at:Time',
 'QuestionManifest': 'scope:Scope question_id:Id entry:Entry policy_version:=ra-w2-necessity/1 template:Label initial_rule:Bool local_policy_ref:Id? rule:ExactSource? projection:ProjectionBinding? candidate_refs:CandidateRef[0:2] synthetic_review_refs:Id[0:2] missing:Missing[0:8] valid_from:Time expires_at:Time',
 'RetrievalRequest': 'question_digest:Hash purpose:=offline_context_explanation keywords:Word[0:8] tags:Tag[0:8] selected:ExactSource[1:32] top_k:=4',
 'RetrievalResult': 'question_digest:Hash bindings:ProjectionBinding[0:4] projection_refs:Hash[0:4] scores:Score[0:4] evaluated_at:Time expires_at:Time audit_id:Id',
 'Preparation': 'question_digest:Hash state:PreparationState missing:Missing[0:8] template:Label? references:Id[0:4] prepared_ref:Id? core_digest:Hash? evaluated_at:Time expires_at:Time',
 'BindingCore': 'scope:Scope question_id:Id question_digest:Hash call_ref:Call attempt:=1 w1_binding_digest:Hash payload_digest:Hash body_digest:Hash registry_digest:Hash configuration_digest:Hash config_revision:Id secret_ref:Id secret_version:Id necessity_version:=ra-w2-necessity/1 retrieval_version:=ra-w2-retrieval/1 projections:ProjectionBinding[0:4] candidates:SyntheticCandidateCertificate[0:2] prompt_digest:Hash schema_digest:Hash model:=gpt-5.6-terra profile:=ra-openai-responses/1 destination:=https://api.openai.com:443/v1/responses method:=POST reasoning:=low input_limit:=4096 output_limit:=1024 reserved_tokens:N reserved_microusd:N rate_card:Rate usage_mapping:Mapping currency:=USD token_bound_evidence_ref:Id measurement_kind:=synthetic valid_from:Time expires_at:Time',
 'ReservationKey': 'scope:Scope call_ref:Call attempt:=1 core_digest:Hash',
 'Reservation': 'key:ReservationKey reservation_id:Id owner_id:Id owner_generation:Gen input_limit:=4096 output_limit:=1024 reserved_tokens:N reserved_microusd:N currency:=USD rate_card:Rate usage_mapping:Mapping expires_at:Time measurement_kind:=synthetic',
 'PreparationAuthorityRead': 'read:ReadContext question_digest:Hash stage:PreparationStage',
 'PreparationAuthorityView': 'scope:Scope question_digest:Hash registry_revision:Gen policy_binding_ref:Id? projections:ProjectionBinding[0:4] candidates:SyntheticCandidateCertificate[0:2] missing:Missing[0:8] valid_from:Time expires_at:Time observed_cancel_generation:Gen',
 'AuthorityRead': 'run:RunContext key:ReservationKey stage:Stage expected_w1_digest:Hash expected_config_revision:Id',
 'AuthorityView': 'key:ReservationKey owner_generation:Gen snapshot_ref:Id snapshot_digest:Hash valid_from:Time expires_at:Time observed_cancel_generation:Gen',
 'Admission': 'key:ReservationKey admission_id:Id owner_generation:Gen admitted_at:Time expires_at:Time',
 'AcceptanceTicket': 'ticket_id:Id key_digest:Hash deployment_ref:Id authority_epoch:Id acceptance_generation:Gen owner_generation:Gen body_digest:Hash expires_at:Time',
 'InvalidationContext': 'deployment_ref:Id writer_id:Id process_epoch:Id deadline_at:Time mono_deadline_ns:N cancellation_id:Id',
 'InvalidationRequest': 'operation_id:Id operation_digest:Hash deployment_ref:Id writer_id:Id action:=INVALIDATE_DEPLOYMENT reason:InvalidationReason deadline_at:Time',
 'InvalidationAck': 'operation_id:Id operation_digest:Hash deployment_ref:Id authority_epoch:Id acceptance_generation:Gen barrier_sequence:Positive state:=CLOSED disposition:Disposition event_digest:Hash',
 'InvalidationResolution': 'operation_id:Id operation_digest:Hash deployment_ref:Id barrier_digest:Hash database_outcome:DatabaseOutcome transaction_evidence_ref:Id?',
 'ReopenRequest': 'deployment_ref:Id recovery_decision_id:Id expected_authority_epoch:Id expected_acceptance_generation:Gen pending_set_digest:Hash transaction_evidence_digest:Hash qualification_digest:Hash stream_closure_evidence_digest:Hash',
 'WriterFence': 'deployment_ref:Id recovery_decision_id:Id authority_epoch:Id acceptance_generation:Gen',
 'GateAck': 'deployment_ref:Id authority_epoch:Id acceptance_generation:Gen barrier_sequence:Positive state:=OPEN event_digest:Hash',
 'SendPermit': 'key:ReservationKey permit_id:Id admission_id:Id owner_generation:Gen guard_epoch:Id authority_epoch:Id acceptance_generation:Gen ticket_id:Id body_digest:Hash marker_event_id:Id observer_epoch:Id expires_at:Time',
 'Observation': 'key_digest:Hash call_ref:Call event_id:Id observer_epoch:Id authority_epoch:Id acceptance_generation:Gen sequence:Positive owner_generation:Gen kind:ObservationKind permit_id:Id? body_digest:Hash observed_at:Time terminal:Terminal usage:UsageView evidence_digest:Hash',
 'UsageView': 'state:UsageState input_tokens:N? output_tokens:N? cached_input_tokens:N? cache_write_tokens:N? reasoning_tokens:N? total_tokens:N? mapping:Mapping',
 'Settlement': 'key_digest:Hash settlement_id:Id final_event_id:Id revision:Gen state:SettlementState settled_tokens:N settled_microusd:N held_tokens:N held_microusd:N refund_tokens:N refund_microusd:N overrun_tokens:N overrun_microusd:N accounting_digest:Hash',
 'Completion': 'key_digest:Hash completion_id:Id outcome_code:W1Code? delivery:Delivery usage:UsageView settlement_ref:Id? display_state:DisplayState evaluated_at:Time expires_at:Time',
 'WriteAck': 'permit_id:Id event_id:Id sequence:Positive accepted_at:Time',
 'ObservationAck': 'event_id:Id sequence:Positive event_digest:Hash',
 'CallEvidence': 'key_digest:Hash evidence_digest:Hash',
 'CancelAck': 'key_digest:Hash cancellation_generation:Gen state:=CANCELLED',
 'Error': 'code:PortCode key_digest:Hash?',
 # Local registry evidence is never forwarded into a W1 payload. These records
 # make independently supplied synthetic decisions inspectable and revocable.
 'ScopeMapping': 'scope:Scope project_number:Positive context_id:Positive valid_from:Time expires_at:Time',
 'Decision': 'decision_id:Id role:DecisionRole source_id:Id source_version:Ver source_digest:Hash projection_digest:Hash actor:=local_operator evidence:=independent_synthetic_reviewed valid_from:Time expires_at:Time',
 'ProjectionReview': 'binding:ProjectionBinding content_digest:Hash examples_digest:Hash counterexamples_digest:Hash reviewer_decision:Id valid_from:Time expires_at:Time',
 'SyntheticScenario': 'source_id:Id source_version:Ver shape:=single_resource_path_get_json_object actor_mode:Actor relationship:Relationship expected_access:ExpectedAccess facts_ref:Id? expectation_ref:Id? authorship:=independent_synthetic valid_from:Time expires_at:Time',
 'LocalPolicy': 'policy_ref:Id policy_version:=ra-w2-necessity/1 rule:=explicit_access_facts template:=access-basis/1 review_ref:Id valid_from:Time expires_at:Time',
 'ModelPermission': 'permission_id:Id scope:Scope config_revision:Id account_ref:Id secret_ref:Id secret_version:Id enabled:Bool permissions:Permission[7:7] token_bound_evidence_ref:Id valid_from:Time expires_at:Time measurement_kind:=synthetic',
 'TokenCertificate': 'evidence_id:Id config_revision:Id model:=gpt-5.6-terra profile:=ra-openai-responses/1 prompt_digest:Hash schema_digest:Hash projection_digest:Hash counting_version:=synthetic-fixture/1 input_limit:=4096 output_limit:=1024 measurement_kind:=synthetic valid_from:Time expires_at:Time',
 'TaskManifest': 'scope:Scope questions:QuestionManifest[1:64] review_ref:Id valid_from:Time expires_at:Time',
}
COMPOSITE = frozenset(('ReadContext RunContext RecoveryContext QuestionManifest RetrievalRequest '
    'RetrievalResult BindingCore ReservationKey Reservation PreparationAuthorityRead PreparationAuthorityView '
    'AuthorityRead AuthorityView Admission SendPermit ScopeMapping ProjectionReview ModelPermission TaskManifest').split())
ENUMS = {'Missing': MISSING, 'PreparationState': STATES, 'Stage': STAGES, 'ObservationKind': KINDS,
    'Terminal': TERMINALS, 'PortCode': PORT_CODES, 'Actor': ('anonymous', 'bearer'),
    'Entry': ('Q1', 'Q2', 'Q3', 'Q4'), 'UsageState': ('known', 'unknown', 'invalid'),
    'PreparationStage': ('before_rules', 'before_retrieval', 'after_retrieval', 'after_render'),
    'Tag': ('access', 'ownership', 'sharing', 'uncertainty', 'session', 'synthetic', 'bola', 'scope'),
    'Disposition': ('PENDING', 'COMMITTED', 'ROLLED_BACK', 'UNKNOWN'),
    'DatabaseOutcome': ('COMMITTED', 'ROLLED_BACK', 'UNKNOWN'),
    'InvalidationReason': ('LIFECYCLE', 'CONFIGURATION', 'CANCELLATION', 'RECOVERY'),
    'SettlementState': ('ZERO', 'KNOWN', 'UNKNOWN', 'CONFLICT'),
    'Delivery': ('not_sent', 'unknown', 'responded'), 'DisplayState': ('SUPPRESSED', 'ELIGIBLE_NOW')}
ENUMS.update(DecisionRole=('possession', 'use', 'reuse', 'review', 'validation', 'publish', 'egress'),
             Permission=('protocol', 'transport', 'model', 'data', 'retention', 'account', 'budget'),
             Relationship=('owner', 'non_owner', 'shared', 'unspecified'),
             ExpectedAccess=('allowed', 'denied', 'unspecified'))
PATTERNS = {'Id': r'[A-Za-z0-9_-]{1,64}', 'Label': r'[A-Za-z0-9_./-]{1,96}',
            'Hash': '[0-9a-f]{64}', 'Call': 'q_[0-9a-f]{32}',
            'Knowledge': 'knowledge-[1-9][0-9]{0,5}', 'Word': '[a-z]{1,32}'}


def format_for(name):
    return 'ra-w2-' + re.sub(r'(?<!^)(?=[A-Z])', '-', name).lower() + '/1'


def _scalar(kind, value):
    if kind.endswith('?'):
        return value is None or _scalar(kind[:-1], value)
    array = re.fullmatch(r'(.+)\[(\d+):(\d+)\]', kind)
    if array:
        element, lower, upper = array.groups()
        require(type(value) is list and int(lower) <= len(value) <= int(upper), 'LIMIT_EXCEEDED')
        for item in value:
            require(_scalar(element, item))
        if element != 'Score':
            require(len({canonical(v) for v in value}) == len(value))
        return True
    if kind in LAYOUTS:
        _validate(kind, value)
        return True
    if kind.startswith('='):
        expected = int(kind[1:]) if kind[1:].isdigit() else kind[1:]
        return type(value) is type(expected) and value == expected
    if kind in PATTERNS:
        return type(value) is str and re.fullmatch(PATTERNS[kind], value) is not None
    if kind in ENUMS:
        return type(value) is str and value in ENUMS[kind]
    if kind in ('N', 'Positive', 'Gen', 'Ver', 'Score'):
        lower, upper = {'N': (0, MAX_N), 'Positive': (1, MAX_N), 'Gen': (1, 2147483647),
                        'Ver': (1, 10000), 'Score': (1, 24)}[kind]
        return type(value) is int and lower <= value <= upper
    if kind == 'Bool':
        return type(value) is bool
    if kind == 'Time':
        utc(value)
        return True
    if kind in ('Rate', 'Mapping'):
        return type(value) is str and value == {'Rate': RATE_CARD, 'Mapping': USAGE_MAPPING}[kind]
    if kind == 'W1Code':
        from app.ai.proposals.codec import CODES
        return type(value) is str and value in CODES
    raise RuntimeError('W2_SCHEMA_DEFINITION_INVALID')


def _validate(name, value):
    require(type(value) is dict)
    require(value.get('format') == format_for(name), 'VERSION_UNSUPPORTED')
    fields = dict(item.split(':', 1) for item in LAYOUTS[name].split())
    require(set(value) == {'format', *fields})
    for key, kind in fields.items():
        require(_scalar(kind, value[key]))
    if 'valid_from' in fields:
        require(utc(value['valid_from']) < utc(value['expires_at']))
    if name == 'RunContext':
        require(0 < (utc(value['deadline_at']) - utc(value['started_at'])).total_seconds() <= 30)
        require(0 < value['mono_deadline_ns'] - value['mono_start_ns'] <= 30_000_000_000)
    if name in ('Reservation', 'BindingCore'):
        require(value['reserved_tokens'] >= 5120 and value['reserved_microusd'] >= 22528)
    if name == 'UsageView':
        counts = [value[k] for k in ('input_tokens', 'output_tokens', 'cached_input_tokens',
                  'cache_write_tokens', 'reasoning_tokens', 'total_tokens')]
        i, o, c, w, r, total = counts
        if value['state'] == 'known':
            require(all(v is not None for v in (i, o, c, w, total)))
            require(c + w <= i and i + o == total and (r is None or r <= o))
        else:
            require(all(v is None for v in counts))
    if name == 'RetrievalResult':
        require(len(value['bindings']) == len(value['projection_refs']) == len(value['scores']))
    if name == 'Preparation':
        require((value['prepared_ref'] is None) == (value['core_digest'] is None))
        require(value['prepared_ref'] is None or value['state'] == 'PRIORITY_UNRESOLVED')
    if 'missing' in fields:
        require(value['missing'] == sorted(value['missing']))


def _freeze(value):
    if type(value) is dict:
        if 'format' in value:
            name = next(n for n in LAYOUTS if format_for(n) == value['format'])
            return Record(name, MappingProxyType({k: _freeze(v) for k, v in value.items()}), _INTERNAL)
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    return tuple(_freeze(v) for v in value) if type(value) is list else value


_INTERNAL = object()


class Record(Mapping):
    __slots__ = ('_name', '_values')

    def __init__(self, name, values, token):
        require(token is _INTERNAL)
        object.__setattr__(self, '_name', name)
        object.__setattr__(self, '_values', values)

    def __setattr__(self, name, value):
        raise TypeError('immutable W2 record')

    def __getattr__(self, name):
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def document(self):
        def thaw(value):
            if isinstance(value, Mapping):
                return {k: thaw(v) for k, v in value.items()}
            return [thaw(v) for v in value] if type(value) is tuple else value
        return thaw(self)

    def encode(self):
        return canonical(self.document())

    def fingerprint(self):
        domain = 'ra-ai-budget/1' if self._name == 'BindingCore' else self.format
        return sha256(domain.encode() + b'\n' + self.encode()).hexdigest()

    def __repr__(self):
        return f'<W2 {self._name}>'


def decode(name, raw):
    require(name in LAYOUTS, 'VERSION_UNSUPPORTED')
    composite = name in COMPOSITE
    try:
        value = bounded_json(raw, maximum=32768 if composite else 4096,
                             depth=8 if composite else 6, nodes=4096 if composite else 512)
    except ProposalRejected as exc:
        raise PortError('LIMIT_EXCEEDED' if exc.code == 'OUTPUT_LIMIT' else 'INVALID_RECORD') from None
    _validate(name, value)
    # Nested records independently retain their record-specific caps too.
    for key, kind in (item.split(':', 1) for item in LAYOUTS[name].split()):
        nested = kind.rstrip('?').split('[')[0]
        if nested in LAYOUTS and value[key] is not None:
            for child in value[key] if '[' in kind else [value[key]]:
                decode(nested, canonical(child))
    return _freeze(value)


def make(name, **values):
    count = 0
    def thaw(value, depth=0):
        nonlocal count
        count += 1
        require(count <= 4096 and depth <= 8, 'LIMIT_EXCEEDED')
        if type(value) is Record:
            return {k: thaw(v, depth+1) for k, v in value.items()}
        if type(value) is dict:
            require(len(value) <= 512, 'LIMIT_EXCEEDED')
            return {k: thaw(v, depth+1) for k, v in value.items()}
        if type(value) in (list, tuple):
            require(len(value) <= 512, 'LIMIT_EXCEEDED')
            return [thaw(v, depth+1) for v in value]
        if type(value) is str:
            require(len(value) <= 32768, 'LIMIT_EXCEEDED')
        return value
    try:
        return decode(name, canonical(thaw({'format': format_for(name), **values})))
    except (ValueError, TypeError, OverflowError, RecursionError, ProposalRejected):
        raise PortError() from None


def usage_view(usage):
    from dataclasses import asdict
    return make('UsageView', **asdict(usage))
