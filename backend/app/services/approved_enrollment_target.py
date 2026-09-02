import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.asset_candidate_dns_validation import (
    AssetCandidateDNSValidation,
)
from app.db.models.asset_candidate_evaluation import AssetCandidateEvaluation
from app.db.models.asset_enrollment_decision import AssetEnrollmentDecision
from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.target import Target


class ApprovedEnrollmentTargetNotFoundError(Exception):
    pass


class ApprovedEnrollmentTargetInactiveRevisionError(Exception):
    pass


class ApprovedEnrollmentTargetRejectedError(Exception):
    pass


class ApprovedEnrollmentTargetProvenanceError(Exception):
    pass


class ApprovedEnrollmentTargetDNSOutcomeError(Exception):
    pass


class ApprovedEnrollmentTargetNetworkModeError(Exception):
    pass


class ApprovedEnrollmentTargetConflictError(Exception):
    pass


class ApprovedEnrollmentTargetConfigurationError(Exception):
    pass


_DNS_NETWORK_MODES = {
    "asset_candidate_dns_private_local_only": "private_local",
    "asset_candidate_dns_public_only": "external_public_authorized",
}


def canonical_enrollment_origin(
    *, scheme: str, hostname: str, port: int | None
) -> str:
    default_port = 80 if scheme == "http" else 443
    port_suffix = "" if port is None or port == default_port else f":{port}"
    return f"{scheme}://{hostname}{port_suffix}"


def _equivalent_origin_representations(
    *, scheme: str, hostname: str, port: int | None, canonical: str
) -> tuple[str, ...]:
    variants = {canonical, f"{canonical}/"}
    default_port = 80 if scheme == "http" else 443
    if port is None or port == default_port:
        explicit_default = f"{scheme}://{hostname}:{default_port}"
        variants.update({explicit_default, f"{explicit_default}/"})
    return tuple(sorted(variants))


def create_target_from_approved_enrollment(
    db: Session,
    *,
    profile_id: int,
    revision_id: int,
    evaluation_id: int,
    validation_id: int,
    decision_id: int,
    name: str,
    environment: str,
    scheme: str,
    port: int | None,
    network_mode: str,
) -> Target:
    if scheme not in {"http", "https"}:
        raise ApprovedEnrollmentTargetConfigurationError
    if port is not None and not 1 <= port <= 65535:
        raise ApprovedEnrollmentTargetConfigurationError
    if not 1 <= len(name) <= 120 or not 1 <= len(environment) <= 50:
        raise ApprovedEnrollmentTargetConfigurationError
    if network_mode not in {"private_local", "external_public_authorized"}:
        raise ApprovedEnrollmentTargetConfigurationError
    profile = db.scalar(select(AuthorizationProfile).where(
        AuthorizationProfile.id == profile_id
    ).with_for_update())
    if profile is None:
        raise ApprovedEnrollmentTargetNotFoundError

    revision = db.scalar(select(AuthorizationRevision).where(
        AuthorizationRevision.id == revision_id,
        AuthorizationRevision.authorization_profile_id == profile_id,
    ).with_for_update())
    if revision is None:
        raise ApprovedEnrollmentTargetNotFoundError
    if revision.lifecycle_state != "active":
        raise ApprovedEnrollmentTargetInactiveRevisionError

    evaluation = db.scalar(select(AssetCandidateEvaluation).where(
        AssetCandidateEvaluation.id == evaluation_id,
        AssetCandidateEvaluation.authorization_revision_id == revision_id,
    ))
    validation = db.scalar(select(AssetCandidateDNSValidation).where(
        AssetCandidateDNSValidation.id == validation_id,
        AssetCandidateDNSValidation.asset_candidate_evaluation_id == evaluation_id,
        AssetCandidateDNSValidation.authorization_revision_id == revision_id,
    ))
    decision = db.scalar(select(AssetEnrollmentDecision).where(
        AssetEnrollmentDecision.id == decision_id,
        AssetEnrollmentDecision.asset_candidate_dns_validation_id == validation_id,
        AssetEnrollmentDecision.authorization_revision_id == revision_id,
    ))
    if evaluation is None or validation is None or decision is None:
        raise ApprovedEnrollmentTargetNotFoundError
    if decision.decision != "approved":
        raise ApprovedEnrollmentTargetRejectedError
    if not (
        decision.normalized_hostname
        == validation.normalized_hostname
        == evaluation.normalized_hostname
    ):
        raise ApprovedEnrollmentTargetProvenanceError

    required_mode = _DNS_NETWORK_MODES.get(validation.decision_code)
    if required_mode is None:
        raise ApprovedEnrollmentTargetDNSOutcomeError
    if network_mode != required_mode:
        raise ApprovedEnrollmentTargetNetworkModeError

    base_url = canonical_enrollment_origin(
        scheme=scheme,
        hostname=decision.normalized_hostname,
        port=port,
    )
    origin_lock_key = int.from_bytes(
        hashlib.sha256(base_url.encode("ascii")).digest()[:8],
        byteorder="big",
        signed=True,
    )
    db.scalar(select(func.pg_advisory_xact_lock(origin_lock_key)))
    existing = db.scalar(select(Target).where(
        Target.asset_enrollment_decision_id == decision_id
    ))
    requested_state = (
        name,
        environment,
        base_url,
        network_mode,
        profile_id,
        revision_id,
    )
    if existing is not None:
        existing_state = (
            existing.name,
            existing.environment,
            existing.base_url,
            existing.network_mode,
            existing.authorization_profile_id,
            existing.authorization_revision_id,
        )
        if existing_state == requested_state:
            db.commit()
            return existing
        raise ApprovedEnrollmentTargetConflictError

    origin_owner = db.scalar(select(Target.id).where(Target.base_url.in_(
        _equivalent_origin_representations(
            scheme=scheme,
            hostname=decision.normalized_hostname,
            port=port,
            canonical=base_url,
        )
    )).limit(1))
    if origin_owner is not None:
        raise ApprovedEnrollmentTargetConflictError

    target = Target(
        asset_enrollment_decision_id=decision_id,
        authorization_profile_id=profile_id,
        authorization_revision_id=revision_id,
        name=name,
        base_url=base_url,
        environment=environment,
        network_mode=network_mode,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return target
