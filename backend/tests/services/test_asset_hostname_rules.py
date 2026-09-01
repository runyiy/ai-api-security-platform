from types import SimpleNamespace
import socket

import pytest
from sqlalchemy import func, select

from app.db.models.execution_plan import ExecutionPlan
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.db.models.test_run import TestRun
from app.db.session import SessionLocal
from app.network_safety.gateway import NetworkGateway
from app.services.asset_hostname_rule import (
    AssetHostnameRuleValidationError,
    match_asset_candidate,
    normalize_hostname_pattern,
)


@pytest.mark.parametrize(
    ("rule_type", "value", "expected"),
    [
        ("include", "*.EXAMPLE.TEST.", "*.example.test"),
        ("include", "*.BÜCHER.TEST", "*.xn--bcher-kva.test"),
        ("exclude", "ADMIN.EXAMPLE.TEST.", "admin.example.test"),
        ("exclude", "*.INTERNAL.EXAMPLE.TEST", "*.internal.example.test"),
    ],
)
def test_hostname_pattern_normalization(rule_type, value, expected) -> None:
    assert normalize_hostname_pattern(rule_type, value) == expected


@pytest.mark.parametrize(
    ("rule_type", "value"),
    [
        ("include", "*"),
        ("include", "*.com"),
        ("include", "example.test"),
        ("include", "api.*.example.test"),
        ("include", "*example.test"),
        ("exclude", "https://example.test"),
        ("exclude", "user@example.test"),
        ("exclude", "example.test:443"),
        ("exclude", "example.test/path"),
        ("exclude", "127.0.0.1"),
        ("exclude", "::1"),
        ("exclude", "bad label.example.test"),
        ("exclude", "-bad.example.test"),
        ("exclude", f"{'a' * 64}.example.test"),
        ("exclude", f"{'a.' * 126}aa"),
    ],
)
def test_invalid_hostname_patterns_fail_closed(rule_type, value) -> None:
    with pytest.raises(AssetHostnameRuleValidationError):
        normalize_hostname_pattern(rule_type, value)


def rule(rule_id: int, revision_id: int, rule_type: str, pattern: str):
    return SimpleNamespace(
        id=rule_id,
        authorization_revision_id=revision_id,
        rule_type=rule_type,
        hostname_pattern=pattern,
    )


@pytest.mark.parametrize(
    ("hostname", "eligible", "code"),
    [
        ("api.example.test", True, "asset_candidate_included"),
        ("v2.api.example.test", True, "asset_candidate_included"),
        ("example.test", False, "asset_candidate_not_included"),
        ("notexample.test", False, "asset_candidate_not_included"),
        ("bad host", False, "asset_candidate_invalid"),
    ],
)
def test_wildcard_boundary_semantics(hostname, eligible, code) -> None:
    decision = match_asset_candidate(
        authorization_revision_id=10,
        candidate_hostname=hostname,
        rules=[rule(1, 10, "include", "*.example.test")],
    )
    assert decision.eligible is eligible
    assert decision.code == code


def test_exclusion_overrides_include_and_matching_is_order_independent() -> None:
    rules = [
        rule(7, 10, "exclude", "*.internal.example.test"),
        rule(2, 10, "include", "*.example.test"),
        rule(8, 10, "exclude", "admin.internal.example.test"),
        rule(4, 10, "exclude", "admin.internal.example.test"),
    ]
    for ordered in (rules, list(reversed(rules))):
        decision = match_asset_candidate(
            authorization_revision_id=10,
            candidate_hostname="admin.internal.example.test",
            rules=ordered,
        )
        assert decision.eligible is False
        assert decision.code == "asset_candidate_excluded"
        assert decision.matched_include_rule_id == 2
        assert decision.matched_exclude_rule_id == 4


def test_matcher_filters_exact_revision_and_uses_specificity_then_lowest_id() -> None:
    rules = [
        rule(9, 10, "include", "*.example.test"),
        rule(5, 10, "include", "*.api.example.test"),
        rule(3, 10, "include", "*.api.example.test"),
        rule(1, 11, "exclude", "v2.api.example.test"),
    ]
    decision = match_asset_candidate(
        authorization_revision_id=10,
        candidate_hostname="v2.api.example.test",
        rules=rules,
    )
    assert decision.eligible is True
    assert decision.matched_include_rule_id == 3
    assert decision.matched_exclude_rule_id is None


def test_exact_and_wildcard_exclusions_have_strict_semantics() -> None:
    rules = [
        rule(1, 10, "include", "*.example.test"),
        rule(2, 10, "exclude", "admin.example.test"),
        rule(3, 10, "exclude", "*.internal.example.test"),
    ]
    assert match_asset_candidate(
        authorization_revision_id=10,
        candidate_hostname="admin.example.test",
        rules=rules,
    ).matched_exclude_rule_id == 2
    assert match_asset_candidate(
        authorization_revision_id=10,
        candidate_hostname="v2.admin.example.test",
        rules=rules,
    ).eligible is True
    assert match_asset_candidate(
        authorization_revision_id=10,
        candidate_hostname="internal.example.test",
        rules=rules,
    ).eligible is True
    assert match_asset_candidate(
        authorization_revision_id=10,
        candidate_hostname="api.internal.example.test",
        rules=rules,
    ).matched_exclude_rule_id == 3


def test_matcher_has_zero_network_dns_or_persistence_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = (Target, Scope, ExecutionPlan, TestRun)
    with SessionLocal() as db:
        before = {
            model: db.scalar(select(func.count()).select_from(model))
            for model in models
        }
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: pytest.fail("matcher performed DNS resolution"),
    )
    monkeypatch.setattr(
        NetworkGateway,
        "request",
        lambda *args, **kwargs: pytest.fail("matcher called NetworkGateway"),
    )
    decision = match_asset_candidate(
        authorization_revision_id=10,
        candidate_hostname="api.example.test",
        rules=[rule(1, 10, "include", "*.example.test")],
    )
    monkeypatch.undo()
    with SessionLocal() as db:
        after = {
            model: db.scalar(select(func.count()).select_from(model))
            for model in models
        }
    assert decision.code == "asset_candidate_included"
    assert after == before
