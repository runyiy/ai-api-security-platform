from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.target import Target
from app.db.session import SessionLocal
from app.main import app


client = TestClient(app)


def minimal_payload() -> dict[str, object]:
    unique = uuid4()
    return {
        "name": f"profile-{unique}",
        "program_name": f"program-{unique}",
        "authorization_type": "self_owned",
    }


def delete_profile(profile_id: int) -> None:
    with SessionLocal() as db:
        db.execute(
            delete(AuthorizationProfile).where(
                AuthorizationProfile.id == profile_id
            )
        )
        db.commit()


def test_minimal_create_is_fail_closed_and_does_not_create_targets() -> None:
    with SessionLocal() as db:
        target_count_before = db.scalar(
            select(func.count(Target.id))
        )

    response = client.post(
        "/api/authorization-profiles",
        json=minimal_payload(),
    )

    assert response.status_code == 201
    body = response.json()

    try:
        assert body["automation_allowed"] is False
        assert body["max_requests_per_second"] == 1.0
        assert body["allow_get"] is False
        assert body["allow_post"] is False
        assert body["allow_patch"] is False
        assert body["allow_put"] is False
        assert body["allow_delete"] is False
        assert body["require_human_execution_approval"] is True

        with SessionLocal() as db:
            target_count_after = db.scalar(
                select(func.count(Target.id))
            )
            bound_target_count = db.scalar(
                select(func.count(Target.id)).where(
                    Target.authorization_profile_id == body["id"]
                )
            )

        assert target_count_after == target_count_before
        assert bound_target_count == 0
    finally:
        delete_profile(body["id"])


def test_explicit_values_round_trip() -> None:
    payload = {
        **minimal_payload(),
        "program_url": "https://example.test/security",
        "authorization_reference": "authorization-001",
        "valid_from": "2026-09-01T00:00:00Z",
        "valid_until": "2026-10-01T00:00:00Z",
        "automation_allowed": True,
        "max_requests_per_second": 2.5,
        "allow_get": True,
        "allow_post": True,
        "allow_patch": True,
        "allow_put": True,
        "allow_delete": True,
        "require_human_execution_approval": False,
        "notes": "Explicitly configured test profile.",
    }
    response = client.post(
        "/api/authorization-profiles",
        json=payload,
    )

    assert response.status_code == 201
    body = response.json()

    try:
        assert body["name"] == payload["name"]
        assert body["program_name"] == payload["program_name"]
        assert body["authorization_type"] == payload["authorization_type"]
        assert datetime.fromisoformat(
            body["valid_from"].replace("Z", "+00:00")
        ) == datetime(2026, 9, 1, tzinfo=timezone.utc)
        assert datetime.fromisoformat(
            body["valid_until"].replace("Z", "+00:00")
        ) == datetime(2026, 10, 1, tzinfo=timezone.utc)

        for field in (
            "program_url",
            "authorization_reference",
            "automation_allowed",
            "max_requests_per_second",
            "allow_get",
            "allow_post",
            "allow_patch",
            "allow_put",
            "allow_delete",
            "require_human_execution_approval",
            "notes",
        ):
            assert body[field] == payload[field]
    finally:
        delete_profile(body["id"])


def test_list_get_and_missing_get() -> None:
    created_ids: list[int] = []

    try:
        for _ in range(2):
            response = client.post(
                "/api/authorization-profiles",
                json=minimal_payload(),
            )
            assert response.status_code == 201
            created_ids.append(response.json()["id"])

        list_response = client.get(
            "/api/authorization-profiles"
        )
        assert list_response.status_code == 200
        listed = list_response.json()
        listed_ids = [profile["id"] for profile in listed]
        assert listed_ids == sorted(listed_ids)
        assert set(created_ids).issubset(listed_ids)

        get_response = client.get(
            f"/api/authorization-profiles/{created_ids[0]}"
        )
        assert get_response.status_code == 200
        assert get_response.json()["id"] == created_ids[0]

        missing_response = client.get(
            "/api/authorization-profiles/2147483647"
        )
        assert missing_response.status_code == 404
    finally:
        for profile_id in created_ids:
            delete_profile(profile_id)


