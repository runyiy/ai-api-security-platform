from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


DNS_DECISION_CODES = (
    "asset_candidate_dns_public_only",
    "asset_candidate_dns_private_local_only",
    "asset_candidate_dns_prohibited",
    "asset_candidate_dns_resolution_failed",
    "asset_candidate_dns_invalid",
    "asset_candidate_dns_cname_cycle",
    "asset_candidate_dns_cname_limit_exceeded",
    "asset_candidate_dns_address_limit_exceeded",
)
DNS_ADDRESS_CATEGORIES = (
    "loopback", "private", "link_local", "unspecified",
    "multicast", "special", "public",
)


class AssetCandidateDNSValidation(Base):
    __tablename__ = "asset_candidate_dns_validations"
    __table_args__ = (
        CheckConstraint(
            "decision_code IN (" + ", ".join(
                f"'{code}'" for code in DNS_DECISION_CODES
            ) + ")",
            name="ck_asset_candidate_dns_validations_decision_code",
        ),
        Index("ix_dns_validations_evaluation_id", "asset_candidate_evaluation_id"),
        Index("ix_dns_validations_revision_id", "authorization_revision_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_candidate_evaluation_id: Mapped[int] = mapped_column(
        ForeignKey(
            "asset_candidate_evaluations.id",
            name="fk_dns_validations_asset_candidate_evaluation_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    authorization_revision_id: Mapped[int] = mapped_column(
        ForeignKey(
            "authorization_revisions.id",
            name="fk_dns_validations_authorization_revision_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    decision_code: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    terminal_hostname: Mapped[str | None] = mapped_column(String(253), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssetCandidateDNSCNAMEHop(Base):
    __tablename__ = "asset_candidate_dns_cname_hops"
    __table_args__ = (
        CheckConstraint(
            "ordinal BETWEEN 1 AND 8",
            name="ck_asset_candidate_dns_cname_hops_ordinal",
        ),
        UniqueConstraint(
            "dns_validation_id", "ordinal",
            name="uq_asset_candidate_dns_cname_hops_validation_ordinal",
        ),
        Index("ix_dns_cname_hops_validation_id", "dns_validation_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dns_validation_id: Mapped[int] = mapped_column(
        ForeignKey(
            "asset_candidate_dns_validations.id",
            name="fk_dns_cname_hops_dns_validation_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(nullable=False)
    hostname: Mapped[str] = mapped_column(String(253), nullable=False)


class AssetCandidateDNSAddress(Base):
    __tablename__ = "asset_candidate_dns_addresses"
    __table_args__ = (
        CheckConstraint(
            "ordinal BETWEEN 1 AND 16",
            name="ck_asset_candidate_dns_addresses_ordinal",
        ),
        CheckConstraint(
            "category IN (" + ", ".join(
                f"'{category}'" for category in DNS_ADDRESS_CATEGORIES
            ) + ")",
            name="ck_asset_candidate_dns_addresses_category",
        ),
        UniqueConstraint(
            "dns_validation_id", "ordinal",
            name="uq_asset_candidate_dns_addresses_validation_ordinal",
        ),
        Index("ix_dns_addresses_validation_id", "dns_validation_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dns_validation_id: Mapped[int] = mapped_column(
        ForeignKey(
            "asset_candidate_dns_validations.id",
            name="fk_dns_addresses_dns_validation_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(nullable=False)
    address: Mapped[str] = mapped_column(String(45), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
