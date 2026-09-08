from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import inspect
import json

import pytest

from app.analyzers.bola import AnalysisOutcome, BOLAResponseFingerprintEvidence, analyze_bola_run
from app.db.models.resource import Resource
from app.db.models.test_run import TestRun as StoredRun
from tests.analyzers.test_bola import build_baseline_run, build_cross_run, build_resource, build_test_case


@pytest.mark.parametrize(("baseline", "probe", "bps"), [
    (0, 0, 10000), (100, 100, 10000), (50, 100, 5000), (100, 50, 5000),
    (1, 3, 3333), (3, 1, 3333), (0, 100, 0), (100, 0, 0),
    (10**20, 3 * 10**20, 3333),
])
@pytest.mark.parametrize("equal", [False, True])
def test_comparator_uses_only_typed_fingerprint_metadata(baseline, probe, bps, equal, monkeypatch):
    from app.analyzers.bola import BOLAResponseSimilarityEvidence, compare_response_fingerprints
    fingerprint = BOLAResponseFingerprintEvidence(
        baseline_digest="a" * 64, probe_digest=("a" if equal else "b") * 64,
        baseline_body_bytes=baseline, probe_body_bytes=probe,
    )
    before = asdict(fingerprint)
    def prohibited(*args, **kwargs):
        pytest.fail("comparator crossed metadata-only boundary")
    for model, fields in ((StoredRun, ("response_body", "request_data")), (Resource, ("external_id", "resource_type"))):
        for field in fields:
            monkeypatch.setattr(model, field, property(prohibited))
    for name in ("loads", "dumps"):
        monkeypatch.setattr(json, name, prohibited)
    monkeypatch.setattr(hashlib, "sha256", prohibited)
    monkeypatch.setattr("app.analyzers.bola.fingerprint_response_pair", prohibited)
    assert list(inspect.signature(compare_response_fingerprints).parameters) == ["fingerprint"]
    result = compare_response_fingerprints(fingerprint)
    assert isinstance(result, BOLAResponseSimilarityEvidence)
    assert asdict(result) == {
        "comparator_id": "sha256_exact_and_length_ratio", "comparator_version": "1",
        "exact_digest_match": equal, "length_similarity_bps": bps,
    }
    assert type(result.length_similarity_bps) is int
    assert type(result.exact_digest_match) is bool
    assert compare_response_fingerprints(fingerprint) == result
    assert asdict(fingerprint) == before
    assert "a" * 64 not in repr(result)
    assert "b" * 64 not in repr(result)
    for field in asdict(result):
        with pytest.raises(FrozenInstanceError):
            setattr(result, field, "changed")


@pytest.mark.parametrize("invalid", [None, {}, "body", b"body", StoredRun(), Resource()])
def test_comparator_rejects_anything_except_fingerprint_evidence(invalid):
    from app.analyzers.bola import compare_response_fingerprints
    with pytest.raises(TypeError):
        compare_response_fingerprints(invalid)


@pytest.mark.parametrize(("body", "probe", "status", "outcome", "confidence"), [
    ('{"id":2001}', '{"id":2001}', 200, AnalysisOutcome.POTENTIAL_BOLA, .99),
    ('{"id":2001}', '{ "id": 2001 }', 200, AnalysisOutcome.POTENTIAL_BOLA, .99),
    ('{"id":2001}', '{"project_id":2001}', 200, AnalysisOutcome.POTENTIAL_BOLA, .95),
    ('{"id":2001}', '{"id":2001,"padding":"' + 'x' * 100000 + '"}', 200, AnalysisOutcome.POTENTIAL_BOLA, .95),
    ('{"id":2001}', '{"id":2001}', 403, AnalysisOutcome.PASS, None),
    ('{"ok":true}', '{"ok":true}', 200, AnalysisOutcome.INCONCLUSIVE, None),
], ids=["equal", "whitespace", "different_key", "large_body", "pass", "inconclusive"])
def test_similarity_never_changes_classification_or_prior_evidence(body, probe, status, outcome, confidence, monkeypatch):
    from app.analyzers.bola import BOLAResponseSimilarityEvidence
    def analyze():
        return analyze_bola_run(test_case=build_test_case(), resource=build_resource(),
            owner_baseline_run=build_baseline_run(body=body), cross_owner_run=build_cross_run(body=probe, status=status))
    result = analyze()
    assert result.outcome == outcome
    assert result.confidence == confidence
    assert result.severity == ("high" if outcome == AnalysisOutcome.POTENTIAL_BOLA else None)
    if outcome == AnalysisOutcome.POTENTIAL_BOLA:
        expected = result.fingerprint_evidence
        similarity = result.similarity_evidence
        assert isinstance(similarity, BOLAResponseSimilarityEvidence)
        assert similarity.exact_digest_match == (expected.baseline_digest == expected.probe_digest)
        assert similarity.length_similarity_bps == min(expected.baseline_body_bytes, expected.probe_body_bytes) * 10000 // max(expected.baseline_body_bytes, expected.probe_body_bytes)
        assert result.evidence is not None and result.excerpt_evidence is not None
    else:
        assert result.similarity_evidence is None
    # Even arbitrary extremes from the metadata producer cannot change decisions,
    # confidence, severity, or any preexisting evidence layer.
    for equal, bps in ((True, 10000), (False, 0)):
        monkeypatch.setattr("app.analyzers.bola.compare_response_fingerprints",
            lambda fingerprint: BOLAResponseSimilarityEvidence(exact_digest_match=equal, length_similarity_bps=bps))
        changed = analyze()
        assert replace(changed, similarity_evidence=None) == replace(result, similarity_evidence=None)
    with pytest.raises(FrozenInstanceError):
        result.similarity_evidence = None
