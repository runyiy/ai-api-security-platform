"""Bounded local operator API; inputs are references, never instructions."""
import json

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.research_context import ResearchIntakeInput, ResearchContextCorrection, ResearchContextClose, ResearchContextRead
from app.services.research_context import (
    ResearchContextError, create_context, correct_context, close_context, read_context,
)

MAX_REQUEST_BYTES = 16384
MAX_RESPONSE_BYTES = 32768
NO_STORE = {"Cache-Control": "no-store"}


class IntakeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request):
            try:
                if request.query_params:
                    raise ResearchContextError("intake_invalid_request", 422)
                return await handler(request)
            except ResearchContextError as exc:
                return JSONResponse({"detail": exc.code}, status_code=exc.status, headers=NO_STORE)
            except RequestValidationError:
                return JSONResponse({"detail": "intake_invalid_request"}, status_code=422, headers=NO_STORE)
            except Exception:
                # No exception repr, SQL parameters, paths, raw text or offending values in logs/responses.
                return JSONResponse({"detail": "intake_failed"}, status_code=500, headers=NO_STORE)
        return bounded


router = APIRouter(prefix="/research-projects", tags=["research-intake"], route_class=IntakeRoute)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid_number(value):
    raise ValueError()


def reader(schema):
    async def parse(request: Request):
        if (request.headers.getlist("content-type") != ["application/json"]
                or request.headers.getlist("content-encoding") not in ([], ["identity"])):
            raise ResearchContextError("intake_media_type_unsupported", 415)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
                raise ResearchContextError("intake_request_limit_exceeded", 413)
            raw.extend(chunk)
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                               parse_float=_invalid_number, parse_constant=_invalid_number)
            return schema.model_validate(value)
        except (ValueError, RecursionError, OverflowError):
            raise ResearchContextError("intake_invalid_request", 422) from None
    return parse


def request_body(schema):
    value = schema.model_json_schema()
    definitions = value.pop("$defs", {})
    def inline(node):
        if isinstance(node, list):
            return [inline(n) for n in node]
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(definitions[node["$ref"].rsplit("/", 1)[1]])
            return {k: inline(v) for k, v in node.items()}
        return node
    return {"requestBody": {"required": True, "content": {
        "application/json": {"schema": inline(value)}}}}


def encoded(value, status=200):
    raw = ResearchContextRead.model_validate(value).model_dump_json().encode("utf-8")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ResearchContextError("intake_response_limit_exceeded", 500)
    return Response(raw, status_code=status, media_type="application/json", headers=NO_STORE)


@router.post("/{project_number}/contexts", response_model=ResearchContextRead, openapi_extra=request_body(ResearchIntakeInput), status_code=201)
def create(project_number: int, payload=Depends(reader(ResearchIntakeInput)), db: Session = Depends(get_db)):
    with db.begin():
        return encoded(create_context(db, {"project_number": project_number, "intake": payload.model_dump()}), 201)


@router.get("/{project_number}/contexts/{context_id}", response_model=ResearchContextRead)
def read(project_number: int, context_id: int, db: Session = Depends(get_db)):
    return encoded(read_context(db, project_number, context_id))


@router.get("/{project_number}/contexts/{context_id}/versions/{version}", response_model=ResearchContextRead)
def history(project_number: int, context_id: int, version: int, db: Session = Depends(get_db)):
    if not 1 <= version <= 10000:
        raise ResearchContextError("intake_invalid_request", 422)
    return encoded(read_context(db, project_number, context_id, version=version))


@router.post("/{project_number}/contexts/{context_id}/versions", response_model=ResearchContextRead, openapi_extra=request_body(ResearchContextCorrection))
def correct(project_number: int, context_id: int, payload=Depends(reader(ResearchContextCorrection)),
            db: Session = Depends(get_db)):
    with db.begin():
        return encoded(correct_context(db, project_number, context_id, payload))


@router.post("/{project_number}/contexts/{context_id}/close", response_model=ResearchContextRead, openapi_extra=request_body(ResearchContextClose))
def close(project_number: int, context_id: int, payload=Depends(reader(ResearchContextClose)),
          db: Session = Depends(get_db)):
    with db.begin():
        return encoded(close_context(db, project_number, context_id, payload))
