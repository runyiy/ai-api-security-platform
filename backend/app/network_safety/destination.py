from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
import ipaddress
import re
import socket
from typing import Protocol, TypeAlias
from urllib.parse import urlsplit


PRIVATE_LOCAL = "private_local"
EXTERNAL_PUBLIC_AUTHORIZED = "external_public_authorized"
NETWORK_MODES = frozenset({PRIVATE_LOCAL, EXTERNAL_PUBLIC_AUTHORIZED})

IPAddress: TypeAlias = ipaddress.IPv4Address | ipaddress.IPv6Address


class NetworkDestinationError(ValueError):
    pass


class AddressCategory(StrEnum):
    LOOPBACK = "loopback"
    PRIVATE = "private"
    LINK_LOCAL = "link_local"
    UNSPECIFIED = "unspecified"
    MULTICAST = "multicast"
    SPECIAL = "special"
    PUBLIC = "public"


@dataclass(frozen=True)
class CanonicalDestination:
    scheme: str
    hostname: str
    port: int
    is_ip_literal: bool
    ip_address: IPAddress | None


@dataclass(frozen=True)
class DestinationPolicyDecision:
    allowed: bool
    code: str
    reason: str
    mode: str
    destination: CanonicalDestination | None
    resolved_addresses: tuple[str, ...]
    address_categories: tuple[AddressCategory, ...]


class DNSResolver(Protocol):
    def resolve(self, hostname: str) -> Iterable[IPAddress | str]: ...


class SystemDNSResolver:
    """Preflight resolver only; it does not bind DNS results to a connection."""

    def resolve(self, hostname: str) -> tuple[IPAddress, ...]:
        try:
            results = socket.getaddrinfo(
                hostname,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise NetworkDestinationError("DNS resolution failed") from exc

        addresses: list[IPAddress] = []
        for result in results:
            try:
                raw_address = result[4][0]
                addresses.append(ipaddress.ip_address(raw_address))
            except (IndexError, TypeError, ValueError) as exc:
                raise NetworkDestinationError(
                    "DNS resolution returned a malformed address"
                ) from exc
        return _deduplicate_addresses(addresses)


def parse_canonical_destination(url: str) -> CanonicalDestination:
    if not isinstance(url, str) or not url or "\\" in url:
        raise NetworkDestinationError("destination URL is malformed")
    if any(character.isspace() for character in url):
        raise NetworkDestinationError("destination URL contains whitespace")

    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise NetworkDestinationError("destination URL is malformed") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise NetworkDestinationError("only http and https are supported")
    if parsed.username is not None or parsed.password is not None:
        raise NetworkDestinationError("destination userinfo is prohibited")
    if not parsed.hostname:
        raise NetworkDestinationError("destination hostname is missing")
    if "%" in parsed.hostname:
        raise NetworkDestinationError("scoped IP literals are unsupported")

    try:
        port = parsed.port
    except ValueError as exc:
        raise NetworkDestinationError("destination port is invalid") from exc
    effective_port = port if port is not None else (80 if scheme == "http" else 443)
    if not 1 <= effective_port <= 65535:
        raise NetworkDestinationError("destination port is invalid")

    raw_hostname = parsed.hostname.rstrip(".").lower()
    if not raw_hostname:
        raise NetworkDestinationError("destination hostname is missing")
    try:
        literal = ipaddress.ip_address(raw_hostname)
    except ValueError:
        try:
            hostname = raw_hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise NetworkDestinationError("destination hostname is invalid") from exc
        if (
            len(hostname) > 253
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or re.fullmatch(r"[a-z0-9-]+", label) is None
                for label in hostname.split(".")
            )
        ):
            raise NetworkDestinationError("destination hostname is invalid")
        literal = None
    else:
        hostname = literal.compressed

    return CanonicalDestination(
        scheme=scheme,
        hostname=hostname,
        port=effective_port,
        is_ip_literal=literal is not None,
        ip_address=literal,
    )


