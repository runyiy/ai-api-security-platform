from collections.abc import Callable

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.scope import Scope
from app.db.models.target import Target


ExecutionAuthorization = tuple[
    Target,
    AuthorizationRevision | None,
    list[Scope],
]


def load_execution_authorization(
    db: Session,
    target_id: int,
) -> ExecutionAuthorization:
    target = db.get(Target, target_id)
    if target is None:
        raise LookupError("Target not found.")

    revision = None
    if target.authorization_revision_id is not None:
        revision = db.get(
            AuthorizationRevision,
            target.authorization_revision_id,
        )

    scopes = list(
        db.scalars(
            select(Scope).where(
                Scope.target_id == target_id,
                Scope.is_active.is_(True),
            )
        ).all()
    )
    return target, revision, scopes


def build_execution_authorization_refresh(
    bind: Engine,
    target_id: int,
) -> Callable[[], ExecutionAuthorization]:
    fresh_session = sessionmaker(
        bind=bind,
        autoflush=False,
        expire_on_commit=False,
    )

    def refresh() -> ExecutionAuthorization:
        with fresh_session() as db:
            result = load_execution_authorization(db, target_id)
            db.expunge_all()
            return result

    return refresh
