from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OpenAPIImportRecord(Base):
    __tablename__ = "openapi_import_records"
    __table_args__ = (
        CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_openapi_import_records_sha256",
        ),
        CheckConstraint(
            "document_size_bytes BETWEEN 0 AND 1000000",
            name="ck_openapi_import_records_document_size",
        ),
        CheckConstraint(
            "discovered_endpoint_count >= 0",
            name="ck_openapi_import_records_endpoint_count",
        ),
        CheckConstraint(
            "content_encoding IN ('identity', 'gzip')",
            name="ck_openapi_import_records_content_encoding",
        ),
        CheckConstraint(
            "decoded_document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_openapi_import_records_decoded_sha256",
        ),
        CheckConstraint(
            "decoded_document_size_bytes BETWEEN 0 AND 1000000",
            name="ck_openapi_import_records_decoded_size",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(
        ForeignKey("targets.id", ondelete="RESTRICT"), nullable=False
    )
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    document_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_encoding: Mapped[str] = mapped_column(String(8), nullable=False)
    decoded_document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    decoded_document_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    discovered_endpoint_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
