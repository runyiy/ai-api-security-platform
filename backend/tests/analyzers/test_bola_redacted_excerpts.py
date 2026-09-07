from dataclasses import FrozenInstanceError, asdict
import json

import pytest

from app.analyzers.bola import AnalysisOutcome, analyze_bola_run
from tests.analyzers.test_bola import (
    build_baseline_run, build_cross_run, build_resource, build_test_case,
)


MARKER = "[MATCHED_RESOURCE_IDENTIFIER]"
FALLBACK = '{"[MATCHED_RESOURCE_IDENTIFIER_FIELD]":"[MATCHED_RESOURCE_IDENTIFIER]"}'


def analyze(baseline, probe, resource_type="project"):
    resource = build_resource()
    resource.resource_type = resource_type
    return analyze_bola_run(
        test_case=build_test_case(), resource=resource,
        owner_baseline_run=build_baseline_run(body=json.dumps(baseline)),
        cross_owner_run=build_cross_run(body=json.dumps(probe)),
    )


@pytest.mark.parametrize(("baseline", "probe", "baseline_key", "probe_key"), [
    ({"id": 2001}, {"id": "2001"}, "id", "id"),
    ({"project_id": "2001"}, {"project_id": 2001}, "project_id", "project_id"),
    ({"id": 2001}, {"project_id": "2001"}, "id", "project_id"),
    ({"PROJECT_ID": 2001}, {"ID": 2001}, "PROJECT_ID", "ID"),
    ({"data": [{"name": "private", "project_id": 2001}]},
     [{"user": {"id": 2001, "email": "private@example.test"}}], "project_id", "id"),
    ({"data": {"project_id": 2001}, "id": 2001},
     {"id": 2001, "project_id": 2001}, "project_id", "id"),
    ([{"id": False}, {"project_id": "different"}, {"project_id": 2001}, {"id": 2001}],
     {"id": {"project_id": 2001}, "project_id": 2001}, "project_id", "project_id"),
])
def test_actual_first_matching_field_only(baseline, probe, baseline_key, probe_key):
    result = analyze(baseline, probe)
    assert result.outcome == AnalysisOutcome.POTENTIAL_BOLA
    from app.analyzers.bola import BOLARedactedExcerptEvidence
    excerpt = result.excerpt_evidence
    assert isinstance(excerpt, BOLARedactedExcerptEvidence)
    assert asdict(excerpt) == {
        "extractor_id": "bola_matched_identifier_field", "extractor_version": "1",
        "baseline_excerpt": json.dumps({baseline_key: MARKER}, separators=(",", ":")),
        "probe_excerpt": json.dumps({probe_key: MARKER}, separators=(",", ":")),
    }
    assert "2001" not in repr(excerpt)
    assert analyze(baseline, probe).excerpt_evidence == excerpt
    for field in asdict(excerpt):
        with pytest.raises(FrozenInstanceError):
            setattr(excerpt, field, "changed")
    with pytest.raises(FrozenInstanceError):
        result.excerpt_evidence = None


@pytest.mark.parametrize("resource_type", ["project", "x" * 100, "界" * 100, 'a"b\\c'])
def test_large_secret_body_and_schema_length_key_remain_bounded(resource_type):
    key = f"{resource_type}_id"
    body = {"data": [{key: "2001", "Authorization": "Bearer private-token",
                     "cookie": "session=private-session", "password": "private-password",
                     "api_key": "private-key", "credentials": {"token": "private-token"},
                     "email": "private@example.test", "business": "private-business" * 100000}]}
    result = analyze(body, body, resource_type)
    assert result.outcome == AnalysisOutcome.POTENTIAL_BOLA
    for excerpt in (result.excerpt_evidence.baseline_excerpt, result.excerpt_evidence.probe_excerpt):
        assert len(excerpt) <= 192
        assert json.loads(excerpt) == {key: MARKER}
        assert excerpt == json.dumps({key: MARKER}, ensure_ascii=False, separators=(",", ":"))
        assert "private" not in excerpt
        assert "2001" not in excerpt


@pytest.mark.parametrize(("baseline", "probe", "status", "outcome"), [
    ('{"id":2001}', '{"id":2001}', 403, AnalysisOutcome.PASS),
    ('{"id":2001}', '{"id":2001}', 500, AnalysisOutcome.INCONCLUSIVE),
    ('not JSON', '{"id":2001}', 200, AnalysisOutcome.INCONCLUSIVE),
    ('{"id":2001}', 'not JSON', 200, AnalysisOutcome.INCONCLUSIVE),
    ('{"id":2001}', 'null', 200, AnalysisOutcome.INCONCLUSIVE),
    ('{"id":2001}', '{"other_id":2001}', 200, AnalysisOutcome.INCONCLUSIVE),
    ('{"id":2001}', '{"id":true}', 200, AnalysisOutcome.INCONCLUSIVE),
    ('{"id":2001}', '{"id":[2001]}', 200, AnalysisOutcome.INCONCLUSIVE),
])
def test_non_finding_results_have_no_excerpt(baseline, probe, status, outcome):
    result = analyze_bola_run(
        test_case=build_test_case(), resource=build_resource(),
        owner_baseline_run=build_baseline_run(body=baseline),
        cross_owner_run=build_cross_run(body=probe, status=status),
    )
    assert result.outcome == outcome
    assert result.evidence is None
    assert result.excerpt_evidence is None


