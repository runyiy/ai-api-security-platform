"""Fresh synthetic development examples, independent of frozen evaluation material."""
from datetime import timedelta
from unittest.mock import Mock
import pytest
from sqlalchemy import delete, select
from app.db.models.research_observation import ObservationControl, ObservationPreparation, ObservationRecord, ObservationPayload, ObservationEvent
from app.db.session import SessionLocal
from app.services import research_observation as service
from app.services.research_context import create_context
from app.schemas.research_observation import canonical
from tests.research_intake_fixtures import NOW, REF, intake, intake_target, two_intake_targets  # noqa: F401

TABLES = {m.__tablename__ for m in (ObservationControl, ObservationPreparation, ObservationRecord, ObservationPayload, ObservationEvent)}


def observation(project=1, port=58123):
    return {"format": "ra-observation", "version": "1", "project_ref": f"project_{project}",
        "batch_ref": "batch_1", "preparation_ref": "preparation_1", "prepared_at": "2031-04-03T12:00:00Z",
        "entries": [{"entry_ref": "entry_1", "source_entry_index": 0, "observed_at": "2031-04-03T04:59:59-07:00",
            "method": "GET", "origin": f"http://127.0.0.1:{port}", "path_template": "/folders/{resource_id}",
            "query_names": [], "actor_ref": None, "resource_labels": ["resource_1"],
            "response": {"status_code": 200, "media_kind": "json_object", "capture_state": "complete",
                         "object_labels": ["resource_1"], "session_state": "unknown"}}]}


def preparation(ids):
    return {"preparation_ref": "preparation_1", "context_version": 1, "target_id": ids["target"],
        "source": REF, "review": REF, "converter_version": "synthetic_fixture_v1", "data_eligibility": "synthetic",
        "retention_seconds": 2592000, "valid_until": (NOW+timedelta(days=30)).isoformat(),
        "batch_refs": ["batch_1", "batch_2"], "entry_refs": ["entry_1", "entry_2"],
        "actor_refs": ["actor_1"], "resource_labels": ["resource_1", "resource_2"],
        "path_templates": ["/folders/{resource_id}"], "query_names": ["page"], "corrects_preparation": None}


def call(fn, context_id, *args, project=1, now=NOW, **kwargs):
    with SessionLocal() as db:
        result = fn(db, project, context_id, *args, now=now, **kwargs)
        db.commit()
        return result


@pytest.fixture
def observation_context(intake_target):
    with SessionLocal() as db:
        ctx = create_context(db, {"project_number": 1, "intake": intake(intake_target)}, now=NOW)["context_id"]
        db.commit()
    try:
        call(service.maintain, ctx, {"action": "reconcile", "review": REF})
        call(service.prepare, ctx, preparation(intake_target))
        yield ctx, intake_target
    finally:
        cleanup(ctx)


def cleanup(ctx):
    with SessionLocal() as db:
        for model in (ObservationPayload, ObservationRecord, ObservationPreparation, ObservationEvent, ObservationControl):
            db.execute(delete(model).where(model.context_id == ctx))
        db.commit()


@pytest.fixture(autouse=True)
def zero_capabilities(monkeypatch):
    blocked = Mock(side_effect=AssertionError("observation crossed capability boundary"))
    for path in ("app.credentials.bearer.BearerCredentialService.resolve", "app.credentials.bearer.BearerCredentialService.resolve_binding",
                 "app.ai.mock_provider.MockAIProvider.analyze", "app.services.plan_execution.PlanExecutionService.execute",
                 "app.network_safety.gateway.NetworkGateway.request", "socket.getaddrinfo"):
        monkeypatch.setattr(path, blocked)
    yield
    blocked.assert_not_called()
