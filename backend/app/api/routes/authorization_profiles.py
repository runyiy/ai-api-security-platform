from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.asset_candidate_evaluation import AssetCandidateEvaluation
from app.db.models.asset_candidate_dns_validation import (
    AssetCandidateDNSAddress,
    AssetCandidateDNSCNAMEHop,
    AssetCandidateDNSValidation,
)
from app.db.models.asset_hostname_rule import AssetHostnameRule
from app.db.models.asset_enrollment_decision import AssetEnrollmentDecision
from app.db.session import get_db
from app.schemas.authorization_profile import (
    AuthorizationProfileCreate,
    AuthorizationProfileRead,
    AuthorizationProfileUpdate,
)
from app.schemas.authorization_revision import AuthorizationRevisionRead
from app.schemas.asset_hostname_rule import (
    AssetHostnameRuleCreate,
    AssetHostnameRuleRead,
)
from app.schemas.asset_candidate_evaluation import (
    AssetCandidateEvaluationCreate,
    AssetCandidateEvaluationRead,
)
from app.schemas.asset_candidate_dns_validation import (
    AssetCandidateDNSAddressRead,
    AssetCandidateDNSCNAMEHopRead,
    AssetCandidateDNSValidationCreate,
    AssetCandidateDNSValidationRead,
    AssetCandidateDNSValidationSummary,
)
from app.schemas.asset_enrollment_decision import (
    AssetEnrollmentDecisionCreate,
    AssetEnrollmentDecisionRead,
)
from app.services.asset_candidate_dns import (
    AssetCandidateDNSResolver,
    DnspythonAssetCandidateDNSResolver,
)
from app.services.asset_candidate_dns_validation import (
    AssetCandidateDNSValidationInactiveError,
    AssetCandidateDNSValidationIneligibleError,
    AssetCandidateDNSValidationNotFoundError,
    PersistedAssetCandidateDNSValidation,
    create_asset_candidate_dns_validation,
)
from app.services.asset_enrollment_decision import (
    AssetEnrollmentDecisionNotFoundError,
    AssetEnrollmentDecisionProvenanceError,
    create_asset_enrollment_decision,
    load_exact_dns_validation,
)
from app.services.asset_candidate_evaluation import (
    AssetCandidateEvaluationInactiveError,
    AssetCandidateEvaluationInvalidError,
    AssetCandidateEvaluationNotFoundError,
    create_asset_candidate_evaluation,
)
from app.services.asset_hostname_rule import (
    AssetHostnameRuleImmutableError,
    AssetHostnameRuleNotFoundError,
    AssetHostnameRuleValidationError,
    create_asset_hostname_rule,
    delete_asset_hostname_rule,
)
from app.services.authorization_revision import (
    InvalidRevisionTransitionError,
    RevisionNotFoundError,
    create_revision,
    transition_revision,
)


router = APIRouter(
    prefix="/authorization-profiles",
    tags=["authorization-profiles"],
)

MAX_ASSET_CANDIDATE_EVALUATION_PAGE_SIZE = 100
MAX_ASSET_CANDIDATE_DNS_VALIDATION_PAGE_SIZE = 100
MAX_ASSET_ENROLLMENT_DECISION_PAGE_SIZE = 100


def get_asset_candidate_dns_resolver() -> AssetCandidateDNSResolver:
    return DnspythonAssetCandidateDNSResolver()


WRITABLE_PROFILE_FIELDS = tuple(
    AuthorizationProfileCreate.model_fields
)


def get_profile_or_404(
    *,
    db: Session,
    profile_id: int,
) -> AuthorizationProfile:
    profile = db.get(
        AuthorizationProfile,
        profile_id,
    )

    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AuthorizationProfile not found.",
        )

    return profile


