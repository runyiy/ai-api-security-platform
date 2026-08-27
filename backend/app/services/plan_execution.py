from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.auth.context import (
    AuthenticationContextError,
    apply_authentication_context,
    build_authentication_context,
)
from app.credentials.bearer import BearerCredentialError, BearerCredentialService
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.endpoint import Endpoint
from app.db.models.execution_plan import ExecutionPlan
from app.db.models.plan_action import PlanAction
from app.db.models.resource import Resource
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.db.models.test_case import TestCase
from app.db.models.test_identity import TestIdentity
from app.db.models.test_run import TestRun
from app.executors.http import (
    ExecutionBlockedError,
    HTTPExecutionError,
    PolicyEnforcedHTTPExecutor,
)
from app.services.execution_authorization import load_execution_authorization
from app.services.execution_plan_approval import (
    PlanIntegrityError,
    is_plan_approved,
    validate_persisted_plan_integrity,
)
from app.services.safety_audit import (
    SafetyAuditService,
    build_policy_decision_observer,
)
from app.services.test_execution import decode_response_body, redact_headers


class PlanExecutionError(RuntimeError):
    pass


class PlanExecutionNotFoundError(PlanExecutionError):
    pass


class PlanExecutionService:
    def __init__(
        self, *, db: Session, executor: PolicyEnforcedHTTPExecutor
    ) -> None:
        self.db = db
        self.executor = executor

    def execute(self, *, execution_plan_id: int) -> TestRun:
        if self.db.get(ExecutionPlan, execution_plan_id) is None:
            raise PlanExecutionNotFoundError("ExecutionPlan not found.")
        try:
            plan = validate_persisted_plan_integrity(self.db, execution_plan_id)
        except PlanIntegrityError as exc:
            raise PlanExecutionError("ExecutionPlan integrity validation failed.") from exc

        actions = list(
            self.db.scalars(
                select(PlanAction)
                .where(PlanAction.execution_plan_id == plan.id)
                .order_by(PlanAction.ordinal, PlanAction.id)
            )
        )
        if len(actions) != 1:
            raise PlanExecutionError(
                "ExecutionPlan must contain exactly one executable action."
            )
        action = actions[0]
        if action.method != "GET":
            raise PlanExecutionError("ExecutionPlan action must be GET.")
        if action.test_case_id is None or action.resource_id is None:
            raise PlanExecutionError("ExecutionPlan action provenance is incomplete.")

        target = self.db.get(Target, plan.target_id)
        revision = self.db.get(AuthorizationRevision, plan.authorization_revision_id)
        actor = self.db.get(TestIdentity, plan.actor_identity_id)
        test_case = self.db.get(TestCase, action.test_case_id)
        resource = self.db.get(Resource, action.resource_id)
        endpoint = (
            self.db.get(Endpoint, test_case.endpoint_id)
            if test_case is not None
            else None
        )
        if any(
            item is None
            for item in (target, revision, actor, test_case, resource, endpoint)
        ):
            raise PlanExecutionNotFoundError("ExecutionPlan provenance is unavailable.")
        assert target is not None
        assert revision is not None
        assert actor is not None
        assert test_case is not None
        assert resource is not None
        assert endpoint is not None

        if (
            not target.is_enabled
            or target.authorization_revision_id != revision.id
            or target.authorization_profile_id != revision.authorization_profile_id
            or revision.lifecycle_state != "active"
        ):
            self._raise_preflight_blocked(
                target_id=target.id,
                revision_id=revision.id,
                test_case_id=test_case.id,
                plan_id=plan.id,
                action_id=action.id,
                code="execution_plan_authorization_invalid",
                reason="ExecutionPlan authorization is no longer active and exact.",
            )
        if (
            actor.target_id != target.id
            or resource.target_id != target.id
            or endpoint.target_id != target.id
            or test_case.actor_identity_id != actor.id
            or test_case.resource_id != resource.id
        ):
            self._raise_preflight_blocked(
                target_id=target.id,
                revision_id=revision.id,
                test_case_id=test_case.id,
                plan_id=plan.id,
                action_id=action.id,
                code="execution_plan_provenance_invalid",
                reason="ExecutionPlan provenance is inconsistent.",
            )

        approval_satisfied = False
        if revision.require_human_execution_approval:
            approval_satisfied = is_plan_approved(self.db, plan.id)
            if not approval_satisfied:
                self._raise_preflight_blocked(
                    target_id=target.id,
                    revision_id=revision.id,
                    test_case_id=test_case.id,
                    plan_id=plan.id,
                    action_id=action.id,
                    code="execution_plan_approval_required",
                    reason="Exact ExecutionPlan approval is required.",
                )

        try:
            bearer_token = None
            if actor.auth_type == "bearer":
                if plan.credential_binding_id is None:
                    raise BearerCredentialError("Bearer credential is unavailable.")
                bearer_token = BearerCredentialService(db=self.db).resolve_binding(
                    identity=actor,
                    credential_binding_id=plan.credential_binding_id,
                )
            elif plan.credential_binding_id is not None:
                raise AuthenticationContextError(
                    "Anonymous identity cannot use a credential binding."
                )
            auth_context = build_authentication_context(
                actor, bearer_token=bearer_token
            )
            request_headers = apply_authentication_context(
                request_headers={"Accept": "application/json"},
                context=auth_context,
            )
        except (AuthenticationContextError, BearerCredentialError) as exc:
            self._raise_preflight_blocked(
                target_id=target.id,
                revision_id=revision.id,
                test_case_id=test_case.id,
                plan_id=plan.id,
                action_id=action.id,
                code="execution_plan_credential_invalid",
                reason=str(exc),
            )

        observed_status = test_case.status
        if observed_status == "running":
            self.db.commit()
            raise PlanExecutionError("TestCase is already running.")
        acquired = self.db.scalar(
            update(TestCase)
            .where(TestCase.id == test_case.id, TestCase.status == observed_status)
            .values(status="running")
            .returning(TestCase.id)
            .execution_options(synchronize_session=False)
        )
        if acquired is None:
            self.db.commit()
            raise PlanExecutionError("TestCase execution state changed.")
        set_committed_value(test_case, "status", "running")

        request_data = {
            "method": action.method,
            "url": action.url,
            "headers": redact_headers(request_headers),
            "actor_identity_id": actor.id,
            "resource_id": resource.id,
        }
        plan_id = plan.id
        action_id = action.id
        target_id = target.id
        revision_id = revision.id
        scopes = list(
            self.db.scalars(
                select(Scope).where(
                    Scope.target_id == target_id,
                    Scope.is_active.is_(True),
                )
            )
        )
        self.db.commit()

        def refresh_authorization():
            with Session(
                bind=self.db.get_bind(), expire_on_commit=False
            ) as fresh_db:
                try:
                    validate_persisted_plan_integrity(fresh_db, plan_id)
                except PlanIntegrityError as exc:
                    raise ExecutionBlockedError(
                        code="execution_plan_integrity_changed",
                        reason="ExecutionPlan integrity changed before execution.",
                    ) from exc
                fresh_target, fresh_revision, fresh_scopes = (
                    load_execution_authorization(fresh_db, target_id)
                )
                if fresh_revision is None or fresh_revision.id != revision_id:
                    raise ExecutionBlockedError(
                        code="authorization_revision_changed",
                        reason="Target authorization revision changed before execution.",
                    )
                if approval_satisfied and not is_plan_approved(fresh_db, plan_id):
                    raise ExecutionBlockedError(
                        code="execution_plan_approval_changed",
                        reason="ExecutionPlan approval changed before execution.",
                    )
                fresh_db.expunge_all()
                return fresh_target, fresh_revision, fresh_scopes

        observer = build_policy_decision_observer(
            self.db.get_bind(),
            operation="test_execution",
            target_id=target_id,
            test_case_id=test_case.id,
            execution_plan_id=plan_id,
            plan_action_id=action_id,
        )
        try:
            result = self.executor.execute(
                target=target,
                authorization_revision=revision,
                scopes=scopes,
                method=action.method,
                url=action.url,
                headers=request_headers,
                refresh_authorization=refresh_authorization,
                policy_decision_observer=observer,
            )
        except ExecutionBlockedError as exc:
            test_case.status = "blocked"
            SafetyAuditService(self.db).append_execution_outcome(
                outcome="blocked",
                target_id=target_id,
                authorization_revision_id=revision_id,
                test_case_id=test_case.id,
                execution_plan_id=plan_id,
                plan_action_id=action_id,
                code=exc.code,
                reason=exc.reason,
            )
            self.db.commit()
            raise
        except HTTPExecutionError as exc:
            return self._finish(
                test_case=test_case,
                request_data=request_data,
                target_id=target_id,
                revision_id=revision_id,
                plan_id=plan_id,
                action_id=action_id,
                outcome="failed",
                error_message=str(exc),
            )
        return self._finish(
            test_case=test_case,
            request_data=request_data,
            target_id=target_id,
            revision_id=revision_id,
            plan_id=plan_id,
            action_id=action_id,
            outcome="succeeded",
            response_status=result.status_code,
            response_body=decode_response_body(result.body),
            duration_ms=result.duration_ms,
        )

    def _raise_preflight_blocked(
        self,
        *,
        target_id: int,
        revision_id: int,
        test_case_id: int,
        plan_id: int,
        action_id: int,
        code: str,
        reason: str,
    ) -> None:
        try:
            SafetyAuditService(self.db).append_execution_outcome(
                outcome="blocked",
                target_id=target_id,
                authorization_revision_id=revision_id,
                test_case_id=test_case_id,
                execution_plan_id=plan_id,
                plan_action_id=action_id,
                code=code,
                reason=reason,
            )
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            raise ExecutionBlockedError(
                code="safety_audit_persistence_failed",
                reason="Required safety audit record could not be persisted.",
            ) from exc
        raise ExecutionBlockedError(code=code, reason=reason)

    def _finish(
        self,
        *,
        test_case: TestCase,
        request_data: dict[str, object],
        target_id: int,
        revision_id: int,
        plan_id: int,
        action_id: int,
        outcome: str,
        response_status: int | None = None,
        response_body: str | None = None,
        duration_ms: int | None = None,
        error_message: str | None = None,
    ) -> TestRun:
        run = TestRun(
            test_case_id=test_case.id,
            authorization_revision_id=revision_id,
            request_data=request_data,
            response_status=response_status,
            response_body=response_body,
            duration_ms=duration_ms,
            error_message=error_message,
        )
        self.db.add(run)
        self.db.flush()
        SafetyAuditService(self.db).append_execution_outcome(
            outcome=outcome,
            target_id=target_id,
            authorization_revision_id=revision_id,
            test_case_id=test_case.id,
            execution_plan_id=plan_id,
            plan_action_id=action_id,
            test_run=run,
            code=(
                "http_execution_succeeded"
                if outcome == "succeeded"
                else "http_execution_failed"
            ),
            reason=(
                "HTTP execution completed."
                if outcome == "succeeded"
                else "HTTP execution failed."
            ),
        )
        test_case.status = "completed" if outcome == "succeeded" else "failed"
        self.db.commit()
        self.db.refresh(run)
        return run
