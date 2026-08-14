from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.redaction import (
    sanitize_request_data,
    sanitize_response_body,
)
from app.db.models.endpoint import Endpoint
from app.db.models.finding import Finding
from app.db.models.finding_ai_analysis import (
    FindingAIAnalysis,
)
from app.db.models.resource import Resource
from app.db.models.security_report import (
    SecurityReport,
)
from app.db.models.test_case import TestCase
from app.db.models.test_run import TestRun
from app.reports.markdown import (
    render_security_report_markdown,
)

class SecurityReportError(
    RuntimeError
):
    pass

class SecurityReportService:
    def __init__(
        self,
        *,
        db: Session,
    ) -> None:
        self.db = db

    def generate(
        self,
        *,
        finding_id: int,
    ) -> SecurityReport:
        finding = self.db.scalar(
            select(Finding)
            .where(
                Finding.id == finding_id
            )
            .with_for_update()
        )

        if finding is None:
            raise SecurityReportError(
                "Finding not found."
            )

        if finding.status != "confirmed":
            raise SecurityReportError(
                "Only confirmed findings can "
                "generate formal security reports."
            )

        test_run = self.db.get(
            TestRun,
            finding.test_run_id,
        )

        if test_run is None:
            raise SecurityReportError(
                "TestRun not found."
            )

        test_case = self.db.get(
            TestCase,
            test_run.test_case_id,
        )

        if test_case is None:
            raise SecurityReportError(
                "TestCase not found."
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
            raise SecurityReportError(
                "Endpoint not found."
            )

        if resource is None:
            raise SecurityReportError(
                "Resource not found."
            )

        if finding.endpoint_id != endpoint.id:
            raise SecurityReportError(
                "Finding evidence is inconsistent "
                "with its endpoint."
            )

        if finding.target_id != endpoint.target_id:
            raise SecurityReportError(
                "Finding target is inconsistent "
                "with its endpoint."
            )

        latest_ai_analysis = self.db.scalar(
            select(FindingAIAnalysis)
            .where(
                FindingAIAnalysis.finding_id
                == finding.id
            )
            .order_by(
                FindingAIAnalysis.id.desc()
            )
            .limit(1)
        )

        latest_version = self.db.scalar(
            select(
                func.max(
                    SecurityReport.version
                )
            ).where(
                SecurityReport.finding_id
                == finding.id
            )
        )

        next_version = (
            (latest_version or 0)
            + 1
        )

        request_data = (
            sanitize_request_data(
                test_run.request_data
            )
        )

        response_body = (
            sanitize_response_body(
                test_run.response_body
            )
        )

        affected_endpoint = (
            f"{endpoint.method} "
            f"{endpoint.path}"
        )

        prerequisites = (
            "A valid authenticated test identity "
            "that does not own the target "
            f"{resource.resource_type} is required. "
            "Testing must remain within the "
            "configured authorized scope."
        )

        request_url = request_data.get(
            "url",
            endpoint.path,
        )

        steps = [
            (
                "Use the authenticated test "
                f"identity with ID "
                f"{test_case.actor_identity_id}."
            ),
            (
                f"Send {endpoint.method} "
                f"to {request_url}. "
                "Authentication credentials are "
                "intentionally omitted from this "
                "report."
            ),
            (
                "Request the target "
                f"{resource.resource_type} with "
                f"external ID "
                f"{resource.external_id!r}, "
                "which is owned by test identity "
                f"{resource.owner_identity_id}."
            ),
            (
                "Observe the HTTP response and "
                "compare it with the expected "
                "object-level authorization "
                "behavior."
            ),
        ]

        expected_codes = ", ".join(
            str(code)
            for code in test_case.expected_statuses
        )

        expected_result = (
            "The application should deny "
            "cross-owner object access. "
            f"Expected status code(s): "
            f"{expected_codes}."
        )

        actual_status = (
            str(test_run.response_status)
            if test_run.response_status is not None
            else "no HTTP response"
        )

        actual_result = (
            "The cross-owner request produced "
            f"HTTP {actual_status}. "
            "The confirmed finding indicates "
            "that unauthorized object-level "
            "access was successfully reproduced."
        )

        security_impact = (
            "An authenticated user may access "
            f"{resource.resource_type} objects "
            "owned by another identity. "
            "This violates object-level "
            "authorization boundaries and may "
            "expose data or operations belonging "
            "to other users."
        )

        suggested_fix = (
            "Enforce object-level authorization "
            "on every resource access. Query or "
            "authorize the resource using both "
            "the resource identifier and the "
            "currently authenticated principal, "
            "rather than trusting the supplied "
            "object identifier alone."
        )

        if latest_ai_analysis is not None:
            suggested_fix = (
                latest_ai_analysis
                .fix_recommendation
            )

        evidence = {
            "test_run_id": test_run.id,
            "test_case_id": test_case.id,
            "resource_id": resource.id,
            "resource_type": (
                resource.resource_type
            ),
            "resource_external_id": (
                resource.external_id
            ),
            "owner_identity_id": (
                resource.owner_identity_id
            ),
            "actor_identity_id": (
                test_case.actor_identity_id
            ),
            "request": request_data,
            "response_status": (
                test_run.response_status
            ),
            "response_body": response_body,
            "rule_confidence": (
                finding.confidence
            ),
            "rule_severity": (
                finding.severity
            ),
            "human_review_notes": (
                finding.review_notes
            ),
        }

        if latest_ai_analysis is not None:
            evidence["ai_analysis"] = {
                "analysis_id": (
                    latest_ai_analysis.id
                ),
                "provider": (
                    latest_ai_analysis.provider
                ),
                "model_name": (
                    latest_ai_analysis.model_name
                ),
                "confidence": (
                    latest_ai_analysis.confidence
                ),
                "severity": (
                    latest_ai_analysis.severity
                ),
                "false_positive_risk": (
                    latest_ai_analysis
                    .false_positive_risk
                ),
            }

        report_data = {
            "source_ai_analysis_id": (
                latest_ai_analysis.id
                if latest_ai_analysis
                else None
            ),
            "title": (
                f"Confirmed BOLA in "
                f"{affected_endpoint}"
            ),
            "summary": (
                "A confirmed Broken Object Level "
                "Authorization issue allows one "
                "authenticated test identity to "
                "access a resource owned by "
                "another identity."
            ),
            "affected_endpoint": (
                affected_endpoint
            ),
            "prerequisites": prerequisites,
            "steps_to_reproduce": steps,
            "expected_result": expected_result,
            "actual_result": actual_result,
            "security_impact": security_impact,
            "evidence": evidence,
            "suggested_fix": suggested_fix,
        }

        report = SecurityReport(
            finding_id=finding.id,
            target_id=finding.target_id,
            version=next_version,
            report_format="markdown",
            report_data=report_data,
            markdown_content="",
        )

        report.markdown_content = (
            render_security_report_markdown(
                report
            )
        )

        self.db.add(report)
        self.db.commit()
        self.db.refresh(report)

        return report