@router.post(
    "",
    response_model=AuthorizationProfileRead,
    status_code=status.HTTP_201_CREATED,
)
def create_authorization_profile(
    payload: AuthorizationProfileCreate,
    db: Session = Depends(get_db),
) -> AuthorizationProfile:
    profile = AuthorizationProfile(
        **payload.model_dump()
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    return profile


@router.get(
    "",
    response_model=list[AuthorizationProfileRead],
)
def list_authorization_profiles(
    db: Session = Depends(get_db),
) -> list[AuthorizationProfile]:
    return list(
        db.scalars(
            select(AuthorizationProfile).order_by(
                AuthorizationProfile.id
            )
        ).all()
    )


@router.get(
    "/{profile_id}",
    response_model=AuthorizationProfileRead,
)
def get_authorization_profile(
    profile_id: int,
    db: Session = Depends(get_db),
) -> AuthorizationProfile:
    return get_profile_or_404(
        db=db,
        profile_id=profile_id,
    )


@router.patch(
    "/{profile_id}",
    response_model=AuthorizationProfileRead,
)
def update_authorization_profile(
    profile_id: int,
    payload: AuthorizationProfileUpdate,
    db: Session = Depends(get_db),
) -> AuthorizationProfile:
    profile = db.scalar(
        select(AuthorizationProfile)
        .where(AuthorizationProfile.id == profile_id)
        .with_for_update()
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="AuthorizationProfile not found.")
    supplied_values = payload.model_dump(
        exclude_unset=True
    )
    merged_values = {
        field_name: getattr(profile, field_name)
        for field_name in WRITABLE_PROFILE_FIELDS
    }
    merged_values.update(supplied_values)

    try:
        validated_state = (
            AuthorizationProfileCreate.model_validate(
                merged_values
            )
        )
    except ValidationError as exc:
        raise RequestValidationError(
            exc.errors()
        ) from exc

    normalized_values = validated_state.model_dump()

    for field_name in supplied_values:
        setattr(
            profile,
            field_name,
            normalized_values[field_name],
        )

    db.commit()
    db.refresh(profile)

    return profile


@router.delete(
    "/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_authorization_profile(
    profile_id: int,
    db: Session = Depends(get_db),
) -> Response:
    profile = get_profile_or_404(
        db=db,
        profile_id=profile_id,
    )
    db.delete(profile)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "AuthorizationProfile is referenced by retained "
                "revision history or one or more Targets."
            ),
        ) from exc

    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )


@router.post(
    "/{profile_id}/revisions",
    response_model=AuthorizationRevisionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_authorization_revision(
    profile_id: int,
    db: Session = Depends(get_db),
) -> AuthorizationRevision:
    try:
        return create_revision(db, profile_id)
    except RevisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="AuthorizationProfile not found.") from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Revision creation conflict.") from exc


@router.get(
    "/{profile_id}/revisions",
    response_model=list[AuthorizationRevisionRead],
)
def list_authorization_revisions(
    profile_id: int,
    db: Session = Depends(get_db),
) -> list[AuthorizationRevision]:
    get_profile_or_404(db=db, profile_id=profile_id)
    return list(db.scalars(
        select(AuthorizationRevision)
        .where(AuthorizationRevision.authorization_profile_id == profile_id)
        .order_by(AuthorizationRevision.revision_number)
    ).all())


@router.get(
    "/{profile_id}/revisions/{revision_id}",
    response_model=AuthorizationRevisionRead,
)
def get_authorization_revision(
    profile_id: int,
    revision_id: int,
    db: Session = Depends(get_db),
) -> AuthorizationRevision:
    revision = db.scalar(select(AuthorizationRevision).where(
        AuthorizationRevision.id == revision_id,
        AuthorizationRevision.authorization_profile_id == profile_id,
    ))
    if revision is None:
        raise HTTPException(status_code=404, detail="AuthorizationRevision not found.")
    return revision


def apply_revision_transition(
    profile_id: int,
    revision_id: int,
    destination: str,
    db: Session,
) -> AuthorizationRevision:
    try:
        return transition_revision(db, profile_id, revision_id, destination)
    except RevisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="AuthorizationRevision not found.") from exc
    except InvalidRevisionTransitionError as exc:
        raise HTTPException(status_code=409, detail="Invalid revision lifecycle transition.") from exc


