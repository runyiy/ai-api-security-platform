from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select

from app.db.models import (
    Endpoint, Resource, ResourceAccessAssertion, Target, TestCase, TestIdentity,
    TestRun,
)
from app.db.session import SessionLocal
from app.main import app
from app.services.resource_access_resolution import MAX_ASSERTIONS_SCANNED


client = TestClient(app)
NOW = datetime.now(timezone.utc)


def make_candidate(
    *,
    provenance: str = "inferred_candidate",
    state: str = "candidate",
    relationship: str = "non_owner",
    expected_access: str = "allowed",
) -> dict[str, int]:
    with SessionLocal() as db:
        target = Target(
            name=f"review-{uuid4()}",
            base_url=f"https://{uuid4()}.example.test",
            environment="test",
            network_mode="private_local",
        )
        db.add(target)
        db.flush()
        identity = TestIdentity(
            target_id=target.id,
            name="review subject",
            role="user",
            auth_type="bearer",
            credentials=None,
            is_active=True,
        )
        db.add(identity)
        db.flush()
        resource = Resource(
            target_id=target.id,
            resource_type="order",
            external_id="must-not-leak",
            owner_identity_id=identity.id,
        )
        db.add(resource)
        db.flush()
        source_test_run_id = None
        endpoint = test_case = run = None
        if provenance == "observed_baseline":
            endpoint = Endpoint(
                target_id=target.id,
                path="/orders/{id}",
                method="GET",
                requires_auth=True,
                parameters=[],
            )
            db.add(endpoint)
            db.flush()
            test_case = TestCase(
                endpoint_id=endpoint.id,
                actor_identity_id=identity.id,
                resource_id=resource.id,
                test_type="owner_baseline",
                ownership_relation="owner",
                expected_statuses=[200],
                status="completed",
            )
            db.add(test_case)
            db.flush()
            run = TestRun(
                test_case_id=test_case.id,
                request_data={},
                response_status=200,
                response_body=None,
                executed_at=NOW - timedelta(days=2),
            )
            db.add(run)
            db.flush()
            source_test_run_id = run.id
        candidate = ResourceAccessAssertion(
            resource_id=resource.id,
            test_identity_id=identity.id,
            relationship=relationship,
            expected_access=expected_access,
            provenance=provenance,
            confidence=30,
            verification_state=state,
            observed_at=NOW - timedelta(days=2),
            valid_from=NOW - timedelta(days=1),
            valid_until=NOW + timedelta(days=1),
            source_test_run_id=source_test_run_id,
        )
        db.add(candidate)
        db.commit()
        return {
            "target": target.id,
            "identity": identity.id,
            "resource": resource.id,
            "candidate": candidate.id,
            "endpoint": endpoint.id if endpoint is not None else 0,
            "case": test_case.id if test_case is not None else 0,
            "run": run.id if run is not None else 0,
        }


def cleanup(ids: dict[str, int]) -> None:
    with SessionLocal() as db:
        db.execute(delete(ResourceAccessAssertion).where(
            ResourceAccessAssertion.resource_id == ids["resource"]
        ))
        if ids.get("run"):
            db.execute(delete(TestRun).where(TestRun.id == ids["run"]))
        if ids.get("case"):
            db.execute(delete(TestCase).where(TestCase.id == ids["case"]))
        if ids.get("endpoint"):
            db.execute(delete(Endpoint).where(Endpoint.id == ids["endpoint"]))
        db.execute(delete(Resource).where(Resource.id == ids["resource"]))
        db.execute(delete(TestIdentity).where(TestIdentity.id == ids["identity"]))
        db.execute(delete(Target).where(Target.id == ids["target"]))
        db.commit()


def review(ids: dict[str, int], decision="verify", confidence=77):
    return client.post(
        f"/api/resources/{ids['resource']}/access-assertions/"
        f"{ids['candidate']}/review",
        json={"decision": decision, "confidence": confidence},
    )


@pytest.mark.parametrize(
    ("decision", "expected_state"), (("verify", "verified"), ("reject", "rejected"))
)
def test_review_appends_exact_immutable_outcome(decision, expected_state) -> None:
    ids = make_candidate()
    try:
        with SessionLocal() as db:
            source = db.get(ResourceAccessAssertion, ids["candidate"])
            snapshot = tuple(getattr(source, field) for field in (
                "resource_id", "test_identity_id", "relationship", "expected_access",
                "provenance", "confidence", "verification_state", "asserted_at",
                "observed_at", "valid_from", "valid_until", "source_test_run_id",
                "reviewed_assertion_id",
            ))
        response = review(ids, decision, 83)
        assert response.status_code == 201
        body = response.json()
        assert body["id"] != ids["candidate"]
        assert body["resource_id"] == ids["resource"]
        assert body["test_identity_id"] == ids["identity"]
        assert (body["relationship"], body["expected_access"]) == (
            "non_owner", "allowed"
        )
        assert body["provenance"] == "human_verified"
        assert body["verification_state"] == expected_state
        assert body["confidence"] == 83
        assert body["source_test_run_id"] is None
        assert body["reviewed_assertion_id"] == ids["candidate"]
        assert "must-not-leak" not in response.text
        with SessionLocal() as db:
            source = db.get(ResourceAccessAssertion, ids["candidate"])
            assert tuple(getattr(source, field) for field in (
                "resource_id", "test_identity_id", "relationship", "expected_access",
                "provenance", "confidence", "verification_state", "asserted_at",
                "observed_at", "valid_from", "valid_until", "source_test_run_id",
                "reviewed_assertion_id",
            )) == snapshot
    finally:
        cleanup(ids)


