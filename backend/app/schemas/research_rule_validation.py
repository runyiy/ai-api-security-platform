"""Closed synthetic W3 requests; callers cannot submit checks, results or labels."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from app.schemas.research_context import StrictRecord, SyntheticReference, ID
from app.schemas.research_knowledge import ExactRef, ValidationRef
from app.schemas.research_observation import timestamp

MAX_VALIDATIONS = 128
MAX_FEEDBACK = 128
MAX_EVIDENCE = 32768
MAX_CASES = 24


class ValidateInput(StrictRecord):
    reference: ExactRef
    valid_until: str

    @model_validator(mode='after')
    def time(self):
        timestamp(self.valid_until)
        return self


class ValidationReadInput(StrictRecord):
    reference: ExactRef
    validation_ref: ValidationRef


class FeedbackInput(ValidationReadInput):
    proposal: Literal['promotion', 'correction', 'disable']
    reason: Literal['validation_passed', 'incorrect_result', 'no_match', 'contamination', 'qualification_missing']
    correction: ExactRef | None
    review: SyntheticReference

    @model_validator(mode='after')
    def coherent(self):
        if (self.proposal == 'correction') != (self.correction is not None):
            raise ValueError()
        if (self.proposal == 'promotion') != (self.reason == 'validation_passed'):
            raise ValueError()
        if self.reason == 'contamination' and self.proposal != 'disable':
            raise ValueError()
        return self


class FeedbackReadInput(StrictRecord):
    reference: ExactRef
    feedback_id: ID


class FeedbackReviewInput(FeedbackReadInput):
    expected_sequence: Annotated[int, Field(strict=True, ge=0, le=0)]
    decision: Literal['accept', 'reject']
    review: SyntheticReference


class FeedbackReviewBody(StrictRecord):
    actor: Literal['local_operator', 'system']
    reason: Literal['human_review', 'dependency_disabled']
    review: SyntheticReference | None = None
    validation_ref: ValidationRef | None = None

    @model_validator(mode='after')
    def coherent(self):
        if self.actor == 'local_operator':
            if self.reason != 'human_review' or self.review is None or self.validation_ref is None:
                raise ValueError()
        elif self.reason != 'dependency_disabled' or self.review is not None or self.validation_ref is not None:
            raise ValueError()
        return self
