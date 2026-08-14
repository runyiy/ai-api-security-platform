from collections.abc import Iterator
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes.resources import create_resource
from app.api.routes.test_identities import create_test_identity
from app.db.models.resource import Resource
from app.db.models.target import Target
from app.db.models.test_identity import TestIdentity as StoredIdentity
from app.db.session import engine
from app.schemas.resource import ResourceCreate
from app.schemas.test_identity import TestIdentityCreate as IdentityCreate


@pytest.fixture
def resource_owner() -> Iterator[tuple[int, int]]:
    TestSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    unique_name = f"single-create-{uuid4()}"

    with TestSession() as db:
        target = Target(
            name=unique_name,
            base_url="https://example.test",
            environment="test",
            is_enabled=True,
        )
        db.add(target)
        db.flush()
        owner = StoredIdentity(
            target_id=target.id,
            name=f"{unique_name}-owner",
            role=None,
            auth_type="anonymous",
            credentials=None,
            is_active=True,
        )
        db.add(owner)
        db.commit()
        target_id = target.id
        owner_id = owner.id

    try:
        yield target_id, owner_id
    finally:
        with TestSession() as db:
            db.execute(
                delete(Resource).where(Resource.target_id == target_id)
            )
            db.execute(delete(Target).where(Target.id == target_id))
            db.commit()


def test_concurrent_resource_create_returns_one_conflict(
    resource_owner: tuple[int, int],
) -> None:
    target_id, owner_id = resource_owner
    precheck_complete = threading.Barrier(2)

    class RacingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            result = super().scalar(statement, *args, **kwargs)
            entities = {
                description.get("entity")
                for description in statement.column_descriptions
            }
            if Resource in entities and result is None:
                precheck_complete.wait(timeout=10)
            return result

    ConcurrentSession = sessionmaker(
        bind=engine,
        class_=RacingSession,
        autoflush=False,
        expire_on_commit=False,
    )
    payload = ResourceCreate(
        target_id=target_id,
        resource_type="project",
        external_id="shared-2001",
        owner_identity_id=owner_id,
    )
    created_ids: list[int] = []
    conflicts: list[int] = []
    errors: list[Exception] = []
    losing_session_usable: list[bool] = []

    def create() -> None:
        with ConcurrentSession() as db:
            try:
                resource = create_resource(payload=payload, db=db)
                created_ids.append(resource.id)
            except HTTPException as exc:
                conflicts.append(exc.status_code)
                try:
                    db.scalar(select(func.count(Resource.id)))
                    losing_session_usable.append(True)
                except Exception as session_exc:
                    errors.append(session_exc)
            except Exception as exc:
                errors.append(exc)

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    with ConcurrentSession() as db:
        row_count = db.scalar(
            select(func.count(Resource.id)).where(
                Resource.target_id == target_id,
                Resource.resource_type == "project",
                Resource.external_id == "shared-2001",
            )
        )

    assert errors == []
    assert len(created_ids) == 1
    assert conflicts == [409]
    assert row_count == 1
    assert losing_session_usable == [True]


def test_test_identity_unique_race_rolls_back_and_returns_conflict(
    resource_owner: tuple[int, int],
) -> None:
    target_id, _ = resource_owner
    db = Mock(spec=Session)
    db.get.return_value = Target(id=target_id)
    db.scalar.return_value = None

    original_error = RuntimeError("unique violation")
    original_error.sqlstate = "23505"
    original_error.diag = SimpleNamespace(
        constraint_name="uq_test_identity_target_name"
    )
    db.commit.side_effect = IntegrityError(
        "INSERT",
        {},
        original_error,
    )

    with pytest.raises(HTTPException) as raised:
        create_test_identity(
            payload=IdentityCreate(
                target_id=target_id,
                name="shared-name",
                auth_type="anonymous",
            ),
            db=db,
        )

    assert raised.value.status_code == 409
    db.rollback.assert_called_once_with()


def test_unrelated_integrity_error_is_not_mapped_to_conflict(
    resource_owner: tuple[int, int],
) -> None:
    target_id, _ = resource_owner
    db = Mock(spec=Session)
    db.get.return_value = Target(id=target_id)
    db.scalar.return_value = None

    original_error = RuntimeError("foreign key violation")
    original_error.sqlstate = "23503"
    original_error.diag = SimpleNamespace(
        constraint_name="test_identities_target_id_fkey"
    )
    integrity_error = IntegrityError(
        "INSERT",
        {},
        original_error,
    )
    db.commit.side_effect = integrity_error

    with pytest.raises(IntegrityError) as raised:
        create_test_identity(
            payload=IdentityCreate(
                target_id=target_id,
                name="shared-name",
                auth_type="anonymous",
            ),
            db=db,
        )

    assert raised.value is integrity_error
    db.rollback.assert_called_once_with()
