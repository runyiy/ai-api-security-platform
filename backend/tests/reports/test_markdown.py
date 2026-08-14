from app.db.models.security_report import (
    SecurityReport,
)
from app.reports.markdown import (
    render_security_report_markdown,
)


def build_report() -> SecurityReport:
    return SecurityReport(
        id=1,
        finding_id=1,
        source_ai_analysis_id=None,
        version=1,
        title=(
            "Confirmed BOLA in "
            "GET /api/projects/{project_id}"
        ),
        summary=(
            "Cross-owner object access "
            "was confirmed."
        ),
        affected_endpoint=(
            "GET /api/projects/{project_id}"
        ),
        prerequisites=(
            "Two distinct test identities."
        ),
        steps_to_reproduce=[
            "Authenticate as User A.",
            "Request Project 2001.",
        ],
        expected_result=(
            "Access should be denied."
        ),
        actual_result=(
            "HTTP 200 returned Project 2001."
        ),
        security_impact=(
            "Cross-user data disclosure."
        ),
        evidence={
            "response_status": 200,
            "request": {
                "headers": {
                    "Authorization":
                        "[REDACTED]"
                }
            },
        },
        suggested_fix=(
            "Validate resource ownership."
        ),
    )


def test_markdown_contains_required_sections():
    markdown = (
        render_security_report_markdown(
            build_report()
        )
    )

    assert "# Confirmed BOLA" in markdown

    assert "## Summary" in markdown
    assert "## Affected Endpoint" in markdown
    assert "## Prerequisites" in markdown
    assert "## Steps To Reproduce" in markdown
    assert "## Expected Result" in markdown
    assert "## Actual Result" in markdown
    assert "## Security Impact" in markdown
    assert "## Evidence" in markdown
    assert "## Suggested Fix" in markdown


def test_report_does_not_contain_token():
    markdown = (
        render_security_report_markdown(
            build_report()
        )
    )

    assert "[REDACTED]" in markdown