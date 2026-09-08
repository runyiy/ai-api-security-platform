"""Pure planning metadata from explicit, already-resolved access facts."""
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal


MAX_BOLA_MATRIX_FACTS: Final = 512

ResolutionState = Literal["resolved", "insufficient", "conflict"]
Relationship = Literal["owner", "shared", "non_owner", "unspecified"]
ExpectedAccess = Literal["allowed", "denied", "unspecified"]
SubjectKind = Literal["authenticated", "anonymous"]
CandidateKind = Literal["owner_access", "cross_subject_access", "shared_access", "anonymous_access"]
PlanningErrorCode = Literal[
    "bola_matrix_fact_limit_exceeded",
    "bola_matrix_duplicate_access_fact",
    "bola_matrix_invalid_access_fact",
]


class BOLAMatrixPlanningError(ValueError):
    def __init__(self, code: PlanningErrorCode):
        super().__init__(code)
        self.code = code


def _positive_id(value: object) -> bool:
    return type(value) is int and value > 0


@dataclass(frozen=True, slots=True)
class BOLAMatrixAccessFact:
    endpoint_id: int
    resource_id: int
    test_identity_id: int
    identity_auth_type: str
    resolution_state: ResolutionState
    relationship: Relationship
    expected_access: ExpectedAccess
    supporting_assertion_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if (
            not all(_positive_id(value) for value in (
                self.endpoint_id, self.resource_id, self.test_identity_id,
            ))
            or not isinstance(self.identity_auth_type, str)
            or self.resolution_state not in ("resolved", "insufficient", "conflict")
            or self.relationship not in ("owner", "shared", "non_owner", "unspecified")
            or self.expected_access not in ("allowed", "denied", "unspecified")
            or not isinstance(self.supporting_assertion_ids, tuple)
            or not all(_positive_id(value) for value in self.supporting_assertion_ids)
        ):
            raise BOLAMatrixPlanningError("bola_matrix_invalid_access_fact")


@dataclass(frozen=True, slots=True)
class BOLAMatrixCandidate:
    endpoint_id: int
    resource_id: int
    test_identity_id: int
    subject_kind: SubjectKind
    candidate_kind: CandidateKind
    relationship: Relationship
    expected_access: Literal["allowed", "denied"]
    supporting_assertion_ids: tuple[int, ...]


def plan_bola_matrix(*, facts: Iterable[BOLAMatrixAccessFact]) -> tuple[BOLAMatrixCandidate, ...]:
    """Preserve explicit truth and order; return no partial result on invalid input."""
    try:
        source = iter(facts)
    except TypeError as exc:
        raise BOLAMatrixPlanningError("bola_matrix_invalid_access_fact") from exc

    seen: set[tuple[int, int, int]] = set()
    candidates: list[BOLAMatrixCandidate] = []
    for index, fact in enumerate(source):
        if index == MAX_BOLA_MATRIX_FACTS:
            raise BOLAMatrixPlanningError("bola_matrix_fact_limit_exceeded")
        if not isinstance(fact, BOLAMatrixAccessFact):
            raise BOLAMatrixPlanningError("bola_matrix_invalid_access_fact")
        key = (fact.endpoint_id, fact.resource_id, fact.test_identity_id)
        if key in seen:
            raise BOLAMatrixPlanningError("bola_matrix_duplicate_access_fact")
        seen.add(key)

        if fact.resolution_state != "resolved" or fact.expected_access not in ("allowed", "denied"):
            continue

        subject_kind: SubjectKind
        candidate_kind: CandidateKind
        if fact.identity_auth_type == "anonymous":
            subject_kind = "anonymous"
            candidate_kind = "anonymous_access"
        else:
            subject_kind = "authenticated"
            if fact.relationship == "owner":
                candidate_kind = "owner_access"
            elif fact.relationship == "shared":
                candidate_kind = "shared_access"
            elif fact.relationship == "non_owner":
                candidate_kind = "cross_subject_access"
            else:
                continue

        candidates.append(BOLAMatrixCandidate(
            endpoint_id=fact.endpoint_id,
            resource_id=fact.resource_id,
            test_identity_id=fact.test_identity_id,
            subject_kind=subject_kind,
            candidate_kind=candidate_kind,
            relationship=fact.relationship,
            expected_access=fact.expected_access,
            supporting_assertion_ids=fact.supporting_assertion_ids,
        ))
    return tuple(candidates)
