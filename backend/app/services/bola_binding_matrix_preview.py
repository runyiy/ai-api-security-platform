"""Bounded read-only composition of explicit proposed slot/Resource assignments."""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from sqlalchemy.orm import Session

from app.generators.bola_matrix import MAX_BOLA_MATRIX_FACTS, BOLAMatrixAccessFact, BOLAMatrixPlanningError
from app.services.bola_binding_selection import BOLAReviewedBindingSelection, select_bola_binding
from app.services.bola_matrix_preview import BOLAMatrixPreview, BOLAMatrixPreviewError, preview_bola_matrix


MAX_BOLA_MATRIX_ASSIGNMENTS: Final = 32


class BOLABindingMatrixPreviewError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _positive_id(value: object) -> bool:
    return type(value) is int and value > 0


@dataclass(frozen=True, slots=True)
class BOLAResourceSlotAssignment:
    binding_id: int
    resource_id: int

    def __post_init__(self) -> None:
        if not _positive_id(self.binding_id) or not _positive_id(self.resource_id):
            raise BOLABindingMatrixPreviewError("bola_binding_matrix_invalid_input")


@dataclass(frozen=True, slots=True)
class BOLAAssignedSlotPreview:
    binding: BOLAReviewedBindingSelection
    preview: BOLAMatrixPreview


@dataclass(frozen=True, slots=True)
class BOLAMultiBindingPreview:
    endpoint_id: int
    evaluation_time: datetime
    test_identity_ids: tuple[int, ...]
    slots: tuple[BOLAAssignedSlotPreview, ...]


def _check_preview(
    preview: BOLAMatrixPreview,
    endpoint_id: int,
    resource_id: int,
    identity_ids: tuple[int, ...],
    evaluation_time: datetime,
) -> None:
    if (
        not isinstance(preview, BOLAMatrixPreview)
        or preview.endpoint_id != endpoint_id
        or preview.resource_id != resource_id
        or not isinstance(preview.evaluation_time, datetime)
        or preview.evaluation_time.utcoffset() is None
        or preview.evaluation_time.astimezone(timezone.utc) != evaluation_time.astimezone(timezone.utc)
        or not isinstance(preview.facts, tuple)
        or not isinstance(preview.candidates, tuple)
        or not all(isinstance(fact, BOLAMatrixAccessFact)
                   and fact.endpoint_id == endpoint_id and fact.resource_id == resource_id
                   for fact in preview.facts)
        or tuple(fact.test_identity_id for fact in preview.facts) != identity_ids
    ):
        raise BOLABindingMatrixPreviewError("bola_binding_matrix_dependency_mismatch")


def preview_bola_binding_matrix(
    db: Session,
    *,
    endpoint_id: int,
    assignments: Iterable[BOLAResourceSlotAssignment],
    test_identity_ids: Iterable[int],
    evaluation_time: datetime,
) -> BOLAMultiBindingPreview:
    """Group independent Resource previews for proposed, currently reviewed slots.

    The caller owns the clean read transaction. Selection does not prove Resource
    membership in a slot, parent-child relationships, or execution permission.
    """
    if db.new or db.dirty or db.deleted:
        raise BOLABindingMatrixPreviewError("bola_binding_matrix_session_not_clean")
    if not _positive_id(endpoint_id):
        raise BOLABindingMatrixPreviewError("bola_binding_matrix_invalid_input")
    if not isinstance(evaluation_time, datetime):
        raise BOLAMatrixPreviewError("bola_matrix_evaluation_time_invalid")
    if evaluation_time.utcoffset() is None:
        raise BOLAMatrixPreviewError("evaluation_time_timezone_required")

    try:
        source = iter(assignments)
    except TypeError:
        raise BOLABindingMatrixPreviewError("bola_binding_matrix_invalid_input") from None
    proposed: list[BOLAResourceSlotAssignment] = []
    binding_ids: set[int] = set()
    for index, assignment in enumerate(source):
        if index == MAX_BOLA_MATRIX_ASSIGNMENTS:
            raise BOLABindingMatrixPreviewError("bola_binding_matrix_assignment_limit_exceeded")
        if (not isinstance(assignment, BOLAResourceSlotAssignment)
                or not _positive_id(assignment.binding_id) or not _positive_id(assignment.resource_id)):
            raise BOLABindingMatrixPreviewError("bola_binding_matrix_invalid_input")
        if assignment.binding_id in binding_ids:
            raise BOLABindingMatrixPreviewError("bola_binding_matrix_duplicate_binding")
        binding_ids.add(assignment.binding_id)
        proposed.append(assignment)
    if not proposed:
        raise BOLABindingMatrixPreviewError("bola_binding_matrix_invalid_input")

    if isinstance(test_identity_ids, (str, bytes)):
        raise BOLAMatrixPlanningError("bola_matrix_invalid_access_fact")
    try:
        identity_source = iter(test_identity_ids)
    except TypeError:
        raise BOLAMatrixPlanningError("bola_matrix_invalid_access_fact") from None
    identities: list[int] = []
    seen_identities: set[int] = set()
    for index, identity_id in enumerate(identity_source):
        if index == MAX_BOLA_MATRIX_FACTS:
            raise BOLAMatrixPlanningError("bola_matrix_fact_limit_exceeded")
        if not _positive_id(identity_id):
            raise BOLAMatrixPlanningError("bola_matrix_invalid_access_fact")
        if identity_id in seen_identities:
            raise BOLAMatrixPlanningError("bola_matrix_duplicate_access_fact")
        seen_identities.add(identity_id)
        identities.append(identity_id)
    if len(proposed) * len(identities) > MAX_BOLA_MATRIX_FACTS:
        raise BOLAMatrixPlanningError("bola_matrix_fact_limit_exceeded")
    identity_ids = tuple(identities)

    with db.no_autoflush:
        bindings: list[BOLAReviewedBindingSelection] = []
        selected_slots: set[tuple[str, str]] = set()
        for assignment in proposed:
            binding = select_bola_binding(db, endpoint_id=endpoint_id, binding_id=assignment.binding_id)
            if (not isinstance(binding, BOLAReviewedBindingSelection)
                    or binding.endpoint_id != endpoint_id or binding.binding_id != assignment.binding_id
                    or binding.review_state != "confirmed" or binding.location not in ("path", "query")
                    or not isinstance(binding.selector, str) or not binding.selector):
                raise BOLABindingMatrixPreviewError("bola_binding_matrix_dependency_mismatch")
            slot = (binding.location, binding.selector)
            if slot in selected_slots:
                raise BOLABindingMatrixPreviewError("bola_binding_matrix_duplicate_slot")
            selected_slots.add(slot)
            bindings.append(binding)

        resource_previews: dict[int, BOLAMatrixPreview] = {}
        for assignment in proposed:
            if assignment.resource_id not in resource_previews:
                preview = preview_bola_matrix(
                    db, endpoint_id=endpoint_id, resource_id=assignment.resource_id,
                    test_identity_ids=identity_ids, evaluation_time=evaluation_time,
                )
                _check_preview(preview, endpoint_id, assignment.resource_id, identity_ids, evaluation_time)
                resource_previews[assignment.resource_id] = preview

        slots = tuple(BOLAAssignedSlotPreview(binding, resource_previews[assignment.resource_id])
                      for binding, assignment in zip(bindings, proposed, strict=True))
        return BOLAMultiBindingPreview(endpoint_id, evaluation_time, identity_ids, slots)
