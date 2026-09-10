"""Bounded trusted-local W1 commands; no execution or caller health-evidence API."""
from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas import research_intent as s
from app.services import research_intent as service
from app.schemas.research_observation import ObservationError
from app.schemas.research_subject import SubjectError
from app.schemas.research_knowledge import KnowledgeError
from app.services.research_context import ResearchContextError
from app.services.bola_binding_selection import BOLABindingSelectionError
from app.services.test_execution import TestExecutionError
from app.services.test_case_planning import TestCasePlanningError
from app.api.routes.research_contexts import request_body

NO_STORE={'Cache-Control':'no-store'}


class IntentRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def bounded(request):
            try:
                if request.query_params: raise s.IntentError('intent_invalid',422)
                return await handler(request)
            except s.IntentError as exc:
                return JSONResponse({'status':'rejected','code':exc.code},status_code=exc.status,headers=NO_STORE)
            except RequestValidationError:
                return JSONResponse({'status':'rejected','code':'intent_invalid'},status_code=422,headers=NO_STORE)
            except (ObservationError,SubjectError,KnowledgeError,ResearchContextError,BOLABindingSelectionError,TestExecutionError,TestCasePlanningError):
                return JSONResponse({'status':'rejected','code':'intent_unavailable'},status_code=409,headers=NO_STORE)
            except Exception:
                return JSONResponse({'status':'rejected','code':'intent_failed'},status_code=500,headers=NO_STORE)
        return bounded


router=APIRouter(prefix='/research-projects/{project}/contexts/{context_id}/intents',tags=['research-intents'],route_class=IntentRoute)


def body(schema):
    async def parsed(request: Request):
        if request.headers.getlist('content-type')!=['application/json'] or request.headers.getlist('content-encoding') not in ([],['identity']):
            raise s.IntentError('intent_media_type',415)
        raw=bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk)>s.MAX_INPUT: raise s.IntentError('intent_input_limit',413)
            raw.extend(chunk)
        return s.validate(schema,s.parse(bytes(raw)))
    return parsed


def encoded(value, clock=None):
    result=s.Receipt.model_validate(value).model_dump()
    raw=s.output(result)
    if len(raw)>s.MAX_OUTPUT: raise s.IntentError('intent_response_limit',500)
    service.final_boundary(result,clock)
    return Response(raw,media_type='application/json',headers=NO_STORE)


@router.post('/mappings/{number}',openapi_extra=request_body(s.MappingInput))
def mapping(project:int,context_id:int,number:int,payload=Depends(body(s.MappingInput)),db:Session=Depends(get_db)):
    clock=service._clock(None)
    with db.begin(): return encoded(service.confirm_mapping(db,project,context_id,number,payload,now=clock),clock)


@router.post('/manifests/{number}',openapi_extra=request_body(s.ManifestInput))
def manifest(project:int,context_id:int,number:int,payload=Depends(body(s.ManifestInput)),db:Session=Depends(get_db)):
    clock=service._clock(None)
    with db.begin(): return encoded(service.record_manifest(db,project,context_id,number,payload,now=clock),clock)


@router.post('/budget-decisions',openapi_extra=request_body(s.BudgetDecision))
def budget(project:int,context_id:int,payload=Depends(body(s.BudgetDecision)),db:Session=Depends(get_db)):
    clock=service._clock(None)
    with db.begin(): return encoded(service.decide_budget(db,project,context_id,payload,now=clock),clock)


@router.post('/versions/{number}',openapi_extra=request_body(s.ConvertInput))
def convert(project:int,context_id:int,number:int,payload=Depends(body(s.ConvertInput)),db:Session=Depends(get_db)):
    clock=service._clock(None)
    with db.begin(): return encoded(service.convert(db,project,context_id,number,payload,now=clock),clock)


@router.post('/read/{kind}',openapi_extra=request_body(s.Reference))
def read(project:int,context_id:int,kind:str,payload=Depends(body(s.Reference)),db:Session=Depends(get_db)):
    clock=service._clock(None)
    with db.begin(): return encoded(service.read(db,project,context_id,kind,payload,now=clock),clock)
