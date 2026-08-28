from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExecutionPlanProgress(Base):
    __tablename__ = "execution_plan_progress"
    __table_args__ = (
        CheckConstraint("fencing_generation > 0", name="ck_execution_plan_progress_generation"),
        CheckConstraint(
            "phase IN ('pre_network', 'network_started', 'in_doubt')",
            name="ck_execution_plan_progress_phase",
        ),
    )

    execution_plan_id: Mapped[int] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="RESTRICT"), primary_key=True
    )
    fencing_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    phase: Mapped[str] = mapped_column(String(24), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
