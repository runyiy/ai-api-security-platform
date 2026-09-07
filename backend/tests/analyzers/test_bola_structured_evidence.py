from dataclasses import FrozenInstanceError, asdict, is_dataclass

import pytest

from app.analyzers.bola import AnalysisOutcome, analyze_bola_run
from tests.analyzers.test_bola import (
    build_baseline_run, build_cross_run, build_resource, build_test_case,
)


def test_potential_bola_returns_only_immutable_selected_facts():
    result = analyze_bola_run(
        test_case=build_test_case(), resource=build_resource(),
        owner_baseline_run=build_baseline_run(status=201),
        cross_owner_run=build_cross_run(status=202),
    )
    assert result.outcome == AnalysisOutcome.POTENTIAL_BOLA
    assert result.confidence == 0.99
    evidence = result.evidence
    assert is_dataclass(evidence)
    assert asdict(evidence) == {
        "probe_test_run_id": 200,
        "baseline_test_run_id": 100,
        "evidence_type": "bola_resource_identifier_pair",
        "rule_id": "bola_resource_identifier_presence",
        "rule_version": "1",
        "reason_code": "baseline_and_probe_contain_target_resource_identifier",
        "baseline_status_code": 201,
        "probe_status_code": 202,
        "baseline_resource_identifier_present": True,
        "probe_resource_identifier_present": True,
    }
    with pytest.raises(FrozenInstanceError):
        evidence.rule_version = "changed"
    with pytest.raises(FrozenInstanceError):
        result.evidence = None


@pytest.mark.parametrize(("baseline", "probe", "outcome"), [
    (build_baseline_run(), build_cross_run(status=403), AnalysisOutcome.PASS),
    (None, build_cross_run(), AnalysisOutcome.INCONCLUSIVE),
    (build_baseline_run(status=500), build_cross_run(), AnalysisOutcome.INCONCLUSIVE),
    (build_baseline_run(), build_cross_run(body='{"ok": true}'), AnalysisOutcome.INCONCLUSIVE),
])
def test_non_finding_outcomes_have_no_structured_evidence(baseline, probe, outcome):
    result = analyze_bola_run(
        test_case=build_test_case(), resource=build_resource(),
        owner_baseline_run=baseline, cross_owner_run=probe,
    )
    assert result.outcome == outcome
    assert result.evidence is None