@router.post(
    "/{profile_id}/revisions/{revision_id}/activate",
    response_model=AuthorizationRevisionRead,
)
def activate_authorization_revision(
    profile_id: int,
    revision_id: int,
    db: Session = Depends(get_db),
) -> AuthorizationRevision:
    return apply_revision_transition(profile_id, revision_id, "active", db)


@router.post(
    "/{profile_id}/revisions/{revision_id}/revoke",
    response_model=AuthorizationRevisionRead,
)
def revoke_authorization_revision(
    profile_id: int,
    revision_id: int,
    db: Session = Depends(get_db),
) -> AuthorizationRevision:
    return apply_revision_transition(profile_id, revision_id, "revoked", db)


def _asset_rule_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AssetHostnameRuleNotFoundError):
        return HTTPException(status_code=404, detail="Asset hostname rule not found.")
    if isinstance(exc, AssetHostnameRuleImmutableError):
        return HTTPException(
            status_code=409,
            detail="Asset hostname rules are immutable for this revision.",
        )
    return HTTPException(status_code=422, detail="Asset hostname rule is invalid.")


@router.post(
    "/{profile_id}/revisions/{revision_id}/asset-hostname-rules",
    response_model=AssetHostnameRuleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_revision_asset_hostname_rule(
    profile_id: int,
    revision_id: int,
    payload: AssetHostnameRuleCreate,
    db: Session = Depends(get_db),
) -> AssetHostnameRule:
    try:
        return create_asset_hostname_rule(
            db,
            profile_id=profile_id,
            revision_id=revision_id,
            **payload.model_dump(),
        )
    except (
        AssetHostnameRuleNotFoundError,
        AssetHostnameRuleImmutableError,
        AssetHostnameRuleValidationError,
    ) as exc:
        db.rollback()
        raise _asset_rule_error(exc) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Asset hostname rule already exists.",
        ) from exc


@router.get(
    "/{profile_id}/revisions/{revision_id}/asset-hostname-rules",
    response_model=list[AssetHostnameRuleRead],
)
def list_revision_asset_hostname_rules(
    profile_id: int,
    revision_id: int,
    db: Session = Depends(get_db),
) -> list[AssetHostnameRule]:
    revision = db.scalar(select(AuthorizationRevision).where(
        AuthorizationRevision.id == revision_id,
        AuthorizationRevision.authorization_profile_id == profile_id,
    ))
    if revision is None:
        raise HTTPException(status_code=404, detail="AuthorizationRevision not found.")
    return list(db.scalars(select(AssetHostnameRule).where(
        AssetHostnameRule.authorization_revision_id == revision_id
    ).order_by(AssetHostnameRule.id)).all())


@router.delete(
    "/{profile_id}/revisions/{revision_id}/asset-hostname-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_revision_asset_hostname_rule(
    profile_id: int,
    revision_id: int,
    rule_id: int,
    db: Session = Depends(get_db),
) -> Response:
    try:
        delete_asset_hostname_rule(
            db,
            profile_id=profile_id,
            revision_id=revision_id,
            rule_id=rule_id,
        )
    except (AssetHostnameRuleNotFoundError, AssetHostnameRuleImmutableError) as exc:
        db.rollback()
        raise _asset_rule_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _evaluation_revision_or_404(
    db: Session, profile_id: int, revision_id: int
) -> AuthorizationRevision:
    revision = db.scalar(select(AuthorizationRevision).where(
        AuthorizationRevision.id == revision_id,
        AuthorizationRevision.authorization_profile_id == profile_id,
    ))
    if revision is None:
        raise HTTPException(status_code=404, detail="AuthorizationRevision not found.")
    return revision


