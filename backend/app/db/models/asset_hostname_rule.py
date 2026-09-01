from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssetHostnameRule(Base):
    __tablename__ = "asset_hostname_rules"
    __table_args__ = (
        CheckConstraint(
            "rule_type IN ('include', 'exclude')",
            name="ck_asset_hostname_rules_rule_type",
        ),
        UniqueConstraint(
            "authorization_revision_id",
            "rule_type",
            "hostname_pattern",
            name="uq_asset_hostname_rules_revision_type_pattern",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    authorization_revision_id: Mapped[int] = mapped_column(
        ForeignKey(
            "authorization_revisions.id",
            name="fk_asset_hostname_rules_authorization_revision_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    rule_type: Mapped[str] = mapped_column(String(10), nullable=False)
    hostname_pattern: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
