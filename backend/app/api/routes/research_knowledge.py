"""Bounded offline knowledge API; genuine proof and explicit review gate publication."""
from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas import research_knowledge as s
from app.services import research_knowledge as service
from app.services import research_rule_validation as validation
from app.schemas import research_rule_validation as v
from app.api.routes.research_contexts import request_body

NO_STORE = {'Cache-Control': 'no-store'}


class KnowledgeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def bounded(request):
            try:
                if request.query_params:
                    raise s.KnowledgeError('knowledge_invalid', 422)
                return await handler(request)
            except s.KnowledgeError as exc:
                return JSONResponse({'status':'rejected','code':exc.code},status_code=exc.status,headers=NO_STORE)
            except RequestValidationError:
                return JSONResponse({'status':'rejected','code':'knowledge_invalid'},status_code=422,headers=NO_STORE)
            except Exception:
                return JSONResponse({'status':'rejected','code':'knowledge_failed'},status_code=500,headers=NO_STORE)
        return bounded


router = APIRouter(prefix='/research-projects/{project}/contexts/{context_id}/knowledge', tags=['research-knowledge'], route_class=KnowledgeRoute)


def body(schema, limit=s.MAX_INPUT):
    async def parsed(request: Request):
        if request.headers.getlist('content-type') != ['application/json'] or request.headers.getlist('content-encoding') not in ([], ['identity']):
            raise s.KnowledgeError('knowledge_media_type',415)
        raw=bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk)>limit:
                raise s.KnowledgeError('knowledge_input_limit',413)
            raw.extend(chunk)
        return s.validate(schema,bytes(raw),limit)
    return parsed


def encoded(value):
    raw=s.canonical(value)
    if value.get('eligibility_until') and service._time(None) >= s.timestamp(value['eligibility_until']):
        raise s.KnowledgeError()
    if len(raw)>s.MAX_RESPONSE:
        raise s.KnowledgeError('knowledge_response_limit',500)
    return Response(raw,media_type='application/json',headers=NO_STORE)


@router.post('/versions',openapi_extra=request_body(s.RecordInput))
def record(project:int,context_id:int,payload=Depends(body(s.RecordInput)),db:Session=Depends(get_db)):
    with db.begin():
        return encoded(service.record(db,project,context_id,payload))


@router.post('/decisions',openapi_extra=request_body(s.DecisionInput))
def decide(project:int,context_id:int,payload=Depends(body(s.DecisionInput)),db:Session=Depends(get_db)):
    with db.begin():
        return encoded(service.decide(db,project,context_id,payload))


@router.post('/query',openapi_extra=request_body(s.QueryInput))
def query(project:int,context_id:int,payload=Depends(body(s.QueryInput,s.MAX_QUERY)),db:Session=Depends(get_db)):
    with db.begin():
        return encoded(service.retrieve(db,project,context_id,payload))


@router.post('/audit-maintenance',openapi_extra=request_body(s.AuditInput))
def audit_maintenance(project:int,context_id:int,payload=Depends(body(s.AuditInput)),db:Session=Depends(get_db)):
    with db.begin():
        return encoded(service.rotate_audit(db,project,context_id,payload))


@router.post('/validations',openapi_extra=request_body(v.ValidateInput))
def validate_rule(project:int,context_id:int,payload=Depends(body(v.ValidateInput)),db:Session=Depends(get_db)):
    with db.begin():
        return encoded(validation.validate_rule(db,project,context_id,payload))


@router.post('/validations/read',openapi_extra=request_body(v.ValidationReadInput))
def read_validation(project:int,context_id:int,payload=Depends(body(v.ValidationReadInput)),db:Session=Depends(get_db)):
    with db.begin():
        return encoded(validation.read_validation(db,project,context_id,payload))


@router.post('/feedback',openapi_extra=request_body(v.FeedbackInput))
def submit_feedback(project:int,context_id:int,payload=Depends(body(v.FeedbackInput)),db:Session=Depends(get_db)):
    with db.begin():
        return encoded(validation.submit_feedback(db,project,context_id,payload))


@router.post('/feedback/read',openapi_extra=request_body(v.FeedbackReadInput))
def read_feedback(project:int,context_id:int,payload=Depends(body(v.FeedbackReadInput)),db:Session=Depends(get_db)):
    with db.begin():
        return encoded(validation.read_feedback(db,project,context_id,payload))


@router.post('/feedback/reviews',openapi_extra=request_body(v.FeedbackReviewInput))
def review_feedback(project:int,context_id:int,payload=Depends(body(v.FeedbackReviewInput)),db:Session=Depends(get_db)):
    with db.begin():
        return encoded(validation.review_feedback(db,project,context_id,payload))
