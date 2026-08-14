from collections.abc import Iterator
import threading
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.dml import Insert

from app.api.routes.test_cases import generate_bola_cases
from app.db.models.endpoint import Endpoint
from app.db.models.resource import Resource
from app.db.models.target import Target
from app.db.models.test_case import TestCase as StoredCase
from app.db.models.test_identity import TestIdentity as StoredIdentity
from app.db.session import engine
from app.schemas.test_case import GenerateBOLATestCasesRequest


@pytest.fixture
def bola_target_id() -> Iterator[int]:
    TestSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    unique_name = f"bola-generation-{uuid4()}"

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
        other_actor = StoredIdentity(
            target_id=target.id,
            name=f"{unique_name}-other",
            role=None,
            auth_type="anonymous",
            credentials=None,
            is_active=True,
        )
        endpoint = Endpoint(
            target_id=target.id,
            path="/projects/{project_id}",
            method="GET",
            operation_id="get_project",
            requires_auth=True,
            parameters=[],
            request_body=None,
            security=None,
        )
        db.add_all([owner, other_actor, endpoint])
        db.flush()

        resource = Resource(
            target_id=target.id,
            resource_type="project",
            external_id="2001",
            owner_identity_id=owner.id,
        )
        db.add(resource)
        db.commit()
        target_id = target.id

    try:
        yield target_id
    finally:
        with TestSession() as db:
            db.execute(
                delete(StoredCase).where(
                    StoredCase.endpoint_id.in_(
                        select(Endpoint.id).where(
                            Endpoint.target_id == target_id
                        )
                    )
                )
            )
            db.execute(
                delete(Resource).where(Resource.target_id == target_id)
            )
            db.execute(delete(Target).where(Target.id == target_id))
            db.commit()


def test_concurrent_bola_generation_is_conflict_safe(
    bola_target_id: int,
) -> None:
    insert_ready = threading.Barrier(2)

    class RacingSession(Session):
        def scalars(self, statement, *args, **kwargs):
            if isinstance(statement, Insert):
                insert_ready.wait(timeout=10)
            return super().scalars(statement, *args, **kwargs)

    ConcurrentSession = sessionmaker(
        bind=engine,
        class_=RacingSession,
        autoflush=False,
        expire_on_commit=False,
    )
    payload = GenerateBOLATestCasesRequest(
        target_id=bola_target_id
    )
    responses = []
    errors: list[Exception] = []

    def generate() -> None:
        try:
            with ConcurrentSession() as db:
                responses.append(
                    generate_bola_cases(payload=payload, db=db)
                )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=generate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    with ConcurrentSession() as db:
        keys = list(
            db.execute(
                select(
                    StoredCase.endpoint_id,
                    StoredCase.actor_identity_id,
                    StoredCase.resource_id,
                    StoredCase.test_type,
                )
                .join(
                    Endpoint,
                    StoredCase.endpoint_id == Endpoint.id,
                )
                .where(Endpoint.target_id == bola_target_id)
            ).all()
        )

    assert errors == []
    assert len(responses) == 2
    assert all(response.generated == 2 for response in responses)
    assert sum(response.created for response in responses) == 2
    assert sum(response.existing for response in responses) == 2
    assert len(keys) == 2
    assert len(set(keys)) == 2


def test_bola_generation_remains_sequentially_idempotent(
    bola_target_id: int,
) -> None:
    TestSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    payload = GenerateBOLATestCasesRequest(
        target_id=bola_target_id
    )

    with TestSession() as db:
        first = generate_bola_cases(payload=payload, db=db)
    with TestSession() as db:
        second = generate_bola_cases(payload=payload, db=db)

    assert first.generated == 2
    assert first.created == 2
    assert first.existing == 0
    assert second.generated == 2
    assert second.created == 0
    assert second.existing == 2
