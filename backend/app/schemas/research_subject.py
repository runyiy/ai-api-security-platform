"""Bounded synthetic operator proposals, never executable or verified facts."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from app.schemas.research_context import ID, Version, StrictRecord, SyntheticReference
from app.schemas.research_observation import BoundedJSON, canonical, timestamp, ObservationError

MAX_INPUT = 8192
MAX_RESPONSE = 32768


class SubjectError(Exception):
    def __init__(self, code="research_subject_unavailable", status=409):
        self.code, self.status = code, status
        super().__init__(code)


class SourceReference(StrictRecord):
    observation_id: ID
    source_entry_index: Annotated[int, Field(strict=True, ge=0, le=999999)]


class SubjectInput(StrictRecord):
    format: Literal["research-subject-v1"]
    context_version: Version
    target_id: ID
    identity_choice: Literal["missing", "anonymous", "bearer"]
    test_identity_id: ID | None
    credential_binding_id: ID | None
    session_state: Literal["unknown", "operator_reported_valid", "expired", "login_page", "mfa_required", "authentication_failed", "not_applicable"]
    session_reported_at: Annotated[str, Field(max_length=40)] | None
    session_reference: SyntheticReference | None
    credential_update: Literal["unknown", "needed", "operator_reported_updated", "not_applicable"]
    resource_id: ID | None
    owner_identity_id: ID | None
    endpoint_id: ID | None
    binding_id: ID | None
    relationship: Literal["owner", "shared", "non_owner", "unspecified"]
    expected_access: Literal["allowed", "denied", "unspecified"]
    fact_reference: SyntheticReference | None
    membership: Literal["unknown", "operator_proposed"]
    membership_reference: SyntheticReference | None
    assertion_ids: Annotated[list[ID], Field(max_length=16)]
    sources: Annotated[list[SourceReference], Field(max_length=8)]
    review: SyntheticReference

    @model_validator(mode="after")
    def coherent(self):
        if (self.identity_choice == "missing") != (self.test_identity_id is None):
            raise ValueError("invalid")
        if self.identity_choice != "bearer" and self.credential_binding_id is not None:
            raise ValueError("invalid")
        if self.identity_choice != "bearer" and self.credential_update != "not_applicable":
            raise ValueError("invalid")
        if self.session_state == "not_applicable" and self.identity_choice != "anonymous":
            raise ValueError("invalid")
        if self.session_reported_at is not None:
            timestamp(self.session_reported_at)
        if (self.session_reported_at is None) != (self.session_reference is None):
            raise ValueError("invalid")
        if self.session_state not in {"unknown", "not_applicable"} and self.session_reference is None:
            raise ValueError("invalid")
        if self.binding_id is not None and self.endpoint_id is None:
            raise ValueError("invalid")
        if self.assertion_ids and (self.resource_id is None or self.test_identity_id is None):
            raise ValueError("invalid")
        if (self.relationship != "unspecified" or self.expected_access != "unspecified") and self.fact_reference is None:
            raise ValueError("invalid")
        if (self.membership == "operator_proposed") != (self.membership_reference is not None):
            raise ValueError("invalid")
        if len(set(self.assertion_ids)) != len(self.assertion_ids):
            raise ValueError("invalid")
        if len({(s.observation_id, s.source_entry_index) for s in self.sources}) != len(self.sources):
            raise ValueError("invalid")
        return self


class SubjectCorrection(StrictRecord):
    expected_version: Version
    correction_reference: SyntheticReference
    proposal: SubjectInput


def validate(schema, value):
    try:
        data = value.model_dump(warnings=False) if isinstance(value, schema) else value
        raw = canonical(data)
        if len(raw) > MAX_INPUT:
            raise SubjectError("research_subject_input_limit", 413)
        # Share W2's independent depth/node/encoding/duplicate-key limits.
        return schema.model_validate(BoundedJSON(raw).parse())
    except SubjectError:
        raise
    except (ValueError, TypeError, AttributeError, RecursionError, ObservationError):
        raise SubjectError("research_subject_invalid", 422) from None


class FactResolution(StrictRecord):
    state: Literal["resolved", "insufficient", "conflict"]
    relationship: Literal["owner", "shared", "non_owner", "unspecified"]
    expected_access: Literal["allowed", "denied", "unspecified"]
    supporting_assertion_ids: Annotated[list[ID], Field(max_length=256)]


class SubjectRead(StrictRecord):
    format: Literal["research-subject-v1"]
    context_id: ID
    proposal_number: ID
    version_number: Version
    latest_version: Version
    recorded_at: str
    evaluated_at: str
    provenance: Literal["operator_proposed_unverified"]
    status: Literal["NEEDS_INPUT"]
    availability: Literal["available", "unavailable"]
    proposal: SubjectInput | None
    facts: FactResolution | None
    missing_inputs: Annotated[list[Annotated[str, Field(max_length=64)]], Field(min_length=1, max_length=32)]
    correction_reference: SyntheticReference | None
    execution_preparation_allowed: Literal[False]
    execution_authorized: Literal[False]
