"""Deterministic evaluator over frozen local files, never an execution verifier."""
from collections import Counter
from math import sqrt

from .contracts import ContractError, Ledger, Results, validate
from .corpus import ROOT, digest, load

CELLS = [(mode, cache, trial) for mode, caches in (("A", ["none"]), ("B", ["cold", "warm"]), ("C", ["cold", "warm"])) for cache in caches for trial in (1, 2, 3)]


def ratio(n, d):
    return None if d == 0 else round(n / d, 8)


def wilson(n, d):
    if d == 0: return None
    z = 1.959963984540054
    p = n / d
    center = (p + z*z/(2*d))/(1+z*z/d)
    half = z*sqrt(p*(1-p)/d+z*z/(4*d*d))/(1+z*z/d)
    return [round(max(0, center-half), 8), round(min(1, center+half), 8)]


def metrics(ids, labels, results):
    counts = dict.fromkeys(("tp", "fp", "tn", "fn", "predicted_positive", "labeled_positive", "executed", "evaluated", "selected", "unsupported", "inconclusive_refusal", "inconclusive_positive", "missing", "missing_positive", "unexecuted", "unexecuted_positive"), 0)
    for cid in ids:
        label, result = labels[cid], results.get(cid)
        counts["selected"] += 1
        counts["labeled_positive"] += label.primary == "positive"
        if result is None:
            counts["missing"] += 1
            counts["missing_positive"] += label.primary == "positive"
            continue
        counts["evaluated"] += 1
        counts["executed"] += result.execution == "fixture_observed"
        p = result.prediction
        counts["predicted_positive"] += p == "potential_bola"
        counts["unsupported"] += p == "unsupported"
        counts["inconclusive_refusal"] += p in {"inconclusive", "blocked", "unsupported"}
        counts["unexecuted"] += result.execution == "not_executed"
        if p == "potential_bola": counts["tp" if label.primary == "positive" else "fp"] += 1
        elif p == "consistent":
            if label.primary == "positive": counts["fn"] += 1
            elif label.primary == "negative": counts["tn"] += 1
        elif label.primary == "positive":
            counts["inconclusive_positive"] += p in {"inconclusive", "blocked", "unsupported"}
            counts["unexecuted_positive"] += p == "unexecuted"
    counts["precision"] = ratio(counts["tp"], counts["tp"] + counts["fp"])
    counts["effective_recall"] = ratio(counts["tp"], counts["labeled_positive"])
    counts["precision_wilson95"] = wilson(counts["tp"], counts["tp"] + counts["fp"])
    counts["effective_recall_wilson95"] = wilson(counts["tp"], counts["labeled_positive"])
    return counts


def unique_index(items, expected):
    found = {}
    for item in items:
        key = item.key()
        if key not in expected: raise ContractError("unexpected_attempt")
        if key in found: raise ContractError("duplicate_attempt")
        found[key] = item
    return found


def check_evidence(result, case):
    if len(set(result.evidence_ids)) != len(result.evidence_ids) or len(set(result.call_ids)) != len(result.call_ids):
        raise ContractError("duplicate_reference")
    known = {o.evidence_id: o for o in case.observations}
    if not set(result.evidence_ids) <= set(known): raise ContractError("unexpected_evidence")
    if result.execution == "not_executed" and result.evidence_ids:
        raise ContractError("unexecuted_evidence")
    if result.execution == "fixture_observed" and not result.evidence_ids:
        raise ContractError("missing_observation_reference")
    if result.prediction in {"potential_bola", "consistent"}:
        pair = [known[e] for e in result.evidence_ids]
        if (result.execution != "fixture_observed" or len(pair) != 2 or
            any(not o.complete or not isinstance(o.body, dict) or o.status is None for o in pair) or
            {o.actor for o in pair} != {case.baseline_actor, case.selected_actor} or
            {o.resource for o in pair} != {case.resource} or len({o.revision for o in pair}) != 1):
            raise ContractError("incomplete_conclusive_evidence")
    if result.prediction in {"unsupported", "unexecuted", "blocked"} and result.execution != "not_executed":
        raise ContractError("nonexecuting_prediction")


