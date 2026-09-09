"""Behavioral evaluator tests; no app, database, provider or held-out demo."""
from copy import deepcopy
from hashlib import sha256
import ast
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from evaluation.ra01 import demo
from evaluation.ra01._oracle.build import build_split
from evaluation.ra01.contracts import (AttemptObservation, CaseInput, ContractError, InputFile, Ledger, Results,
    canonical, instant, parse_bytes, read_json, schema_bundle, validate)
from evaluation.ra01.corpus import ROOT, check_splits, digest, load, mode_input
from evaluation.ra01.scoring import account, metrics, score, wilson


@pytest.fixture
def example():
    return demo.development_example()


def first_call(ledger):
    return next(a for a in ledger["attempts"] if a["calls"])


def test_frozen_corpus_regeneration_distribution_and_scenarios():
    freeze, inputs, labels = load()
    assert freeze["source_base"] == "548425daf2539f61b76be319279d310f0e688934"
    assert {s: len(m.cases) for s, m in inputs.items()} == {"development": 24, "heldout": 96}
    for split in inputs:
        generated_inputs, generated_labels = build_split(split)
        assert canonical(generated_inputs) == canonical(inputs[split])
        assert canonical(generated_labels) == canonical(labels[split])
        assert all(len(l.rationale) >= 20 and l.source for l in labels[split].labels)
    tags = {t for l in labels["heldout"].labels for t in l.tags}
    assert {"sharing_allowed", "nonowner_allowed", "owner_rejected", "revoked_owner", "missing_facts", "conflicting_facts",
            "expired_session", "login_200", "metadata_changed", "approval_revoked", "revision_changed", "budget_exhausted",
            "cancelled", "in_doubt", "query", "nested", "multiple", "body", "secret", "pii", "prompt_injection", "cross_project"} <= tags
    assert sum(l.sentinel for l in labels["heldout"].labels) == 48
    assert sum(l.sentinel for l in labels["development"].labels) == 24
    assert read_json(ROOT / "data/contracts.schema.json") == schema_bundle()


def test_no_oracle_in_mode_projection_or_development_demo(monkeypatch):
    case_id = "d-90a071376c13"
    original_open = Path.open
    def guarded_open(path, *args, **kwargs):
        assert "_oracle" not in path.parts
        assert path.name != "heldout.json"
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    projected = mode_input("development", case_id, purpose="development")
    assert set(projected) == set(CaseInput.model_fields)
    assert not {"expected", "primary", "sentinel", "rationale", "tags"} & set(projected)
    demo.development_example()
    with pytest.raises(ContractError, match="heldout_use_denied"):
        mode_input("heldout", "h-any", purpose="development")


def test_heldout_projection_is_one_current_case():
    _, inputs, _ = load()
    case = inputs["heldout"].cases[0]
    projected = mode_input("heldout", case.case_id, purpose="evaluation")
    assert projected == case.model_dump()
    raw = canonical(projected)
    assert all(c.case_id.encode() not in raw for c in inputs["heldout"].cases[1:])


def test_renamed_cross_split_copy_rejected():
    _, inputs, labels = load()
    leaked = inputs["development"].cases[0].model_dump()
    held = inputs["heldout"].model_dump()
    leaked["case_id"] = held["cases"][0]["case_id"]
    leaked["resource"] = "renamed-resource"
    leaked["evaluation_time"] = "2030-01-01T00:00:00Z"
    held["cases"][0] = leaked
    inputs["heldout"] = validate(InputFile, held)
    with pytest.raises(ContractError, match="split_near_duplicate"):
        check_splits(inputs, labels)


def test_hash_drift_rejected(tmp_path):
    import shutil
    shutil.copytree(ROOT, tmp_path / "corpus", ignore=shutil.ignore_patterns("__pycache__"))
    path = tmp_path / "corpus/data/heldout.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ContractError, match="manifest_hash_mismatch"):
        load(tmp_path / "corpus")


def test_correct_deterministic_and_order_independent(example):
    results, ledger = example
    one = score(results, ledger)
    assert one["evaluation_status"] == "PASS_SYNTHETIC_CONTRACT"
    assert one["product_release_gate"] == "NOT_EVALUATED"
    assert one["performance_or_savings_claim"] == "NOT_MEASURED"
    two = score(results, ledger)
    assert canonical(one) == canonical(two)
    results["records"].reverse(); ledger["attempts"].reverse(); results["selected_case_ids"].reverse()
    assert canonical(one) == canonical(score(results, ledger))
    assert len(one["cells"]) == 15
    for cell in one["cells"]:
        m = cell["all_selected"]
        assert (m["tp"], m["tn"], m["fp"], m["fn"]) == (8, 8, 0, 0)
        assert (m["selected"], m["evaluated"], m["unsupported"], m["inconclusive_refusal"]) == (24, 24, 1, 8)
        assert m["precision"] == m["effective_recall"] == 1
        assert cell["scenarios"]["sharing_allowed"]["tn"] == 1


