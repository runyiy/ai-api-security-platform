from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EndpointResourceBinding(Base):
    __tablename__ = "endpoint_resource_bindings"

    __table_args__ = (
        CheckConstraint(
            "location IN ('path', 'query', 'body')",
            name="ck_endpoint_resource_bindings_location",
        ),
        CheckConstraint(
            "length(selector) BETWEEN 1 AND 500",
            name="ck_endpoint_resource_bindings_selector_length",
        ),
        CheckConstraint(
            "provenance IN ('operator_supplied', 'openapi_inferred', "
            "'heuristic_inferred')",
            name="ck_endpoint_resource_bindings_provenance",
        ),
        CheckConstraint(
            "confidence BETWEEN 0 AND 100",
            name="ck_endpoint_resource_bindings_confidence",
        ),
        CheckConstraint(
            "review_state IN ('candidate', 'confirmed', 'rejected')",
            name="ck_endpoint_resource_bindings_review_state",
        ),
        UniqueConstraint(
            "endpoint_id",
            "location",
            "selector",
            "provenance",
            name="uq_endpoint_resource_binding_exact",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey(
            "endpoints.id",
            name="fk_endpoint_resource_bindings_endpoint_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    location: Mapped[str] = mapped_column(String(16), nullable=False)
    selector: Mapped[str] = mapped_column(String(500), nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    review_state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