@router.post(
    "/{profile_id}/revisions/{revision_id}/asset-candidate-evaluations",
    response_model=AssetCandidateEvaluationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_revision_asset_candidate_evaluation(
    profile_id: int,
    revision_id: int,
    payload: AssetCandidateEvaluationCreate,
    db: Session = Depends(get_db),
) -> AssetCandidateEvaluation:
    try:
        return create_asset_candidate_evaluation(
            db,
            profile_id=profile_id,
            revision_id=revision_id,
            hostname=payload.hostname,
        )
    except AssetCandidateEvaluationNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=404, detail="AuthorizationRevision not found."
        ) from exc
    except AssetCandidateEvaluationInactiveError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Asset candidates require an active authorization revision.",
        ) from exc
    except AssetCandidateEvaluationInvalidError as exc:
        db.rollback()
        raise HTTPException(
            status_code=422, detail="Asset candidate hostname is invalid."
        ) from exc
    except IntegrityError:
        db.rollback()
        raise


@router.get(
    "/{profile_id}/revisions/{revision_id}/asset-candidate-evaluations",
    response_model=list[AssetCandidateEvaluationRead],
)
def list_revision_asset_candidate_evaluations(
    profile_id: int,
    revision_id: int,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(
        default=50,
        ge=1,
        le=MAX_ASSET_CANDIDATE_EVALUATION_PAGE_SIZE,
    ),
    db: Session = Depends(get_db),
) -> list[AssetCandidateEvaluation]:
    _evaluation_revision_or_404(db, profile_id, revision_id)
    return list(db.scalars(
        select(AssetCandidateEvaluation)
        .where(
            AssetCandidateEvaluation.authorization_revision_id == revision_id,
            AssetCandidateEvaluation.id > after_id,
        )
        .order_by(AssetCandidateEvaluation.id)
        .limit(limit)
    ).all())


@router.get(
    "/{profile_id}/revisions/{revision_id}/asset-candidate-evaluations/"
    "{evaluation_id}",
    response_model=AssetCandidateEvaluationRead,
)
def get_revision_asset_candidate_evaluation(
    profile_id: int,
    revision_id: int,
    evaluation_id: int,
    db: Session = Depends(get_db),
) -> AssetCandidateEvaluation:
    _evaluation_revision_or_404(db, profile_id, revision_id)
    evaluation = db.scalar(select(AssetCandidateEvaluation).where(
        AssetCandidateEvaluation.id == evaluation_id,
        AssetCandidateEvaluation.authorization_revision_id == revision_id,
    ))
    if evaluation is None:
        raise HTTPException(
            status_code=404, detail="Asset candidate evaluation not found."
        )
    return evaluation


def _dns_evaluation_or_404(
    db: Session, profile_id: int, revision_id: int, evaluation_id: int
) -> AssetCandidateEvaluation:
    revision = db.scalar(select(AuthorizationRevision.id).where(
        AuthorizationRevision.id == revision_id,
        AuthorizationRevision.authorization_profile_id == profile_id,
    ))
    evaluation = db.scalar(select(AssetCandidateEvaluation).where(
        AssetCandidateEvaluation.id == evaluation_id,
        AssetCandidateEvaluation.authorization_revision_id == revision_id,
    ))
    if revision is None or evaluation is None:
        raise HTTPException(
            status_code=404, detail="Asset candidate evaluation not found."
        )
    return evaluation


def _dns_validation_response(
    persisted: PersistedAssetCandidateDNSValidation,
) -> AssetCandidateDNSValidationRead:
    validation = persisted.validation
    return AssetCandidateDNSValidationRead(
        id=validation.id,
        asset_candidate_evaluation_id=validation.asset_candidate_evaluation_id,
        authorization_revision_id=validation.authorization_revision_id,
        decision_code=validation.decision_code,
        normalized_hostname=validation.normalized_hostname,
        terminal_hostname=validation.terminal_hostname,
        created_at=validation.created_at,
        cname_chain=[
            AssetCandidateDNSCNAMEHopRead(
                ordinal=hop.ordinal, hostname=hop.hostname
            )
            for hop in persisted.cname_hops
        ],
        addresses=[
            AssetCandidateDNSAddressRead(
                ordinal=address.ordinal,
                address=address.address,
                category=address.category,
            )
            for address in persisted.addresses
        ],
    )


