"""Bounded local task commands. None of these records authorizes execution."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.research_context import ID, Version, StrictRecord, SyntheticReference
from app.schemas.research_intent import Digest, Number, Reference

MAX_TASKS = 128
MAX_VERSIONS = 16
MAX_EVENTS = 64
MAX_REQUESTS = 100
MAX_INPUT = 32768
MAX_CORE = 65536
MAX_OUTPUT = 131072

Sequence = Annotated[int, Field(strict=True, ge=0, le=MAX_EVENTS)]
Role = Literal['health_baseline', 'health_probe', 'baseline', 'probe']


class TaskError(Exception):
    def __init__(self, code='task_unavailable', status=409):
        self.code, self.status = code, status
        super().__init__(code)


class TaskRef(StrictRecord):
    number: Number
    version: Annotated[int, Field(strict=True, ge=1, le=MAX_VERSIONS)]
    digest: Digest


class Limits(StrictRecord):
    target_requests: Annotated[int, Field(strict=True, ge=1, le=100)]
    duration_seconds: Annotated[int, Field(strict=True, ge=1, le=1800)]
    rate_millirequests_per_second: Annotated[int, Field(strict=True, ge=1, le=1000)]
    concurrency: Literal[1]
    ai_calls: Literal[0]
    ai_tokens: Literal[0]
    ai_cost_microusd: Literal[0]

    @model_validator(mode='before')
    @classmethod
    def exact_integers(cls, value):
        if isinstance(value, dict) and any(type(v) is not int for v in value.values()):
            raise ValueError('task_invalid')
        return value


class Selection(StrictRecord):
    intent: Reference
    plan_id: ID
    plan_digest: Digest
    role: Role


class CreateVersion(StrictRecord):
    previous: TaskRef | None
    expected_sequence: Sequence
    context_version: Version
    target_id: ID
    authorization_revision_id: ID
    limits: Limits
    members: Annotated[list[Selection], Field(min_length=1, max_length=MAX_REQUESTS)]
    evidence: SyntheticReference

    @model_validator(mode='after')
    def unique_plans(self):
        if len({m.plan_id for m in self.members}) != len(self.members):
            raise ValueError('task_invalid')
        return self


class Decision(StrictRecord):
    task: TaskRef
    expected_sequence: Sequence
    decision: Literal['approved', 'revoked']
    evidence: SyntheticReference


class Transition(StrictRecord):
    task: TaskRef
    expected_sequence: Sequence
    action: Literal['pause', 'cancel', 'start', 'resume', 'dispatch']
    evidence: SyntheticReference


class Member(Selection):
    ordinal: Annotated[int, Field(strict=True, ge=1, le=MAX_REQUESTS)]
    intent_id: ID
    manifest: Reference
    manifest_id: ID
    digest_version: Literal['v1']
    action_id: ID
    depends_on: Annotated[list[ID], Field(max_length=3)]


class Core(StrictRecord):
    protocol: Literal['ra-local-task/1']
    number: Number
    version: Annotated[int, Field(strict=True, ge=1, le=MAX_VERSIONS)]
    context_id: ID
    context_version: Version
    target_id: ID
    authorization_revision_id: ID
    permission_digest: Digest
    previous: TaskRef | None
    limits: Limits
    members: Annotated[list[Member], Field(min_length=1, max_length=MAX_REQUESTS)]
    evidence: SyntheticReference
    recorded_at: str
    eligibility_until: str


class Allocation(StrictRecord):
    plan_id: ID
    first_version: Annotated[int, Field(strict=True, ge=1, le=MAX_VERSIONS)]
    requests: Literal[1]
    state: Literal['held_unreconciled']


class PlanObservation(StrictRecord):
    plan_id: ID
    # Absence of M8 rows is explicitly NOT evidence of unused budget.
    phase: Literal['no_record', 'pre_network', 'network_started', 'in_doubt', 'unavailable']
    claim_present: bool | None
    canonical_run_id: ID | None
    verification_attempt_id: ID | None
    cancelled: bool | None
    exact_approval: Literal['approved', 'missing', 'revoked', 'not_required', 'unavailable']


class Event(StrictRecord):
    sequence: Annotated[int, Field(strict=True, ge=1, le=MAX_EVENTS)]
    version: Annotated[int, Field(strict=True, ge=1, le=MAX_VERSIONS)]
    kind: Literal['version_created', 'approved', 'revoked', 'paused', 'cancelled']
    actor: Literal['local_operator']
    evidence: SyntheticReference
    recorded_at: str


class View(StrictRecord):
    protocol: Literal['ra-local-task-view/1']
    reference: TaskRef
    current_version: Annotated[int, Field(strict=True, ge=1, le=MAX_VERSIONS)]
    sequence: Annotated[int, Field(strict=True, ge=1, le=MAX_EVENTS)]
    state: Literal['awaiting_budget_approval', 'budget_approved', 'budget_revoked', 'paused', 'cancelled', 'superseded']
    core: Core
    events: Annotated[list[Event], Field(min_length=1, max_length=MAX_EVENTS)]
    allocations: Annotated[list[Allocation], Field(max_length=MAX_REQUESTS)]
    held_requests: Annotated[int, Field(strict=True, ge=0, le=MAX_REQUESTS)]
    selected_requests: Annotated[int, Field(strict=True, ge=1, le=MAX_REQUESTS)]
    observations: Annotated[list[PlanObservation], Field(max_length=MAX_REQUESTS)]
    dependencies_current: bool
    budget_current: bool
    blockers: Annotated[list[Literal['execution_disabled', 'reconciliation_required',
        'budget_approval_missing', 'dependencies_unavailable', 'exact_plan_approval_missing',
        'plan_cancelled', 'task_inactive']], Field(max_length=7)]
    observed_at: str
    execution_authorized: Literal[False]
    runtime_enforcement: Literal['w2_not_implemented']
