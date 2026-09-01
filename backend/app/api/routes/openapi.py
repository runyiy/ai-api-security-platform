from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.endpoint import Endpoint
from app.db.models.openapi_import_record import OpenAPIImportRecord
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.db.session import get_db
from app.services.execution_authorization import (
    build_execution_authorization_refresh,
)
from app.services.openapi_credentials import build_openapi_credential_refresh
from app.services.safety_audit import build_policy_decision_observer
from app.executors.runtime import platform_rate_limiter
from app.network_safety.runtime import network_gateway
from app.policies.scope_policy import (
    ScopePolicyEngine,
)
from app.scanners.openapi import (
    OpenAPIExecutionBlocked,
    OpenAPIPolicyDenied,
    OpenAPIScanError,
    OpenAPIScanner,
    ParsedEndpoint,
)
from app.schemas.endpoint import (
    EndpointRead,
)
from app.schemas.openapi import (
    OpenAPIImportRequest,
    OpenAPIImportResponse,
)


router = APIRouter(
    tags=["openapi"],
)


policy_engine = ScopePolicyEngine(
    platform_allowed_hosts=(
        settings.allowed_target_host_set
    )
)


scanner = OpenAPIScanner(
    policy_engine=policy_engine,
    rate_limiter=platform_rate_limiter,
    network_gateway=network_gateway,
)


def endpoint_changed(
    endpoint: Endpoint,
    parsed: ParsedEndpoint,
) -> bool:
    return any(
        (
            endpoint.operation_id
            != parsed.operation_id,

            endpoint.requires_auth
            != parsed.requires_auth,

            endpoint.parameters
            != parsed.parameters,

            endpoint.request_body
            != parsed.request_body,

            endpoint.security
            != parsed.security,
        )
    )


@router.post(
    "/openapi/import",
    response_model=OpenAPIImportResponse,
)
def import_openapi(
    payload: OpenAPIImportRequest,
    db: Session = Depends(get_db),
) -> OpenAPIImportResponse:
    target = db.get(
        Target,
        payload.target_id,
    )

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found.",
        )

    authorization_revision = None

    if target.authorization_revision_id is not None:
        authorization_revision = db.get(
            AuthorizationRevision,
            target.authorization_revision_id,
        )

    scopes = list(
        db.scalars(
            select(Scope).where(
                Scope.target_id
                == target.id,
                Scope.is_active.is_(True),
            )
        ).all()
    )

    # Keep the authorization snapshot usable without allowing expired ORM
    # attributes to reopen a transaction during rate, DNS, or network work.
    db.expunge_all()
    db.commit()
    refresh_authorization = build_execution_authorization_refresh(
        db.get_bind(),
        target.id,
    )
    policy_decision_observer = build_policy_decision_observer(
        db.get_bind(),
        operation="openapi_import",
        target_id=target.id,
    )

    scan_kwargs = {}
    if payload.credential_binding_id is not None:
        scan_kwargs = {
            "credential_binding_id": payload.credential_binding_id,
            "refresh_credential": build_openapi_credential_refresh(
                db.get_bind(),
                target_id=target.id,
                credential_binding_id=payload.credential_binding_id,
            ),
        }

    try:
        scan_result = scanner.scan(
            target=target,
            authorization_revision=authorization_revision,
            scopes=scopes,
            source_url=payload.source_url,
            refresh_authorization=refresh_authorization,
            policy_decision_observer=policy_decision_observer,
            **scan_kwargs,
        )

    except OpenAPIPolicyDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": (
                    exc.decision.code
                ),
                "reason": (
                    exc.decision.reason
                ),
            },
        ) from exc

    except OpenAPIExecutionBlocked as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code, "reason": exc.reason},
        ) from exc

    except OpenAPIScanError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    created = 0
    updated = 0
    unchanged = 0
    parsed_endpoints = scan_result.endpoints

    try:
        for parsed in parsed_endpoints:
            endpoint_values = {
                "target_id": target.id,
                "path": parsed.path,
                "method": parsed.method,
                "operation_id": parsed.operation_id,
                "requires_auth": parsed.requires_auth,
                "parameters": parsed.parameters,
                "request_body": parsed.request_body,
                "security": parsed.security,
            }
            inserted_id = db.scalar(
                insert(Endpoint)
                .values(endpoint_values)
                .on_conflict_do_nothing(
                    constraint="uq_endpoint_target_path_method"
                )
                .returning(Endpoint.id)
            )
            if inserted_id is not None:
                created += 1
                continue
            endpoint = db.scalar(
                select(Endpoint).where(
                    Endpoint.target_id == target.id,
                    Endpoint.path == parsed.path,
                    Endpoint.method == parsed.method,
                )
            )
            if endpoint is None:
                raise RuntimeError("Endpoint conflict row not found.")
            if not endpoint_changed(endpoint, parsed):
                unchanged += 1
                continue
            endpoint.operation_id = parsed.operation_id
            endpoint.requires_auth = parsed.requires_auth
            endpoint.parameters = parsed.parameters
            endpoint.request_body = parsed.request_body
            endpoint.security = parsed.security
            updated += 1

        record = OpenAPIImportRecord(
            target_id=target.id,
            source_url=scan_result.source_url,
            document_sha256=scan_result.document_sha256,
            document_size_bytes=scan_result.document_size_bytes,
            content_encoding=scan_result.content_encoding,
            decoded_document_sha256=scan_result.decoded_document_sha256,
            decoded_document_size_bytes=scan_result.decoded_document_size_bytes,
            discovered_endpoint_count=len(parsed_endpoints),
            credential_binding_id=scan_result.credential_binding_id,
        )
        db.add(record)
        db.flush()
        db.commit()
    except Exception:
        db.rollback()
        raise

    return OpenAPIImportResponse(
        target_id=target.id,
        source_url=scan_result.source_url,
        import_record_id=record.id,
        document_sha256=scan_result.document_sha256,
        content_encoding=scan_result.content_encoding,
        decoded_document_sha256=scan_result.decoded_document_sha256,
        discovered=len(
            parsed_endpoints
        ),
        created=created,
        updated=updated,
        unchanged=unchanged,
    )


@router.get(
    "/targets/{target_id}/endpoints",
    response_model=list[EndpointRead],
)
def list_target_endpoints(
    target_id: int,
    db: Session = Depends(get_db),
) -> list[Endpoint]:
    target = db.get(
        Target,
        target_id,
    )

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found.",
        )

    endpoints = list(
        db.scalars(
            select(Endpoint)
            .where(
                Endpoint.target_id
                == target_id
            )
            .order_by(
                Endpoint.path,
                Endpoint.method,
            )
        ).all()
    )

    return endpoints