@router.post(
    "/{profile_id}/revisions/{revision_id}/asset-candidate-evaluations/"
    "{evaluation_id}/dns-validations",
    response_model=AssetCandidateDNSValidationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_revision_asset_candidate_dns_validation(
    profile_id: int,
    revision_id: int,
    evaluation_id: int,
    payload: AssetCandidateDNSValidationCreate | None = Body(default=None),
    resolver: AssetCandidateDNSResolver = Depends(
        get_asset_candidate_dns_resolver
    ),
) -> AssetCandidateDNSValidationRead:
    del payload
    try:
        persisted = create_asset_candidate_dns_validation(
            profile_id=profile_id,
            revision_id=revision_id,
            evaluation_id=evaluation_id,
            resolver=resolver,
        )
    except AssetCandidateDNSValidationNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Asset candidate evaluation not found."
        ) from exc
    except AssetCandidateDNSValidationInactiveError as exc:
        raise HTTPException(
            status_code=409,
            detail="DNS validation requires an active authorization revision.",
        ) from exc
    except AssetCandidateDNSValidationIneligibleError as exc:
        raise HTTPException(
            status_code=409,
            detail="DNS validation requires an included asset candidate.",
        ) from exc
    return _dns_validation_response(persisted)


@router.get(
    "/{profile_id}/revisions/{revision_id}/asset-candidate-evaluations/"
    "{evaluation_id}/dns-validations",
    response_model=list[AssetCandidateDNSValidationSummary],
)
def list_revision_asset_candidate_dns_validations(
    profile_id: int,
    revision_id: int,
    evaluation_id: int,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(
        default=50,
        ge=1,
        le=MAX_ASSET_CANDIDATE_DNS_VALIDATION_PAGE_SIZE,
    ),
    db: Session = Depends(get_db),
) -> list[AssetCandidateDNSValidation]:
    _dns_evaluation_or_404(db, profile_id, revision_id, evaluation_id)
    return list(db.scalars(
        select(AssetCandidateDNSValidation).where(
            AssetCandidateDNSValidation.asset_candidate_evaluation_id
            == evaluation_id,
            AssetCandidateDNSValidation.authorization_revision_id == revision_id,
            AssetCandidateDNSValidation.id > after_id,
        ).order_by(AssetCandidateDNSValidation.id).limit(limit)
    ).all())


@router.get(
    "/{profile_id}/revisions/{revision_id}/asset-candidate-evaluations/"
    "{evaluation_id}/dns-validations/{validation_id}",
    response_model=AssetCandidateDNSValidationRead,
)
def get_revision_asset_candidate_dns_validation(
    profile_id: int,
    revision_id: int,
    evaluation_id: int,
    validation_id: int,
    db: Session = Depends(get_db),
) -> AssetCandidateDNSValidationRead:
    _dns_evaluation_or_404(db, profile_id, revision_id, evaluation_id)
    validation = db.scalar(select(AssetCandidateDNSValidation).where(
        AssetCandidateDNSValidation.id == validation_id,
        AssetCandidateDNSValidation.asset_candidate_evaluation_id == evaluation_id,
        AssetCandidateDNSValidation.authorization_revision_id == revision_id,
    ))
    if validation is None:
        raise HTTPException(
            status_code=404, detail="DNS validation not found."
        )
    hops = tuple(db.scalars(select(AssetCandidateDNSCNAMEHop).where(
        AssetCandidateDNSCNAMEHop.dns_validation_id == validation_id
    ).order_by(AssetCandidateDNSCNAMEHop.ordinal)).all())
    addresses = tuple(db.scalars(select(AssetCandidateDNSAddress).where(
        AssetCandidateDNSAddress.dns_validation_id == validation_id
    ).order_by(AssetCandidateDNSAddress.ordinal)).all())
    return _dns_validation_response(PersistedAssetCandidateDNSValidation(
        validation=validation, cname_hops=hops, addresses=addresses
    ))


