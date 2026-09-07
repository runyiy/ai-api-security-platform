from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.analyzers.bola import (
    AnalysisOutcome,
    BOLAAnalysisResult,
    analyze_bola_run,
)
from app.db.models.endpoint import Endpoint
from app.db.models.finding import Finding
from app.db.models.resource import Resource
from app.db.models.test_case import TestCase
from app.db.models.test_run import TestRun
from app.generators.bola import (
    BOLA_CROSS_OWNER,
    OWNER_BASELINE,
)


class FindingAnalysisError(
    RuntimeError
):
    pass


class FindingAnalysisNotFoundError(
    FindingAnalysisError
):
    pass


@dataclass(frozen=True)
class FindingAnalysisOutcome:
    analysis: BOLAAnalysisResult
    finding: Finding | None


class FindingAnalysisService:
    def __init__(
        self,
        *,
        db: Session,
    ) -> None:
        self.db = db

    def analyze_test_run(
        self,
        *,
        test_run_id: int,
        baseline_test_run_id: int,
    ) -> FindingAnalysisOutcome:
        test_run = self.db.get(
            TestRun,
            test_run_id,
        )

        if test_run is None:
            raise FindingAnalysisNotFoundError(
                "TestRun not found."
            )

        test_case = self.db.get(
            TestCase,
            test_run.test_case_id,
        )

        if test_case is None:
            raise FindingAnalysisNotFoundError(
                "TestCase not found."
            )

        if (
            test_case.test_type
            != BOLA_CROSS_OWNER
        ):
            result = BOLAAnalysisResult(
                outcome=(
                    AnalysisOutcome.INCONCLUSIVE
                ),
                reason=(
                    "Only cross-owner BOLA "
                    "runs are analyzed here."
                ),
            )

            return FindingAnalysisOutcome(
                analysis=result,
                finding=None,
            )

        endpoint = self.db.get(
            Endpoint,
            test_case.endpoint_id,
        )

        resource = self.db.get(
            Resource,
            test_case.resource_id,
        )

        if endpoint is None:
            raise FindingAnalysisNotFoundError(
                "Endpoint not found."
            )

        if resource is None:
            raise FindingAnalysisNotFoundError(
                "Resource not found."
            )

        if (
            endpoint.target_id
            != resource.target_id
        ):
            raise FindingAnalysisError(
                "Endpoint and resource belong "
                "to different targets."
            )

        baseline_run = self.db.get(TestRun, baseline_test_run_id)
        if baseline_run is None:
            raise FindingAnalysisNotFoundError(
                "finding_baseline_test_run_not_found"
            )
        baseline_case = self.db.get(TestCase, baseline_run.test_case_id)
        if (
            baseline_run.id == test_run.id
            or baseline_case is None
            or baseline_case.test_type != OWNER_BASELINE
            or baseline_case.endpoint_id != test_case.endpoint_id
            or baseline_case.resource_id != test_case.resource_id
            or baseline_case.actor_identity_id == test_case.actor_identity_id
        ):
            raise FindingAnalysisError("finding_evidence_pair_invalid")

        existing = self.db.scalar(select(Finding).where(
            Finding.test_run_id == test_run.id,
            Finding.category == "BOLA",
        ))
        if existing is not None:
            self._validate_baseline_binding(existing, baseline_run.id)

        result = analyze_bola_run(
            test_case=test_case,
            cross_owner_run=test_run,
            owner_baseline_run=baseline_run,
            resource=resource,
        )

        if (
            result.outcome
            != AnalysisOutcome.POTENTIAL_BOLA
        ):
            return FindingAnalysisOutcome(
                analysis=result,
                finding=None,
            )

        title = (
            f"Potential BOLA in "
            f"{endpoint.method} "
            f"{endpoint.path}"
        )

        description = (
            f"Cross-owner access to "
            "the target resource "
            "returned a successful response "
            "containing evidence of the target "
            "resource. Human review is required."
        )

        finding_id = self.db.scalar(
            insert(Finding)
            .values(
                target_id=endpoint.target_id,
                endpoint_id=endpoint.id,
                test_run_id=test_run.id,
                baseline_test_run_id=baseline_run.id,
                category="BOLA",
                severity=(
                    result.severity
                    or "unknown"
                ),
                confidence=(
                    result.confidence
                    or 0.0
                ),
                status="potential",
                title=title,
                description=description,
            )
            .on_conflict_do_nothing(
                constraint=(
                    "uq_finding_test_run_category"
                )
            )
            .returning(Finding.id)
        )

        if finding_id is not None:
            finding = self.db.get(
                Finding,
                finding_id,
            )
        else:
            finding = self.db.scalar(
                select(Finding).where(
                    Finding.test_run_id
                    == test_run.id,
                    Finding.category
                    == "BOLA",
                )
            )

        if finding is None:
            raise RuntimeError(
                "Finding conflict row not found."
            )

        self._validate_baseline_binding(finding, baseline_run.id)

        if finding_id is None:
            finding.severity = (
                result.severity
                or "unknown"
            )

            finding.confidence = (
                result.confidence
                or 0.0
            )

            finding.title = title
            finding.description = description

        self.db.commit()
        self.db.refresh(finding)

        return FindingAnalysisOutcome(
            analysis=result,
            finding=finding,
        )

    @staticmethod
    def _validate_baseline_binding(finding: Finding, baseline_test_run_id: int) -> None:
        # Null legacy provenance is deliberately not guessed or backfilled.
        if finding.baseline_test_run_id != baseline_test_run_id:
            raise FindingAnalysisError("finding_evidence_pair_conflict")
