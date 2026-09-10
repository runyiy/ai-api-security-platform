"""Offline W3 context only. No legacy writes, secret resolution or execution."""
from datetime import timezone
import re
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import load_only
from app.db.models.research_subject import ResearchSubjectVersion
from app.db.models.test_identity import TestIdentity
from app.db.models.resource import Resource
from app.db.models.endpoint import Endpoint
from app.db.models.endpoint_resource_binding import EndpointResourceBinding
from app.db.models.resource_access_assertion import ResourceAccessAssertion
from app.db.models.credential_binding import CredentialBinding
from app.db.models.credential_secret_version import CredentialSecretVersion
from app.db.models.research_observation import ObservationRecord, ObservationPreparation
from app.schemas.research_subject import SubjectInput, SubjectCorrection, SubjectError, validate
from app.schemas.research_observation import ObservationError, timestamp
from app.services import research_observation as observation
from app.services.research_context import ResearchContextError
from app.services.resource_access_resolution import resolve_resource_access, ResourceAccessResolutionError
from app.services.bola_binding_selection import select_bola_binding, BOLABindingSelectionError


def _context(db, project, context_id):
    try:
        return observation._locked(db, project, context_id)
    except ObservationError:
        raise SubjectError() from None


def _number(value):
    if type(value) is not int or not 1 <= value <= 1024:
        raise SubjectError("research_subject_invalid", 422)


def _latest(db, context_id, number):
    return db.scalar(select(ResearchSubjectVersion).where(
        ResearchSubjectVersion.context_id == context_id, ResearchSubjectVersion.proposal_number == number)
        .order_by(ResearchSubjectVersion.version_number.desc()).limit(1).execution_options(populate_existing=True))


def _identity(db, target, identity_id):
    if identity_id is None:
        return None
    row = db.scalar(select(TestIdentity).options(load_only(
        TestIdentity.id, TestIdentity.target_id, TestIdentity.auth_type, TestIdentity.is_active, raiseload=True))
        .where(TestIdentity.id == identity_id, TestIdentity.target_id == target)
        .with_for_update(read=True).execution_options(populate_existing=True))
    if row is None or not row.is_active:
        raise SubjectError()
    return row


