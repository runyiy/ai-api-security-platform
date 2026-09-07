"""Synthetic persisted runs for exact finding evidence contract tests."""
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.db.models import (
    Endpoint, Finding, FindingEvidenceRecord, Resource, Target, TestCase as StoredCase,
    TestIdentity as StoredIdentity, TestRun as StoredRun,
)
from app.db.session import SessionLocal
from app.generators.bola import BOLA_CROSS_OWNER, OWNER_BASELINE


@pytest.fixture
def evidence_pair():
    with SessionLocal() as db:
        target = Target(name=f"evidence-{uuid4()}", base_url="https://example.test",
                        environment="test", network_mode="private_local")
        db.add(target)
        db.flush()
        actors = [StoredIdentity(target_id=target.id, name=name, auth_type="anonymous",
                                 is_active=True) for name in ("baseline", "probe")]
        endpoints = [Endpoint(target_id=target.id, path=path, method="GET",
                              requires_auth=True, parameters=[])
                     for path in ("/projects/{id}", "/other/{id}")]
        db.add_all([*actors, *endpoints])
        db.flush()
        resources = [Resource(target_id=target.id, resource_type="project",
                              external_id=value, owner_identity_id=actors[0].id)
                     for value in ("private-resource-marker", "other-resource")]
        db.add_all(resources)
        db.flush()
        baseline = StoredCase(endpoint_id=endpoints[0].id, resource_id=resources[0].id,
                              actor_identity_id=actors[0].id, test_type=OWNER_BASELINE,
                              ownership_relation="owner", expected_statuses=[200],
                              status="completed")
        probe = StoredCase(endpoint_id=endpoints[0].id, resource_id=resources[0].id,
                           actor_identity_id=actors[1].id, test_type=BOLA_CROSS_OWNER,
                           ownership_relation="cross_owner", expected_statuses=[403],
                           status="completed")
        db.add_all([baseline, probe])
        db.flush()
        body = json.dumps({"id": resources[0].external_id, "secret": "response-secret"})
        now = datetime.now(timezone.utc)
        runs = [StoredRun(test_case_id=case.id,
                          request_data={"headers": {"Authorization": "request-secret"}},
                          response_status=status, response_body=body, executed_at=at)
                for case, status, at in (
                    (baseline, 200, now - timedelta(days=1)),
                    (probe, 200, now),
                    (baseline, 403, now + timedelta(seconds=1)),
                )]
        db.add_all(runs)
        db.commit()
        ids = dict(target=target.id, endpoint=endpoints[0].id,
                   other_endpoint=endpoints[1].id, resource=resources[0].id,
                   other_resource=resources[1].id, owner=actors[0].id,
                   actor=actors[1].id, baseline_case=baseline.id, probe_case=probe.id,
                   baseline=runs[0].id, probe=runs[1].id, decoy=runs[2].id)
    try:
        yield ids
    finally:
        with SessionLocal() as db:
            db.execute(delete(FindingEvidenceRecord).where(
                FindingEvidenceRecord.finding_id.in_(select(Finding.id).where(
                    Finding.target_id == ids["target"]))))
            db.execute(delete(Finding).where(Finding.target_id == ids["target"]))
            case_ids = select(StoredCase.id).where(StoredCase.endpoint_id.in_(
                [ids["endpoint"], ids["other_endpoint"]]))
            db.execute(delete(StoredRun).where(StoredRun.test_case_id.in_(case_ids)))
            db.execute(delete(StoredCase).where(StoredCase.id.in_(case_ids)))
            db.execute(delete(Resource).where(Resource.target_id == ids["target"]))
            db.execute(delete(Endpoint).where(Endpoint.target_id == ids["target"]))
            db.execute(delete(StoredIdentity).where(StoredIdentity.target_id == ids["target"]))
            db.execute(delete(Target).where(Target.id == ids["target"]))
            db.commit()
