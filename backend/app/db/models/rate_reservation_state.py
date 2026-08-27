from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RateReservationState(Base):
    __tablename__ = "rate_reservation_states"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    next_allowed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
