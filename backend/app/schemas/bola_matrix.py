"""Strict transport types for the existing, non-executable matrix preview."""
from datetime import datetime, timezone
import re
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.generators.bola_matrix import (
    MAX_BOLA_MATRIX_FACTS, CandidateKind, ExpectedAccess, Relationship,
    ResolutionState, SubjectKind,
)
from app.services.bola_binding_matrix_preview import MAX_BOLA_MATRIX_ASSIGNMENTS, BOLAMultiBindingPreview
from app.services.resource_access_resolution import MAX_ASSERTIONS_SCANNED


Identifier = Annotated[int, Field(strict=True, ge=1, le=2147483647)]
RFC3339 = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class BOLASlotAssignmentRequest(StrictModel):
    binding_id: Identifier
    resource_id: Identifier


class BOLAMatrixPreviewRequest(StrictModel):
    endpoint_id: Identifier
    assignments: list[BOLASlotAssignmentRequest] = Field(min_length=1, max_length=MAX_BOLA_MATRIX_ASSIGNMENTS)
    test_identity_ids: list[Identifier] = Field(max_length=MAX_BOLA_MATRIX_FACTS)
    evaluation_time: AwareDatetime = Field(
        description="Required RFC3339 string with offset or Z; at most six fractional digits; no implicit clock.",
        json_schema_extra={"pattern": RFC3339.pattern},
    )

    @field_validator("evaluation_time", mode="before")
    @classmethod
    def explicit_time(cls, value):
        if type(value) is not str or RFC3339.fullmatch(value) is None:
            raise ValueError("Explicit RFC3339 time required.")
        parsed = datetime.fromisoformat(value.upper())
        parsed.astimezone(timezone.utc)  # Reject UTC overflow, without changing the offset.
        return parsed

    @model_validator(mode="after")
    def unique_bounded_cells(self):
        if (
            len({a.binding_id for a in self.assignments}) != len(self.assignments)
            or len(set(self.test_identity_ids)) != len(self.test_identity_ids)
            or len(self.assignments) * len(self.test_identity_ids) > MAX_BOLA_MATRIX_FACTS
        ):
            raise ValueError("Unique IDs and at most 512 requested cells required.")
        return self


class BOLAReviewedBindingResponse(StrictModel):
    endpoint_id: Identifier
    binding_id: Identifier
    location: Literal["path", "query"]
    selector: str = Field(min_length=1, max_length=128)
    review_state: Literal["confirmed"]


class BOLAAccessFactResponse(StrictModel):
    endpoint_id: Identifier
    resource_id: Identifier
    test_identity_id: Identifier
    identity_auth_type: str = Field(max_length=30)
    resolution_state: ResolutionState
    relationship: Relationship
    expected_access: ExpectedAccess
    supporting_assertion_ids: tuple[Identifier, ...] = Field(max_length=MAX_ASSERTIONS_SCANNED)


class BOLACandidateResponse(StrictModel):
    endpoint_id: Identifier
    resource_id: Identifier
    test_identity_id: Identifier
    subject_kind: SubjectKind
    candidate_kind: CandidateKind
    relationship: Relationship
    expected_access: Literal["allowed", "denied"]
    supporting_assertion_ids: tuple[Identifier, ...] = Field(max_length=MAX_ASSERTIONS_SCANNED)


class BOLAResourcePreviewResponse(StrictModel):
    endpoint_id: Identifier
    resource_id: Identifier
    evaluation_time: AwareDatetime
    facts: tuple[BOLAAccessFactResponse, ...] = Field(max_length=MAX_BOLA_MATRIX_FACTS)
    candidates: tuple[BOLACandidateResponse, ...] = Field(max_length=MAX_BOLA_MATRIX_FACTS)


class BOLAAssignedSlotResponse(StrictModel):
    binding: BOLAReviewedBindingResponse
    preview: BOLAResourcePreviewResponse


def _bounded_tuple(value, limit):
    # Check immutable collection bounds before allocating any converted output.
    if type(value) is not tuple or len(value) > limit:
        raise ValueError("Invalid preview collection.")
    return value


