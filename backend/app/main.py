from fastapi import FastAPI

from app.api.routes.authorization_profiles import (
    router as authorization_profiles_router,
)
from app.api.routes.openapi import (
    router as openapi_router,
)
from app.api.routes.policy import (
    router as policy_router,
)
from app.api.routes.scopes import (
    router as scopes_router,
)
from app.api.routes.targets import (
    router as targets_router,
)
from app.api.routes.test_identities import (
    router as test_identities_router,
)
from app.api.routes.resources import (
    router as resources_router,
)
from app.api.routes.test_cases import (
    router as test_cases_router,
)
from app.api.routes.test_runs import (
    router as test_runs_router,
)
from app.api.routes.findings import (
    router as findings_router,
)
from app.api.routes.ai_analysis import (
    router as ai_analysis_router,
)
from app.api.routes.security_reports import (
    router as security_reports_router,
)
app = FastAPI(
    title="AI API Security Testing Platform",
    version="0.1.0",
)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
    }


app.include_router(
    authorization_profiles_router,
    prefix="/api",
)

app.include_router(
    targets_router,
    prefix="/api",
)

app.include_router(
    scopes_router,
    prefix="/api",
)

app.include_router(
    policy_router,
    prefix="/api",
)

app.include_router(
    openapi_router,
    prefix="/api",
)

app.include_router(
    test_identities_router,
    prefix="/api",
)

app.include_router(
    resources_router,
    prefix="/api",
)

app.include_router(
    test_cases_router,
    prefix="/api",
)

app.include_router(
    test_runs_router,
    prefix="/api",
)

app.include_router(
    findings_router,
    prefix="/api",
)

app.include_router(
    ai_analysis_router,
    prefix="/api",
)

app.include_router(
    security_reports_router,
    prefix="/api",
)
