"""Owned synthetic graphs; no frozen corpus or operator credentials."""
import base64
from datetime import timedelta
import secrets
import pytest
from sqlalchemy import delete
from app.db.models import TestIdentity, Resource, Endpoint
from app.db.models.endpoint_resource_binding import EndpointResourceBinding
from app.db.models.resource_access_assertion import ResourceAccessAssertion
from app.db.models.credential_binding import CredentialBinding
from app.db.models.credential_secret_version import CredentialSecretVersion
from app.db.models.research_subject import ResearchSubjectVersion
from app.db.session import SessionLocal
from app.services.research_context import create_context
from app.services import research_observation as observation_service
from app.schemas.research_observation import canonical
from tests.research_observation_fixtures import preparation, observation, cleanup, call, zero_capabilities  # noqa: F401
from tests.research_intake_fixtures import two_intake_targets, intake, NOW, REF  # noqa: F401


@pytest.fixture
def subject_encryption(monkeypatch):
    """Opt-in per-test encryption; restore both settings after worker teardown."""
    from pydantic import SecretStr
    from app.core.config import settings

    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    monkeypatch.setattr(settings, "credential_encryption_key", SecretStr(key))
    monkeypatch.setattr(settings, "credential_encryption_key_version", "research-subject-test-v1")


def proposal(g):
    return {"format": "research-subject-v1", "context_version": 1, "target_id": g["target"],
        "identity_choice": "anonymous", "test_identity_id": g["anonymous"], "credential_binding_id": None,
        "session_state": "not_applicable", "session_reported_at": None, "session_reference": None,
        "credential_update": "not_applicable", "resource_id": g["resource"], "owner_identity_id": None,
        "endpoint_id": g["endpoint"], "binding_id": g["slot"], "relationship": "unspecified",
        "expected_access": "unspecified", "fact_reference": None, "membership": "unknown",
        "membership_reference": None, "assertion_ids": [], "sources": [], "review": REF}


def assertion(g, relationship="non_owner", access="allowed", *, identity=None, state="verified", **kwargs):
    with SessionLocal() as db:
        a = ResourceAccessAssertion(resource_id=g["resource"], test_identity_id=identity or g["anonymous"],
            relationship=relationship, expected_access=access, provenance="target_fixture",
            confidence=80, verification_state=state, asserted_at=NOW-timedelta(seconds=1), **kwargs)
        db.add(a); db.commit()
        return a.id


@pytest.fixture
def subject_pair(two_intake_targets):
    graphs=[]
    try:
        for project, ids in enumerate(two_intake_targets,1):
            with SessionLocal() as db:
                ctx = create_context(db, {"project_number": project, "intake": intake(ids)}, now=NOW)["context_id"]
                actors = [TestIdentity(target_id=ids["target"], name=f"synthetic_{kind}", auth_type=kind,
                                      credentials=None, is_active=True) for kind in ("anonymous", "bearer")]
                db.add_all(actors); db.flush()
                resource=Resource(target_id=ids["target"], resource_type="folder", external_id=str(7000+project), owner_identity_id=actors[1].id)
                endpoint=Endpoint(target_id=ids["target"], path="/folders/{resource_id}", method="GET")
                db.add_all([resource,endpoint]); db.flush()
                slot=EndpointResourceBinding(endpoint_id=endpoint.id, location="path",selector="resource_id",provenance="operator_supplied",confidence=100,review_state="confirmed")
                db.add(slot);db.commit()
                g={**ids,"ctx":ctx,"project":project,"anonymous":actors[0].id,"bearer":actors[1].id,"resource":resource.id,"endpoint":endpoint.id,"slot":slot.id}
            graphs.append(g)
            call(observation_service.maintain,ctx,{"action":"reconcile","review":REF},project=project)
            call(observation_service.prepare,ctx,preparation(ids),project=project)
            g["observation"] = call(observation_service.accept,ctx,"preparation_1",canonical(observation(project,58122+project)),project=project)["observation_id"]
        yield graphs
    finally:
        for g in reversed(graphs):
            with SessionLocal() as db:
                db.execute(delete(ResearchSubjectVersion).where(ResearchSubjectVersion.context_id==g["ctx"]))
                db.execute(delete(ResourceAccessAssertion).where(ResourceAccessAssertion.resource_id==g["resource"]))
                from sqlalchemy import select
                bindings=list(db.scalars(select(CredentialBinding.id).where(CredentialBinding.test_identity_id.in_([g["anonymous"],g["bearer"]]))))
                db.execute(delete(CredentialSecretVersion).where(CredentialSecretVersion.credential_binding_id.in_(bindings)))
                db.execute(delete(CredentialBinding).where(CredentialBinding.id.in_(bindings)))
                db.execute(delete(EndpointResourceBinding).where(EndpointResourceBinding.endpoint_id==g["endpoint"]))
                db.execute(delete(Resource).where(Resource.id==g["resource"]))
                db.execute(delete(Endpoint).where(Endpoint.id==g["endpoint"]))
                db.execute(delete(TestIdentity).where(TestIdentity.id.in_([g["anonymous"],g["bearer"]])))
                db.commit()
            cleanup(g["ctx"])
