"""Universal M13 evidence policy; future explicit management is a separate feature."""
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class FindingEvidenceRetentionPolicy:
    policy_id: str
    policy_version: str
    retention_mode: str
    automatic_deletion_enabled: bool
    raw_response_body_retained: bool


V1_FINDING_EVIDENCE_RETENTION_POLICY: Final = FindingEvidenceRetentionPolicy(
    policy_id="m13_minimized_finding_evidence",
    policy_version="1",
    retention_mode="explicit_management_only",
    automatic_deletion_enabled=False,
    raw_response_body_retained=False,
)
