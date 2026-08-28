from datetime import datetime

from pydantic import BaseModel


class ExecutionPlanCancellationRead(BaseModel):
    execution_plan_id: int
    requested_at: datetime