def account(attempt, freeze):
    """Missing actuals retain reservation; inclusive subsets are never added twice."""
    issues = set()
    t = freeze["thresholds"]
    b = freeze["synthetic_fixture_budget"]
    amounts = Counter(dict.fromkeys(("calls", "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens",
        "reasoning_tokens", "embedding_tokens", "actual_cost_microusd", "duration_ms", "reserved_tokens",
        "reserved_cost_microusd", "estimated_cost_microusd", "accounted_tokens", "unsettled_reserved_tokens",
        "accounted_cost_microusd", "unsettled_reserved_cost_microusd"), 0))
    unknown_fields = 0
    def add(name, value):
        nonlocal unknown_fields
        if value is None:
            unknown_fields += 1
            issues.add("unknown_measurement")
        else: amounts[name] += value
    if attempt.mode == "A" and attempt.calls: raise ContractError("rule_mode_model_call")
    if len(attempt.calls) > t["c_calls_max"]: issues.add("call_budget")
    for call in attempt.calls:
        amounts["calls"] += 1
        for name in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens", "reasoning_tokens", "embedding_tokens", "actual_cost_microusd", "duration_ms", "reserved_tokens", "reserved_cost_microusd", "estimated_cost_microusd"):
            add(name, getattr(call, name))
        if call.state != "completed": issues.add("unresolved_call")
        inp, out = call.input_tokens, call.output_tokens
        if inp is not None and call.cached_input_tokens is not None and call.cache_write_tokens is not None and call.cached_input_tokens + call.cache_write_tokens > inp:
            raise ContractError("cache_subsets_exceed_input")
        if out is not None and call.reasoning_tokens is not None and call.reasoning_tokens > out:
            raise ContractError("reasoning_exceeds_output")
        if (inp is not None and inp > t["input_per_call_max"]) or (out is not None and out > t["output_including_reasoning_per_call_max"]): issues.add("token_per_call_budget")
        if call.reserved_tokens < call.input_limit_tokens + call.output_limit_tokens or call.reserved_cost_microusd < call.cost_limit_microusd:
            issues.add("insufficient_pre_call_reservation")
        if call.input_limit_tokens > t["input_per_call_max"] or call.output_limit_tokens > t["output_including_reasoning_per_call_max"]:
            issues.add("unbounded_call_limits")
        if (inp is not None and inp > call.input_limit_tokens) or (out is not None and out > call.output_limit_tokens) or (call.actual_cost_microusd is not None and call.actual_cost_microusd > call.cost_limit_microusd):
            issues.add("declared_call_limit_exceeded")
        actual_tokens = None if inp is None or out is None or call.state == "unknown" else inp + out
        unresolved_cost = call.actual_cost_microusd is None or call.state == "unknown"
        if call.state == "unknown": unknown_fields += 1
        if actual_tokens is not None and actual_tokens > call.reserved_tokens: issues.add("reservation_exceeded")
        if call.actual_cost_microusd is not None and call.actual_cost_microusd > call.reserved_cost_microusd: issues.add("reservation_exceeded")
        # Retain each dimension's full reservation when its actual is unavailable.
        amounts["accounted_tokens"] += call.reserved_tokens if actual_tokens is None else actual_tokens
        amounts["unsettled_reserved_tokens"] += call.reserved_tokens if actual_tokens is None else 0
        amounts["accounted_cost_microusd"] += call.reserved_cost_microusd if unresolved_cost else call.actual_cost_microusd
        amounts["unsettled_reserved_cost_microusd"] += call.reserved_cost_microusd if unresolved_cost else 0
    # These are synthetic fixture hard limits only. Real B/task budgets are pending.
    if amounts["accounted_tokens"] > b["per_attempt_tokens_max"] or amounts["reserved_tokens"] > b["per_attempt_tokens_max"]: issues.add("task_token_budget")
    if amounts["accounted_cost_microusd"] > b["per_attempt_cost_microusd_max"] or amounts["reserved_cost_microusd"] > b["per_attempt_cost_microusd_max"]: issues.add("task_cost_budget")
    limits = {"model_wall_ms": t["c_model_wall_ms_max"], "task_wall_ms": t["task_wall_ms_max"], "target_get_requests": t["target_get_requests_max"], "peak_concurrency": t["concurrency_max"], "peak_rate_milli_rps": min(attempt.revision_rate_milli_rps, attempt.platform_rate_milli_rps)}
    for name, maximum in limits.items():
        value = getattr(attempt, name)
        add(name, value)
        if value is not None and value > maximum: issues.add(name + "_budget")
    if attempt.model_wall_ms is not None and attempt.task_wall_ms is not None and attempt.model_wall_ms > attempt.task_wall_ms: raise ContractError("inconsistent_wall_time")
    if attempt.model_wall_ms is not None and sum(c.duration_ms or 0 for c in attempt.calls) > attempt.model_wall_ms: raise ContractError("call_time_exceeds_model_wall")
    if not attempt.calls and attempt.model_wall_ms not in {0, None}: raise ContractError("wall_time_without_calls")
    for name, value in attempt.safety.model_dump().items():
        add("safety_" + name, value)
        if value is not None and value > 0: issues.add("safety_" + name)
    for name, value in attempt.human_time.model_dump().items(): add("human_" + name, value)
    add("unattended_ms", attempt.unattended_ms)
    amounts["unknown_fields"] = unknown_fields
    amounts["known_human_total_ms"] = sum(v for k, v in amounts.items() if k.startswith("human_"))
    amounts["known_operator_ms"] = amounts["known_human_total_ms"] - amounts["human_tool_development_ms"]
    return dict(amounts), issues


