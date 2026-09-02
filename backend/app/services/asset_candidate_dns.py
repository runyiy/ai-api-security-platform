from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import ipaddress
from typing import Protocol

import dns.exception
import dns.name
import dns.resolver

from app.network_safety import destination
from app.network_safety.destination import AddressCategory, IPAddress
from app.services.asset_hostname_rule import (
    AssetHostnameRuleValidationError,
    normalize_candidate_hostname,
)


MAX_CNAME_HOPS = 8
MAX_RESOLVED_ADDRESSES = 16
DNS_TIMEOUT_SECONDS = 2.0


class AssetCandidateDNSResolverError(RuntimeError):
    pass


class AssetCandidateDNSResolver(Protocol):
    def lookup_cname(self, hostname: str) -> str | None: ...

    def resolve_addresses(
        self, hostname: str
    ) -> Iterable[str | IPAddress]: ...


@dataclass(frozen=True)
class AssetCandidateDNSDecision:
    code: str
    normalized_hostname: str | None
    cname_chain: tuple[str, ...]
    terminal_hostname: str | None
    resolved_addresses: tuple[str, ...]
    address_categories: tuple[AddressCategory, ...]


class DnspythonAssetCandidateDNSResolver:
    """Bounded classic-DNS adapter for exact CNAME, A, and AAAA queries."""

    def __init__(
        self,
        *,
        resolver: dns.resolver.Resolver | None = None,
        timeout_seconds: float = DNS_TIMEOUT_SECONDS,
    ) -> None:
        if not 0 < timeout_seconds <= DNS_TIMEOUT_SECONDS:
            raise ValueError("DNS timeout must be positive and bounded")
        self.resolver = resolver or dns.resolver.Resolver(configure=True)
        self.timeout_seconds = timeout_seconds
        self.resolver.timeout = timeout_seconds
        self.resolver.lifetime = timeout_seconds

    @staticmethod
    def _absolute_name(hostname: str) -> dns.name.Name:
        try:
            normalized = normalize_candidate_hostname(hostname)
            name = dns.name.from_text(f"{normalized}.")
        except (AssetHostnameRuleValidationError, dns.exception.DNSException) as exc:
            raise AssetCandidateDNSResolverError from exc
        if not name.is_absolute():
            raise AssetCandidateDNSResolverError
        return name

    def _resolve(self, hostname: str, record_type: str):
        try:
            return self.resolver.resolve(
                self._absolute_name(hostname),
                record_type,
                search=False,
                lifetime=self.timeout_seconds,
            )
        except dns.resolver.NoAnswer:
            return None
        except (
            dns.resolver.NXDOMAIN,
            dns.resolver.NoNameservers,
            dns.resolver.LifetimeTimeout,
            dns.exception.Timeout,
            dns.exception.DNSException,
            OSError,
        ) as exc:
            raise AssetCandidateDNSResolverError from exc

    def lookup_cname(self, hostname: str) -> str | None:
        answer = self._resolve(hostname, "CNAME")
        if answer is None:
            return None
        try:
            records = list(answer)
            if len(records) != 1:
                raise AssetCandidateDNSResolverError
            return records[0].target.to_text()
        except (AttributeError, TypeError, ValueError) as exc:
            raise AssetCandidateDNSResolverError from exc

    def resolve_addresses(self, hostname: str) -> tuple[str, ...]:
        addresses: list[str] = []
        for record_type in ("A", "AAAA"):
            answer = self._resolve(hostname, record_type)
            if answer is None:
                continue
            try:
                addresses.extend(record.address for record in answer)
            except (AttributeError, TypeError, ValueError) as exc:
                raise AssetCandidateDNSResolverError from exc
        return tuple(addresses)


def _decision(
    code: str,
    *,
    normalized_hostname: str | None,
    cname_chain: tuple[str, ...] = (),
    terminal_hostname: str | None = None,
    resolved_addresses: tuple[str, ...] = (),
    address_categories: tuple[AddressCategory, ...] = (),
) -> AssetCandidateDNSDecision:
    return AssetCandidateDNSDecision(
        code=code,
        normalized_hostname=normalized_hostname,
        cname_chain=cname_chain,
        terminal_hostname=terminal_hostname,
        resolved_addresses=resolved_addresses,
        address_categories=address_categories,
    )


def classify_asset_candidate_dns(
    hostname: str, *, resolver: AssetCandidateDNSResolver
) -> AssetCandidateDNSDecision:
    try:
        normalized = normalize_candidate_hostname(hostname)
    except AssetHostnameRuleValidationError:
        return _decision(
            "asset_candidate_dns_invalid", normalized_hostname=None
        )

    current = normalized
    chain: list[str] = []
    observed = {normalized}
    try:
        while True:
            target = resolver.lookup_cname(current)
            if target is None:
                break
            if len(chain) == MAX_CNAME_HOPS:
                return _decision(
                    "asset_candidate_dns_cname_limit_exceeded",
                    normalized_hostname=normalized,
                    cname_chain=tuple(chain),
                    terminal_hostname=current,
                )
            try:
                canonical_target = normalize_candidate_hostname(target)
            except AssetHostnameRuleValidationError:
                return _decision(
                    "asset_candidate_dns_invalid",
                    normalized_hostname=normalized,
                    cname_chain=tuple(chain),
                    terminal_hostname=current,
                )
            if canonical_target in observed:
                return _decision(
                    "asset_candidate_dns_cname_cycle",
                    normalized_hostname=normalized,
                    cname_chain=tuple((*chain, canonical_target)),
                    terminal_hostname=current,
                )
            chain.append(canonical_target)
            observed.add(canonical_target)
            current = canonical_target

        raw_addresses = resolver.resolve_addresses(current)
        if isinstance(raw_addresses, (str, bytes)):
            raise ValueError
        unique_addresses: set[IPAddress] = set()
        for value in raw_addresses:
            address = (
                value
                if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address))
                else ipaddress.ip_address(value)
            )
            unique_addresses.add(address)
            if len(unique_addresses) > MAX_RESOLVED_ADDRESSES:
                return _decision(
                    "asset_candidate_dns_address_limit_exceeded",
                    normalized_hostname=normalized,
                    cname_chain=tuple(chain),
                    terminal_hostname=current,
                )
        addresses = tuple(sorted(
            unique_addresses,
            key=lambda address: (address.version, int(address)),
        ))
    except Exception:
        return _decision(
            "asset_candidate_dns_resolution_failed",
            normalized_hostname=normalized,
            cname_chain=tuple(chain),
            terminal_hostname=current,
        )

    if not addresses:
        return _decision(
            "asset_candidate_dns_resolution_failed",
            normalized_hostname=normalized,
            cname_chain=tuple(chain),
            terminal_hostname=current,
        )
    categories = tuple(destination.classify_address(address) for address in addresses)
    address_strings = tuple(address.compressed for address in addresses)
    if all(category is AddressCategory.PUBLIC for category in categories):
        code = "asset_candidate_dns_public_only"
    elif all(
        category in {AddressCategory.LOOPBACK, AddressCategory.PRIVATE}
        for category in categories
    ):
        code = "asset_candidate_dns_private_local_only"
    else:
        code = "asset_candidate_dns_prohibited"
    return _decision(
        code,
        normalized_hostname=normalized,
        cname_chain=tuple(chain),
        terminal_hostname=current,
        resolved_addresses=address_strings,
        address_categories=categories,
    )
