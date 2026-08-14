from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.target import Target
from app.db.session import SessionLocal
from app.main import app


client = TestClient(app)


def create_target() -> dict[str, object]:
    response = client.post(
        "/api/targets",
        json={
            "name": f"target-{uuid4()}",
            "base_url": "https://example.test",
            "environment": "test",
        },
    )
    assert response.status_code == 201
    return response.json()


def create_profile() -> dict[str, object]:
    unique = uuid4()
    response = client.post(
        "/api/authorization-profiles",
        json={
            "name": f"profile-{unique}",
            "program_name": f"program-{unique}",
            "authorization_type": "self_owned",
        },
    )
    assert response.status_code == 201
    return response.json()


def delete_rows(
    *,
    target_ids: list[int],
    profile_ids: list[int],
) -> None:
    with SessionLocal() as db:
        if target_ids:
            db.execute(delete(Target).where(Target.id.in_(target_ids)))
        if profile_ids:
            db.execute(
                delete(AuthorizationProfile).where(
                    AuthorizationProfile.id.in_(profile_ids)
                )
            )
        db.commit()


def test_target_creation_remains_unbound() -> None:
    target = create_target()

    try:
        assert target["authorization_profile_id"] is None

        with SessionLocal() as db:
            stored_target = db.get(Target, target["id"])
            assert stored_target is not None
            assert stored_target.authorization_profile_id is None
    finally:
        delete_rows(target_ids=[target["id"]], profile_ids=[])


def test_target_can_bind_rebind_and_explicitly_unbind() -> None:
    target = create_target()
    profile_a = create_profile()
    profile_b = create_profile()
    target_id = target["id"]
    profile_ids = [profile_a["id"], profile_b["id"]]

    try:
        with SessionLocal() as db:
            profile_count_before = db.scalar(
                select(func.count(AuthorizationProfile.id))
            )

        bind_response = client.patch(
            f"/api/targets/{target_id}/authorization-profile",
            json={"authorization_profile_id": profile_a["id"]},
        )
        assert bind_response.status_code == 200
        assert (
            bind_response.json()["authorization_profile_id"]
            == profile_a["id"]
        )

        with SessionLocal() as db:
            stored_target = db.get(Target, target_id)
            assert stored_target is not None
            assert (
                stored_target.authorization_profile_id
                == profile_a["id"]
            )

        rebind_response = client.patch(
            f"/api/targets/{target_id}/authorization-profile",
            json={"authorization_profile_id": profile_b["id"]},
        )
        assert rebind_response.status_code == 200
        assert (
            rebind_response.json()["authorization_profile_id"]
            == profile_b["id"]
        )

        unbind_response = client.patch(
            f"/api/targets/{target_id}/authorization-profile",
            json={"authorization_profile_id": None},
        )
        assert unbind_response.status_code == 200
        assert unbind_response.json()["authorization_profile_id"] is None

        with SessionLocal() as db:
            stored_target = db.get(Target, target_id)
            assert stored_target is not None
            assert stored_target.authorization_profile_id is None
            assert db.scalar(
                select(func.count(AuthorizationProfile.id))
            ) == profile_count_before
            assert all(
                db.get(AuthorizationProfile, profile_id) is not None
                for profile_id in profile_ids
            )
    finally:
        delete_rows(target_ids=[target_id], profile_ids=profile_ids)


def test_missing_target_returns_not_found_without_creating_profiles() -> None:
    with SessionLocal() as db:
        profile_count_before = db.scalar(
            select(func.count(AuthorizationProfile.id))
        )

    response = client.patch(
        "/api/targets/2147483647/authorization-profile",
        json={"authorization_profile_id": None},
    )

    assert response.status_code == 404

    with SessionLocal() as db:
        assert db.scalar(
            select(func.count(AuthorizationProfile.id))
        ) == profile_count_before


def test_missing_profile_preserves_existing_binding_atomically() -> None:
    target = create_target()
    profile = create_profile()
    target_id = target["id"]
    profile_id = profile["id"]

    try:
        bind_response = client.patch(
            f"/api/targets/{target_id}/authorization-profile",
            json={"authorization_profile_id": profile_id},
        )
        assert bind_response.status_code == 200

        with SessionLocal() as db:
            maximum_profile_id = db.scalar(
                select(func.max(AuthorizationProfile.id))
            )
            missing_profile_id = (maximum_profile_id or 0) + 1_000_000
            profile_count_before = db.scalar(
                select(func.count(AuthorizationProfile.id))
            )

        failed_response = client.patch(
            f"/api/targets/{target_id}/authorization-profile",
            json={"authorization_profile_id": missing_profile_id},
        )
        assert failed_response.status_code == 404

        with SessionLocal() as db:
            stored_target = db.get(Target, target_id)
            assert stored_target is not None
            assert stored_target.authorization_profile_id == profile_id
            assert db.scalar(
                select(func.count(AuthorizationProfile.id))
            ) == profile_count_before
            assert db.get(AuthorizationProfile, profile_id) is not None
    finally:
        delete_rows(target_ids=[target_id], profile_ids=[profile_id])