class BOLAMatrixPreviewResponse(StrictModel):
    endpoint_id: Identifier
    evaluation_time: AwareDatetime
    test_identity_ids: tuple[Identifier, ...] = Field(max_length=MAX_BOLA_MATRIX_FACTS)
    slots: tuple[BOLAAssignedSlotResponse, ...] = Field(min_length=1, max_length=MAX_BOLA_MATRIX_ASSIGNMENTS)

    @classmethod
    def from_preview(cls, value: BOLAMultiBindingPreview, request: BOLAMatrixPreviewRequest):
        if type(value) is not BOLAMultiBindingPreview:
            raise ValueError("Invalid preview type.")
        slots = _bounded_tuple(value.slots, MAX_BOLA_MATRIX_ASSIGNMENTS)
        identities = _bounded_tuple(value.test_identity_ids, MAX_BOLA_MATRIX_FACTS)
        if len(slots) * len(identities) > MAX_BOLA_MATRIX_FACTS:
            raise ValueError("Invalid preview work bound.")
        converted = []
        for slot in slots:
            binding, preview = slot.binding, slot.preview
            facts = _bounded_tuple(preview.facts, len(identities))
            candidates = _bounded_tuple(preview.candidates, len(identities))
            converted.append(BOLAAssignedSlotResponse(
                binding=BOLAReviewedBindingResponse(
                    endpoint_id=binding.endpoint_id, binding_id=binding.binding_id,
                    location=binding.location, selector=binding.selector, review_state=binding.review_state,
                ),
                preview=BOLAResourcePreviewResponse(
                    endpoint_id=preview.endpoint_id, resource_id=preview.resource_id,
                    evaluation_time=preview.evaluation_time,
                    facts=tuple(BOLAAccessFactResponse(
                        endpoint_id=f.endpoint_id, resource_id=f.resource_id, test_identity_id=f.test_identity_id,
                        identity_auth_type=f.identity_auth_type, resolution_state=f.resolution_state,
                        relationship=f.relationship, expected_access=f.expected_access,
                        supporting_assertion_ids=f.supporting_assertion_ids,
                    ) for f in facts),
                    candidates=tuple(BOLACandidateResponse(
                        endpoint_id=c.endpoint_id, resource_id=c.resource_id, test_identity_id=c.test_identity_id,
                        subject_kind=c.subject_kind, candidate_kind=c.candidate_kind,
                        relationship=c.relationship, expected_access=c.expected_access,
                        supporting_assertion_ids=c.supporting_assertion_ids,
                    ) for c in candidates),
                ),
            ))
        result = cls(endpoint_id=value.endpoint_id, evaluation_time=value.evaluation_time,
                     test_identity_ids=identities, slots=tuple(converted))
        # Verify transport identity/correlation only; access decisions remain in M14.
        instant = request.evaluation_time.astimezone(timezone.utc)
        if (
            result.endpoint_id != request.endpoint_id
            or result.evaluation_time.astimezone(timezone.utc) != instant
            or result.test_identity_ids != tuple(request.test_identity_ids)
            or len(result.slots) != len(request.assignments)
        ):
            raise ValueError("Invalid preview correlation.")
        for slot, assignment in zip(result.slots, request.assignments, strict=True):
            preview = slot.preview
            if (
                slot.binding.endpoint_id != result.endpoint_id
                or slot.binding.binding_id != assignment.binding_id
                or preview.endpoint_id != result.endpoint_id
                or preview.resource_id != assignment.resource_id
                or preview.evaluation_time.astimezone(timezone.utc) != instant
                or tuple(f.test_identity_id for f in preview.facts) != result.test_identity_ids
                or any(f.endpoint_id != result.endpoint_id or f.resource_id != assignment.resource_id
                       for f in (*preview.facts, *preview.candidates))
                or any(c.test_identity_id not in result.test_identity_ids for c in preview.candidates)
            ):
                raise ValueError("Invalid slot correlation.")
        return result


class BOLAMatrixPreviewErrorResponse(StrictModel):
    detail: str = Field(max_length=80)