def score(result_value, ledger_value, *, root=ROOT):
    freeze, inputs, label_files = load(root)
    result = validate(Results, result_value)
    ledger = validate(Ledger, ledger_value)
    if result.split != ledger.split: raise ContractError("result_ledger_split")
    if result.freeze_digest != digest(freeze) or ledger.freeze_digest != digest(freeze): raise ContractError("freeze_digest")
    cases = {c.case_id: c for c in inputs[result.split].cases}
    labels = {l.case_id: l for l in label_files[result.split].labels}
    selected = result.selected_case_ids
    if len(set(selected)) != len(selected) or set(selected) != set(cases): raise ContractError("selection_must_equal_frozen_split")
    modes = {m.mode: m for m in result.modes}
    if len(modes) != 3 or set(modes) != {"A", "B", "C"}: raise ContractError("mode_set")
    if modes["A"].provider != "none" or any(modes[m].provider != "offline_stub" for m in ("B", "C")): raise ContractError("synthetic_mode_config")
    if modes["B"].model_version != modes["C"].model_version or modes["B"].settings_version != modes["C"].settings_version or modes["A"].rule_version != modes["C"].rule_version:
        raise ContractError("noncomparable_modes")
    expected = {(*cell, cid) for cell in CELLS for cid in cases}
    results = unique_index(result.records, expected)
    observed = unique_index(ledger.attempts, expected)
    all_call_ids = [c.call_id for a in ledger.attempts for c in a.calls]
    if len(set(all_call_ids)) != len(all_call_ids): raise ContractError("duplicate_observed_call")
    issues, cells = set(), []
    for key in sorted(expected):
        row, obs, case = results.get(key), observed.get(key), cases[key[-1]]
        if row:
            if row.input_digest != digest(case): raise ContractError("result_input_digest")
            check_evidence(row, case)
        if obs and obs.input_digest != digest(case): raise ContractError("ledger_input_digest")
        if row and obs and set(row.call_ids) != {c.call_id for c in obs.calls}: raise ContractError("call_ledger_mismatch")
    for cell in CELLS:
        rows = {cid: results[(*cell, cid)] for cid in cases if (*cell, cid) in results}
        cell_issues, totals = set(), Counter()
        missing_observations = 0
        for cid, case in cases.items():
            row, obs, label = rows.get(cid), observed.get((*cell, cid)), labels[cid]
            if row is None: cell_issues.add("missing_result")
            if obs is None:
                cell_issues.add("missing_observer_ledger"); missing_observations += 1
            else:
                amounts, errors = account(obs, freeze)
                peaks = {n: max(totals[n], amounts.get(n, 0)) for n in ("peak_concurrency", "peak_rate_milli_rps")}
                totals.update(amounts)
                for name, value in peaks.items(): totals[name] = value
                cell_issues |= errors
                if label.primary == "uncertain" and obs.target_get_requests:
                    cell_issues.add("unsafe_uncertain_execution")
            if label.sentinel and (row is None or row.prediction != label.expected): cell_issues.add("sentinel_mismatch")
            if label.primary == "uncertain" and row and row.prediction in {"consistent", "potential_bola"}: cell_issues.add("unknown_promoted_to_conclusion")
        supported_ids = [cid for cid in cases if labels[cid].supported]
        all_metrics = metrics(cases, labels, rows)
        supported_metrics = metrics(supported_ids, labels, rows)
        if supported_metrics["precision"] is None or supported_metrics["precision"] < freeze["thresholds"]["supported_precision_min"]: cell_issues.add("precision_threshold")
        if all_metrics["effective_recall"] is None or all_metrics["effective_recall"] < freeze["thresholds"]["effective_recall_min"]: cell_issues.add("effective_recall_threshold")
        tags = sorted({tag for label in labels.values() for tag in label.tags})
        complete = totals["unknown_fields"] == 0 and missing_observations == 0
        cells.append({"mode": cell[0], "cache": cell[1], "trial": cell[2], "all_selected": all_metrics, "supported": supported_metrics,
            "scenarios": {tag: metrics([cid for cid in cases if tag in labels[cid].tags], labels, rows) for tag in tags},
            "accounting": {"complete": complete, "known_totals": dict(sorted(totals.items())),
                "actual_total_tokens": totals["input_tokens"] + totals["output_tokens"] if complete else None,
                "actual_total_cost_microusd": totals["actual_cost_microusd"] if complete else None,
                "missing_observer_attempts": missing_observations}, "failures": sorted(cell_issues)})
        issues |= cell_issues
    comparisons = []
    by_cell = {(c["mode"], c["cache"], c["trial"]): c for c in cells}
    for cache in ("cold", "warm"):
        for trial in (1, 2, 3):
            c = by_cell[("C", cache, trial)]
            for baseline in ("A", "B"):
                other = by_cell[(baseline, "none" if baseline == "A" else cache, trial)]
                precision_pair = (c["supported"]["precision"], other["supported"]["precision"])
                recall_pair = (c["all_selected"]["effective_recall"], other["all_selected"]["effective_recall"])
                complete = not c["failures"] and not other["failures"]
                def less(field):
                    x, y = c["accounting"][field], other["accounting"][field]
                    return None if x is None or y is None else x < y
                comparisons.append({"mode": "C", "baseline": baseline, "cache": cache, "trial": trial,
                    "matched_complete_synthetic_cells": complete,
                    "quality_not_worse": None if None in precision_pair + recall_pair else (precision_pair[0] >= precision_pair[1] and recall_pair[0] >= recall_pair[1]),
                    "tokens_strictly_lower": less("actual_total_tokens"), "cost_strictly_lower": less("actual_total_cost_microusd"),
                    "operator_time_strictly_lower": None if not complete else c["accounting"]["known_totals"]["known_operator_ms"] < other["accounting"]["known_totals"]["known_operator_ms"],
                    "synthetic_arithmetic_only": True, "savings_claim_eligible": False})
    return {"version": result.version, "split": result.split, "freeze_digest": result.freeze_digest,
        "measurement_kind": "synthetic", "evaluation_status": "FAIL" if issues else "PASS_SYNTHETIC_CONTRACT",
        "product_release_gate": "NOT_EVALUATED", "reviewer_approval": "PENDING", "performance_or_savings_claim": "NOT_MEASURED",
        "business_outcomes": {"infrastructure_cost": None, "vendor_result": None, "awarded": None, "paid": None},
        "failures": sorted(issues), "cells": cells, "comparisons": comparisons}
