"""Trusted, ephemeral W1 inputs. These are not database records or approvals.

A later W2 consumer must supply current authority and an independently issued
receipt. No implementation here reads project evidence or creates that ledger.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import re
from typing import Callable, ContextManager, Protocol

from .codec import canonical, require
from .contract import SHAPE

MODEL = 'gpt-5.6-terra'
PROFILE = 'ra-openai-responses/1'
USAGE_MAPPING = 'openai-responses-2026-09-11/1'
RATE_CARD = 'openai-terra-usd-standard-2026-09-11/1'
PERMISSIONS = ('protocol', 'transport', 'model', 'data', 'retention', 'account', 'budget')


@dataclass(frozen=True)
class Configuration:
    revision: str
    account_ref: str
    secret_ref: str
    secret_version: str
    enabled: bool = False
    model: str = MODEL
    profile: str = PROFILE
    usage_mapping: str = USAGE_MAPPING
    rate_card: str = RATE_CARD

    def validate(self):
        require(self.enabled is True, 'PROVIDER_DISABLED')
        require((self.model, self.profile, self.usage_mapping, self.rate_card) ==
                (MODEL, PROFILE, USAGE_MAPPING, RATE_CARD), 'CONFIG_UNAPPROVED')
        for value in (self.revision, self.account_ref, self.secret_ref, self.secret_version):
            require(type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value), 'CONFIG_UNAPPROVED')


@dataclass(frozen=True, repr=False)
class SourceBinding:
    handle: str
    source_id: str
    version: int
    source_digest: str
    projection_digest: str
    category: str
    data_class: str
    scope: str
    decision_refs: tuple[str, ...]  # possession, use, reuse, review, validation, publish, egress
    valid_from: datetime
    expires_at: datetime
    state: str = 'unavailable'
    content_review: str = 'unknown'


@dataclass(frozen=True, repr=False)
class Registry:
    request_ref: str
    project_ref: str
    context_ref: str
    generation: int
    sources: tuple[SourceBinding, ...]
    allowed_pairs: frozenset[tuple[str, str]]
    valid_from: datetime
    expires_at: datetime
    state: str = 'closed'


@dataclass(frozen=True, repr=False)
class PreparedProposal:
    payload: bytes
    registry: Registry

    def digest(self, config: Configuration) -> str:
        # Bound trusted-port metadata before asdict/canonical construction too.
        reg = self.registry
        require(type(reg) is Registry and type(reg.sources) is tuple and len(reg.sources) <= 20,
                'CONTEXT_CHANGED')
        require(type(reg.generation) is int and 1 <= reg.generation <= 2147483647,
                'CONTEXT_CHANGED')
        require(type(reg.allowed_pairs) is frozenset and len(reg.allowed_pairs) <= 32,
                'CONTEXT_CHANGED')
        for value in (reg.request_ref, reg.project_ref, reg.context_ref, reg.state):
            require(type(value) is str and 1 <= len(value) <= 64, 'CONTEXT_CHANGED')
        for source in reg.sources:
            require(type(source) is SourceBinding and type(source.version) is int
                    and 1 <= source.version <= 10000, 'CONTEXT_CHANGED')
            for value in (source.handle, source.source_id, source.source_digest,
                          source.projection_digest, source.category, source.data_class,
                          source.scope, source.state, source.content_review):
                require(type(value) is str and 1 <= len(value) <= 64, 'CONTEXT_CHANGED')
            require(type(source.decision_refs) is tuple and len(source.decision_refs) <= 7
                    and all(type(v) is str and len(v) <= 64 for v in source.decision_refs),
                    'CONTEXT_CHANGED')
        for pair in reg.allowed_pairs:
            require(type(pair) is tuple and len(pair) == 2
                    and all(type(v) is str and len(v) <= 34 for v in pair), 'CONTEXT_CHANGED')
        def timestamp(value):
            require(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None,
                    'CONTEXT_CHANGED')
            return value.isoformat()
        registry = asdict(self.registry)
        registry['valid_from'] = timestamp(self.registry.valid_from)
        registry['expires_at'] = timestamp(self.registry.expires_at)
        registry['allowed_pairs'] = sorted(self.registry.allowed_pairs)
        for source in registry['sources']:
            source['valid_from'] = timestamp(source['valid_from'])
            source['expires_at'] = timestamp(source['expires_at'])
        require(type(self.payload) is bytes, 'DATA_INELIGIBLE')
        return sha256(b'ra-proposal-binding/1\n' + canonical({
            'payload_sha256': sha256(self.payload).hexdigest(),
            'registry': registry, 'config': asdict(config),
        })).hexdigest()


@dataclass(frozen=True, repr=False)
class AuthorizationSnapshot:
    registry: Registry
    config: Configuration
    permissions: tuple[str, ...]
    valid_from: datetime
    expires_at: datetime
    state: str = 'disabled'


@dataclass(frozen=True, repr=False)
class ReservationReceipt:
    call_ref: str
    binding_digest: str
    account_ref: str
    config_revision: str
    expires_at: datetime
    measurement_kind: str  # W1 accepts only synthetic with the in-memory transport.
    input_limit: int
    output_limit: int
    reserved_tokens: int
    reserved_cost_microusd: int
    currency: str
    rate_card: str
    usage_mapping: str


class Authority(Protocol):
    def current(self, project_ref: str, context_ref: str, stage: str) -> AuthorizationSnapshot: ...


class Coordination(Protocol):
    """W2 obligations, intentionally without storage/rate/transaction implementation.

    admit: consume one exact receipt, enforce account/deployment concurrency=1,
    starts >=1s and stricter provider rate, count/deadline/cost/token caps.
    sending: serialize current lifecycle/config checks and mark_send_started with
    the first write only; no lock may span DNS/TLS/wait or response reads.
    record: independently record the bounded W1 terminal/usage projection.
    finish: release only this admission; unknown never refunds its reservation.
    """
    def admit(self, receipt: ReservationReceipt, binding_digest: str, timeout: float) -> None: ...
    def sending(self, receipt: ReservationReceipt, check: Callable[[], None]) -> ContextManager[None]: ...
    def record(self, receipt: ReservationReceipt, outcome: object) -> None: ...
    def finish(self, receipt: ReservationReceipt) -> None: ...


def interval(start, end, now):
    for value in (start, end, now):
        require(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None,
                'CONTEXT_CHANGED')
    require(start <= now < end, 'SOURCE_UNAVAILABLE')


def validate_registry(prepared: PreparedProposal, doc: dict, now: datetime):
    reg = prepared.registry
    require(reg.state == 'active', 'SOURCE_UNAVAILABLE')
    interval(reg.valid_from, reg.expires_at, now)
    require(type(reg.generation) is int and reg.generation > 0, 'CONTEXT_CHANGED')
    require(reg.request_ref == doc['request_ref'], 'REQUEST_MISMATCH')
    require(type(reg.sources) is tuple and len(reg.sources) <= 20, 'DATA_INELIGIBLE')
    items = {item['ref']: item for key in ('candidates', 'rules', 'gaps') for item in doc[key]}
    require(len(reg.sources) == len(items), 'REFERENCE_UNAVAILABLE')
    seen, handles = set(), set()
    for source in reg.sources:
        require(source.handle in items and source.handle not in handles, 'REFERENCE_UNAVAILABLE')
        handles.add(source.handle)
        key = (source.source_id, source.version)
        require(key not in seen and type(source.version) is int and source.version > 0, 'REFERENCE_UNAVAILABLE')
        seen.add(key)
        require(source.state == 'active', 'SOURCE_UNAVAILABLE')
        interval(source.valid_from, source.expires_at, now)
        require(source.data_class == 'synthetic_authored' and source.scope == 'reusable_synthetic'
                and source.content_review == 'independent_synthetic_reviewed', 'DATA_INELIGIBLE')
        categories = ('mechanism', 'rule', 'counterexample') if source.handle.startswith('k_') else ('synthetic_catalog',)
        require(source.category in categories, 'DATA_INELIGIBLE')
        require(type(source.decision_refs) is tuple and len(source.decision_refs) == 7
                and len(set(source.decision_refs)) == 7
                and all(type(v) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', v) for v in source.decision_refs), 'DATA_INELIGIBLE')
        require(type(source.source_digest) is str and re.fullmatch('[0-9a-f]{64}', source.source_digest), 'SOURCE_UNAVAILABLE')
        projection = {k: v for k, v in items[source.handle].items() if k != 'ref'}
        require(sha256(canonical(projection)).hexdigest() == source.projection_digest, 'DATA_INELIGIBLE')
        # Defense in depth only. Exact reviewed projections above are the primary
        # content allowlist; this recognizer is not a general PII/injection oracle.
        if 'claim' in projection:
            claim = projection['claim']
            require(not re.search(r'https?://|www\.|\bBearer\s|\bsk-|[\w.+-]+@[\w.-]+|ignore.{0,40}instructions|system\s*:|<script|synthetic[-_ ](?:secret|pii)|[\x00-\x1f\x7f]', claim, re.I), 'DATA_INELIGIBLE')
    require(type(reg.allowed_pairs) is frozenset and len(reg.allowed_pairs) <= 32, 'SUGGESTION_UNSUPPORTED')
    candidates = {v['ref']: v for v in doc['candidates']}
    rules = {v['ref']: v for v in doc['rules']}
    for pair in reg.allowed_pairs:
        require(type(pair) is tuple and len(pair) == 2, 'SUGGESTION_UNSUPPORTED')
        c, r = pair
        require(c in candidates and r in rules, 'REFERENCE_UNAVAILABLE')
        require(candidates[c]['shape'] == SHAPE and candidates[c]['actor_mode'] in ('anonymous', 'bearer')
                and SHAPE in rules[r]['applicability_codes']
                and candidates[c]['actor_mode'] in rules[r]['applicability_codes'], 'SUGGESTION_UNSUPPORTED')