def classify_address(address: IPAddress | str) -> AddressCategory:
    try:
        parsed = (
            address
            if isinstance(address, (ipaddress.IPv4Address, ipaddress.IPv6Address))
            else ipaddress.ip_address(address)
        )
    except ValueError as exc:
        raise NetworkDestinationError("resolved address is malformed") from exc

    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return classify_address(parsed.ipv4_mapped)
    if parsed == ipaddress.ip_address("169.254.169.254"):
        return AddressCategory.LINK_LOCAL
    if parsed.is_loopback:
        return AddressCategory.LOOPBACK
    if parsed.is_link_local:
        return AddressCategory.LINK_LOCAL
    if parsed.is_unspecified:
        return AddressCategory.UNSPECIFIED
    if parsed.is_multicast:
        return AddressCategory.MULTICAST
    if isinstance(parsed, ipaddress.IPv4Address) and any(
        parsed in network
        for network in (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
    ):
        return AddressCategory.PRIVATE
    if isinstance(parsed, ipaddress.IPv6Address) and parsed in ipaddress.ip_network(
        "fc00::/7"
    ):
        return AddressCategory.PRIVATE
    if parsed.is_reserved:
        return AddressCategory.SPECIAL
    if parsed.is_global:
        return AddressCategory.PUBLIC
    return AddressCategory.SPECIAL


def evaluate_destination_policy(
    *,
    mode: str,
    url: str,
    resolver: DNSResolver,
) -> DestinationPolicyDecision:
    if mode not in NETWORK_MODES:
        return _denied(
            mode=mode,
            code="network_mode_invalid",
            reason="Target network mode is invalid.",
        )
    try:
        destination = parse_canonical_destination(url)
    except NetworkDestinationError:
        return _denied(
            mode=mode,
            code="destination_invalid",
            reason="Destination URL is invalid.",
        )

    try:
        raw_addresses: Iterable[IPAddress | str]
        if destination.ip_address is not None:
            raw_addresses = (destination.ip_address,)
        else:
            raw_addresses = resolver.resolve(destination.hostname)
        addresses = _canonicalize_resolver_results(raw_addresses)
    except Exception:
        return _denied(
            mode=mode,
            code="destination_resolution_failed",
            reason="Destination resolution failed closed.",
            destination=destination,
        )
    if not addresses:
        return _denied(
            mode=mode,
            code="destination_resolution_empty",
            reason="Destination resolution returned no addresses.",
            destination=destination,
        )

    categories = tuple(classify_address(address) for address in addresses)
    address_strings = tuple(address.compressed for address in addresses)
    if mode == PRIVATE_LOCAL:
        allowed_categories = {AddressCategory.LOOPBACK, AddressCategory.PRIVATE}
        if all(category in allowed_categories for category in categories):
            return DestinationPolicyDecision(
                allowed=True,
                code="private_destination_allowed",
                reason="Resolution contains only loopback or private addresses.",
                mode=mode,
                destination=destination,
                resolved_addresses=address_strings,
                address_categories=categories,
            )
        return _denied(
            mode=mode,
            code="private_destination_prohibited",
            reason="Private/local mode prohibits any public or special address.",
            destination=destination,
            addresses=address_strings,
            categories=categories,
        )

    if all(category is AddressCategory.PUBLIC for category in categories):
        return DestinationPolicyDecision(
            allowed=True,
            code="public_destination_classified",
            reason="Resolution contains only public/global addresses.",
            mode=mode,
            destination=destination,
            resolved_addresses=address_strings,
            address_categories=categories,
        )
    return _denied(
        mode=mode,
        code="public_destination_prohibited",
        reason="External mode classification requires only public addresses.",
        destination=destination,
        addresses=address_strings,
        categories=categories,
    )


def _canonicalize_resolver_results(
    values: Iterable[IPAddress | str],
) -> tuple[IPAddress, ...]:
    if isinstance(values, (str, bytes)):
        raise NetworkDestinationError("resolver result is malformed")
    try:
        parsed = [
            value
            if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address))
            else ipaddress.ip_address(value)
            for value in values
        ]
    except (TypeError, ValueError) as exc:
        raise NetworkDestinationError("resolver result is malformed") from exc
    return _deduplicate_addresses(parsed)


def _deduplicate_addresses(addresses: Iterable[IPAddress]) -> tuple[IPAddress, ...]:
    return tuple(
        sorted(set(addresses), key=lambda address: (address.version, int(address)))
    )


def _denied(
    *,
    mode: str,
    code: str,
    reason: str,
    destination: CanonicalDestination | None = None,
    addresses: tuple[str, ...] = (),
    categories: tuple[AddressCategory, ...] = (),
) -> DestinationPolicyDecision:
    return DestinationPolicyDecision(
        allowed=False,
        code=code,
        reason=reason,
        mode=mode,
        destination=destination,
        resolved_addresses=addresses,
        address_categories=categories,
    )
