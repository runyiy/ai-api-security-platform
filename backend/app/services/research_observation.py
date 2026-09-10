"""Offline synthetic intake. All operations use the W1 context lock first.

No caller commit, network, credentials, executor, raw staging or global lookup.
"""
from datetime import timedelta, timezone
import hashlib
import ipaddress
from urllib.parse import urlsplit
import re
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.db.models.research_observation import (
    ObservationControl, ObservationPreparation, ObservationRecord, ObservationPayload, ObservationEvent,
)
from app.db.models.research_context import ResearchTargetAssociation
from app.db.models.target import Target
from app.schemas.research_context import ResearchIntakeInput
from app.schemas.research_observation import (
    ObservationError, PreparationInput, ReviewInput, HoldInput, ControlInput,
    canonical, parse_observation, timestamp, target_origin, validate,
)
from app.services.research_context import _clean, _context, _latest, _now, ResearchContextError

BOOT_TOKEN = str(uuid.uuid4())
DAYS90 = timedelta(days=90)


def _locked(db, project, context_id):
    if type(project) is not int or not 1 <= project <= 1000000 or type(context_id) is not int or not 1 <= context_id <= 2147483647:
        raise ObservationError()
    try:
        _clean(db)
        return _context(db, project, context_id, lock=True)
    except ResearchContextError:
        raise ObservationError() from None


def _time(now):
    try:
        return _now(now)
    except ResearchContextError:
        raise ObservationError("observation_time_invalid", 422) from None


def _cap(db, model, context_id, cap):
    if db.scalar(select(func.count()).select_from(model).where(model.context_id == context_id)) >= cap:
        raise ObservationError("observation_capacity_exceeded", 409)


def _event(db, context, code, now, record=None, review=None):
    _cap(db, ObservationEvent, context.id, 8192)
    db.add(ObservationEvent(context_id=context.id, observation_id=record.id if record else None,
                            code=code, recorded_at=now, review=review,
                            hold_until=record.hold_until if record and code == "hold" else None))
    db.flush()  # An audit failure is also a failure of read/intake/lifecycle receipt.


def _control(db, context, now):
    control = db.get(ObservationControl, context.id, populate_existing=True)
    if control is None or control.recovery_token != BOOT_TOKEN:
        raise ObservationError("observation_recovery_required")
    # No scheduler: overdue online purge work blocks new consumption/admission.
    rows = db.scalars(select(ObservationRecord).join(ObservationPayload,
        (ObservationPayload.context_id == ObservationRecord.context_id) &
        (ObservationPayload.observation_id == ObservationRecord.id)).where(
        ObservationRecord.context_id == context.id).limit(1025))
    for row in rows:
        if row.hold_until and now < row.hold_until:
            continue
        due = max(row.unavailable_at or row.expires_at, row.hold_until or row.unavailable_at or row.expires_at)
        if _state(row, now) != "available" and now >= due + timedelta(days=1):
            raise ObservationError("observation_maintenance_required")


def _target(db, context, target_id):
    # Close/transfer is serialized by context then association locks, as in W1.
    if context.closed_at:
        raise ObservationError()
    association = db.scalar(select(ResearchTargetAssociation.id).where(
        ResearchTargetAssociation.context_id == context.id,
        ResearchTargetAssociation.target_id == target_id,
        ResearchTargetAssociation.released_at.is_(None)).with_for_update(read=True))
    if association is None:
        raise ObservationError()
    t = db.execute(select(Target.base_url, Target.network_mode).where(Target.id == target_id)
                   .with_for_update(read=True)).one_or_none()
    if t is None or t.network_mode != "private_local":
        raise ObservationError()
    try:
        value = target_origin(t.base_url)
        host = urlsplit(value).hostname
        if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
            raise ValueError()
        return value
    except ValueError:
        raise ObservationError() from None


