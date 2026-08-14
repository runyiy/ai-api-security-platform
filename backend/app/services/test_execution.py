from urllib.parse import quote

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.auth.context import (
    AuthenticationContextError,
    apply_authentication_context,
    build_authentication_context,
)
from app.db.models.endpoint import Endpoint
from app.db.models.resource import Resource
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.db.models.test_case import TestCase
from app.db.models.test_identity import (
    TestIdentity,
)
from app.db.models.test_run import TestRun
from app.executors.http import (
    ExecutionBlockedError,
    HTTPExecutionError,
    PolicyEnforcedHTTPExecutor,
)
from app.generators.bola import (
    detect_resource_binding,
)


class TestExecutionError(RuntimeError):
    pass


class TestExecutionNotFoundError(
    TestExecutionError
):
    pass


def build_test_case_url(
    *,
    target: Target,
    endpoint: Endpoint,
    resource: Resource,
) -> str:
    if endpoint.target_id != target.id:
        raise TestExecutionError(
            "Endpoint belongs to a different target."
        )

    if resource.target_id != target.id:
        raise TestExecutionError(
            "Resource belongs to a different target."
        )

    binding = detect_resource_binding(
        endpoint
    )

    if binding is None:
        raise TestExecutionError(
            "Unable to determine endpoint "
            "resource binding."
        )

    if (
        binding.resource_type
        != resource.resource_type
    ):
        raise TestExecutionError(
            "Resource type does not match endpoint."
        )

    placeholder = (
        "{"
        f"{binding.parameter_name}"
        "}"
    )

    if placeholder not in endpoint.path:
        raise TestExecutionError(
            "Endpoint resource placeholder "
            "is missing."
        )

    encoded_external_id = quote(
        resource.external_id,
        safe="",
    )

    request_path = endpoint.path.replace(
        placeholder,
        encoded_external_id,
        1,
    )

    if "{" in request_path or "}" in request_path:
        raise TestExecutionError(
            "Endpoint contains unresolved "
            "path parameters."
        )

    return (
        target.base_url.rstrip("/")
        + request_path
    )

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "x-api-key",
}


def redact_headers(
    headers: dict[str, str],
) -> dict[str, str]:
    result: dict[str, str] = {}

    for name, value in headers.items():
        if (
            name.strip().lower()
            in SENSITIVE_HEADER_NAMES
        ):
            result[name] = "[REDACTED]"
        else:
            result[name] = value

    return result


MAX_STORED_RESPONSE_BYTES = 64_000


def decode_response_body(
    body: bytes,
) -> str:
    return body[
        :MAX_STORED_RESPONSE_BYTES
    ].decode(
        "utf-8",
        errors="replace",
    )


class TestExecutionService:
    def __init__(
        self,
        *,
        db: Session,
        executor: PolicyEnforcedHTTPExecutor,
    ) -> None:
        self.db = db
        self.executor = executor

    def execute(
        self,
        *,
        test_case_id: int,
    ) -> TestRun:
        test_case = self.db.get(
            TestCase,
            test_case_id,
        )

        if test_case is None:
            raise TestExecutionNotFoundError(
                "TestCase not found."
            )

        observed_status = test_case.status

        if observed_status == "running":
            self.db.commit()
            raise TestExecutionError(
                "TestCase is already running."
            )

        endpoint = self.db.get(
            Endpoint,
            test_case.endpoint_id,
        )

        actor = self.db.get(
            TestIdentity,
            test_case.actor_identity_id,
        )

        resource = self.db.get(
            Resource,
            test_case.resource_id,
        )

        if endpoint is None:
            raise TestExecutionNotFoundError(
                "Endpoint not found."
            )

        if actor is None:
            raise TestExecutionNotFoundError(
                "Actor identity not found."
            )

        if resource is None:
            raise TestExecutionNotFoundError(
                "Resource not found."
            )

        target = self.db.get(
            Target,
            endpoint.target_id,
        )

        if target is None:
            raise TestExecutionNotFoundError(
                "Target not found."
            )

        if actor.target_id != target.id:
            raise TestExecutionError(
                "Actor belongs to a different target."
            )

        if resource.target_id != target.id:
            raise TestExecutionError(
                "Resource belongs to a different target."
            )

        scopes = list(
            self.db.scalars(
                select(Scope).where(
                    Scope.target_id == target.id,
                    Scope.is_active.is_(True),
                )
            ).all()
        )

        request_url = build_test_case_url(
            target=target,
            endpoint=endpoint,
            resource=resource,
        )

        try:
            auth_context = (
                build_authentication_context(
                    actor
                )
            )

            request_headers = (
                apply_authentication_context(
                    request_headers={
                        "Accept": "application/json",
                    },
                    context=auth_context,
                )
            )

        except AuthenticationContextError as exc:
            test_case.status = "blocked"
            self.db.commit()

            raise TestExecutionError(
                str(exc)
            ) from exc

        request_data = {
            "method": endpoint.method,
            "url": request_url,
            "headers": redact_headers(
                request_headers
            ),
            "actor_identity_id": actor.id,
            "resource_id": resource.id,
        }

        acquired_test_case_id = self.db.scalar(
            update(TestCase)
            .where(
                TestCase.id == test_case.id,
                TestCase.status == observed_status,
            )
            .values(status="running")
            .returning(TestCase.id)
            .execution_options(
                synchronize_session=False
            )
        )

        if acquired_test_case_id is None:
            self.db.commit()
            raise TestExecutionError(
                "TestCase execution state changed."
            )

        set_committed_value(
            test_case,
            "status",
            "running",
        )

        # 关闭 SELECT 阶段开启的事务，
        # 不要拿着数据库事务等待网络。
        self.db.commit()

        try:
            result = self.executor.execute(
                target=target,
                scopes=scopes,
                method=endpoint.method,
                url=request_url,
                headers=request_headers,
            )

        except ExecutionBlockedError:
            test_case.status = "blocked"
            self.db.commit()
            raise

        except HTTPExecutionError as exc:
            test_run = TestRun(
                test_case_id=test_case.id,
                request_data=request_data,
                response_status=None,
                response_body=None,
                duration_ms=None,
                error_message=str(exc),
            )

            self.db.add(test_run)

            test_case.status = "failed"

            self.db.commit()
            self.db.refresh(test_run)

            return test_run

        test_run = TestRun(
            test_case_id=test_case.id,
            request_data=request_data,
            response_status=result.status_code,
            response_body=decode_response_body(
                result.body
            ),
            duration_ms=result.duration_ms,
            error_message=None,
        )

        self.db.add(test_run)

        test_case.status = "completed"

        self.db.commit()
        self.db.refresh(test_run)

        return test_run
