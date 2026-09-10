"""Operator intake only. No policy evaluation, credentials, network or execution."""
from datetime import datetime, timezone
import hashlib
import json
import math

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.research_context import ResearchContext, ResearchContextVersion, ResearchTargetAssociation
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.schemas.research_context import (
    ResearchContextCreate, ResearchContextCorrection, ResearchContextClose, ResearchIntakeInput,
)


class ResearchContextError(Exception):
    def __init__(self, code="intake_context_unavailable", status=409):
        self.code, self.status = code, status
        super().__init__(code)


def _clean(db):
    if db.new or db.dirty or db.deleted:
        raise ResearchContextError("intake_session_not_clean")


def _now(now):
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ResearchContextError("intake_invalid_request", 422)
    return now.astimezone(timezone.utc)


def _validate(schema, value):
    # Revalidate even a constructed/mutated Pydantic instance at the service boundary.
    try:
        return schema.model_validate(value.model_dump(warnings=False) if isinstance(value, schema) else value)
    except (ValueError, TypeError, AttributeError):
        raise ResearchContextError("intake_invalid_request", 422) from None


def _authorization_metadata(db, context, item):
    # Callers hold the context lock (or own its uncommitted insertion). Keep
    # the association locked until the caller transaction ends as well.
    if context.closed_at is not None:
        return None, None, [], None
    association_id = db.scalar(select(ResearchTargetAssociation.id).where(
        ResearchTargetAssociation.context_id == context.id,
        ResearchTargetAssociation.target_id == item.target_id,
        ResearchTargetAssociation.released_at.is_(None),
    ).with_for_update(read=True))
    if association_id is None:
        return None, None, [], None
    t = db.execute(select(Target.id, Target.authorization_profile_id,
        Target.authorization_revision_id, Target.base_url, Target.is_enabled, Target.network_mode)
        .where(Target.id == item.target_id).with_for_update(read=True)).mappings().one_or_none()
    if t is None:
        return None, None, [], None
    r = None
    if (item.authorization_revision_id is not None
            and item.authorization_revision_id == t["authorization_revision_id"]):
        r = db.execute(select(AuthorizationRevision.id, AuthorizationRevision.authorization_profile_id,
            AuthorizationRevision.revision_number, AuthorizationRevision.lifecycle_state,
            AuthorizationRevision.valid_from, AuthorizationRevision.valid_until,
            AuthorizationRevision.automation_allowed, AuthorizationRevision.allow_get,
            AuthorizationRevision.max_requests_per_second, AuthorizationRevision.require_human_execution_approval)
            .where(AuthorizationRevision.id == item.authorization_revision_id,
                   AuthorizationRevision.authorization_profile_id == t["authorization_profile_id"])
            ).mappings().one_or_none()
    # Do not read Scope, hash metadata or compare rates for an unavailable or
    # foreign selection. In particular, never distinguish a foreign ID's existence.
    if item.authorization_revision_id is not None and r is None:
        return t, None, [], None
    scopes = [dict(row) for row in db.execute(select(Scope.id, Scope.hostname, Scope.path_pattern,
        Scope.allowed_methods, Scope.is_active).where(Scope.target_id == item.target_id).order_by(Scope.id).limit(257)).mappings()]
    value = {"target": dict(t), "revision": dict(r) if r else None, "scopes": scopes}
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                      default=lambda v: v.isoformat()).encode()).hexdigest()
    return t, r, scopes, digest


def _permission_status(t, r, scopes, item, original, now):
    if t is None or (item.authorization_revision_id is not None and r is None):
        return "unavailable"
    if item.authorization_revision_id is None or item.permission_source is None:
        return "missing"
    if (r["authorization_profile_id"] != t["authorization_profile_id"]
            or r["id"] != t["authorization_revision_id"]):
        return "mismatched"
    if r["lifecycle_state"] != "active":
        return r["lifecycle_state"] if r["lifecycle_state"] in {"draft", "superseded", "revoked"} else "unavailable"
    if r["valid_from"] and now < r["valid_from"]:
        return "not_yet_valid"
    if r["valid_until"] and now >= r["valid_until"]:
        return "expired"
    if not t["is_enabled"] or t["network_mode"] != "private_local":
        return "target_unavailable"
    if not r["allow_get"] or not r["automation_allowed"]:
        return "get_not_permitted"
    if len(scopes) > 256:
        return "scope_limit_exceeded"
    if not any(s["is_active"] and "GET" in s["allowed_methods"] for s in scopes):
        return "scope_missing"
    if original is False:
        return "changed"
    return "referenced_current"


def _context(db, project_number, context_id, *, lock=False):
    if type(project_number) is not int or type(context_id) is not int or min(project_number, context_id) < 1:
        raise ResearchContextError("intake_context_unavailable", 404)
    q = select(ResearchContext).where(ResearchContext.id == context_id,
                                    ResearchContext.project_number == project_number)
    if lock:
        q = q.with_for_update()
    row = db.scalar(q.execution_options(populate_existing=True))
    if row is None:
        raise ResearchContextError("intake_context_unavailable", 404)
    return row


def _latest(db, context_id):
    return db.scalar(select(ResearchContextVersion).where(ResearchContextVersion.context_id == context_id)
                     .order_by(ResearchContextVersion.version_number.desc()).limit(1)
                     .execution_options(populate_existing=True))


