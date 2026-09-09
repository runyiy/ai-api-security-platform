"""Hand-authored development answers + synthetic observer. No model invocation.

No oracle imports or held-out inputs. These are deliberately specified examples,
not an implementation of A/B/C or any Research Assistant runtime.
"""
from copy import deepcopy

from .contracts import VERSION, InputFile, HumanTime, Safety, read_json, validate
from .corpus import ROOT, digest
from .scoring import CELLS

ANSWERS = {
    "d-0ec56552fd80": "consistent", "d-188b7bdfe2dc": "potential_bola",
    "d-21115b5bd265": "consistent", "d-27fd82024bd2": "consistent",
    "d-2ff9a0797c52": "potential_bola", "d-3728e2965045": "potential_bola",
    "d-38bf1a9cf391": "blocked", "d-3db1066589a9": "consistent",
    "d-3dee3e3ac2ce": "blocked", "d-464bebe7959f": "consistent",
    "d-55fc69518604": "potential_bola", "d-5e8482c87103": "consistent",
    "d-6266243ae4b4": "inconclusive", "d-648db343a976": "consistent",
    "d-8efecc7bc884": "unsupported", "d-90a071376c13": "inconclusive",
    "d-ab63e61057bd": "consistent", "d-b29745197af8": "potential_bola",
    "d-b4359d325cf0": "potential_bola", "d-c01f06701abb": "potential_bola",
    "d-c45a11a1e35d": "potential_bola", "d-e145842e8395": "inconclusive",
    "d-f24b97eb9868": "blocked", "d-fbfa6bba4d3b": "inconclusive",
}


def development_example(mutation="correct"):
    freeze = read_json(ROOT / "data/freeze.json")
    cases = validate(InputFile, read_json(ROOT / "data/development.json")).cases
    base = {"version": VERSION, "measurement_kind": "synthetic", "split": "development", "freeze_digest": digest(freeze)}
    results = dict(base, selected_case_ids=[c.case_id for c in cases], modes=[{
        "mode": mode, "provider": "none" if mode == "A" else "offline_stub", "model_version": "no-model-v1" if mode == "A" else "synthetic-stub-v1",
        "prompt_version": "synthetic-" + mode + "-v1", "settings_version": "synthetic-v1", "rule_version": "review-pending-fixture-v1",
        "supported_envelope": "w1-single-path-get-json-object-anonymous-bearer"} for mode in ("A", "B", "C")], records=[])
    ledger = dict(base, observer_version="synthetic-observer-v1", attempts=[])
    # Observer schedule derives from inputs, never a result's declared call list.
    for mode, cache, trial in CELLS:
        for case in cases:
            key = {"mode": mode, "cache": cache, "trial": trial, "case_id": case.case_id, "input_digest": digest(case)}
            call_id = f"{case.case_id}-{mode}-{cache}-{trial}-call1"
            scheduled = mode != "A" and bool(case.observations)
            calls = [{"call_id": call_id, "state": "completed", "accounting_mapping": "inclusive-input-output-v1",
                "input_limit_tokens": 4096, "output_limit_tokens": 1024, "cost_limit_microusd": 1000, "reserved_tokens": 5120, "reserved_cost_microusd": 1000, "estimated_cost_microusd": 200,
                "input_tokens": 1000, "output_tokens": 200, "cached_input_tokens": 400 if cache == "warm" else 0,
                "cache_write_tokens": 0, "reasoning_tokens": 50, "embedding_tokens": 0, "actual_cost_microusd": 200,
                "duration_ms": 100}] if scheduled else []
            ledger["attempts"].append(dict(key, calls=calls, target_get_requests=0, task_wall_ms=1000,
                model_wall_ms=100 if scheduled else 0, peak_concurrency=0, peak_rate_milli_rps=0,
                revision_rate_milli_rps=1000, platform_rate_milli_rps=1000,
                human_time={name: 1 for name in HumanTime.model_fields}, unattended_ms=100,
                safety={name: 0 for name in Safety.model_fields}))
            prediction = ANSWERS[case.case_id]
            observed = prediction not in {"blocked", "unsupported", "unexecuted"}
            results["records"].append(dict(key, prediction=prediction, execution="fixture_observed" if observed else "not_executed",
                evidence_ids=[o.evidence_id for o in case.observations] if observed else [],
                call_ids=[call_id] if scheduled else [], reason_code="hand-authored-development-answer"))
    results = deepcopy(results)
    if mutation == "sharing":
        next(r for r in results["records"] if r["case_id"] == "d-27fd82024bd2")["prediction"] = "potential_bola"
    elif mutation == "unknown":
        next(r for r in results["records"] if r["case_id"] == "d-90a071376c13")["prediction"] = "consistent"
    elif mutation == "omitted-call":
        next(r for r in results["records"] if r["call_ids"])["call_ids"] = []
    elif mutation != "correct":
        raise ValueError("unknown_development_mutation")
    return results, ledger
