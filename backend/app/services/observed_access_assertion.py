from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.endpoint import Endpoint
from app.db.models.resource import Resource
from app.db.models.resource_access_assertion import ResourceAccessAssertion
from app.db.models.test_case import TestCase
from app.db.models.test_identity import TestIdentity
from app.db.models.test_run import TestRun


class ObservedAccessAssertionError(RuntimeError):
    pass


def load_eligible_source(
    db: Session,
    test_run_id: int,
) -> tuple[TestRun, TestCase, Resource, TestIdentity]:
    run = db.scalar(
        select(TestRun).where(TestRun.id == test_run_id).with_for_update()
    )
    if run is None:
        raise ObservedAccessAssertionError("source_test_run_not_found")
    test_case = db.get(TestCase, run.test_case_id)
    if test_case is None:
        raise ObservedAccessAssertionError("source_test_case_not_found")
    if test_case.test_type != "owner_baseline":
        raise ObservedAccessAssertionError("source_test_run_not_owner_baseline")
    expected_statuses = test_case.expected_statuses
    if (
        run.response_status is None
        or not 200 <= run.response_status < 300
        or not isinstance(expected_statuses, list)
        or run.response_status not in expected_statuses
        or run.error_message is not None
        or run.executed_at is None
    ):
        raise ObservedAccessAssertionError("source_test_run_not_successful")
    resource = db.get(Resource, test_case.resource_id)
    identity = db.get(TestIdentity, test_case.actor_identity_id)
    endpoint = db.get(Endpoint, test_case.endpoint_id)
    if resource is None or identity is None or endpoint is None:
        raise ObservedAccessAssertionError("source_provenance_missing")
    if not (
        resource.target_id == identity.target_id == endpoint.target_id
    ):
        raise ObservedAccessAssertionError("source_provenance_inconsistent")
    return run, test_case, resource, identity


def derive_observed_access_assertion(
    db: Session,
    test_run_id: int,
) -> ResourceAccessAssertion:
    run, _test_case, resource, identity = load_eligible_source(db, test_run_id)
    existing = db.scalar(select(ResourceAccessAssertion).where(
        ResourceAccessAssertion.source_test_run_id == run.id
    ))
    if existing is not None:
        return validate_existing_assertion(existing, run, resource, identity)
    created_id = db.scalar(
        insert(ResourceAccessAssertion)
        .values(
            resource_id=resource.id,
            test_identity_id=identity.id,
            relationship="unspecified",
            expected_access="allowed",
            provenance="observed_baseline",
            confidence=50,
            verification_state="candidate",
            observed_at=run.executed_at,
            valid_from=None,
            valid_until=None,
            source_test_run_id=run.id,
        )
        .on_conflict_do_nothing(
            index_elements=[ResourceAccessAssertion.source_test_run_id]
        )
        .returning(ResourceAccessAssertion.id)
    )
    assertion = (
        db.get(ResourceAccessAssertion, created_id)
        if created_id is not None
        else db.scalar(select(ResourceAccessAssertion).where(
            ResourceAccessAssertion.source_test_run_id == run.id
        ))
    )
    if assertion is None:
        raise ObservedAccessAssertionError("assertion_persistence_conflict")
    return validate_existing_assertion(assertion, run, resource, identity)


def validate_existing_assertion(
    assertion: ResourceAccessAssertion,
    run: TestRun,
    resource: Resource,
    identity: TestIdentity,
) -> ResourceAccessAssertion:
    if not (
        assertion.resource_id == resource.id
        and assertion.test_identity_id == identity.id
        and assertion.relationship == "unspecified"
        and assertion.expected_access == "allowed"
        and assertion.provenance == "observed_baseline"
        and assertion.confidence == 50
        and assertion.verification_state == "candidate"
        and assertion.observed_at == run.executed_at
        and assertion.valid_from is None
        and assertion.valid_until is None
        and assertion.source_test_run_id == run.id
    ):
        raise ObservedAccessAssertionError("source_assertion_conflict")
    return assertion
