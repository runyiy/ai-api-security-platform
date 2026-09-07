from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Any, Literal

from app.db.models.resource import Resource
from app.db.models.test_case import TestCase
from app.db.models.test_run import TestRun
from app.generators.bola import (
    BOLA_CROSS_OWNER,
)


class AnalysisOutcome(StrEnum):
    PASS = "pass"
    POTENTIAL_BOLA = "potential_bola"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class BOLAStructuredEvidence:
    probe_test_run_id: int
    baseline_test_run_id: int
    baseline_status_code: int
    probe_status_code: int
    baseline_resource_identifier_present: bool
    probe_resource_identifier_present: bool
    evidence_type: Literal["bola_resource_identifier_pair"] = "bola_resource_identifier_pair"
    rule_id: Literal["bola_resource_identifier_presence"] = "bola_resource_identifier_presence"
    rule_version: Literal["1"] = "1"
    reason_code: Literal["baseline_and_probe_contain_target_resource_identifier"] = (
        "baseline_and_probe_contain_target_resource_identifier"
    )


MATCHED_RESOURCE_IDENTIFIER = "[MATCHED_RESOURCE_IDENTIFIER]"
MAX_EXCERPT_LENGTH = 192


class BOLAExcerptError(ValueError):
    """The exact matching field cannot be represented within the evidence bound."""


@dataclass(frozen=True, slots=True)
class BOLARedactedExcerptEvidence:
    baseline_excerpt: str
    probe_excerpt: str
    extractor_id: Literal["bola_matched_identifier_field"] = "bola_matched_identifier_field"
    extractor_version: Literal["1"] = "1"


@dataclass(frozen=True)
class BOLAAnalysisResult:
    outcome: AnalysisOutcome

    reason: str

    confidence: float | None = None
    severity: str | None = None
    evidence: BOLAStructuredEvidence | None = None
    excerpt_evidence: BOLARedactedExcerptEvidence | None = None

def parse_json_body(
    body: str | None,
) -> Any | None:
    if body is None:
        return None

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None

def normalize_scalar(
    value: Any,
) -> str | None:
    if isinstance(value, bool):
        return None

    if isinstance(
        value,
        (str, int, float),
    ):
        return str(value)

    return None


def json_matching_resource_id_key(
    *, value: Any, resource: Resource,
) -> str | None:
    """Return only the first matching key in the existing depth-first order."""
    allowed_keys = {"id", f"{resource.resource_type}_id"}
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in allowed_keys:
                if normalize_scalar(child) == resource.external_id:
                    return str(key)
            matched_key = json_matching_resource_id_key(value=child, resource=resource)
            if matched_key is not None:
                return matched_key
    elif isinstance(value, list):
        for child in value:
            matched_key = json_matching_resource_id_key(value=child, resource=resource)
            if matched_key is not None:
                return matched_key
    return None


def json_contains_resource_id(*, value: Any, resource: Resource) -> bool:
    return json_matching_resource_id_key(value=value, resource=resource) is not None


def redacted_identifier_excerpt(key: str) -> str:
    excerpt = json.dumps(
        {key: MATCHED_RESOURCE_IDENTIFIER}, ensure_ascii=False, separators=(",", ":"),
    )
    # JSON escaping can expand even a schema-bounded resource type. Never
    # truncate the actual field or send oversized evidence to persistence.
    if len(excerpt) > MAX_EXCERPT_LENGTH:
        raise BOLAExcerptError("finding_evidence_excerpt_unrepresentable")
    return excerpt


def is_success_status(
    status_code: int,
) -> bool:
    return 200 <= status_code < 300


