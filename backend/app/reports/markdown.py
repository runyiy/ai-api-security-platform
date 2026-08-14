import json

from app.db.models.security_report import (
    SecurityReport,
)


def render_security_report_markdown(
    report: SecurityReport,
) -> str:
    report_data = report.report_data

    steps = "\n".join(
        f"{index}. {step}"
        for index, step in enumerate(
            report_data["steps_to_reproduce"],
            start=1,
        )
    )

    evidence_json = json.dumps(
        report_data["evidence"],
        indent=2,
        ensure_ascii=False,
    )

    return f"""# {report_data["title"]}

**Report Version:** {report.version}

## Summary

{report_data["summary"]}

## Affected Endpoint

`{report_data["affected_endpoint"]}`

## Prerequisites

{report_data["prerequisites"]}

## Steps To Reproduce

{steps}

## Expected Result

{report_data["expected_result"]}

## Actual Result

{report_data["actual_result"]}

## Security Impact

{report_data["security_impact"]}

## Evidence

```json
{evidence_json}
```

## Suggested Fix

{report_data["suggested_fix"]}
"""
