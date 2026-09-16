"""Trusted-local, bounded W1 task commands. No runtime or executor dependency."""
from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.api.routes.research_contexts import request_body
from app.api.routes.research_intents import body
from app.db.session import get_db
from app.schemas import research_intent as c, research_task as s
from app.services import research_task as service

NO_STORE = {'Cache-Control': 'no-store'}


class TaskRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            try:
                if request.query_params:
                    raise s.TaskError('task_invalid', 422)
                return await handler(request)
            except s.TaskError as exc:
                return JSONResponse({'status': 'rejected', 'code': exc.code}, status_code=exc.status, headers=NO_STORE)
            except c.IntentError as exc:
                return JSONResponse({'status': 'rejected', 'code': 'task_invalid'}, status_code=exc.status, headers=NO_STORE)
            except RequestValidationError:
                return JSONResponse({'status': 'rejected', 'code': 'task_invalid'}, status_code=422, headers=NO_STORE)
            except Exception:
                return JSONResponse({'status': 'rejected', 'code': 'task_unavailable'}, status_code=500, headers=NO_STORE)
        return bounded


router = APIRouter(prefix='/research-projects/{project}/contexts/{context_id}/tasks',
                   tags=['research-tasks'], route_class=TaskRoute)


def encoded(raw):
    return Response(raw, media_type='application/json', headers=NO_STORE)


@router.post('/versions/{number}', openapi_extra=request_body(s.CreateVersion))
def create(project: int, context_id: int, number: int,
           payload=Depends(body(s.CreateVersion)), db: Session = Depends(get_db)):
    with db.begin():
        return service.create_version(db, project, context_id, number, payload, encode=encoded)


@router.post('/read', openapi_extra=request_body(s.TaskRef))
def read(project: int, context_id: int, payload=Depends(body(s.TaskRef)), db: Session = Depends(get_db)):
    with db.begin():
        return service.inspect_task(db, project, context_id, payload, encode=encoded)


@router.post('/budget-decisions', openapi_extra=request_body(s.Decision))
def budget(project: int, context_id: int, payload=Depends(body(s.Decision)), db: Session = Depends(get_db)):
    with db.begin():
        return service.decide_budget(db, project, context_id, payload, encode=encoded)


@router.post('/transitions', openapi_extra=request_body(s.Transition))
def transition(project: int, context_id: int, payload=Depends(body(s.Transition)), db: Session = Depends(get_db)):
    with db.begin():
        return service.transition(db, project, context_id, payload, encode=encoded)