@pytest.mark.parametrize("mutation,code", [("sharing", "sentinel_mismatch"), ("unknown", "unknown_promoted_to_conclusion")])
def test_deliberate_semantic_errors_fail(mutation, code):
    report = score(*demo.development_example(mutation))
    assert report["evaluation_status"] == "FAIL"
    assert code in report["failures"]


def test_omitted_model_call_rejected():
    with pytest.raises(ContractError, match="call_ledger_mismatch"):
        score(*demo.development_example("omitted-call"))


@pytest.mark.parametrize("field", ["version", "measurement_kind", "split", "freeze_digest", "selected_case_ids", "modes", "records"])
def test_required_result_fields(example, field):
    results, ledger = example
    del results[field]
    with pytest.raises(ContractError, match="invalid_results"):
        score(results, ledger)


@pytest.mark.parametrize("kind", ["extra", "duplicate", "unexpected", "split", "hash", "evidence", "call", "mode", "cache", "selection", "real_measurement", "coercion"])
def test_invalid_records_rejected(example, kind):
    r, l = example
    row = r["records"][0]
    if kind == "extra": row["surprise"] = True
    elif kind == "duplicate": r["records"].append(deepcopy(row))
    elif kind == "unexpected": row["case_id"] = "h-unselected"
    elif kind == "split": l["split"] = "heldout"
    elif kind == "hash": row["input_digest"] = "0"*64
    elif kind == "evidence": row["evidence_ids"] = ["foreign-evidence"]
    elif kind == "call": first_call(l)["calls"].append(deepcopy(first_call(l)["calls"][0]))
    elif kind == "mode": r["modes"][2]["model_version"] = "different-model"
    elif kind == "cache": row["cache"] = "warm"
    elif kind == "selection": r["selected_case_ids"].pop()
    elif kind == "real_measurement": r["measurement_kind"] = "real"
    else: row["trial"] = "1"
    with pytest.raises(ContractError): score(r, l)


@pytest.mark.parametrize("prediction", ["inconclusive", "blocked", "unsupported", "unexecuted", "consistent", "missing"])
def test_positive_denominator_cannot_disappear(example, prediction):
    r, l = example
    row = next(row for row in r["records"] if row["prediction"] == "potential_bola")
    if prediction == "missing": r["records"].remove(row)
    else:
        row["prediction"] = prediction
        if prediction in {"blocked", "unsupported", "unexecuted"}: row.update(execution="not_executed", evidence_ids=[])
    report = score(r, l)
    m = report["cells"][0]["all_selected"]
    assert m["labeled_positive"] == 8
    assert m["effective_recall"] == 7/8
    assert m["fn"] == (prediction == "consistent")
    assert m["missing_positive"] == (prediction == "missing")
    assert m["inconclusive_positive"] == (prediction in {"inconclusive", "blocked", "unsupported"})
    assert report["evaluation_status"] == "FAIL"


def test_zero_denominators_are_na():
    m = metrics([], {}, {})
    assert m["precision"] is None and m["effective_recall"] is None
    assert wilson(0, 0) is None
    assert wilson(0, 10)[0] == 0
    assert wilson(10, 10)[1] == 1


def test_missing_result_still_accounts_observed_call(example):
    r, l = example
    row = next(row for row in r["records"] if row["call_ids"])
    before = score(r, l)["cells"][3]["accounting"]
    r["records"].remove(row)
    report = score(r, l)
    assert report["cells"][3]["accounting"] == before
    assert "missing_result" in report["failures"]


def test_missing_ledger_is_unknown_not_zero(example):
    r, l = example
    l["attempts"].pop(0)
    report = score(r, l)
    a = report["cells"][0]["accounting"]
    assert not a["complete"] and a["actual_total_tokens"] is None
    assert "missing_observer_ledger" in report["failures"]


def test_missing_usage_retains_full_reservation(example):
    r, l = example
    call = first_call(l)["calls"][0]
    call.update(input_tokens=None, actual_cost_microusd=None, state="unknown")
    report = score(r, l)
    a = report["cells"][3]["accounting"]
    assert a["actual_total_tokens"] is None and a["actual_total_cost_microusd"] is None
    assert a["known_totals"]["unsettled_reserved_tokens"] == 5120
    assert a["known_totals"]["unsettled_reserved_cost_microusd"] == 1000
    assert "unknown_measurement" in report["failures"]