def _enrollment_validation_or_404(
    db: Session,
    profile_id: int,
    revision_id: int,
    evaluation_id: int,
    validation_id: int,
) -> AssetCandidateDNSValidation:
    try:
        _, validation = load_exact_dns_validation(
            db,
            profile_id=profile_id,
            revision_id=revision_id,
            evaluation_id=evaluation_id,
            validation_id=validation_id,
        )
    except (
        AssetEnrollmentDecisionNotFoundError,
        AssetEnrollmentDecisionProvenanceError,
    ) as exc:
        raise HTTPException(
            status_code=404, detail="DNS validation not found."
        ) from exc
    return validation


@router.post(
    "/{profile_id}/revisions/{revision_id}/asset-candidate-evaluations/"
    "{evaluation_id}/dns-validations/{validation_id}/enrollment-decisions",
    response_model=AssetEnrollmentDecisionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_revision_asset_enrollment_decision(
    profile_id: int,
    revision_id: int,
    evaluation_id: int,
    validation_id: int,
    payload: AssetEnrollmentDecisionCreate,
    db: Session = Depends(get_db),
) -> AssetEnrollmentDecision:
    try:
        return create_asset_enrollment_decision(
            db,
            profile_id=profile_id,
            revision_id=revision_id,
            evaluation_id=evaluation_id,
            validation_id=validation_id,
            **payload.model_dump(),
        )
    except AssetEnrollmentDecisionNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=404, detail="DNS validation not found."
        ) from exc
    except AssetEnrollmentDecisionProvenanceError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="DNS validation provenance is inconsistent."
        ) from exc
    except IntegrityError:
        db.rollback()
        raise


@router.get(
    "/{profile_id}/revisions/{revision_id}/asset-candidate-evaluations/"
    "{evaluation_id}/dns-validations/{validation_id}/enrollment-decisions",
    response_model=list[AssetEnrollmentDecisionRead],
)
def list_revision_asset_enrollment_decisions(
    profile_id: int,
    revision_id: int,
    evaluation_id: int,
    validation_id: int,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(
        default=50, ge=1, le=MAX_ASSET_ENROLLMENT_DECISION_PAGE_SIZE
    ),
    db: Session = Depends(get_db),
) -> list[AssetEnrollmentDecision]:
    _enrollment_validation_or_404(
        db, profile_id, revision_id, evaluation_id, validation_id
    )
    return list(db.scalars(select(AssetEnrollmentDecision).where(
        AssetEnrollmentDecision.asset_candidate_dns_validation_id == validation_id,
        AssetEnrollmentDecision.authorization_revision_id == revision_id,
        AssetEnrollmentDecision.id > after_id,
    ).order_by(AssetEnrollmentDecision.id).limit(limit)).all())


@router.get(
    "/{profile_id}/revisions/{revision_id}/asset-candidate-evaluations/"
    "{evaluation_id}/dns-validations/{validation_id}/enrollment-decisions/"
    "{decision_id}",
    response_model=AssetEnrollmentDecisionRead,
)
def get_revision_asset_enrollment_decision(
    profile_id: int,
    revision_id: int,
    evaluation_id: int,
    validation_id: int,
    decision_id: int,
    db: Session = Depends(get_db),
) -> AssetEnrollmentDecision:
    _enrollment_validation_or_404(
        db, profile_id, revision_id, evaluation_id, validation_id
    )
    enrollment = db.scalar(select(AssetEnrollmentDecision).where(
        AssetEnrollmentDecision.id == decision_id,
        AssetEnrollmentDecision.asset_candidate_dns_validation_id == validation_id,
        AssetEnrollmentDecision.authorization_revision_id == revision_id,
    ))
    if enrollment is None:
        raise HTTPException(
            status_code=404, detail="Enrollment decision not found."
        )
    return enrollment
