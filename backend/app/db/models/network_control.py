from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NetworkGlobalControl(Base):
    __tablename__ = "network_global_control"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_network_global_control_singleton"),
        CheckConstraint(
            "maximum_concurrency BETWEEN 1 AND 1024",
            name="ck_network_global_control_concurrency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    maximum_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NetworkDisabledTarget(Base):
    __tablename__ = "network_disabled_targets"

    target_id: Mapped[int] = mapped_column(
        ForeignKey("targets.id", ondelete="CASCADE"), primary_key=True
    )
    disabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
