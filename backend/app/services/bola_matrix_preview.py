"""Transient, read-only composition of exact metadata, M12 resolution and M14 planning."""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.endpoint import Endpoint
from app.db.models.resource import Resource
from app.db.models.test_identity import TestIdentity
from app.generators.bola_matrix import (
    MAX_BOLA_MATRIX_FACTS,
    BOLAMatrixAccessFact,
    BOLAMatrixCandidate,
    BOLAMatrixPlanningError,
    plan_bola_matrix,
)
from app.services.resource_access_resolution import resolve_resource_access


class BOLAMatrixPreviewError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class BOLAMatrixPreview:
    endpoint_id: int
    resource_id: int
    evaluation_time: datetime
    facts: tuple[BOLAMatrixAccessFact, ...]
    candidates: tuple[BOLAMatrixCandidate, ...]


def _validate_id(value: object) -> None:
    if type(value) is not int or value <= 0:
        raise BOLAMatrixPlanningError("bola_matrix_invalid_access_fact")


def preview_bola_matrix(
    db: Session,
    *,
    endpoint_id: int,
    resource_id: int,
    test_identity_ids: Iterable[int],
    evaluation_time: datetime,
) -> BOLAMatrixPreview:
    """Resolve current selected metadata at an explicit assertion evaluation instant.

    The caller owns the clean Session and read transaction. This preview is not
    an execution permission or a persisted historical metadata snapshot.
    """
    if db.new or db.dirty or db.deleted:
        raise BOLAMatrixPreviewError("bola_matrix_preview_session_not_clean")
    _validate_id(endpoint_id)
    _validate_id(resource_id)
    if not isinstance(evaluation_time, datetime):
        raise BOLAMatrixPreviewError("bola_matrix_evaluation_time_invalid")
    if evaluation_time.utcoffset() is None:
        raise BOLAMatrixPreviewError("evaluation_time_timezone_required")

    if isinstance(test_identity_ids, (str, bytes)):
        raise BOLAMatrixPlanningError("bola_matrix_invalid_access_fact")
    try:
        source = iter(test_identity_ids)
    except TypeError as exc:
        raise BOLAMatrixPlanningError("bola_matrix_invalid_access_fact") from exc
    identity_ids: list[int] = []
    seen: set[int] = set()
    for index, identity_id in enumerate(source):
        if index == MAX_BOLA_MATRIX_FACTS:
            raise BOLAMatrixPlanningError("bola_matrix_fact_limit_exceeded")
        _validate_id(identity_id)
        if identity_id in seen:
            raise BOLAMatrixPlanningError("bola_matrix_duplicate_access_fact")
        seen.add(identity_id)
        identity_ids.append(identity_id)

    with db.no_autoflush:
        endpoint = db.execute(select(Endpoint.target_id).where(Endpoint.id == endpoint_id)).one_or_none()
        if endpoint is None:
            raise BOLAMatrixPreviewError("endpoint_not_found")
        resource = db.execute(select(Resource.target_id).where(Resource.id == resource_id)).one_or_none()
        if resource is None:
            raise BOLAMatrixPreviewError("resource_not_found")
        if endpoint.target_id != resource.target_id:
            raise BOLAMatrixPreviewError("bola_matrix_endpoint_resource_target_mismatch")

        identities = []
        for identity_id in identity_ids:
            identity = db.execute(select(
                TestIdentity.id, TestIdentity.target_id, TestIdentity.auth_type, TestIdentity.is_active,
            ).where(TestIdentity.id == identity_id)).one_or_none()
            if identity is None:
                raise BOLAMatrixPreviewError("test_identity_not_found")
            if identity.target_id != resource.target_id:
                raise BOLAMatrixPreviewError("resource_identity_target_mismatch")
            if not identity.is_active:
                raise BOLAMatrixPreviewError("bola_matrix_identity_inactive")
            identities.append(identity)

        resolved_facts: list[BOLAMatrixAccessFact] = []
        for identity in identities:
            resolution = resolve_resource_access(db, resource_id, identity.id, evaluation_time)
            if (
                resolution.resource_id != resource_id
                or resolution.test_identity_id != identity.id
                or not isinstance(resolution.evaluation_time, datetime)
                or resolution.evaluation_time.utcoffset() is None
                or resolution.evaluation_time.astimezone(timezone.utc) != evaluation_time.astimezone(timezone.utc)
            ):
                raise BOLAMatrixPreviewError("bola_matrix_resolution_mismatch")
            resolved_facts.append(BOLAMatrixAccessFact(
                endpoint_id=endpoint_id,
                resource_id=resource_id,
                test_identity_id=identity.id,
                identity_auth_type=identity.auth_type,
                resolution_state=resolution.state,
                relationship=resolution.relationship,
                expected_access=resolution.expected_access,
                supporting_assertion_ids=resolution.supporting_assertion_ids,
            ))

        facts = tuple(resolved_facts)
        candidates = plan_bola_matrix(facts=facts)
        return BOLAMatrixPreview(endpoint_id, resource_id, evaluation_time, facts, candidates)