def test_exact_scope_eligibility_and_strict_request() -> None:
    ids = make_candidate()
    other = make_candidate()
    try:
        assert client.post(
            f"/api/resources/999999999/access-assertions/{ids['candidate']}/review",
            json={"decision": "verify", "confidence": 10},
        ).status_code == 404
        assert client.post(
            f"/api/resources/{other['resource']}/access-assertions/"
            f"{ids['candidate']}/review",
            json={"decision": "verify", "confidence": 10},
        ).status_code == 404
        for payload in (
            {"decision": "promote", "confidence": 10},
            {"decision": "verify", "confidence": -1},
            {"decision": "verify", "confidence": 101},
            {"decision": "verify", "confidence": 10.0},
            {"decision": "verify", "confidence": 10, "provenance": "human_verified"},
        ):
            assert client.post(
                f"/api/resources/{ids['resource']}/access-assertions/"
                f"{ids['candidate']}/review", json=payload
            ).status_code == 422
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                ResourceAccessAssertion
            ).where(ResourceAccessAssertion.reviewed_assertion_id == ids["candidate"])) == 0
    finally:
        cleanup(ids)
        cleanup(other)


@pytest.mark.parametrize(
    ("provenance", "state"),
    (("human_verified", "candidate"), ("target_fixture", "candidate"),
     ("inferred_candidate", "verified"), ("inferred_candidate", "rejected")),
)
def test_only_machine_candidates_are_reviewable(provenance, state) -> None:
    ids = make_candidate(provenance=provenance, state=state)
    try:
        response = review(ids)
        assert response.status_code == 409
        assert response.json()["detail"] == "resource_access_assertion_not_reviewable"
    finally:
        cleanup(ids)


def test_observed_baseline_candidate_is_reviewable() -> None:
    ids = make_candidate(provenance="observed_baseline")
    try:
        response = review(ids)
        assert response.status_code == 201
        assert response.json()["source_test_run_id"] is None
    finally:
        cleanup(ids)


def test_retry_and_concurrent_identical_review_converge() -> None:
    ids = make_candidate()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: review(ids, "verify", 66), range(2)))
        assert all(response.status_code == 201 for response in responses)
        assert responses[0].json()["id"] == responses[1].json()["id"]
        assert review(ids, "verify", 66).json()["id"] == responses[0].json()["id"]
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                ResourceAccessAssertion
            ).where(ResourceAccessAssertion.reviewed_assertion_id == ids["candidate"])) == 1
    finally:
        cleanup(ids)


def test_conflicting_repeat_and_race_fail_closed() -> None:
    ids = make_candidate()
    try:
        assert review(ids, "verify", 55).status_code == 201
        for decision, confidence in (("reject", 55), ("verify", 56)):
            response = review(ids, decision, confidence)
            assert response.status_code == 409
            assert response.json()["detail"] == "resource_access_assertion_already_reviewed"
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                ResourceAccessAssertion
            ).where(ResourceAccessAssertion.reviewed_assertion_id == ids["candidate"])) == 1
    finally:
        cleanup(ids)


def test_concurrent_conflicting_reviews_leave_one_outcome() -> None:
    ids = make_candidate()
    try:
        requests = (("verify", 45), ("reject", 91))
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda args: review(ids, *args), requests))
        assert sorted(response.status_code for response in responses) == [201, 409]
        loser = next(response for response in responses if response.status_code == 409)
        assert loser.json()["detail"] == "resource_access_assertion_already_reviewed"
        with SessionLocal() as db:
            outcomes = list(db.scalars(select(ResourceAccessAssertion).where(
                ResourceAccessAssertion.reviewed_assertion_id == ids["candidate"]
            )))
            assert len(outcomes) == 1
            assert (outcomes[0].verification_state, outcomes[0].confidence) in {
                ("verified", 45), ("rejected", 91)
            }
    finally:
        cleanup(ids)


def test_historical_resolution_uses_only_verified_review_after_asserted_at() -> None:
    ids = make_candidate()
    try:
        before = datetime.now(timezone.utc)
        verified = review(ids, "verify", 1).json()
        asserted_at = datetime.fromisoformat(verified["asserted_at"])
        historical = client.get(
            f"/api/resources/{ids['resource']}/access-resolution",
            params={"test_identity_id": ids["identity"],
                    "evaluation_time": before.isoformat()},
        ).json()
        current = client.get(
            f"/api/resources/{ids['resource']}/access-resolution",
            params={"test_identity_id": ids["identity"],
                    "evaluation_time": asserted_at.isoformat()},
        ).json()
        assert historical["state"] == "insufficient"
        assert ids["candidate"] not in historical["supporting_assertion_ids"]
        assert current["state"] == "resolved"
        assert (current["relationship"], current["expected_access"]) == (
            "non_owner", "allowed"
        )
        assert current["supporting_assertion_ids"] == [verified["id"]]
        assert MAX_ASSERTIONS_SCANNED == 256
    finally:
        cleanup(ids)


def test_rejected_review_and_candidate_never_resolve() -> None:
    ids = make_candidate()
    try:
        outcome = review(ids, "reject", 100).json()
        result = client.get(
            f"/api/resources/{ids['resource']}/access-resolution",
            params={"test_identity_id": ids["identity"],
                    "evaluation_time": (NOW + timedelta(hours=1)).isoformat()},
        ).json()
        assert result["state"] == "insufficient"
        assert outcome["id"] not in result["supporting_assertion_ids"]
        assert ids["candidate"] not in result["supporting_assertion_ids"]
    finally:
        cleanup(ids)
