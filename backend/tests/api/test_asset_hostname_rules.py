from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.db.models import (
    AssetHostnameRule,
    AuthorizationProfile,
    AuthorizationRevision,
    ExecutionPlan,
    Scope,
    Target,
    TestRun as StoredTestRun,
)
from app.db.session import SessionLocal
from app.main import app


client = TestClient(app)


def create_profile_and_revision() -> tuple[int, int]:
    profile = client.post("/api/authorization-profiles", json={
        "name": f"asset-rules-{uuid4()}",
        "program_name": "Synthetic asset program",
        "authorization_type": "self_owned",
    }).json()
    revision = client.post(
        f"/api/authorization-profiles/{profile['id']}/revisions"
    ).json()
    return profile["id"], revision["id"]


def cleanup_profiles(profile_ids: list[int]) -> None:
    with SessionLocal() as db:
        revision_ids = db.scalars(
            AuthorizationRevision.__table__.select()
            .with_only_columns(AuthorizationRevision.id)
            .where(AuthorizationRevision.authorization_profile_id.in_(profile_ids))
        )
        db.execute(delete(AssetHostnameRule).where(
            AssetHostnameRule.authorization_revision_id.in_(list(revision_ids))
        ))
        db.execute(delete(AuthorizationRevision).where(
            AuthorizationRevision.authorization_profile_id.in_(profile_ids)
        ))
        db.execute(delete(AuthorizationProfile).where(
            AuthorizationProfile.id.in_(profile_ids)
        ))
        db.commit()


def test_draft_rule_crud_normalization_duplicate_and_cross_revision_guards() -> None:
    profile_ids: list[int] = []
    try:
        profile_id, revision_id = create_profile_and_revision()
        other_profile_id, other_revision_id = create_profile_and_revision()
        profile_ids = [profile_id, other_profile_id]
        url = (
            f"/api/authorization-profiles/{profile_id}/revisions/"
            f"{revision_id}/asset-hostname-rules"
        )
        created = client.post(url, json={
            "rule_type": "include",
            "hostname_pattern": "*.BÜCHER.TEST.",
        })
        assert created.status_code == 201
        rule = created.json()
        assert rule["hostname_pattern"] == "*.xn--bcher-kva.test"
        assert set(rule) == {
            "id", "authorization_revision_id", "rule_type",
            "hostname_pattern", "created_at",
        }
        duplicate = client.post(url, json={
            "rule_type": "include",
            "hostname_pattern": "*.XN--BCHER-KVA.TEST",
        })
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "Asset hostname rule already exists."
        assert client.post(url, json={
            "rule_type": "include",
            "hostname_pattern": "*.example.test",
            "unexpected": True,
        }).status_code == 422
        assert client.get(url).json() == [rule]

        cross_profile_url = (
            f"/api/authorization-profiles/{other_profile_id}/revisions/"
            f"{revision_id}/asset-hostname-rules"
        )
        assert client.post(cross_profile_url, json={
            "rule_type": "include", "hostname_pattern": "*.example.test"
        }).status_code == 404
        wrong_revision_delete = (
            f"/api/authorization-profiles/{other_profile_id}/revisions/"
            f"{other_revision_id}/asset-hostname-rules/{rule['id']}"
        )
        assert client.delete(wrong_revision_delete).status_code == 404
        assert client.delete(f"{url}/{rule['id']}").status_code == 204
        assert client.get(url).json() == []
    finally:
        cleanup_profiles(profile_ids)


def test_asset_hostname_rule_crud_has_zero_authorization_execution_side_effects(
) -> None:
    profile_ids: list[int] = []
    tracked_models = (Target, Scope, ExecutionPlan, StoredTestRun)
    try:
        profile_id, revision_id = create_profile_and_revision()
        profile_ids = [profile_id]
        with SessionLocal() as db:
            before = {
                model: db.scalar(select(func.count()).select_from(model))
                for model in tracked_models
            }

        url = (
            f"/api/authorization-profiles/{profile_id}/revisions/"
            f"{revision_id}/asset-hostname-rules"
        )
        created = client.post(url, json={
            "rule_type": "include",
            "hostname_pattern": "*.side-effect-free.test",
        })
        assert created.status_code == 201
        assert client.delete(f"{url}/{created.json()['id']}").status_code == 204

        with SessionLocal() as db:
            after = {
                model: db.scalar(select(func.count()).select_from(model))
                for model in tracked_models
            }
        assert after == before
    finally:
        cleanup_profiles(profile_ids)


def test_historical_rules_are_read_only_and_new_revision_does_not_copy() -> None:
    profile_ids: list[int] = []
    try:
        profile_id, first_revision_id = create_profile_and_revision()
        profile_ids = [profile_id]
        first_url = (
            f"/api/authorization-profiles/{profile_id}/revisions/"
            f"{first_revision_id}/asset-hostname-rules"
        )
        first_rule = client.post(first_url, json={
            "rule_type": "include", "hostname_pattern": "*.example.test"
        }).json()
        assert client.post(
            f"/api/authorization-profiles/{profile_id}/revisions/"
            f"{first_revision_id}/activate"
        ).status_code == 200
        assert client.post(first_url, json={
            "rule_type": "exclude", "hostname_pattern": "admin.example.test"
        }).status_code == 409
        assert client.delete(f"{first_url}/{first_rule['id']}").status_code == 409
        assert client.get(first_url).json() == [first_rule]

        second = client.post(
            f"/api/authorization-profiles/{profile_id}/revisions"
        ).json()
        second_url = (
            f"/api/authorization-profiles/{profile_id}/revisions/"
            f"{second['id']}/asset-hostname-rules"
        )
        assert client.get(second_url).json() == []
        second_rule = client.post(second_url, json={
            "rule_type": "include", "hostname_pattern": "*.other.test"
        }).json()
        assert client.post(
            f"/api/authorization-profiles/{profile_id}/revisions/"
            f"{second['id']}/activate"
        ).status_code == 200
        assert client.get(first_url).json() == [first_rule]
        assert client.post(first_url, json={
            "rule_type": "exclude", "hostname_pattern": "old.example.test"
        }).status_code == 409
        assert client.delete(f"{first_url}/{first_rule['id']}").status_code == 409
        assert client.delete(f"{second_url}/{second_rule['id']}").status_code == 409

        third = client.post(
            f"/api/authorization-profiles/{profile_id}/revisions"
        ).json()
        third_url = (
            f"/api/authorization-profiles/{profile_id}/revisions/"
            f"{third['id']}/asset-hostname-rules"
        )
        third_rule = client.post(third_url, json={
            "rule_type": "include", "hostname_pattern": "*.revoked.test"
        }).json()
        assert client.post(
            f"/api/authorization-profiles/{profile_id}/revisions/"
            f"{third['id']}/revoke"
        ).status_code == 200
        assert client.post(third_url, json={
            "rule_type": "exclude", "hostname_pattern": "blocked.revoked.test"
        }).status_code == 409
        assert client.delete(f"{third_url}/{third_rule['id']}").status_code == 409
    finally:
        cleanup_profiles(profile_ids)
