"""Bounded trusted-local operator interface; no secret or execution input."""
from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.research_subject import MAX_INPUT, MAX_RESPONSE, SubjectError, SubjectInput, SubjectCorrection, SubjectRead, validate
from app.schemas.research_observation import BoundedJSON, ObservationError
from app.api.routes.research_contexts import request_body
from app.services import research_subject as service

NO_STORE = {"Cache-Control": "no-store"}


class SubjectRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def bounded(request):
            try:
                if request.query_params:
                    raise SubjectError("research_subject_invalid", 422)
                return await handler(request)
            except SubjectError as exc:
                return JSONResponse({"status": "rejected", "code": exc.code}, status_code=exc.status, headers=NO_STORE)
            except (RequestValidationError, ObservationError):
                return JSONResponse({"status": "rejected", "code": "research_subject_invalid"}, status_code=422, headers=NO_STORE)
            except Exception:
                return JSONResponse({"status": "rejected", "code": "research_subject_failed"}, status_code=500, headers=NO_STORE)
        return bounded


router = APIRouter(prefix="/research-projects/{project}/contexts/{context_id}/subjects", tags=["research-subjects"], route_class=SubjectRoute)


def body(schema):
    async def parsed(request: Request):
        if request.headers.getlist("content-type") != ["application/json"] or request.headers.getlist("content-encoding") not in ([], ["identity"]):
            raise SubjectError("research_subject_media_type", 415)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk) > MAX_INPUT:
                raise SubjectError("research_subject_input_limit", 413)
            raw.extend(chunk)
        return validate(schema, BoundedJSON(bytes(raw)).parse())
    return parsed


def encoded(value):
    raw = SubjectRead.model_validate(value).model_dump_json().encode("utf-8")
    if len(raw) > MAX_RESPONSE:
        raise SubjectError("research_subject_response_limit", 500)
    return Response(raw, media_type="application/json", headers=NO_STORE)


@router.post("/{number}", openapi_extra=request_body(SubjectInput))
def create(project: int, context_id: int, number: int, payload=Depends(body(SubjectInput)), db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.record(db, project, context_id, number, payload))


@router.post("/{number}/versions", openapi_extra=request_body(SubjectCorrection))
def correct(project: int, context_id: int, number: int, payload=Depends(body(SubjectCorrection)), db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.record(db, project, context_id, number, payload, correction=True))


@router.get("/{number}")
def read(project: int, context_id: int, number: int, db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.read(db, project, context_id, number))


@router.get("/{number}/versions/{version}")
def history(project: int, context_id: int, number: int, version: int, db: Session = Depends(get_db)):
    with db.begin():
        return encoded(service.read(db, project, context_id, number, version=version))