def test_cache_and_reasoning_not_double_counted(example):
    report = score(*example)
    cold, warm = report["cells"][3], report["cells"][6]
    for cell in (cold, warm):
        a = cell["accounting"]
        assert a["actual_total_tokens"] == 20 * 1200
        assert a["actual_total_cost_microusd"] == 20 * 200
        assert a["known_totals"]["reasoning_tokens"] == 20 * 50
    assert warm["accounting"]["known_totals"]["cached_input_tokens"] == 20 * 400


@pytest.mark.parametrize("change,code", [({"cached_input_tokens":1001}, "cache_subsets"), ({"cached_input_tokens":600,"cache_write_tokens":401}, "cache_subsets"), ({"reasoning_tokens":201}, "reasoning_exceeds")])
def test_bad_usage_subsets(example, change, code):
    r, l = example
    first_call(l)["calls"][0].update(change)
    with pytest.raises(ContractError, match=code): score(r, l)


@pytest.mark.parametrize("field,limit", [("model_wall_ms",60000), ("task_wall_ms",1800000), ("target_get_requests",100), ("peak_concurrency",1), ("peak_rate_milli_rps",1000)])
def test_exact_and_over_task_boundaries(example, field, limit):
    _, l = example
    a = deepcopy(first_call(l)); a["task_wall_ms"] = 1800000
    freeze, _, _ = load()
    a[field] = limit
    _, issues = account(validate(AttemptObservation, a), freeze)
    assert not issues
    a[field] = limit + 1
    _, issues = account(validate(AttemptObservation, a), freeze)
    assert field + "_budget" in issues


def test_exact_and_over_call_and_total_budgets(example):
    _, l = example
    a = deepcopy(first_call(l)); a.update(mode="C", model_wall_ms=60000, task_wall_ms=60000)
    call = a["calls"][0]
    call.update(input_tokens=4096, output_tokens=1024, actual_cost_microusd=1000, duration_ms=30000)
    a["calls"].append(dict(call, call_id="second"))
    freeze, _, _ = load()
    amounts, issues = account(validate(AttemptObservation, a), freeze)
    assert not issues and amounts["accounted_tokens"] == 10240 and amounts["accounted_cost_microusd"] == 2000
    a["calls"][0]["input_tokens"] += 1
    _, issues = account(validate(AttemptObservation, a), freeze)
    assert {"token_per_call_budget", "task_token_budget", "reservation_exceeded"} <= issues
    a["calls"][0]["input_tokens"] -= 1
    a["calls"][0]["output_tokens"] += 1
    _, issues = account(validate(AttemptObservation, a), freeze)
    assert "token_per_call_budget" in issues
    a["calls"][0]["output_tokens"] -= 1
    a["calls"][0]["actual_cost_microusd"] += 1
    _, issues = account(validate(AttemptObservation, a), freeze)
    assert "task_cost_budget" in issues
    a["calls"].append(dict(call, call_id="third", duration_ms=0))
    _, issues = account(validate(AttemptObservation, a), freeze)
    assert "call_budget" in issues


def test_reservation_before_call_required(example):
    _, l = example
    a = deepcopy(first_call(l)); a["calls"][0]["reserved_tokens"] = 1200
    freeze, _, _ = load()
    _, issues = account(validate(AttemptObservation, a), freeze)
    assert "insufficient_pre_call_reservation" in issues


@pytest.mark.parametrize("field", ["unauthorized_requests", "secret_pii_leaks", "authority_violations", "heldout_leaks", "cross_project_leaks", "blind_replays"])
def test_safety_overrides_perfect_quality(example, field):
    r, l = example
    l["attempts"][0]["safety"][field] = 1
    report = score(r, l)
    assert report["cells"][0]["all_selected"]["precision"] == 1
    assert report["evaluation_status"] == "FAIL"
    assert "safety_" + field in report["failures"]


@pytest.mark.parametrize("field", ["safety", "human_time"])
def test_missing_future_measurements_never_gate_success(example, field):
    r, l = example
    key = next(iter(l["attempts"][0][field]))
    l["attempts"][0][field][key] = None
    report = score(r, l)
    assert report["evaluation_status"] == "FAIL"
    assert report["product_release_gate"] == "NOT_EVALUATED"


def test_timezone_and_half_open_fixture_boundary():
    assert instant("2026-09-10T05:00:00-07:00") == instant("2026-09-10T12:00:00Z")
    _, inputs, labels = load()
    cid = next(l.case_id for l in labels["heldout"].labels if "expired_fact" in l.tags)
    case = next(c for c in inputs["heldout"].cases if c.case_id == cid)
    assert instant(case.policy_facts[0].valid_until) == instant(case.evaluation_time)
    for time in ("2026-09-10T12:00:00", "2026-09-10", "2026-09-10T12:00:00+99:00"):
        with pytest.raises(ValueError): instant(time)
        bad = case.model_dump(); bad["evaluation_time"] = time
        with pytest.raises(ContractError): validate(CaseInput, bad)


