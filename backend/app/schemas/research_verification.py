"""Bounded local W2 commands; no imported run, health claim, headers or script."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from app.schemas.research_context import StrictRecord, SyntheticReference, ID
from app.schemas.research_intent import Number, Digest, Reference

Role = Literal['baseline', 'probe', 'health_baseline', 'health_probe']
Key = Annotated[str, Field(strict=True, pattern=r'^[a-z][a-z0-9_]{0,31}$')]
Marker = Annotated[str, Field(strict=True, pattern=r'^[A-Za-z0-9_-]{1,64}$')]


class Expectation(StrictRecord):
    role: Role
    object_key: Key
    object_value: Annotated[str, Field(strict=True, pattern=r'^[0-9]{1,16}$')]
    identity_key: Key
    identity_value: Marker | None

    @model_validator(mode='after')
    def distinct(self):
        if len({self.object_key, self.identity_key, 'authenticated', 'error'}) != 4:
            raise ValueError('ambiguous selectors')
        return self


class ConfirmInput(StrictRecord):
    manifest: Reference
    expected_version: Annotated[int, Field(strict=True, ge=0, le=1023)]
    decision: Literal['confirm']
    expectations: Annotated[list[Expectation], Field(min_length=2, max_length=4)]
    evidence: SyntheticReference


class EvidenceRef(StrictRecord):
    id: ID
    digest: Digest


class HealthChoice(StrictRecord):
    role: Literal['baseline', 'probe']
    evidence: EvidenceRef


class HealthSelectionInput(StrictRecord):
    manifest: Reference
    health: Annotated[list[HealthChoice], Field(min_length=1, max_length=2)]
    evidence: SyntheticReference


class PlanInput(StrictRecord):
    intent: Reference
    plan_id: ID


class ApprovalInput(PlanInput):
    expected_sequence: Annotated[int, Field(strict=True, ge=0, le=15)]
    decision: Literal['approved', 'revoked']
    evidence: SyntheticReference


class PairInput(StrictRecord):
    intent: Reference
    baseline: EvidenceRef
    probe: EvidenceRef | None


class Receipt(StrictRecord):
    protocol: Literal['ra-verification-receipt/1']
    kind: Literal['contract', 'health_selection', 'approval', 'execution', 'pair']
    reference: EvidenceRef
    body: dict
    eligibility_until: str
    audit_id: ID
    execution_authorized: Literal[False]
    finding_confirmed: Literal[False]


class HistoricalEvidence(StrictRecord):
    protocol: Literal['ra-historical-execution/1','ra-historical-pair/1']
    reference: EvidenceRef
    body: dict
    qualification: Literal['historical_not_revalidated']
    reusable: Literal[False]
    execution_authorized: Literal[False]
    finding_confirmed: Literal[False]
    audit_id: ID
    observed_at: str
