from collections.abc import Iterable
from dataclasses import dataclass
import ipaddress
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.asset_hostname_rule import AssetHostnameRule
from app.db.models.authorization_revision import AuthorizationRevision


class AssetHostnameRuleError(Exception):
    pass


class AssetHostnameRuleNotFoundError(AssetHostnameRuleError):
    pass


class AssetHostnameRuleImmutableError(AssetHostnameRuleError):
    pass


class AssetHostnameRuleValidationError(AssetHostnameRuleError):
    pass


@dataclass(frozen=True)
class AssetCandidateRuleDecision:
    eligible: bool
    code: str
    normalized_hostname: str | None
    matched_include_rule_id: int | None
    matched_exclude_rule_id: int | None


def _normalize_dns_hostname(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(character.isspace() for character in value)
        or any(character in value for character in "/?#@:\\[]")
    ):
        raise AssetHostnameRuleValidationError
    raw = value[:-1] if value.endswith(".") else value
    if not raw or raw.endswith(".") or "*" in raw:
        raise AssetHostnameRuleValidationError
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        pass
    else:
        raise AssetHostnameRuleValidationError
    try:
        hostname = raw.lower().encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise AssetHostnameRuleValidationError from exc
    labels = hostname.split(".")
    if (
        len(hostname) > 253
        or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or re.fullmatch(r"[a-z0-9-]+", label) is None
            for label in labels
        )
    ):
        raise AssetHostnameRuleValidationError
    return hostname


def normalize_hostname_pattern(rule_type: str, value: str) -> str:
    if rule_type not in {"include", "exclude"} or not isinstance(value, str):
        raise AssetHostnameRuleValidationError
    wildcard = value.startswith("*.")
    if "*" in value and not wildcard:
        raise AssetHostnameRuleValidationError
    if wildcard and value.count("*") != 1:
        raise AssetHostnameRuleValidationError
    if rule_type == "include" and not wildcard:
        raise AssetHostnameRuleValidationError
    suffix = value[2:] if wildcard else value
    normalized = _normalize_dns_hostname(suffix)
    if wildcard and len(normalized.split(".")) < 2:
        raise AssetHostnameRuleValidationError
    return f"*.{normalized}" if wildcard else normalized


def normalize_candidate_hostname(value: str) -> str:
    return _normalize_dns_hostname(value)


def _matches(pattern: str, hostname: str) -> bool:
    if pattern.startswith("*."):
        suffix = pattern[2:]
        return hostname != suffix and hostname.endswith(f".{suffix}")
    return hostname == pattern


def _select_rule(rules: list[AssetHostnameRule]) -> AssetHostnameRule | None:
    if not rules:
        return None
    return max(
        rules,
        key=lambda rule: (
            rule.hostname_pattern.count("."),
            len(rule.hostname_pattern),
            -rule.id,
        ),
    )


def match_asset_candidate(
    *,
    authorization_revision_id: int,
    candidate_hostname: str,
    rules: Iterable[AssetHostnameRule],
) -> AssetCandidateRuleDecision:
    try:
        hostname = normalize_candidate_hostname(candidate_hostname)
    except AssetHostnameRuleValidationError:
        return AssetCandidateRuleDecision(
            False, "asset_candidate_invalid", None, None, None
        )
    relevant = [
        rule
        for rule in rules
        if rule.authorization_revision_id == authorization_revision_id
        and rule.rule_type in {"include", "exclude"}
        and _matches(rule.hostname_pattern, hostname)
    ]
    included = _select_rule([rule for rule in relevant if rule.rule_type == "include"])
    excluded = _select_rule([rule for rule in relevant if rule.rule_type == "exclude"])
    if excluded is not None:
        return AssetCandidateRuleDecision(
            False,
            "asset_candidate_excluded",
            hostname,
            included.id if included is not None else None,
            excluded.id,
        )
    if included is None:
        return AssetCandidateRuleDecision(
            False, "asset_candidate_not_included", hostname, None, None
        )
    return AssetCandidateRuleDecision(
        True, "asset_candidate_included", hostname, included.id, None
    )


def _lock_draft_revision(
    db: Session, profile_id: int, revision_id: int
) -> AuthorizationRevision:
    revision = db.scalar(
        select(AuthorizationRevision)
        .where(
            AuthorizationRevision.id == revision_id,
            AuthorizationRevision.authorization_profile_id == profile_id,
        )
        .with_for_update()
    )
    if revision is None:
        raise AssetHostnameRuleNotFoundError
    if revision.lifecycle_state != "draft":
        raise AssetHostnameRuleImmutableError
    return revision


def create_asset_hostname_rule(
    db: Session,
    *,
    profile_id: int,
    revision_id: int,
    rule_type: str,
    hostname_pattern: str,
) -> AssetHostnameRule:
    _lock_draft_revision(db, profile_id, revision_id)
    rule = AssetHostnameRule(
        authorization_revision_id=revision_id,
        rule_type=rule_type,
        hostname_pattern=normalize_hostname_pattern(rule_type, hostname_pattern),
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def delete_asset_hostname_rule(
    db: Session,
    *,
    profile_id: int,
    revision_id: int,
    rule_id: int,
) -> None:
    _lock_draft_revision(db, profile_id, revision_id)
    rule = db.scalar(
        select(AssetHostnameRule).where(
            AssetHostnameRule.id == rule_id,
            AssetHostnameRule.authorization_revision_id == revision_id,
        )
    )
    if rule is None:
        raise AssetHostnameRuleNotFoundError
    db.delete(rule)
    db.commit()
