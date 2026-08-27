from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.authorization_revision import AuthorizationRevision


SNAPSHOT_FIELDS = (
    "name",
    "program_name",
    "program_url",
    "authorization_type",
    "authorization_reference",
    "valid_from",
    "valid_until",
    "automation_allowed",
    "max_requests_per_second",
    "allow_get",
    "allow_post",
    "allow_patch",
    "allow_put",
    "allow_delete",
    "require_human_execution_approval",
    "notes",
)


class RevisionNotFoundError(Exception):
    pass


class InvalidRevisionTransitionError(Exception):
    pass


def lock_profile(db: Session, profile_id: int) -> AuthorizationProfile | None:
    return db.scalar(
        select(AuthorizationProfile)
        .where(AuthorizationProfile.id == profile_id)
        .with_for_update()
    )


def create_revision(db: Session, profile_id: int) -> AuthorizationRevision:
    profile = lock_profile(db, profile_id)
    if profile is None:
        raise RevisionNotFoundError

    current_max = db.scalar(
        select(func.max(AuthorizationRevision.revision_number)).where(
            AuthorizationRevision.authorization_profile_id == profile_id
        )
    )
    revision = AuthorizationRevision(
        authorization_profile_id=profile.id,
        revision_number=(current_max or 0) + 1,
        lifecycle_state="draft",
        **{field: getattr(profile, field) for field in SNAPSHOT_FIELDS},
    )
    db.add(revision)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise
    db.refresh(revision)
    return revision


def transition_revision(
    db: Session,
    profile_id: int,
    revision_id: int,
    destination: str,
) -> AuthorizationRevision:
    profile = lock_profile(db, profile_id)
    if profile is None:
        raise RevisionNotFoundError

    revisions = list(
        db.scalars(
            select(AuthorizationRevision)
            .where(AuthorizationRevision.authorization_profile_id == profile_id)
            .order_by(AuthorizationRevision.id)
            .with_for_update()
        ).all()
    )
    selected = next((item for item in revisions if item.id == revision_id), None)
    if selected is None:
        raise RevisionNotFoundError

    if destination == "active":
        if selected.lifecycle_state != "draft":
            raise InvalidRevisionTransitionError
        active_revisions = []
        for revision in revisions:
            if revision.lifecycle_state == "active":
                revision.lifecycle_state = "superseded"
                active_revisions.append(revision)
        if active_revisions:
            db.flush(active_revisions)
        selected.lifecycle_state = "active"
    elif destination == "revoked":
        if selected.lifecycle_state not in {"draft", "active"}:
            raise InvalidRevisionTransitionError
        selected.lifecycle_state = "revoked"
    else:
        raise InvalidRevisionTransitionError

    db.commit()
    db.refresh(selected)
    return selected
