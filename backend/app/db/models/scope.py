from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Scope(Base):
    __tablename__ = "scopes"

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    target_id: Mapped[int] = mapped_column(
        ForeignKey(
            "targets.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    hostname: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    path_pattern: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    allowed_methods: Mapped[list[str]] = mapped_column(
        ARRAY(String(10)),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )