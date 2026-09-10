"""Bounded local operator API. Serialize responses before committing audits."""
from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.research_observation import MAX_BYTES, BoundedJSON, ObservationError, ObservationInput, PreparationInput, ReviewInput, HoldInput, ControlInput, canonical, validate
from app.services import research_observation as service
from app.api.routes.research_contexts import request_body

NO_STORE = {"Cache-Control": "no-store"}


class ObservationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def bounded(request):
            try:
                if request.query_params:
                    raise ObservationError("observation_shape_invalid", 422)
                return await handler(request)
            except ObservationError as exc:
                return JSONResponse({"status": "rejected", "code": exc.code}, status_code=exc.status, headers=NO_STORE)
            except RequestValidationError:
                return JSONResponse({"status": "rejected", "code": "observation_shape_invalid"}, status_code=422, headers=NO_STORE)
            except Exception:
                return JSONResponse({"status": "rejected", "code": "observation_failed"}, status_code=500, headers=NO_STORE)
        return bounded


router = APIRouter(prefix="/research-projects/{project}/contexts/{context_id}/observations", tags=["research-observations"], route_class=ObservationRoute)


async def raw_body(request: Request):
    if request.headers.getlist("content-type") != ["application/json"] or request.headers.getlist("content-encoding") not in ([], ["identity"]):
        raise ObservationError("observation_media_type_unsupported", 415)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BYTES:
            raise ObservationError("observation_input_limit", 413)
        raw.extend(chunk)
    return bytes(raw)


def body(schema):
    async def parsed(raw=Depends(raw_body)):
        if len(raw) > 16384:
            raise ObservationError("observation_input_limit", 413)
        return validate(schema, BoundedJSON(raw).parse())
    return parsed


def encoded(value):
    raw = canonical(value)
    if len(raw) > 280000:
        raise ObservationError("observation_response_limit", 500)
    return Response(raw, media_type="application/json", headers=NO_STORE)


@router.post("/maintenance", openapi_extra=request_body(ControlInput))
def maintenance(project: int, context_id: int, payload=Depends(body(ControlInput)), db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.maintain(db, project, context_id, payload))


@router.post("/preparations", openapi_extra=request_body(PreparationInput))
def preparation(project: int, context_id: int, payload=Depends(body(PreparationInput)), db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.prepare(db, project, context_id, payload))


@router.post("/preparations/{ref}/revoke", openapi_extra=request_body(ReviewInput))
def revoke(project: int, context_id: int, ref: str, payload=Depends(body(ReviewInput)), db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.revoke_preparation(db, project, context_id, ref, payload))


@router.post("/preparations/{ref}/batches", openapi_extra=request_body(ObservationInput))
def intake(project: int, context_id: int, ref: str, raw=Depends(raw_body), db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.accept(db, project, context_id, ref, raw))


@router.post("/preparations/{ref}/corrections/{observation_id}", openapi_extra=request_body(ObservationInput))
def correction(project: int, context_id: int, ref: str, observation_id: int, raw=Depends(raw_body), db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.accept(db, project, context_id, ref, raw, corrects_id=observation_id))


@router.get("/{observation_id}")
def read(project: int, context_id: int, observation_id: int, db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.read(db, project, context_id, observation_id))


@router.post("/{observation_id}/human-review", openapi_extra=request_body(ReviewInput))
def human_read(project: int, context_id: int, observation_id: int, payload=Depends(body(ReviewInput)), db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.read(db, project, context_id, observation_id, review=payload))


@router.post("/{observation_id}/hold", openapi_extra=request_body(HoldInput))
def hold(project: int, context_id: int, observation_id: int, payload=Depends(body(HoldInput)), db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.lifecycle(db, project, context_id, observation_id, "hold", payload))


@router.post("/{observation_id}/release", openapi_extra=request_body(ReviewInput))
def release(project: int, context_id: int, observation_id: int, payload=Depends(body(ReviewInput)), db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.lifecycle(db, project, context_id, observation_id, "release", payload))


@router.post("/{observation_id}/delete", openapi_extra=request_body(ReviewInput))
def delete(project: int, context_id: int, observation_id: int, payload=Depends(body(ReviewInput)), db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.lifecycle(db, project, context_id, observation_id, "delete", payload))


@router.post("/{observation_id}/quarantine", openapi_extra=request_body(ReviewInput))
def quarantine(project: int, context_id: int, observation_id: int, payload=Depends(body(ReviewInput)), db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.lifecycle(db, project, context_id, observation_id, "quarantine", payload))