def analyze_bola_run(
    *,
    test_case: TestCase,
    cross_owner_run: TestRun,
    owner_baseline_run: TestRun | None,
    resource: Resource,
) -> BOLAAnalysisResult:
    if (
        test_case.test_type
        != BOLA_CROSS_OWNER
    ):
        return BOLAAnalysisResult(
            outcome=AnalysisOutcome.INCONCLUSIVE,
            reason=(
                "TestCase is not a "
                "cross-owner BOLA test."
            ),
        )

    if cross_owner_run.error_message:
        return BOLAAnalysisResult(
            outcome=AnalysisOutcome.INCONCLUSIVE,
            reason=(
                "Cross-owner request did not "
                "complete successfully."
            ),
        )

    if cross_owner_run.response_status is None:
        return BOLAAnalysisResult(
            outcome=AnalysisOutcome.INCONCLUSIVE,
            reason=(
                "Cross-owner request has no "
                "HTTP response status."
            ),
        )

    if owner_baseline_run is None:
        return BOLAAnalysisResult(
            outcome=AnalysisOutcome.INCONCLUSIVE,
            reason=(
                "No owner baseline run is available."
            ),
        )

    if owner_baseline_run.error_message:
        return BOLAAnalysisResult(
            outcome=AnalysisOutcome.INCONCLUSIVE,
            reason=(
                "Owner baseline execution failed."
            ),
        )

    if owner_baseline_run.response_status is None:
        return BOLAAnalysisResult(
            outcome=AnalysisOutcome.INCONCLUSIVE,
            reason=(
                "Owner baseline has no "
                "HTTP response status."
            ),
        )

    if not is_success_status(
        owner_baseline_run.response_status
    ):
        return BOLAAnalysisResult(
            outcome=AnalysisOutcome.INCONCLUSIVE,
            reason=(
                "Owner baseline did not "
                "return a successful response."
            ),
        )

    cross_status = (
        cross_owner_run.response_status
    )

    if cross_status in {
        401,
        403,
        404,
    }:
        return BOLAAnalysisResult(
            outcome=AnalysisOutcome.PASS,
            reason=(
                "Cross-owner access was rejected "
                "while the owner baseline succeeded."
            ),
        )

    if not is_success_status(
        cross_status
    ):
        return BOLAAnalysisResult(
            outcome=AnalysisOutcome.INCONCLUSIVE,
            reason=(
                "Cross-owner response is neither "
                "a normal success nor a standard "
                "authorization rejection."
            ),
        )

    baseline_json = parse_json_body(
        owner_baseline_run.response_body
    )

    cross_json = parse_json_body(
        cross_owner_run.response_body
    )

    if cross_json is None:
        return BOLAAnalysisResult(
            outcome=AnalysisOutcome.INCONCLUSIVE,
            reason=(
                "Cross-owner request returned a "
                "successful status, but the body "
                "cannot be validated as JSON."
            ),
        )

    baseline_key = json_matching_resource_id_key(value=baseline_json, resource=resource)
    cross_key = json_matching_resource_id_key(value=cross_json, resource=resource)
    baseline_contains_resource = baseline_key is not None
    cross_contains_resource = cross_key is not None

    if baseline_key is not None and cross_key is not None:
        confidence = 0.95

        if baseline_json == cross_json:
            confidence = 0.99

        return BOLAAnalysisResult(
            outcome=(
                AnalysisOutcome.POTENTIAL_BOLA
            ),
            reason=(
                "The owner baseline succeeded, "
                "and a different authenticated "
                "identity also received a "
                "successful response containing "
                "the target resource identifier."
            ),
            confidence=confidence,
            severity="high",
            excerpt_evidence=BOLARedactedExcerptEvidence(
                baseline_excerpt=redacted_identifier_excerpt(baseline_key),
                probe_excerpt=redacted_identifier_excerpt(cross_key),
            ),
            evidence=BOLAStructuredEvidence(
                probe_test_run_id=cross_owner_run.id,
                baseline_test_run_id=owner_baseline_run.id,
                baseline_status_code=owner_baseline_run.response_status,
                probe_status_code=cross_status,
                baseline_resource_identifier_present=baseline_contains_resource,
                probe_resource_identifier_present=cross_contains_resource,
            ),
        )

    return BOLAAnalysisResult(
        outcome=AnalysisOutcome.INCONCLUSIVE,
        reason=(
            "Cross-owner access returned a "
            "successful status, but the current "
            "rules cannot confirm that the "
            "target resource was disclosed."
        ),
    )