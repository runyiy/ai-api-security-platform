from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExecutionPlanClaim(Base):
    __tablename__ = "execution_plan_claims"
    __table_args__ = (
        CheckConstraint(
            "fencing_generation > 0",
            name="ck_execution_plan_claims_generation_positive",
        ),
    )

    execution_plan_id: Mapped[int] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="RESTRICT"), primary_key=True
    )
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fencing_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
