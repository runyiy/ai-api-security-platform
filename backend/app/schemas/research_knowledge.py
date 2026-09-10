"""Synthetic knowledge metadata. No publication or execution authority."""
import hashlib
import re
from typing import Annotated, Literal
from pydantic import Field, model_validator
from app.schemas.research_context import StrictRecord, SyntheticReference, ID, Version
from app.schemas.research_observation import BoundedJSON, ObservationError, canonical, timestamp

MAX_INPUT, MAX_QUERY, MAX_CONTENT, MAX_RESPONSE = 32768, 8192, 16384, 65536
MAX_SCAN, MAX_VERSIONS, MAX_EVENTS, MAX_AUDIT, MAX_RESULTS = 256, 128, 16, 4096, 8
Key = Annotated[str, Field(pattern=r'^knowledge-[1-9][0-9]{0,5}$', max_length=64)]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$', max_length=64)]
Tag = Literal['access', 'ownership', 'sharing', 'uncertainty', 'session', 'synthetic', 'bola', 'scope']
Claim = Literal['Ownership does not imply access.', 'Sharing may allow non-owner access.',
                'Missing facts require input.', 'Session claims are not health proof.']


class KnowledgeError(Exception):
    def __init__(self, code='knowledge_unavailable', status=409):
        self.code, self.status = code, status
        super().__init__(code)


class KnowledgeJSON(BoundedJSON):
    def value(self, depth):
        if self.nodes >= 2048:
            raise KnowledgeError('knowledge_input_limit', 413)
        return super().value(depth)  # Existing independent depth=5 and UTF-8 safeguards.


def validate(schema, value, limit=MAX_INPUT):
    try:
        raw = value if isinstance(value, bytes) else canonical(value.model_dump(warnings=False) if isinstance(value, schema) else value)
        if len(raw) > limit:
            raise KnowledgeError('knowledge_input_limit', 413)
        return schema.model_validate(KnowledgeJSON(raw).parse())
    except KnowledgeError:
        raise
    except (ValueError, TypeError, AttributeError, RecursionError, ObservationError):
        raise KnowledgeError('knowledge_invalid', 422) from None


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class ExactRef(StrictRecord):
    scope: Literal['project', 'reusable_synthetic']
    knowledge_id: Key
    version: Version
    digest: Digest


class Source(StrictRecord):
    kind: Literal['synthetic_authored', 'observation']
    reference: SyntheticReference | None
    observation_id: ID | None
    source_entry_index: Annotated[int, Field(strict=True, ge=0, le=999999)] | None
    payload_digest: Digest | None

    @model_validator(mode='after')
    def coherent(self):
        if self.kind == 'synthetic_authored':
            if self.reference is None or any(x is not None for x in (self.observation_id, self.source_entry_index, self.payload_digest)):
                raise ValueError()
        elif self.reference is not None or any(x is None for x in (self.observation_id, self.source_entry_index, self.payload_digest)):
            raise ValueError()
        return self


class Applicability(StrictRecord):
    shape: Literal['general_mechanism', 'single_resource_path_get_json_object']
    actors: Annotated[list[Literal['anonymous', 'bearer']], Field(min_length=1, max_length=2)]
    requires_facts: bool


class Content(StrictRecord):
    contract: Literal['ra-knowledge/1']
    scope: Literal['project', 'reusable_synthetic']
    knowledge_id: Key
    version: Version
    category: Literal['mechanism', 'rule', 'project_evidence', 'counterexample', 'external_triage']
    data_class: Literal['synthetic_authored']
    purpose: Literal['offline_context_explanation']
    claim: Claim
    tags: Annotated[list[Tag], Field(min_length=1, max_length=8)]
    applicability: Applicability
    source_refs: Annotated[list[Source], Field(min_length=1, max_length=16)]
    example_refs: Annotated[list[SyntheticReference], Field(max_length=16)]
    counterexample_refs: Annotated[list[SyntheticReference], Field(max_length=16)]
    supersedes: ExactRef | None

    @model_validator(mode='after')
    def eligible(self):
        if self.category in {'project_evidence', 'external_triage', 'counterexample'}:
            raise ValueError()  # Classification exists; these domains cannot enter the rule store.
        for values in (self.tags, self.applicability.actors, self.source_refs, self.example_refs, self.counterexample_refs):
            keys = [canonical(v.model_dump() if isinstance(v, StrictRecord) else v) for v in values]
            if len(set(keys)) != len(keys):
                raise ValueError()
        if self.category in {'rule', 'counterexample'} and (not self.example_refs or not self.counterexample_refs or self.applicability.shape != 'single_resource_path_get_json_object' or not self.applicability.requires_facts):
            raise ValueError()
        if self.category == 'mechanism' and (self.applicability.shape != 'general_mechanism' or self.applicability.requires_facts):
            raise ValueError()
        if self.scope == 'reusable_synthetic' and any(s.kind != 'synthetic_authored' for s in self.source_refs):
            raise ValueError()
        if (self.version == 1) != (self.supersedes is None):
            raise ValueError()
        if self.supersedes and (self.supersedes.scope != self.scope or self.supersedes.knowledge_id != self.knowledge_id or self.supersedes.version != self.version - 1):
            raise ValueError()
        if len(canonical(self.model_dump())) > MAX_CONTENT:
            raise ValueError()
        return self


