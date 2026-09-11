"""Bounded trusted-local W2 interfaces; execution is an explicit separate action."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.api.routes.research_intents import IntentRoute, body, NO_STORE
from app.api.routes.test_runs import executor
from app.schemas import research_verification as s
from app.services import research_verification as service
from app.services.research_verification_dispatch import dispatch
from app.services.research_verification_clock import Clock
from app.executors.http import ExecutionBlockedError
from app.services.plan_execution import PlanExecutionError
from app.schemas.research_intent import IntentError
from app.api.routes.research_contexts import request_body

router=APIRouter(prefix='/research-projects/{project}/contexts/{context_id}/verification',
                 tags=['research-verification'],route_class=IntentRoute)


def encoded(value,clock):
    raw=service.output(value)
    response=Response(raw,media_type='application/json',headers=NO_STORE)
    service.final_boundary(value,clock)
    return response


@router.post('/contracts/{number}',openapi_extra=request_body(s.ConfirmInput))
def confirm(project:int,context_id:int,number:int,payload=Depends(body(s.ConfirmInput)),db:Session=Depends(get_db)):
    clock=Clock()
    with db.begin():return encoded(service.confirm(db,project,context_id,number,payload,now=clock),clock)


@router.post('/health-selections',openapi_extra=request_body(s.HealthSelectionInput))
def select_health(project:int,context_id:int,payload=Depends(body(s.HealthSelectionInput)),db:Session=Depends(get_db)):
    clock=Clock()
    with db.begin():return encoded(service.select_health(db,project,context_id,payload,now=clock),clock)


@router.post('/plan-decisions',openapi_extra=request_body(s.ApprovalInput))
def approve(project:int,context_id:int,payload=Depends(body(s.ApprovalInput)),db:Session=Depends(get_db)):
    clock=Clock()
    with db.begin():return encoded(service.approve(db,project,context_id,payload,now=clock),clock)


@router.post('/execute',openapi_extra=request_body(s.PlanInput))
def execute(project:int,context_id:int,payload=Depends(body(s.PlanInput)),db:Session=Depends(get_db)):
    # M8 owns canonical/progress commits. Only new receipt/audit encoding rolls
    # back here; request bytes cannot be rolled back after transmission.
    clock=Clock()
    try:
        return dispatch(db,project,context_id,payload,executor=executor,now=clock,encode=encoded)
    except (ExecutionBlockedError,PlanExecutionError):
        raise IntentError("verification_execution_blocked") from None


@router.post('/pairs',openapi_extra=request_body(s.PairInput))
def pair(project:int,context_id:int,payload=Depends(body(s.PairInput)),db:Session=Depends(get_db)):
    clock=Clock()
    with db.begin():return encoded(service.verify_pair(db,project,context_id,payload,now=clock),clock)


@router.post('/read/execution',openapi_extra=request_body(s.EvidenceRef))
def read_execution(project:int,context_id:int,payload=Depends(body(s.EvidenceRef)),db:Session=Depends(get_db)):
    clock=Clock()
    with db.begin():return encoded(service.read_execution(db,project,context_id,payload,now=clock),clock)


def historical_encoded(value,end,clock):
    raw=service.history_output(value)
    response=Response(raw,media_type='application/json',headers=NO_STORE)
    if clock()>=end:raise IntentError('intent_permission_missing')
    return response


@router.post('/history/plan',openapi_extra=request_body(s.PlanInput))
def history_for_plan(project:int,context_id:int,payload=Depends(body(s.PlanInput)),db:Session=Depends(get_db)):
    clock=Clock()
    with db.begin():
        value,end=service.history_for_plan(db,project,context_id,payload,now=clock)
        return historical_encoded(value,end,clock)


@router.post('/history/{kind}',openapi_extra=request_body(s.EvidenceRef))
def history(project:int,context_id:int,kind:str,payload=Depends(body(s.EvidenceRef)),db:Session=Depends(get_db)):
    clock=Clock()
    with db.begin():
        value,end=service.history(db,project,context_id,payload,now=clock,kind=kind)
        return historical_encoded(value,end,clock)
