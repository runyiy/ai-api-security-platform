"""Integrity checks and exact single-case projection. No retrieval or runtime I/O."""
from collections import Counter
from hashlib import sha256
from pathlib import Path

from .contracts import SYNTHETIC_BUDGET, THRESHOLDS, ContractError, InputFile, LabelFile, canonical, read_json, schema_bundle, validate

ROOT = Path(__file__).resolve().parent


def digest(value):
    return sha256(canonical(value)).hexdigest()


def structural_fingerprint(case):
    """Catch renamed copies across splits, including changed IDs/time/scalar text.

Conservative structural duplicate check, not a proof of semantic independence.
Manual review of policies and fixture layouts remains required.
"""
    value = case.model_dump()
    for key in ("case_id", "version", "project_id", "evaluation_time", "resource", "selected_actor", "baseline_actor"):
        value.pop(key)
    def shape(obj, key=""):
        if isinstance(obj, dict):
            return {k: shape(v, k) for k, v in obj.items() if k not in {"fact_id", "evidence_id", "actor", "resource", "revision", "asserted_at", "valid_from", "valid_until", "basis"}}
        if isinstance(obj, list): return [shape(x, key) for x in obj]
        if key in {"relationship", "expected_access", "provenance", "verification", "method", "location", "auth_type", "session", "status", "slots", "complete"}:
            return obj
        if isinstance(obj, str): return "text" if obj else ""
        return obj
    return digest(shape(value))


def check_splits(inputs, labels):
    seen_ids, seen_shapes = set(), set()
    for split in ("development", "heldout"):
        cases = inputs[split].cases
        annotations = labels[split].labels
        ids = [c.case_id for c in cases]
        label_ids = [l.case_id for l in annotations]
        if len(set(ids)) != len(ids) or len(set(label_ids)) != len(label_ids) or set(ids) != set(label_ids):
            raise ContractError("corpus_ids")
        if set(ids) & seen_ids:
            raise ContractError("split_id_leakage")
        shapes = {structural_fingerprint(c) for c in cases}
        if shapes & seen_shapes:
            raise ContractError("split_near_duplicate")
        seen_shapes |= shapes
        seen_ids |= set(ids)
        expected_count = 8 if split == "development" else 32
        if Counter(l.primary for l in annotations) != {"positive": expected_count, "negative": expected_count, "uncertain": expected_count}:
            raise ContractError("primary_distribution")
        for label in annotations:
            if (label.primary == "positive" and label.expected != "potential_bola") or (label.primary == "negative" and label.expected != "consistent") or (label.primary == "uncertain" and label.expected in {"potential_bola", "consistent"}):
                raise ContractError("label_category")


def load(root=ROOT):
    freeze = read_json(root / "data/freeze.json")
    if freeze.get("thresholds") != THRESHOLDS or freeze.get("synthetic_fixture_budget") != SYNTHETIC_BUDGET:
        raise ContractError("threshold_drift")
    expected_files = {"data/development.json", "data/heldout.json", "_oracle/development-labels.json", "_oracle/heldout-labels.json", "data/contracts.schema.json"}
    if set(freeze.get("files", {})) != expected_files:
        raise ContractError("freeze_file_set")
    raw = {}
    for name, expected in freeze["files"].items():
        value = read_json(root / name)
        # Canonical bytes are part of the freeze; whitespace changes also fail.
        if sha256((root / name).read_bytes()).hexdigest() != expected or canonical(value) != (root / name).read_bytes():
            raise ContractError("manifest_hash_mismatch")
        raw[name] = value
    if raw["data/contracts.schema.json"] != schema_bundle():
        raise ContractError("schema_drift")
    inputs = {s: validate(InputFile, raw[f"data/{s}.json"]) for s in ("development", "heldout")}
    labels = {s: validate(LabelFile, raw[f"_oracle/{s}-labels.json"]) for s in inputs}
    if any(inputs[s].split != s or labels[s].split != s for s in inputs):
        raise ContractError("split_mismatch")
    check_splits(inputs, labels)
    if freeze["case_hashes"] != {s: {c.case_id: digest(c) for c in inputs[s].cases} for s in inputs}:
        raise ContractError("case_hash_mismatch")
    if freeze["counts"] != {s: dict(Counter(l.primary for l in labels[s].labels)) for s in inputs}:
        raise ContractError("freeze_counts")
    return freeze, inputs, labels


def mode_input(split, case_id, *, purpose, root=ROOT):
    """A single eligible current case, never labels or other cases' evidence.

This file boundary is a harness contract, not an OS security sandbox. A future
runner must keep evaluator files inaccessible to models/retrieval tools.
"""
    if purpose not in {"evaluation", "development"} or split not in {"development", "heldout"}:
        raise ContractError("projection_purpose")
    if split == "heldout" and purpose != "evaluation":
        raise ContractError("heldout_use_denied")
    # Critically this API never opens the oracle or imports its authoring module.
    manifest = validate(InputFile, read_json(root / f"data/{split}.json"))
    found = [c for c in manifest.cases if c.case_id == case_id]
    if len(found) != 1:
        raise ContractError("unexpected_case")
    freeze = read_json(root / "data/freeze.json")
    if digest(found[0]) != freeze["case_hashes"][split].get(case_id):
        raise ContractError("projection_hash_mismatch")
    return found[0].model_dump(mode="json")
