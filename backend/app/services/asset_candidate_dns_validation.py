from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.asset_candidate_dns_validation import (
    AssetCandidateDNSAddress,
    AssetCandidateDNSCNAMEHop,
    AssetCandidateDNSValidation,
)
from app.db.models.asset_candidate_evaluation import AssetCandidateEvaluation
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.session import SessionLocal
from app.services.asset_candidate_dns import (
    AssetCandidateDNSResolver,
    classify_asset_candidate_dns,
)
from app.services.authorization_revision import lock_profile


class AssetCandidateDNSValidationError(Exception):
    pass


class AssetCandidateDNSValidationNotFoundError(AssetCandidateDNSValidationError):
    pass


class AssetCandidateDNSValidationInactiveError(AssetCandidateDNSValidationError):
    pass


class AssetCandidateDNSValidationIneligibleError(AssetCandidateDNSValidationError):
    pass


@dataclass(frozen=True)
class PersistedAssetCandidateDNSValidation:
    validation: AssetCandidateDNSValidation
    cname_hops: tuple[AssetCandidateDNSCNAMEHop, ...]
    addresses: tuple[AssetCandidateDNSAddress, ...]


def _load_exact(
    db: Session, *, profile_id: int, revision_id: int, evaluation_id: int,
    lock: bool = False,
) -> tuple[AuthorizationRevision, AssetCandidateEvaluation]:
    revision_statement = select(AuthorizationRevision).where(
        AuthorizationRevision.id == revision_id,
        AuthorizationRevision.authorization_profile_id == profile_id,
    )
    if lock:
        revision_statement = revision_statement.with_for_update()
    revision = db.scalar(revision_statement)
    evaluation = db.scalar(select(AssetCandidateEvaluation).where(
        AssetCandidateEvaluation.id == evaluation_id,
        AssetCandidateEvaluation.authorization_revision_id == revision_id,
    ))
    if revision is None or evaluation is None:
        raise AssetCandidateDNSValidationNotFoundError
    if revision.lifecycle_state != "active":
        raise AssetCandidateDNSValidationInactiveError
    if (
        evaluation.decision_code != "asset_candidate_included"
        or not evaluation.normalized_hostname
    ):
        raise AssetCandidateDNSValidationIneligibleError
    return revision, evaluation


def create_asset_candidate_dns_validation(
    *,
    profile_id: int,
    revision_id: int,
    evaluation_id: int,
    resolver: AssetCandidateDNSResolver,
    session_factory: sessionmaker[Session] = SessionLocal,
    before_final_revalidation: Callable[[], None] | None = None,
) -> PersistedAssetCandidateDNSValidation:
    # Phase one owns and closes its connection before DNS network I/O.
    with session_factory() as initial_db:
        _, evaluation = _load_exact(
            initial_db,
            profile_id=profile_id,
            revision_id=revision_id,
            evaluation_id=evaluation_id,
        )
        captured_hostname = evaluation.normalized_hostname

    decision = classify_asset_candidate_dns(
        captured_hostname, resolver=resolver
    )
    if before_final_revalidation is not None:
        before_final_revalidation()

    # Phase two follows lifecycle lock order and commits parent/children once.
    with session_factory() as final_db:
        if lock_profile(final_db, profile_id) is None:
            raise AssetCandidateDNSValidationNotFoundError
        _, evaluation = _load_exact(
            final_db,
            profile_id=profile_id,
            revision_id=revision_id,
            evaluation_id=evaluation_id,
            lock=True,
        )
        if evaluation.normalized_hostname != captured_hostname:
            raise AssetCandidateDNSValidationIneligibleError
        if decision.normalized_hostname != captured_hostname:
            raise AssetCandidateDNSValidationIneligibleError

        validation = AssetCandidateDNSValidation(
            asset_candidate_evaluation_id=evaluation_id,
            authorization_revision_id=revision_id,
            decision_code=decision.code,
            normalized_hostname=captured_hostname,
            terminal_hostname=decision.terminal_hostname,
        )
        final_db.add(validation)
        final_db.flush()
        cname_hops = tuple(
            AssetCandidateDNSCNAMEHop(
                dns_validation_id=validation.id,
                ordinal=ordinal,
                hostname=hostname,
            )
            for ordinal, hostname in enumerate(decision.cname_chain, start=1)
        )
        addresses = tuple(
            AssetCandidateDNSAddress(
                dns_validation_id=validation.id,
                ordinal=ordinal,
                address=address,
                category=category.value,
            )
            for ordinal, (address, category) in enumerate(
                zip(
                    decision.resolved_addresses,
                    decision.address_categories,
                    strict=True,
                ),
                start=1,
            )
        )
        final_db.add_all((*cname_hops, *addresses))
        final_db.commit()
        final_db.refresh(validation)
        return PersistedAssetCandidateDNSValidation(
            validation=validation,
            cname_hops=cname_hops,
            addresses=addresses,
        )