@pytest.mark.parametrize("resource_type", ['"' * 100, '\\' * 100, '\x01' * 100])
@pytest.mark.parametrize("fallback_side", ["baseline", "probe", "both"])
def test_escaped_key_preserves_potential_bola_with_immutable_bounded_fallback(resource_type, fallback_side):
    from app.analyzers.bola import BOLARedactedExcerptEvidence
    key = f"{resource_type}_id"
    body = {"data": [{key: "2001", "id": 2001, "Authorization": "Bearer private-token",
                     "cookie": "session=private-session", "password": "private-password",
                     "api_key": "private-key", "credentials": {"token": "private-token"},
                     "email": "private@example.test", "business": "private-business" * 100000}]}
    baseline = body if fallback_side in ("baseline", "both") else {"id": 2001}
    probe = body if fallback_side in ("probe", "both") else {"id": 2001}
    result = analyze(baseline, probe, resource_type)
    assert result.outcome == AnalysisOutcome.POTENTIAL_BOLA
    assert result.confidence == (0.99 if fallback_side == "both" else 0.95)
    assert result.severity == "high"
    assert result.evidence.baseline_resource_identifier_present is True
    assert result.evidence.probe_resource_identifier_present is True
    evidence = result.excerpt_evidence
    assert isinstance(evidence, BOLARedactedExcerptEvidence)
    assert asdict(evidence) == {
        "extractor_id": "bola_matched_identifier_field", "extractor_version": "1",
        "baseline_excerpt": FALLBACK if fallback_side in ("baseline", "both") else '{"id":"[MATCHED_RESOURCE_IDENTIFIER]"}',
        "probe_excerpt": FALLBACK if fallback_side in ("probe", "both") else '{"id":"[MATCHED_RESOURCE_IDENTIFIER]"}',
    }
    for excerpt in (evidence.baseline_excerpt, evidence.probe_excerpt):
        assert len(excerpt) <= 192
        assert excerpt == json.dumps(json.loads(excerpt), separators=(",", ":"))
        assert len(json.loads(excerpt)) == 1
        assert list(json.loads(excerpt).values()) == [MARKER]
        assert "2001" not in excerpt
        assert "private" not in excerpt
        assert key not in excerpt
    with pytest.raises(FrozenInstanceError):
        evidence.baseline_excerpt = "changed"
    assert analyze(baseline, probe, resource_type) == result


@pytest.mark.parametrize(("quotes", "length"), [(53, 192), (54, 193)])
def test_fallback_applies_only_above_literal_serialized_bound(quotes, length):
    resource_type = "x" * (100 - quotes) + '"' * quotes
    key = f"{resource_type}_id"
    literal = json.dumps({key: MARKER}, separators=(",", ":"))
    assert len(literal) == length
    result = analyze({key: 2001}, {key: 2001}, resource_type)
    assert result.outcome == AnalysisOutcome.POTENTIAL_BOLA
    expected = literal if length == 192 else FALLBACK
    assert result.excerpt_evidence.baseline_excerpt == expected
    assert result.excerpt_evidence.probe_excerpt == expected


@pytest.mark.parametrize("resource_type", ['"' * 100, '\\' * 100, '\x01' * 100])
@pytest.mark.parametrize(("status", "matched", "outcome"), [
    (403, True, AnalysisOutcome.PASS),
    (500, True, AnalysisOutcome.INCONCLUSIVE),
    (200, False, AnalysisOutcome.INCONCLUSIVE),
])
def test_escaped_keys_do_not_change_non_finding_semantics(resource_type, status, matched, outcome):
    resource = build_resource()
    resource.resource_type = resource_type
    baseline = json.dumps({f"{resource_type}_id": 2001})
    probe = baseline if matched else '{"other_id":2001}'
    result = analyze_bola_run(
        test_case=build_test_case(), resource=resource,
        owner_baseline_run=build_baseline_run(body=baseline),
        cross_owner_run=build_cross_run(body=probe, status=status),
    )
    assert result.outcome == outcome
    assert result.evidence is None
    assert result.excerpt_evidence is None


def test_each_exact_body_is_parsed_only_once(monkeypatch):
    original = json.loads
    parsed = []

    def loads(body):
        parsed.append(body)
        return original(body)

    monkeypatch.setattr(json, "loads", loads)
    result = analyze({"id": 2001}, {"project_id": 2001})
    assert result.outcome == AnalysisOutcome.POTENTIAL_BOLA
    assert parsed == ['{"id": 2001}', '{"project_id": 2001}']
