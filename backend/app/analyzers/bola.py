from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Any

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


@dataclass(frozen=True)
class BOLAAnalysisResult:
    outcome: AnalysisOutcome

    reason: str

    confidence: float | None = None
    severity: str | None = None

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


def json_contains_resource_id(
    *,
    value: Any,
    resource: Resource,
) -> bool:
    allowed_keys = {
        "id",
        f"{resource.resource_type}_id",
    }

    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(
                key
            ).lower()

            if normalized_key in allowed_keys:
                scalar = normalize_scalar(
                    child
                )

                if (
                    scalar
                    == resource.external_id
                ):
                    return True

            if json_contains_resource_id(
                value=child,
                resource=resource,
            ):
                return True

        return False

    if isinstance(value, list):
        return any(
            json_contains_resource_id(
                value=item,
                resource=resource,
            )
            for item in value
        )

    return False


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

    baseline_contains_resource = (
        baseline_json is not None
        and json_contains_resource_id(
            value=baseline_json,
            resource=resource,
        )
    )

    cross_contains_resource = (
        json_contains_resource_id(
            value=cross_json,
            resource=resource,
        )
    )

    if (
        baseline_contains_resource
        and cross_contains_resource
    ):
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