def _qualify(db, context, p, clock):
    # Closed/released context must stop BEFORE any legacy reference lookup.
    observation._eligible_context(db, context, p.context_version)
    observation._target(db, context, p.target_id)
    identity = _identity(db, p.target_id, p.test_identity_id)
    _identity(db, p.target_id, p.owner_identity_id)
    if identity and identity.auth_type != p.identity_choice:
        raise SubjectError()
    resource = None
    if p.resource_id is not None:
        resource = db.scalar(select(Resource).options(load_only(Resource.id, Resource.target_id, raiseload=True))
            .where(Resource.id == p.resource_id, Resource.target_id == p.target_id)
            .with_for_update(read=True).execution_options(populate_existing=True))
        if resource is None:
            raise SubjectError()
    endpoint = None
    if p.endpoint_id is not None:
        endpoint = db.execute(select(Endpoint.id, Endpoint.method, Endpoint.path).where(
            Endpoint.id == p.endpoint_id, Endpoint.target_id == p.target_id).with_for_update(read=True)).one_or_none()
        if endpoint is None:
            raise SubjectError()
    slot = None
    if p.binding_id is not None:
        slot = db.scalar(select(EndpointResourceBinding.id).where(
            EndpointResourceBinding.id == p.binding_id, EndpointResourceBinding.endpoint_id == p.endpoint_id)
            .with_for_update(read=True))
        if slot is None:
            raise SubjectError()
    credential_version = None
    if p.credential_binding_id is not None:
        binding = db.scalar(select(CredentialBinding.id).where(
            CredentialBinding.id == p.credential_binding_id,
            CredentialBinding.test_identity_id == p.test_identity_id,
            CredentialBinding.is_active.is_(True), CredentialBinding.auth_type == "bearer",
            CredentialBinding.source_type == "stored_secret").with_for_update(read=True))
        if binding is None:
            raise SubjectError()
        # The existing token update service holds identity then binding FOR UPDATE.
        # Only a nonsecret version ID is sampled under the same lock order.
        credential_version = db.scalar(select(CredentialSecretVersion.id).where(
            CredentialSecretVersion.credential_binding_id == binding).order_by(CredentialSecretVersion.id.desc()).limit(1))
    for aid in sorted(p.assertion_ids):
        found = db.scalar(select(ResourceAccessAssertion.id).where(ResourceAccessAssertion.id == aid,
            ResourceAccessAssertion.resource_id == p.resource_id,
            ResourceAccessAssertion.test_identity_id == p.test_identity_id).with_for_update(read=True))
        if found is None:
            raise SubjectError()
    now = observation._time(clock)  # After metadata lock waits; API supplies no clock.
    if p.session_reported_at and timestamp(p.session_reported_at) > now:
        raise SubjectError("research_subject_invalid", 422)
    gaps = ["budget_unapproved", "intent_decision_pending"]
    if identity is None:
        gaps.append("identity_missing")
    if p.identity_choice == "bearer":
        gaps.append("session_health_unverified")
        if credential_version is None or p.credential_update != "operator_reported_updated":
            gaps.append("credential_update_needed")
    if p.session_state not in {"not_applicable", "operator_reported_valid"}:
        gaps.append("session_"+p.session_state)
    if p.resource_id is None:
        gaps.append("resource_missing")
    if p.owner_identity_id is None:
        gaps.append("owner_unknown")
    # A Resource-to-slot proposal is never membership approval, even if it looks complete.
    gaps.append("membership_unverified")
    if slot is None:
        gaps.append("slot_missing")
    else:
        try:
            selected = select_bola_binding(db, endpoint_id=p.endpoint_id, binding_id=p.binding_id)
            if (selected.location != "path" or endpoint.method != "GET"
                    or len(re.findall(r"\{[^{}]+\}", endpoint.path)) != 1
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*_id", selected.selector)):
                gaps.append("preview_only_shape")
        except BOLABindingSelectionError:
            gaps.append("slot_unavailable")
    facts = {"state": "insufficient", "relationship": "unspecified", "expected_access": "unspecified", "supporting_assertion_ids": []}
    if resource is not None and identity is not None:
        try:
            resolved = resolve_resource_access(db, resource.id, identity.id, now)
            facts = {k: getattr(resolved, k) for k in facts}
            facts["supporting_assertion_ids"] = list(facts["supporting_assertion_ids"])
        except ResourceAccessResolutionError:
            raise SubjectError("research_subject_facts_limit") from None
    if facts["state"] == "conflict":
        gaps.append("facts_conflict")
    elif facts["state"] == "insufficient" or facts["expected_access"] == "unspecified":
        gaps.append("facts_missing")
    if p.identity_choice == "bearer" and facts["relationship"] == "unspecified":
        gaps.append("relationship_missing")
    if not set(p.assertion_ids) <= set(facts["supporting_assertion_ids"]):
        gaps.append("assertion_not_current_verified")
    for dimension in ("relationship", "expected_access"):
        proposed = getattr(p, dimension)
        if proposed != "unspecified" and facts[dimension] not in {"unspecified", proposed}:
            gaps.append("proposal_fact_conflict")
    if (p.owner_identity_id is not None and identity is not None
            and ((facts["relationship"] == "owner" and p.owner_identity_id != identity.id)
                 or (facts["relationship"] == "non_owner" and p.owner_identity_id == identity.id))):
        gaps.append("proposal_fact_conflict")
    # Do not copy source fields/aliases/session claims into the durable proposal.
    for source in p.sources:
        record = db.scalar(select(ObservationRecord).where(ObservationRecord.context_id == context.id,
            ObservationRecord.id == source.observation_id))
        if record is None:
            raise SubjectError()
        prep = db.scalar(select(ObservationPreparation).where(ObservationPreparation.context_id == context.id,
            ObservationPreparation.id == record.preparation_id, ObservationPreparation.target_id == p.target_id))
        if prep is None:
            raise SubjectError()
        result = observation.read(db, context.project_number, context.id, source.observation_id, now=clock)
        if result["availability"] != "available" or "payload" not in result:
            raise SubjectError()
        if source.source_entry_index not in {e["source_entry_index"] for e in result["payload"]["entries"]}:
            raise SubjectError()
    return now, credential_version, facts, sorted(set(gaps))


def _result(row, latest, now, *, proposal=None, facts=None, gaps=None):
    return {"format": "research-subject-v1", "context_id": row.context_id,
        "proposal_number": row.proposal_number, "version_number": row.version_number,
        "latest_version": latest, "recorded_at": row.recorded_at.astimezone(timezone.utc).isoformat(), "evaluated_at": now.isoformat(),
        "provenance": "operator_proposed_unverified", "status": "NEEDS_INPUT",
        "availability": "available" if proposal is not None else "unavailable",
        "proposal": proposal, "facts": facts, "missing_inputs": gaps or ["source_or_context_unavailable"],
        "correction_reference": row.correction_reference,
        "execution_preparation_allowed": False, "execution_authorized": False}


def record(db, project, context_id, number, payload, *, correction=False, now=None):
    _number(number)
    data = validate(SubjectCorrection if correction else SubjectInput, payload)
    p = data.proposal if correction else data
    context = _context(db, project, context_id)
    try:
        with db.begin_nested():
            old = _latest(db, context.id, number)
            if (not correction and old is not None) or (correction and (old is None or old.version_number != data.expected_version)):
                raise SubjectError("research_subject_version_conflict")
            if old:
                previous = validate(SubjectInput, old.proposal)
                # Correction cannot detach a lifecycle dependency or transfer a proposal.
                if previous.target_id != p.target_id or not {
                    (s.observation_id, s.source_entry_index) for s in previous.sources
                } <= {(s.observation_id, s.source_entry_index) for s in p.sources}:
                    raise SubjectError()
            if db.scalar(select(func.count()).select_from(ResearchSubjectVersion).where(
                    ResearchSubjectVersion.context_id == context.id)) >= 1024:
                raise SubjectError("research_subject_capacity_exceeded")
            at, credential_version, facts, gaps = _qualify(db, context, p, now)
            row = ResearchSubjectVersion(context_id=context.id, target_id=p.target_id, context_version=p.context_version,
                proposal_number=number, version_number=old.version_number+1 if old else 1,
                proposal=p.model_dump(), credential_version_id=credential_version, recorded_at=at,
                correction_reference=data.correction_reference.model_dump() if correction else None)
            db.add(row)
            db.flush()
            return _result(row, row.version_number, at, proposal=p.model_dump(), facts=facts, gaps=gaps)
    except (ObservationError, ResearchContextError, IntegrityError):
        raise SubjectError() from None


def read(db, project, context_id, number, *, version=None, now=None):
    _number(number)
    if version is not None:
        _number(version)
    context = _context(db, project, context_id)
    with db.begin_nested():
        latest = _latest(db, context.id, number)
        row = latest if version is None else db.scalar(select(ResearchSubjectVersion).where(
            ResearchSubjectVersion.context_id == context.id, ResearchSubjectVersion.proposal_number == number,
            ResearchSubjectVersion.version_number == version))
        if row is None or latest is None:
            raise SubjectError()
        at = observation._time(now)
        try:
            # Roll back all source read audits if any dependency becomes unavailable.
            with db.begin_nested():
                p = validate(SubjectInput, row.proposal)
                at, credential_version, facts, gaps = _qualify(db, context, p, now)
                if credential_version != row.credential_version_id:
                    gaps.append("credential_changed")
                return _result(row, latest.version_number, at, proposal=p.model_dump(), facts=facts, gaps=sorted(set(gaps)))
        except ObservationError as exc:
            if exc.code == "observation_capacity_exceeded":
                raise SubjectError("research_subject_audit_unavailable") from None
            return _result(row, latest.version_number, at)
        except (ResearchContextError, SubjectError):
            return _result(row, latest.version_number, at)
