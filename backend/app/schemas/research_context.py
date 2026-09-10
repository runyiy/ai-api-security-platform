"""Synthetic-only intake metadata; references never assert permission or approval."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[int, Field(strict=True, ge=1, le=2147483647)]
SmallID = Annotated[int, Field(strict=True, ge=1, le=1000000)]
Version = Annotated[int, Field(strict=True, ge=1, le=10000)]


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SyntheticReference(StrictRecord):
    kind: Literal["synthetic_fixture"]
    fixture_id: SmallID
    version: Version


class ResearchRule(StrictRecord):
    rule: Literal["get_only", "no_redirects", "explicit_access_facts"]
    source: SyntheticReference


class ResearchTargetInput(StrictRecord):
    target_id: ID
    association_review: SyntheticReference
    authorization_revision_id: ID | None
    permission_source: SyntheticReference | None

    @model_validator(mode="after")
    def reference_requires_revision(self):
        if self.permission_source is not None and self.authorization_revision_id is None:
            raise ValueError("intake_invalid_request")
        return self


class ResearchBudget(StrictRecord):
    """Storage bounds for proposed limits, NOT runtime budget approval."""
    target_requests: Annotated[int, Field(strict=True, ge=0, le=100)] | None
    duration_seconds: Annotated[int, Field(strict=True, ge=0, le=1800)] | None
    concurrency: Annotated[int, Field(strict=True, ge=0, le=1)] | None
    rate_millirequests_per_second: Annotated[int, Field(strict=True, ge=0, le=1000)] | None
    model_tokens: Annotated[int, Field(strict=True, ge=0, le=1000000000)] | None
    model_cost_microusd: Annotated[int, Field(strict=True, ge=0, le=1000000000)] | None
    currency: Literal["USD"]
    accounting_version: Literal["proposal_v1"]
    approval_reference: SyntheticReference | None

    @model_validator(mode="after")
    def coherent_limits(self):
        activity = (self.duration_seconds, self.concurrency, self.rate_millirequests_per_second)
        if self.target_requests == 0 and any(v not in (None, 0) for v in activity):
            raise ValueError("intake_invalid_request")
        if self.target_requests is not None and self.target_requests > 0 and 0 in activity:
            raise ValueError("intake_invalid_request")
        if self.model_tokens == 0 and self.model_cost_microusd not in (None, 0):
            raise ValueError("intake_invalid_request")
        return self


class ResearchIntakeInput(StrictRecord):
    version: Literal["research-intake-v1"]
    purpose: Literal["synthetic_bola_research"]
    data_eligibility: Literal["synthetic", "unknown"]
    eligibility_reference: SyntheticReference | None
    rules: Annotated[list[ResearchRule], Field(min_length=0, max_length=3)]
    targets: Annotated[list[ResearchTargetInput], Field(min_length=1, max_length=16)]
    budget: ResearchBudget

    @model_validator(mode="after")
    def unique_inputs(self):
        if len({r.rule for r in self.rules}) != len(self.rules):
            raise ValueError("intake_invalid_request")
        if len({t.target_id for t in self.targets}) != len(self.targets):
            raise ValueError("intake_invalid_request")
        return self


class ResearchContextCreate(StrictRecord):
    project_number: SmallID
    intake: ResearchIntakeInput


class ResearchContextCorrection(StrictRecord):
    expected_version: Version
    correction_reference: SyntheticReference
    intake: ResearchIntakeInput


class ResearchContextClose(StrictRecord):
    expected_version: Version
    closure_reference: SyntheticReference


class PermissionReadiness(StrictRecord):
    target_id: ID
    authorization_revision_id: ID | None
    status: Literal["missing", "mismatched", "draft", "superseded", "revoked", "unavailable",
                    "not_yet_valid", "expired", "target_unavailable", "get_not_permitted",
                    "scope_missing", "scope_limit_exceeded", "changed", "referenced_current"]


class IntakeActivityLimits(StrictRecord):
    target_requests: Literal[0]
    provider_calls: Literal[0]
    provider_cost_microusd: Literal[0]


class ResearchContextRead(StrictRecord):
    version: Literal["research-intake-v1"]
    context_id: ID
    project_number: SmallID
    context_version: Version
    latest_version: Version
    is_current: bool
    state: Literal["draft", "closed"]
    recorded_at: str
    evaluated_at: str
    provenance: Literal["operator_recorded_unverified"]
    correction_reference: SyntheticReference | None
    closure_reference: SyntheticReference | None
    intake: ResearchIntakeInput
    permissions: Annotated[list[PermissionReadiness], Field(min_length=1, max_length=16)]
    missing_inputs: list[Literal["permission_missing", "data_ineligible", "budget_unapproved", "facts_missing"]]
    budget_missing_fields: list[str]
    budget_rate_exceeded: bool
    budget_approval: Literal["unverified"]
    execution_preparation_allowed: Literal[False]
    execution_authorized: Literal[False]
    activity_limits: IntakeActivityLimits
