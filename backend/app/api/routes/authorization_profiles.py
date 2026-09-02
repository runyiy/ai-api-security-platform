from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
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
from app.db.models.asset_hostname_rule import AssetHostnameRule
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
    db: Session = Depends(get_db),
) -> list[AssetCandidateEvaluation]:
    _evaluation_revision_or_404(db, profile_id, revision_id)
    return list(db.scalars(
        select(AssetCandidateEvaluation)
        .where(AssetCandidateEvaluation.authorization_revision_id == revision_id)
        .order_by(AssetCandidateEvaluation.id)
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