def _eligible_context(db, context, version):
    latest = _latest(db, context.id)
    intake = ResearchIntakeInput.model_validate(latest.intake)
    if (context.closed_at or latest.version_number != version or intake.data_eligibility != "synthetic"
            or intake.eligibility_reference is None):
        raise ObservationError("observation_data_ineligible")


def _preparation(db, context, ref):
    if not isinstance(ref, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", ref):
        raise ObservationError()
    row = db.scalar(select(ObservationPreparation).where(
        ObservationPreparation.context_id == context.id,
        ObservationPreparation.preparation_ref == ref).execution_options(populate_existing=True))
    if row is None:
        raise ObservationError()
    return row


def _live_preparation(db, context, ref, clock):
    row = _preparation(db, context, ref)
    data = validate(PreparationInput, row.registry)
    if row.revoked_at:
        raise ObservationError()
    _eligible_context(db, context, data.context_version)
    if _target(db, context, row.target_id) != row.origin:
        raise ObservationError()
    # Sample runtime time AFTER ownership/Target lock waits. Explicit times are
    # deterministic service-test inputs; the operator API never accepts a clock.
    now = _time(clock)
    _control(db, context, now)
    if now >= timestamp(data.valid_until):
        raise ObservationError()
    return row, now


def _record(db, context, observation_id):
    if type(observation_id) is not int or not 1 <= observation_id <= 2147483647:
        raise ObservationError()
    row = db.scalar(select(ObservationRecord).where(ObservationRecord.context_id == context.id,
        ObservationRecord.id == observation_id).execution_options(populate_existing=True))
    if row is None:
        raise ObservationError()
    return row


def _state(row, now):
    if row.state != "available":
        return row.state
    if row.hold_until and now < row.hold_until:
        return "held"
    if now >= row.expires_at:
        return "expired"
    return "available"


def _mark_unavailable(row, at, *, end_hold=False):
    # A late delete/quarantine/replay must not restart an already expired clock.
    since = row.unavailable_at or min(row.expires_at, at)
    if end_hold:
        if row.hold_until and row.hold_started_at and row.hold_started_at <= at:
            since = max(since, min(row.hold_until, at))
        row.hold_until = None
    row.unavailable_at = since


def _receipt(row, state):
    return {"status": "accepted", "context_id": row.context_id, "observation_id": row.id,
        "preparation_id": row.preparation_id, "batch_ref": row.batch_ref,
        "format_version": row.format_version, "lifecycle_version": row.lifecycle_version,
        "provenance": row.provenance, "availability": state,
        "accepted_at": row.accepted_at.astimezone(timezone.utc).isoformat(), "expires_at": row.expires_at.astimezone(timezone.utc).isoformat(),
        "digest": row.digest, "entry_order": row.entry_order, "corrects_id": row.corrects_id,
        "execution_authorized": False, "access_truth": "unknown"}


def _payload(db, row):
    p = db.scalar(select(ObservationPayload).where(ObservationPayload.context_id == row.context_id,
                                                  ObservationPayload.observation_id == row.id))
    if p is None or hashlib.sha256(p.canonical_payload.encode("utf-8")).hexdigest() != row.digest:
        raise ObservationError("observation_source_unavailable")
    # Revalidate minimized representation; cannot silently revive corrupt stored data.
    value = parse_observation(p.canonical_payload.encode("utf-8"), row.accepted_at).model_dump()
    if canonical(value).decode() != p.canonical_payload:
        raise ObservationError("observation_source_unavailable")
    return value


def prepare(db, project, context_id, payload, *, now=None):
    payload = validate(PreparationInput, payload)
    context = _locked(db, project, context_id)
    try:
        with db.begin_nested():
            _eligible_context(db, context, payload.context_version)
            selected_origin = _target(db, context, payload.target_id)
            now = _time(now)
            _control(db, context, now)
            if not now < timestamp(payload.valid_until) <= now + timedelta(days=30):
                raise ObservationError("observation_time_invalid", 422)
            if len(canonical(payload.model_dump())) > 16384:
                raise ObservationError("observation_input_limit", 413)
            _cap(db, ObservationPreparation, context.id, 128)
            if payload.corrects_preparation:
                _preparation(db, context, payload.corrects_preparation)
            row = ObservationPreparation(context_id=context.id, target_id=payload.target_id,
                preparation_ref=payload.preparation_ref, registry=payload.model_dump(),
                origin=selected_origin, approved_at=now)
            db.add(row)
            db.flush()
            _event(db, context, "preparation_reviewed", now, review=payload.review.model_dump())
            return {"status": "recorded", "preparation_ref": row.preparation_ref,
                    "project_ref": f"project_{project}", "data_eligibility": "synthetic",
                    "execution_authorized": False}
    except IntegrityError:
        raise ObservationError() from None


def accept(db, project, context_id, preparation_ref, raw, *, corrects_id=None, now=None):
    if corrects_id is not None and (type(corrects_id) is not int or not 1 <= corrects_id <= 2147483647):
        raise ObservationError()
    context = _locked(db, project, context_id)
    try:
        with db.begin_nested():
            prep, now = _live_preparation(db, context, preparation_ref, now)  # Before decoding, within this transaction.
            value = parse_observation(raw, now)
            registry = prep.registry
            if value.project_ref != f"project_{project}" or value.preparation_ref != prep.preparation_ref:
                raise ObservationError()
            if value.batch_ref not in registry["batch_refs"]:
                raise ObservationError()
            for e in value.entries:
                if (e.origin != prep.origin or e.entry_ref not in registry["entry_refs"]
                    or e.path_template not in registry["path_templates"]
                    or not set(e.query_names) <= set(registry["query_names"])
                    or (e.actor_ref is not None and e.actor_ref not in registry["actor_refs"])
                    or not set(e.resource_labels) <= set(registry["resource_labels"])):
                    raise ObservationError()
            # Only qualified canonical payloads are hashed; no original-file hash.
            approved = canonical(value.model_dump())
            digest = hashlib.sha256(approved).hexdigest()
            old = db.scalar(select(ObservationRecord).where(ObservationRecord.context_id == context.id,
                ObservationRecord.batch_ref == value.batch_ref))
            if old:
                if old.digest != digest or old.preparation_id != prep.id or old.corrects_id != corrects_id:
                    raise ObservationError("observation_retry_conflict")
                state = _state(old, now)
                if state == "available":
                    _payload(db, old)
                _event(db, context, "retry", now, old)
                return _receipt(old, state)
            existing = list(db.scalars(select(ObservationRecord).where(
                ObservationRecord.context_id == context.id).order_by(ObservationRecord.id).limit(1025)))
            reused = {r.id for r in existing if {x["entry_ref"] for x in r.entry_order} & {e.entry_ref for e in value.entries}}
            if reused and corrects_id not in reused:
                raise ObservationError("observation_correction_required")
            if corrects_id is not None:
                _record(db, context, corrects_id)  # Scoped soft link; original is never overwritten.
            _cap(db, ObservationRecord, context.id, 1024)
            row = ObservationRecord(format_version="ra-observation/1", lifecycle_version="ra-observation-lifecycle/1",
                provenance="operator_import_unverified", context_id=context.id, preparation_id=prep.id, batch_ref=value.batch_ref,
                digest=digest, entry_order=[{"entry_ref": e.entry_ref, "source_entry_index": e.source_entry_index} for e in value.entries],
                corrects_id=corrects_id, accepted_at=now,
                expires_at=now+timedelta(seconds=registry["retention_seconds"]), state="available")
            db.add(row)
            db.flush()
            db.add(ObservationPayload(context_id=context.id, observation_id=row.id, canonical_payload=approved.decode()))
            _event(db, context, "accepted", now, row)
            return _receipt(row, "available")
    except IntegrityError:
        raise ObservationError() from None


def read(db, project, context_id, observation_id, *, review=None, now=None):
    clock = now
    reviewed = validate(ReviewInput, review) if review is not None else None
    context = _locked(db, project, context_id)
    with db.begin_nested():
        row = _record(db, context, observation_id)
        now = _time(clock)
        state = _state(row, now)
        # History is project-local; unavailable guards prevent all live Target metadata reads after close.
        try:
            if context.closed_at:
                raise ObservationError()
            if state not in ("available", "held"):
                _event(db, context, "read", now, row)
                return _receipt(row, state)
            prep = db.scalar(select(ObservationPreparation).where(ObservationPreparation.context_id == context.id,
                                                                  ObservationPreparation.id == row.preparation_id))
            prep, now = _live_preparation(db, context, prep.preparation_ref, clock)
            state = _state(row, now)
        except ObservationError:
            state = "quarantined"
        result = _receipt(row, state)
        if state == "available" or (state == "held" and reviewed is not None):
            result["payload"] = _payload(db, row)
        _event(db, context, "human_read" if reviewed else "read", now, row,
               reviewed.review.model_dump() if reviewed else None)
        return result


def lifecycle(db, project, context_id, observation_id, action, payload, *, now=None):
    clock = now
    p = validate(HoldInput if action == "hold" else ReviewInput, payload)
    context = _locked(db, project, context_id)
    with db.begin_nested():
        row = _record(db, context, observation_id)
        now = _time(clock)
        state = _state(row, now)
        if action == "hold":
            prep = db.scalar(select(ObservationPreparation).where(ObservationPreparation.context_id == context.id,
                                                                  ObservationPreparation.id == row.preparation_id))
            prep, now = _live_preparation(db, context, prep.preparation_ref, clock)
            state = _state(row, now)
            if state not in ("available", "held"):
                raise ObservationError("observation_source_unavailable")
            _payload(db, row)
            until = timestamp(p.until)
            if not now < until <= now + timedelta(days=30):
                raise ObservationError("observation_time_invalid", 422)
            row.hold_started_at, row.hold_until = now, until
            row.hold_review, row.hold_reason = p.review.model_dump(), p.reason
        elif action == "release":
            if not row.hold_until or not row.hold_started_at or not row.hold_started_at <= now < row.hold_until:
                raise ObservationError("observation_hold_not_active")
            if row.state != "available" or now >= row.expires_at:
                _mark_unavailable(row, now, end_hold=True)
            row.hold_until = None
        elif action in ("delete", "quarantine"):
            row.state = "deleted" if action == "delete" else "quarantined"
            _mark_unavailable(row, now, end_hold=action == "quarantine")
        else:
            raise ObservationError("observation_shape_invalid", 422)
        _event(db, context, action, now, row, p.review.model_dump())
        return _receipt(row, _state(row, now))


def revoke_preparation(db, project, context_id, ref, payload, *, now=None):
    p = validate(ReviewInput, payload)
    context = _locked(db, project, context_id)
    now = _time(now)
    with db.begin_nested():
        prep = _preparation(db, context, ref)
        prep.revoked_at = prep.revoked_at or now
        _event(db, context, "preparation_revoked", now, review=p.review.model_dump())
        return {"status": "unavailable"}


def maintain(db, project, context_id, payload, *, now=None):
    """Explicit bounded recovery/cleanup, never a worker or a backup assertion.

    Each process starts closed. A restored DB must be suspended before exposure;
    this synthetic-only gate does not prove any physical backup inventory.
    """
    p = validate(ControlInput, payload)
    context = _locked(db, project, context_id)
    now = _time(now)
    with db.begin_nested():
        control = db.get(ObservationControl, context.id, populate_existing=True)
        if control is None:
            control = ObservationControl(context_id=context.id)
            db.add(control)
        control.recovery_token = None
        control.reviewed_at, control.review = now, p.review.model_dump()
        purged, tombstones = 0, 0
        # Purge old audit first so an explicit recovery can resolve the audit cap.
        db.execute(delete(ObservationEvent).where(ObservationEvent.context_id == context.id,
                                                 ObservationEvent.recorded_at <= now-DAYS90))
        if p.action == "rotate_audit":
            # Explicit operator-reviewed log retirement, not an audit bypass.
            # Keep the cap; suspension + retirement + replacement audit are atomic.
            count = db.scalar(select(func.count()).select_from(ObservationEvent).where(
                ObservationEvent.context_id == context.id))
            if count != 8192:
                raise ObservationError("observation_rotation_not_required")
            retired = list(db.scalars(select(ObservationEvent.id).where(
                ObservationEvent.context_id == context.id).order_by(ObservationEvent.id).limit(1024)))
            db.execute(delete(ObservationEvent).where(ObservationEvent.context_id == context.id,
                                                     ObservationEvent.id.in_(retired)))
        if p.action == "reconcile":
            rows = list(db.scalars(select(ObservationRecord).where(ObservationRecord.context_id == context.id)
                                   .order_by(ObservationRecord.id).limit(1025)))
            if len(rows) > 1024:
                raise ObservationError("observation_capacity_exceeded")
            # Replay only project-local known IDs before opening the process gate.
            if not set(p.deleted_observation_ids) <= {r.id for r in rows}:
                raise ObservationError()
            for row in rows:
                if row.id in p.deleted_observation_ids:
                    row.state = "deleted"
                    _mark_unavailable(row, now)
                state = _state(row, now)
                prep = db.scalar(select(ObservationPreparation).where(
                    ObservationPreparation.context_id == context.id, ObservationPreparation.id == row.preparation_id))
                if context.closed_at:
                    row.state = "quarantined"
                    state = "quarantined"
                    _mark_unavailable(row, min(context.closed_at, prep.revoked_at or context.closed_at))
                if prep.revoked_at:
                    # Revocation overrides hold even after delete/close/quarantine.
                    row.state, state = "quarantined", "quarantined"
                    _mark_unavailable(row, prep.revoked_at, end_hold=True)
                if state in ("available", "held"):
                    try:
                        data = validate(PreparationInput, prep.registry)
                        _eligible_context(db, context, data.context_version)
                        if _target(db, context, prep.target_id) != prep.origin:
                            raise ObservationError()
                        if now >= timestamp(data.valid_until) and state != "held":
                            raise ObservationError()
                        _payload(db, row)
                    except ObservationError:
                        row.state, state = "quarantined", "quarantined"
                        _mark_unavailable(row, now, end_hold=True)
                if state != "available":
                    if row.hold_until and now < row.hold_until:
                        continue
                    row.unavailable_at = max(row.unavailable_at or row.expires_at,
                                             row.hold_until or row.unavailable_at or row.expires_at)
                    purged += db.execute(delete(ObservationPayload).where(
                        ObservationPayload.context_id == context.id, ObservationPayload.observation_id == row.id)).rowcount
                    if now >= row.unavailable_at + DAYS90:
                        db.delete(row)
                        tombstones += 1
            db.flush()
            # Expired/revoked, unreferenced preparations also have bounded metadata retention.
            for prep in db.scalars(select(ObservationPreparation).where(ObservationPreparation.context_id == context.id)):
                end = prep.revoked_at or timestamp(prep.registry["valid_until"])
                if now >= end + DAYS90 and not db.scalar(select(ObservationRecord.id).where(
                        ObservationRecord.context_id == context.id, ObservationRecord.preparation_id == prep.id).limit(1)):
                    db.delete(prep)
            if not context.closed_at:
                control.recovery_token = BOOT_TOKEN
        _event(db, context, "audit_rotated_1024" if p.action == "rotate_audit" else p.action,
               now, review=p.review.model_dump())
        result = {"status": "ready" if control.recovery_token else "unavailable", "purged_payloads": purged,
                  "removed_tombstones": tombstones, "private_data_admitted": False, "exports_enabled": False}
        if p.action == "rotate_audit":
            result["retired_audit_events"] = 1024
        return result
