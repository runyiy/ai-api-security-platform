from fastapi import (
    FastAPI,
    Header,
)


app = FastAPI(
    title="OpenAPI Test Target",
)


@app.get(
    "/api/projects/{project_id}",
)
def get_project(
    project_id: int,
    authorization: str | None = Header(
        default=None
    ),
) -> dict[str, object]:
    return {
        "id": project_id,
        "authorization": authorization,
    }


@app.post(
    "/api/projects",
)
def create_project(
    payload: dict[str, object],
) -> dict[str, object]:
    return payload


@app.patch(
    "/api/projects/{project_id}",
)
def update_project(
    project_id: int,
    payload: dict[str, object],
) -> dict[str, object]:
    return {
        "id": project_id,
        **payload,
    }


@app.delete(
    "/api/projects/{project_id}",
)
def delete_project(
    project_id: int,
) -> dict[str, int]:
    return {
        "id": project_id,
    }