import json

from app.db.models.security_report import (
    SecurityReport,
)


def render_security_report_markdown(
    report: SecurityReport,
) -> str:
    steps = "\n".join(
        f"{index}. {step}"
        for index, step in enumerate(
            report.steps_to_reproduce,
            start=1,
        )
    )

    evidence_json = json.dumps(
        report.evidence,
        indent=2,
        ensure_ascii=False,
    )

    return f"""# {report.title}

**Report Version:** {report.version}

## Summary

{report.summary}

## Affected Endpoint

`{report.affected_endpoint}`

## Prerequisites

{report.prerequisites}

## Steps To Reproduce

{steps}

## Expected Result

{report.expected_result}

## Actual Result

{report.actual_result}

## Security Impact

{report.security_impact}

## Evidence

```json
{evidence_json}