def test_patch_changes_only_supplied_fields() -> None:
    create_response = client.post(
        "/api/authorization-profiles",
        json=minimal_payload(),
    )
    assert create_response.status_code == 201
    original = create_response.json()

    try:
        patch_response = client.patch(
            f"/api/authorization-profiles/{original['id']}",
            json={
                "notes": "updated",
                "allow_get": True,
            },
        )
        assert patch_response.status_code == 200
        updated = patch_response.json()
        assert updated["notes"] == "updated"
        assert updated["allow_get"] is True
        assert updated["name"] == original["name"]
        assert updated["program_name"] == original["program_name"]
        assert updated["automation_allowed"] is False
        assert updated["allow_post"] is False
    finally:
        delete_profile(original["id"])


def test_non_positive_rate_and_required_whitespace_are_rejected() -> None:
    for rate in (0, -1):
        response = client.post(
            "/api/authorization-profiles",
            json={
                **minimal_payload(),
                "max_requests_per_second": rate,
            },
        )
        assert response.status_code == 422

    response = client.post(
        "/api/authorization-profiles",
        json={
            **minimal_payload(),
            "name": "   ",
        },
    )
    assert response.status_code == 422


def test_invalid_create_validity_window_is_rejected() -> None:
    response = client.post(
        "/api/authorization-profiles",
        json={
            **minimal_payload(),
            "valid_from": "2026-09-10T00:00:00Z",
            "valid_until": "2026-09-01T00:00:00Z",
        },
    )

    assert response.status_code == 422


def test_partial_patch_validates_merged_validity_window() -> None:
    create_response = client.post(
        "/api/authorization-profiles",
        json={
            **minimal_payload(),
            "valid_from": "2026-09-10T00:00:00Z",
        },
    )
    assert create_response.status_code == 201
    profile = create_response.json()

    try:
        patch_response = client.patch(
            f"/api/authorization-profiles/{profile['id']}",
            json={
                "valid_until": "2026-09-01T00:00:00Z",
            },
        )
        assert patch_response.status_code == 422

        get_response = client.get(
            f"/api/authorization-profiles/{profile['id']}"
        )
        assert get_response.status_code == 200
        assert get_response.json()["valid_until"] is None
    finally:
        delete_profile(profile["id"])


def test_unreferenced_profile_delete_succeeds() -> None:
    create_response = client.post(
        "/api/authorization-profiles",
        json=minimal_payload(),
    )
    assert create_response.status_code == 201
    profile_id = create_response.json()["id"]

    delete_response = client.delete(
        f"/api/authorization-profiles/{profile_id}"
    )
    assert delete_response.status_code == 204

    missing_response = client.get(
        f"/api/authorization-profiles/{profile_id}"
    )
    assert missing_response.status_code == 404


def test_referenced_profile_delete_returns_conflict_and_preserves_rows() -> None:
    create_response = client.post(
        "/api/authorization-profiles",
        json=minimal_payload(),
    )
    assert create_response.status_code == 201
    profile_id = create_response.json()["id"]
    target_id: int | None = None

    try:
        with SessionLocal() as db:
            target = Target(
                name=f"target-{uuid4()}",
                base_url="https://example.test",
                environment="test",
                is_enabled=True,
                authorization_profile_id=profile_id,
            )
            db.add(target)
            db.commit()
            target_id = target.id

        delete_response = client.delete(
            f"/api/authorization-profiles/{profile_id}"
        )
        assert delete_response.status_code == 409

        with SessionLocal() as db:
            preserved_target = db.get(Target, target_id)
            preserved_profile = db.get(
                AuthorizationProfile,
                profile_id,
            )
            assert preserved_target is not None
            assert preserved_profile is not None
            assert (
                preserved_target.authorization_profile_id
                == profile_id
            )
    finally:
        with SessionLocal() as db:
            if target_id is not None:
                db.execute(
                    delete(Target).where(Target.id == target_id)
                )
            db.execute(
                delete(AuthorizationProfile).where(
                    AuthorizationProfile.id == profile_id
                )
            )
            db.commit()


def test_missing_patch_and_delete_return_not_found() -> None:
    missing_id = 2147483647
    patch_response = client.patch(
        f"/api/authorization-profiles/{missing_id}",
        json={"notes": "missing"},
    )
    delete_response = client.delete(
        f"/api/authorization-profiles/{missing_id}"
    )

    assert patch_response.status_code == 404
    assert delete_response.status_code == 404