class RecordInput(StrictRecord):
    context_version: Version
    target_id: ID
    content: Content
    review: SyntheticReference


class DecisionInput(StrictRecord):
    reference: ExactRef
    expected_sequence: Annotated[int, Field(strict=True, ge=0, le=16)]
    action: Literal['review', 'reuse', 'withdraw', 'disable', 'publish']
    review: SyntheticReference
    valid_from: str | None
    valid_until: str | None

    @model_validator(mode='after')
    def window(self):
        if self.action in {'review', 'reuse', 'publish'}:
            if self.valid_from is None or self.valid_until is None or timestamp(self.valid_from) >= timestamp(self.valid_until):
                raise ValueError()
        elif self.valid_from is not None or self.valid_until is not None:
            raise ValueError()
        return self


class EventBody(StrictRecord):
    digest: Digest
    actor: Literal['local_operator', 'synthetic_test_reviewer']
    evidence: Literal['operator_recorded', 'synthetic_test_only']
    review: SyntheticReference
    context_version: Version
    valid_from: str | None
    valid_until: str | None
    review_event_id: ID | None
    reuse_event_id: ID | None
    validation_ref: Literal['NOT_RUN', 'synthetic_test_only']


class QueryInput(StrictRecord):
    context_version: Version
    subject_number: Annotated[int, Field(strict=True, ge=1, le=1024)]
    subject_version: Annotated[int, Field(strict=True, ge=1, le=1024)]
    purpose: Literal['offline_context_explanation']
    keywords: Annotated[list[Annotated[str, Field(pattern=r'^[a-z]{1,32}$', max_length=32)]], Field(max_length=8)]
    tags: Annotated[list[Tag], Field(max_length=8)]
    top_k: Annotated[int, Field(strict=True, ge=1, le=8)]
    selected: Annotated[list[ExactRef], Field(max_length=32)]

    @model_validator(mode='after')
    def coherent(self):
        if not self.keywords and not self.tags:
            raise ValueError()
        if len(set(self.keywords)) != len(self.keywords) or len(set(self.tags)) != len(self.tags):
            raise ValueError()
        return self  # Exact selected duplicates are deduplicated, never version-upgraded.


class AuditInput(StrictRecord):
    review: SyntheticReference


class Match(StrictRecord):
    reference: ExactRef
    content: Content
    score: Annotated[int, Field(strict=True, ge=1, le=24)]
    review_event_id: ID
    reuse_event_id: ID | None
    publication_event_id: ID
    publication_evidence: Literal['synthetic_test_only']
    review_actor: Literal['local_operator', 'synthetic_test_reviewer']
    publication_actor: Literal['local_operator', 'synthetic_test_reviewer']
    applicability: Literal['general_explanation', 'context_facts_present']


class QueryRead(StrictRecord):
    format: Literal['ra-knowledge-retrieval/1']
    context_id: ID
    context_version: Version
    subject_number: ID
    subject_version: Version
    evaluated_at: str
    audit_id: ID
    eligibility_until: str | None
    status: Literal['matched_synthetic', 'no_match', 'needs_input', 'unsupported', 'source_unavailable']
    missing_inputs: Annotated[list[Annotated[str, Field(max_length=64)]], Field(max_length=40)]
    matches: Annotated[list[Match], Field(max_length=8)]
    execution_authorized: Literal[False]
    ordinary_publication_allowed: Literal[False]
