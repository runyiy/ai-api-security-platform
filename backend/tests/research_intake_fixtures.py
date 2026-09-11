"""Exclusively synthetic intake fixtures. No Target requests."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.db.base import Base
from app.db.models import AuthorizationProfile, AuthorizationRevision, Scope, Target
from app.db.models.research_context import ResearchContext, ResearchContextVersion, ResearchTargetAssociation
from app.db.session import SessionLocal, engine

NOW = datetime(2031, 4, 3, 12, tzinfo=timezone.utc)
NEW_TABLES = {"research_contexts", "research_context_versions", "research_target_associations"}
OBSERVATION_TABLES = {"research_observation_controls", "research_observation_events",
                     "research_observation_preparations", "research_observation_records", "research_observation_payloads"}
KNOWLEDGE_TABLES = {"research_knowledge_versions", "research_knowledge_events", "research_knowledge_audit"}
RULE_VALIDATION_TABLES = {"research_rule_validations", "research_rule_feedback", "research_rule_feedback_reviews"}
INTENT_TABLES = {'research_intent_mappings','research_intent_manifests','research_intent_versions',
                 'research_intent_budget_decisions','research_intent_plan_members','research_intent_audit'}
VERIFICATION_TABLES = {"research_verification_"+name for name in ("contracts","health_selections","attempts","witnesses","pairs","audit","clock_faults")}
REF = {"kind": "synthetic_fixture", "fixture_id": 1, "version": 1}


def intake(ids):
    return {
        "version": "research-intake-v1", "purpose": "synthetic_bola_research",
        "data_eligibility": "synthetic", "eligibility_reference": dict(REF),
        "rules": [{"rule": "get_only", "source": dict(REF)}],
        "targets": [{"target_id": ids["target"], "association_review": dict(REF),
                     "authorization_revision_id": ids["revision"], "permission_source": dict(REF)}],
        "budget": {"target_requests": 10, "duration_seconds": 60, "concurrency": 1,
                   "rate_millirequests_per_second": 500, "model_tokens": None,
                   "model_cost_microusd": None, "currency": "USD", "accounting_version": "proposal_v1",
                   "approval_reference": None},
    }


def snapshot(*, legacy=False):
    with engine.connect() as db:
        return {t.name: list(db.execute(select(t).order_by(*t.primary_key.columns)).mappings())
                for t in Base.metadata.sorted_tables if not legacy or t.name not in (NEW_TABLES | OBSERVATION_TABLES | KNOWLEDGE_TABLES | RULE_VALIDATION_TABLES | INTENT_TABLES | VERIFICATION_TABLES | {"research_subject_versions"})}


@pytest.fixture
def intake_target():
    with SessionLocal() as db:
        profile = AuthorizationProfile(name="synthetic", program_name="synthetic", authorization_type="lab")
        db.add(profile)
        db.flush()
        revision = AuthorizationRevision(authorization_profile_id=profile.id, revision_number=1,
            lifecycle_state="active", name="synthetic", program_name="synthetic", authorization_type="lab",
            automation_allowed=True, allow_get=True, valid_from=NOW-timedelta(days=3650),
            valid_until=NOW+timedelta(days=3650), max_requests_per_second=1)
        db.add(revision)
        db.flush()
        target = Target(name="synthetic", base_url="http://127.0.0.1:58123", network_mode="private_local",
            authorization_profile_id=profile.id, authorization_revision_id=revision.id)
        db.add(target)
        db.flush()
        scope = Scope(target_id=target.id, hostname="127.0.0.1", path_pattern="/folders/*", allowed_methods=["GET"])
        db.add(scope)
        db.commit()
        ids = {"target": target.id, "revision": revision.id, "profile": profile.id, "scope": scope.id}
    try:
        yield ids
    finally:
        with SessionLocal() as db:
            context_ids = list(db.scalars(select(ResearchTargetAssociation.context_id)
                .where(ResearchTargetAssociation.target_id == ids["target"])))
            db.execute(delete(ResearchContextVersion).where(ResearchContextVersion.context_id.in_(context_ids)))
            db.execute(delete(ResearchTargetAssociation).where(ResearchTargetAssociation.context_id.in_(context_ids)))
            db.execute(delete(ResearchContext).where(ResearchContext.id.in_(context_ids)))
            db.execute(delete(Scope).where(Scope.target_id == ids["target"]))
            db.execute(delete(Target).where(Target.id == ids["target"]))
            db.execute(delete(AuthorizationRevision).where(AuthorizationRevision.authorization_profile_id == ids["profile"]))
            db.execute(delete(AuthorizationProfile).where(AuthorizationProfile.id == ids["profile"]))
            db.commit()


@pytest.fixture
def two_intake_targets():
    """Two independently owned fixture graphs, including distinct profiles/revisions."""
    first = intake_target.__wrapped__()
    second = intake_target.__wrapped__()
    try:
        a, b = next(first), next(second)
        with SessionLocal() as db:
            db.get(Target, b["target"]).base_url = "http://127.0.0.1:58124"
            db.commit()
        yield a, b
    finally:
        second.close()
        first.close()
