from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExecutionPlanCancellation(Base):
    __tablename__ = "execution_plan_cancellations"

    execution_plan_id: Mapped[int] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="RESTRICT"), primary_key=True
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