def _append(db, context, intake, number, reference, now):
    snapshots = {}
    for item in intake.targets:
        t, r, _, digest = _authorization_metadata(db, context, item)
        if (t is None or t["network_mode"] != "private_local"
                or (item.authorization_revision_id is not None and r is None)):
            raise ResearchContextError()
        snapshots[str(item.target_id)] = digest
    row = ResearchContextVersion(context_id=context.id, version_number=number,
        intake=intake.model_dump(mode="json"), permission_snapshots=snapshots,
        correction_reference=reference.model_dump() if reference else None,
        provenance="operator_recorded_unverified", recorded_at=now)
    db.add(row)
    db.flush()
    return row


def create_context(db: Session, payload, *, now=None):
    payload = _validate(ResearchContextCreate, payload)
    now = _now(now)
    _clean(db)
    try:
        with db.begin_nested():
            context = ResearchContext(project_number=payload.project_number, created_at=now)
            db.add(context)
            db.flush()
            for item in sorted(payload.intake.targets, key=lambda t: t.target_id):
                # Establish exclusive active ownership before any live metadata read.
                db.add(ResearchTargetAssociation(context_id=context.id, target_id=item.target_id,
                    review_reference=item.association_review.model_dump(), reviewed_at=now))
            db.flush()  # Partial unique index arbitrates concurrent ownership, not a precheck.
            row = _append(db, context, payload.intake, 1, None, now)
            return _readiness(db, context, row, now)
    except IntegrityError:
        raise ResearchContextError() from None


def correct_context(db: Session, project_number, context_id, payload, *, now=None):
    payload = _validate(ResearchContextCorrection, payload)
    now = _now(now)
    _clean(db)
    with db.begin_nested():
        context = _context(db, project_number, context_id, lock=True)
        latest = _latest(db, context.id)
        if context.closed_at or latest.version_number != payload.expected_version or latest.version_number >= 10000:
            raise ResearchContextError("intake_version_conflict")
        old = ResearchIntakeInput.model_validate(latest.intake)
        # Changing project membership requires close + explicit review in a new context.
        if {i.target_id: i.association_review for i in old.targets} != {
                i.target_id: i.association_review for i in payload.intake.targets}:
            raise ResearchContextError()
        row = _append(db, context, payload.intake, latest.version_number + 1,
                      payload.correction_reference, now)
        return _readiness(db, context, row, now)


def close_context(db: Session, project_number, context_id, payload, *, now=None):
    payload = _validate(ResearchContextClose, payload)
    now = _now(now)
    _clean(db)
    with db.begin_nested():
        context = _context(db, project_number, context_id, lock=True)
        row = _latest(db, context.id)
        if context.closed_at or row.version_number != payload.expected_version:
            raise ResearchContextError("intake_version_conflict")
        context.closed_at, context.closure_reference = now, payload.closure_reference.model_dump()
        associations = db.scalars(select(ResearchTargetAssociation)
            .where(ResearchTargetAssociation.context_id == context.id).order_by(ResearchTargetAssociation.target_id))
        for association in associations:
            association.released_at = now
        db.flush()
        return _readiness(db, context, row, now)


def read_context(db: Session, project_number, context_id, *, version=None, now=None):
    _clean(db)
    now = _now(now)
    with db.no_autoflush:
        # Serializes latest/history reads with correction and close/transfer.
        # Caller retains the transaction and must promptly end it after reading.
        context = _context(db, project_number, context_id, lock=True)
        row = _latest(db, context.id) if version is None else db.scalar(select(ResearchContextVersion)
            .where(ResearchContextVersion.context_id == context.id,
                   ResearchContextVersion.version_number == version))
        if row is None:
            raise ResearchContextError("intake_context_unavailable", 404)
        return _readiness(db, context, row, now)


def _readiness(db, context, row, now):
    intake = ResearchIntakeInput.model_validate(row.intake)
    permissions, budget_rate_exceeded = [], False
    for item in intake.targets:
        t, r, scopes, digest = _authorization_metadata(db, context, item)
        status = _permission_status(t, r, scopes, item,
            row.permission_snapshots.get(str(item.target_id)) == digest, now)
        rate = intake.budget.rate_millirequests_per_second
        if r and rate is not None:
            cap = r["max_requests_per_second"]
            budget_rate_exceeded |= not math.isfinite(cap) or cap <= 0 or rate > cap * 1000
        permissions.append({"target_id": item.target_id,
            "authorization_revision_id": item.authorization_revision_id, "status": status})
    latest = _latest(db, context.id)
    gaps = []
    if any(p["status"] != "referenced_current" for p in permissions):
        gaps.append("permission_missing")
    if intake.data_eligibility != "synthetic" or intake.eligibility_reference is None:
        gaps.append("data_ineligible")
    # A reference is a recorded claim, never an approval authority. Real B/task caps remain unresolved.
    gaps.extend(["budget_unapproved", "facts_missing"])
    budget_missing = [k for k, v in intake.budget.model_dump().items() if v is None]
    return {
        "version": "research-intake-v1", "context_id": context.id,
        "project_number": context.project_number, "context_version": row.version_number,
        "latest_version": latest.version_number, "is_current": row.version_number == latest.version_number,
        "state": "closed" if context.closed_at else "draft", "recorded_at": row.recorded_at.isoformat(),
        "evaluated_at": now.isoformat(), "provenance": "operator_recorded_unverified",
        "correction_reference": row.correction_reference, "closure_reference": context.closure_reference,
        "intake": intake.model_dump(mode="json"), "permissions": permissions,
        "missing_inputs": gaps, "budget_missing_fields": budget_missing,
        "budget_rate_exceeded": budget_rate_exceeded, "budget_approval": "unverified",
        "execution_preparation_allowed": False, "execution_authorized": False,
        "activity_limits": {"target_requests": 0, "provider_calls": 0, "provider_cost_microusd": 0},
    }
