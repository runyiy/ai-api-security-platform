from dataclasses import FrozenInstanceError, asdict
import hashlib
import json

import pytest

from app.analyzers.bola import AnalysisOutcome, analyze_bola_run
from tests.analyzers.test_bola import build_baseline_run, build_cross_run, build_resource, build_test_case


@pytest.mark.parametrize(("baseline", "probe"), [
    (None, ""), ("abc", "abc"), ("abc", "abd"),
    ('{"id":2001}', '{ "id": 2001 }'),
    ('{"id":2001,"name":"private"}', '{"name":"private","id":2001}'),
    ("é界😀", "e\u0301界😀"), ("not JSON\r\n", "not JSON\n"),
    ('{"secret":"private-token"}', "private-business" * 100000),
])
def test_pure_exact_utf8_fingerprint_pair(baseline, probe, monkeypatch, caplog):
    from app.analyzers.bola import BOLAResponseFingerprintEvidence, fingerprint_response_pair
    def prohibited(*args, **kwargs):
        pytest.fail("fingerprinting parsed or redacted input")
    monkeypatch.setattr(json, "loads", prohibited)
    monkeypatch.setattr("app.ai.redaction.sanitize_response_body", prohibited)
    result = fingerprint_response_pair(baseline_body=baseline, probe_body=probe)
    assert isinstance(result, BOLAResponseFingerprintEvidence)
    raw_baseline = b"" if baseline is None else baseline.encode("utf-8")
    raw_probe = b"" if probe is None else probe.encode("utf-8")
    assert asdict(result) == {
        "algorithm": "sha256", "fingerprint_version": "1",
        "baseline_digest": hashlib.sha256(raw_baseline).hexdigest(),
        "probe_digest": hashlib.sha256(raw_probe).hexdigest(),
        "baseline_body_bytes": len(raw_baseline), "probe_body_bytes": len(raw_probe),
    }
    assert (result.baseline_digest == result.probe_digest) == (raw_baseline == raw_probe)
    assert fingerprint_response_pair(baseline_body=baseline, probe_body=probe) == result
    for field in asdict(result):
        with pytest.raises(FrozenInstanceError):
            setattr(result, field, "changed")
    for marker in ("private-token", "private-business", "secret", "é", "界", "😀"):
        assert marker not in repr(result)
        assert marker not in caplog.text


@pytest.mark.parametrize(("baseline", "probe", "status", "expected", "confidence"), [
    ('{"id":2001}', '{"id":2001}', 200, AnalysisOutcome.POTENTIAL_BOLA, .99),
    ('{"id":2001}', '{ "id": 2001 }', 200, AnalysisOutcome.POTENTIAL_BOLA, .99),
    ('{"id":2001,"name":"a"}', '{"name":"a","id":2001}', 200, AnalysisOutcome.POTENTIAL_BOLA, .99),
    ('{"id":2001}', '{"project_id":2001}', 200, AnalysisOutcome.POTENTIAL_BOLA, .95),
    ('{"id":2001}', '{"id":2001}', 403, AnalysisOutcome.PASS, None),
    ('{"ok":true}', '{"ok":true}', 200, AnalysisOutcome.INCONCLUSIVE, None),
    ('{"id":2001}', 'not JSON', 200, AnalysisOutcome.INCONCLUSIVE, None),
    ('{"id":2001}', None, 200, AnalysisOutcome.INCONCLUSIVE, None),
])
def test_fingerprints_never_decide_classification(baseline, probe, status, expected, confidence):
    result = analyze_bola_run(test_case=build_test_case(), resource=build_resource(),
        owner_baseline_run=build_baseline_run(body=baseline),
        cross_owner_run=build_cross_run(body=probe, status=status))
    assert result.outcome == expected
    assert result.confidence == confidence
    fingerprint = result.fingerprint_evidence
    assert fingerprint is not None
    assert fingerprint.baseline_digest == hashlib.sha256(baseline.encode("utf-8")).hexdigest()
    assert fingerprint.probe_digest == hashlib.sha256(b"" if probe is None else probe.encode("utf-8")).hexdigest()
    if expected == AnalysisOutcome.POTENTIAL_BOLA:
        assert result.evidence.baseline_resource_identifier_present is True
        assert result.evidence.probe_resource_identifier_present is True
        assert result.excerpt_evidence.baseline_excerpt == '{"id":"[MATCHED_RESOURCE_IDENTIFIER]"}'
    else:
        assert result.evidence is result.excerpt_evidence is None
    with pytest.raises(FrozenInstanceError):
        result.fingerprint_evidence = None