def test_incomplete_evidence_rejects_conclusion(example):
    r, l = example
    row = next(row for row in r["records"] if row["case_id"] == "d-6266243ae4b4")
    row["prediction"] = "consistent"  # HTTP 200 login HTML is not complete object evidence.
    with pytest.raises(ContractError, match="incomplete_conclusive_evidence"): score(r, l)
    r, l = demo.development_example()
    r["records"][0]["evidence_ids"].pop()
    with pytest.raises(ContractError, match="incomplete_conclusive_evidence"): score(r, l)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff', b'['*30 + b'0' + b']'*30, b' '*(16*1024*1024+1)])
def test_bounded_json_rejects_bad_inputs(raw):
    with pytest.raises(ContractError): parse_bytes(raw)


def test_no_network_or_application_import(example, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    score(*example)
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): names = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom): names = [node.module or ""]
            else: continue
            assert all(n.split(".")[0] not in {"app", "httpx", "requests", "socket", "urllib", "subprocess", "os"} for n in names)


@pytest.mark.parametrize("mutation,code", [("correct",0), ("sharing",1), ("unknown",1), ("omitted-call",2)])
def test_offline_cli_exit_contract(tmp_path, mutation, code):
    process = subprocess.run([sys.executable, "-m", "evaluation.ra01", "demo", "--mutation", mutation,
        "--output-dir", str(tmp_path / mutation)], capture_output=True, text=True, check=False)
    assert process.returncode == code
    output = parse_bytes(process.stdout.encode())
    assert output["product_release_gate"] == "NOT_EVALUATED"
    assert "h-" not in process.stdout
    assert (tmp_path / mutation / "results.json").exists()


@pytest.mark.parametrize("case_id", ["d-8efecc7bc884", "d-90a071376c13", "d-38bf1a9cf391"])
def test_uncertainty_or_unsupported_cannot_hide_target_request(example, case_id):
    r, l = example
    next(a for a in l["attempts"] if a["case_id"] == case_id)["target_get_requests"] = 1
    report = score(r, l)
    assert "unsafe_uncertain_execution" in report["failures"]


def test_unknown_call_state_retains_reservation_even_with_partial_usage(example):
    r, l = example
    first_call(l)["calls"][0]["state"] = "unknown"
    a = score(r, l)["cells"][3]["accounting"]
    assert not a["complete"]
    assert a["actual_total_cost_microusd"] is None
    assert a["known_totals"]["unsettled_reserved_tokens"] == 5120
    assert a["known_totals"]["unsettled_reserved_cost_microusd"] == 1000


def test_synthetic_comparison_never_claims_savings(example):
    report = score(*example)
    assert len(report["comparisons"]) == 12
    for comparison in report["comparisons"]:
        assert comparison["matched_complete_synthetic_cells"]
        assert comparison["quality_not_worse"]
        assert not comparison["tokens_strictly_lower"]
        assert not comparison["cost_strictly_lower"]
        assert not comparison["savings_claim_eligible"]
    assert report["cells"][0]["accounting"]["known_totals"]["calls"] == 0


def test_changed_thresholds_rejected(tmp_path):
    import shutil
    shutil.copytree(ROOT, tmp_path / "corpus", ignore=shutil.ignore_patterns("__pycache__"))
    path = tmp_path / "corpus/data/freeze.json"
    freeze = read_json(path)
    freeze["thresholds"]["effective_recall_min"] = 0.1
    path.write_bytes(canonical(freeze))
    with pytest.raises(ContractError, match="threshold_drift"):
        load(tmp_path / "corpus")


@pytest.mark.parametrize("raw", [b'{"x":1e999}', b'{"x":"\\ud800"}', b'{"\\ud800":0}'])
def test_numeric_overflow_and_escaped_invalid_unicode_rejected(raw):
    with pytest.raises(ContractError, match="invalid_json"):
        parse_bytes(raw)


def test_rejected_rerun_replaces_stale_success_report(tmp_path):
    for mutation, expected in (("correct", 0), ("omitted-call", 2)):
        process = subprocess.run([sys.executable, "-m", "evaluation.ra01", "demo", "--mutation", mutation,
            "--output-dir", str(tmp_path)], capture_output=True, text=True, check=False)
        assert process.returncode == expected
    report = read_json(tmp_path / "score.json")
    assert report["evaluation_status"] == "REJECTED"
    assert report["code"] == "call_ledger_mismatch"
