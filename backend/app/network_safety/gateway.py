from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import ipaddress
import socket
import ssl
import time
from typing import Protocol

import httpcore
from httpcore._backends.sync import SyncStream

from app.network_safety.destination import (
    AddressCategory,
    CanonicalDestination,
    DNSResolver,
    EXTERNAL_PUBLIC_AUTHORIZED,
    IPAddress,
    PRIVATE_LOCAL,
    SystemDNSResolver,
    classify_address,
    evaluate_destination_policy,
)
from app.network_safety.controller import (
    NetworkExecutionController,
    NetworkExecutionControllerProtocol,
    NetworkExecutionDenied,
)


DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
_HEADER_NAME_CHARACTERS = frozenset(
    "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


class NetworkGatewayError(RuntimeError):
    def __init__(self, *, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


@dataclass(frozen=True)
class NetworkGatewayResult:
    status_code: int
    body: bytes
    duration_ms: int
    destination: CanonicalDestination
    selected_ip: str
    peer_ip: str
    content_encoding: str | None = None
    content_type: str | None = None


class _BoundaryStream(httpcore.NetworkStream):
    """Linearize a typed request after connect/TLS, at its first HTTP write.

    No database lock spans connection setup or response reading. A single GET
    header write is the only sending critical section; no redirect or retry.
    """
    def __init__(self, stream, boundary, state=None):
        self.stream, self.boundary = stream, boundary
        self.state = state if state is not None else {'sent': False, 'deadline': None}

    def _timeout(self, timeout):
        if self.state['deadline'] is None:
            return timeout
        remaining = self.state['deadline'] - time.monotonic()
        if remaining <= 0:
            raise NetworkGatewayError(code='response_deadline', reason='Response deadline reached.')
        return min(timeout, remaining) if timeout is not None else remaining

    def write(self, buffer, timeout=None):
        if buffer and not self.state['sent']:
            with self.boundary.sending() as remaining:
                if remaining <= 0:
                    raise NetworkGatewayError(code='request_deadline', reason='Request deadline reached.')
                self.state['sent'] = True
                self.state['deadline'] = time.monotonic() + min(5.0, remaining)
                self.stream.write(buffer, timeout=self._timeout(timeout))
        else:
            self.stream.write(buffer, timeout=self._timeout(timeout))

    def read(self, max_bytes, timeout=None):
        data = self.stream.read(max_bytes, timeout=self._timeout(timeout))
        self._timeout(None)  # A completed OS read cannot extend the absolute bound.
        return data

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        stream = self.stream.start_tls(ssl_context, server_hostname=server_hostname, timeout=timeout)
        return _BoundaryStream(stream, self.boundary, self.state)

    def close(self):
        self.stream.close()

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)


class TCPConnector(Protocol):
    def connect(
        self,
        *,
        ip_address: IPAddress,
        port: int,
        timeout: float | None,
        socket_options: Iterable[tuple] | None,
    ) -> httpcore.NetworkStream: ...


class DirectTCPConnector:
    def connect(
        self,
        *,
        ip_address: IPAddress,
        port: int,
        timeout: float | None,
        socket_options: Iterable[tuple] | None,
    ) -> httpcore.NetworkStream:
        family = (
            socket.AF_INET6
            if isinstance(ip_address, ipaddress.IPv6Address)
            else socket.AF_INET
        )
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            for option in socket_options or ():
                sock.setsockopt(*option)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            address = (
                (ip_address.compressed, port, 0, 0)
                if family == socket.AF_INET6
                else (ip_address.compressed, port)
            )
            sock.connect(address)
            return SyncStream(sock)
        except BaseException:
            sock.close()
            raise


class _BoundNetworkBackend(httpcore.NetworkBackend):
    def __init__(
        self,
        *,
        connector: TCPConnector,
        logical_hostname: str,
        selected_ip: IPAddress,
        mode: str,
        controller: NetworkExecutionControllerProtocol,
        target_id: int,
        request_boundary=None,
    ) -> None:
        self.connector = connector
        self.logical_hostname = logical_hostname
        self.selected_ip = selected_ip
        self.mode = mode
        self.controller = controller
        self.target_id = target_id
        self.request_boundary = request_boundary
        self.peer_ip: IPAddress | None = None

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple] | None = None,
    ) -> httpcore.NetworkStream:
        if host != self.logical_hostname or local_address is not None:
            raise NetworkGatewayError(
                code="destination_connect_failed",
                reason="Gateway connection binding was rejected.",
            )
        _check_network_enabled(self.controller, self.target_id)
        try:
            stream = self.connector.connect(
                ip_address=self.selected_ip,
                port=port,
                timeout=timeout,
                socket_options=socket_options,
            )
        except NetworkGatewayError:
            raise
        except Exception as exc:
            raise NetworkGatewayError(
                code="destination_connect_failed",
                reason="Connection to the approved destination failed.",
            ) from exc

        try:
            peer_ip = _extract_peer_ip(stream.get_extra_info("server_addr"))
            _validate_peer(
                selected_ip=self.selected_ip,
                peer_ip=peer_ip,
                mode=self.mode,
            )
        except BaseException:
            stream.close()
            raise
        self.peer_ip = peer_ip
        return _BoundaryStream(stream, self.request_boundary) if self.request_boundary is not None else stream

    def connect_unix_socket(self, *args, **kwargs) -> httpcore.NetworkStream:
        raise NetworkGatewayError(
            code="destination_connect_failed",
            reason="Unix socket connections are not supported.",
        )


class NetworkGateway:
    def __init__(
        self,
        *,
        resolver: DNSResolver | None = None,
        connector: TCPConnector | None = None,
        ssl_context: ssl.SSLContext | None = None,
        controller: NetworkExecutionControllerProtocol | None = None,
    ) -> None:
        self.resolver = resolver or SystemDNSResolver()
        self.connector = connector or DirectTCPConnector()
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.controller = controller or NetworkExecutionController()

    def request(
        self,
        *,
        target_id: int,
        network_mode: str,
        method: str,
        url: str,
        headers: dict[str, str],
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        request_boundary=None,
    ) -> NetworkGatewayResult:
        if max_response_bytes <= 0 or timeout_seconds <= 0:
            raise NetworkGatewayError(
                code="network_request_failed",
                reason="Gateway request bounds are invalid.",
            )
        try:
            with self.controller.admission(target_id):
                return self._request_admitted(
                    target_id=target_id,
                    network_mode=network_mode,
                    method=method,
                    url=url,
                    headers=headers,
                    max_response_bytes=max_response_bytes,
                    timeout_seconds=timeout_seconds,
                    request_boundary=request_boundary,
                )
        except NetworkExecutionDenied as exc:
            raise NetworkGatewayError(code=exc.code, reason=exc.reason) from exc

    def _request_admitted(
        self,
        *,
        target_id: int,
        network_mode: str,
        method: str,
        url: str,
        headers: dict[str, str],
        max_response_bytes: int,
        timeout_seconds: float,
        request_boundary=None,
    ) -> NetworkGatewayResult:
        decision = evaluate_destination_policy(
            mode=network_mode, url=url, resolver=self.resolver
        )
        if not decision.allowed or decision.destination is None:
            code = (
                decision.code
                if decision.code
                in {
                    "destination_invalid",
                    "destination_resolution_failed",
                    "destination_resolution_empty",
                }
                else "destination_policy_denied"
            )
            raise NetworkGatewayError(code=code, reason=_gateway_reason(code))

        if decision.destination.scheme == "https":
            _validate_ssl_context(self.ssl_context)

        selected_ip = ipaddress.ip_address(decision.resolved_addresses[0])
        backend = _BoundNetworkBackend(
            connector=self.connector,
            logical_hostname=decision.destination.hostname,
            selected_ip=selected_ip,
            mode=network_mode,
            controller=self.controller,
            target_id=target_id,
            request_boundary=request_boundary,
        )
        request_headers = _build_headers(
            headers=headers,
            destination=decision.destination,
        )
        timeout = {
            "connect": timeout_seconds,
            "read": timeout_seconds,
            "write": timeout_seconds,
            "pool": timeout_seconds,
        }
        started_at = time.monotonic()
        try:
            with httpcore.ConnectionPool(
                ssl_context=self.ssl_context,
                max_connections=1,
                max_keepalive_connections=0,
                retries=0,
                http2=False,
                network_backend=backend,
            ) as pool:
                with pool.stream(
                    method=method,
                    url=url,
                    headers=request_headers,
                    extensions={"timeout": timeout},
                ) as response:
                    body = _read_bounded(response.iter_stream(), max_response_bytes)
                    status_code = response.status
                    content_encoding = _content_encoding(response.headers)
                    content_type = _header_value(response.headers, b'content-type')
                    if request_boundary is not None:
                        request_boundary.completed(status_code, body, content_type, content_encoding)
        except NetworkGatewayError:
            raise
        except (httpcore.TimeoutException, socket.timeout) as exc:
            raise NetworkGatewayError(
                code="network_request_failed",
                reason="Network request timed out.",
            ) from exc
        except Exception as exc:
            raise NetworkGatewayError(
                code="network_request_failed",
                reason="Network request failed.",
            ) from exc

        if backend.peer_ip is None:
            raise NetworkGatewayError(
                code="destination_peer_unavailable",
                reason="Connected peer address was unavailable.",
            )
        duration_ms = int((time.monotonic() - started_at) * 1000)
        return NetworkGatewayResult(
            status_code=status_code,
            body=body,
            duration_ms=duration_ms,
            destination=decision.destination,
            selected_ip=selected_ip.compressed,
            peer_ip=backend.peer_ip.compressed,
            content_encoding=content_encoding,
            content_type=content_type,
        )


def _content_encoding(headers: list[tuple[bytes, bytes]]) -> str | None:
    return _header_value(headers, b'content-encoding')


def _header_value(headers, field):
    values = [
        value.decode("latin-1")
        for name, value in headers
        if name.lower() == field
    ]
    return ",".join(values) if values else None


def _check_network_enabled(
    controller: NetworkExecutionControllerProtocol, target_id: int
) -> None:
    try:
        controller.check_enabled(target_id)
    except NetworkExecutionDenied as exc:
        raise NetworkGatewayError(code=exc.code, reason=exc.reason) from exc


def _build_headers(
    *, headers: dict[str, str], destination: CanonicalDestination
) -> list[tuple[bytes, bytes]]:
    result: list[tuple[bytes, bytes]] = []
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            _raise_invalid_headers()
        if (
            not name
            or any(character not in _HEADER_NAME_CHARACTERS for character in name)
            or any(
                ord(character) > 255
                or (ord(character) < 32 and character != "\t")
                or ord(character) == 127
                for character in value
            )
        ):
            _raise_invalid_headers()
        if name.lower() == "host":
            continue
        result.append((name.encode("ascii"), value.encode("latin-1")))
    default_port = 80 if destination.scheme == "http" else 443
    hostname = (
        f"[{destination.hostname}]"
        if ":" in destination.hostname
        else destination.hostname
    )
    host_value = (
        hostname
        if destination.port == default_port
        else f"{hostname}:{destination.port}"
    )
    result.append((b"Host", host_value.encode("ascii")))
    return result


def _raise_invalid_headers() -> None:
    raise NetworkGatewayError(
        code="network_request_failed",
        reason="Request headers are invalid.",
    )


def _validate_ssl_context(ssl_context: ssl.SSLContext) -> None:
    if (
        ssl_context.verify_mode != ssl.CERT_REQUIRED
        or ssl_context.check_hostname is not True
    ):
        raise NetworkGatewayError(
            code="tls_verification_required",
            reason="TLS certificate and hostname verification are required.",
        )


def _read_bounded(chunks: Iterable[bytes], maximum: int) -> bytes:
    body: list[bytes] = []
    total = 0
    for chunk in chunks:
        total += len(chunk)
        if total > maximum:
            raise NetworkGatewayError(
                code="response_too_large",
                reason="Network response exceeded the allowed size.",
            )
        body.append(chunk)
    return b"".join(body)


def _extract_peer_ip(server_address: object) -> IPAddress:
    if not isinstance(server_address, tuple) or not server_address:
        raise NetworkGatewayError(
            code="destination_peer_unavailable",
            reason="Connected peer address was unavailable.",
        )
    try:
        return ipaddress.ip_address(server_address[0])
    except (TypeError, ValueError) as exc:
        raise NetworkGatewayError(
            code="destination_peer_unavailable",
            reason="Connected peer address was unavailable.",
        ) from exc


def _normalized_ip(address: IPAddress) -> IPAddress:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _validate_peer(*, selected_ip: IPAddress, peer_ip: IPAddress, mode: str) -> None:
    normalized_selected = _normalized_ip(selected_ip)
    normalized_peer = _normalized_ip(peer_ip)
    category = classify_address(normalized_peer)
    allowed = (
        category in {AddressCategory.LOOPBACK, AddressCategory.PRIVATE}
        if mode == PRIVATE_LOCAL
        else mode == EXTERNAL_PUBLIC_AUTHORIZED
        and category is AddressCategory.PUBLIC
    )
    if not allowed:
        raise NetworkGatewayError(
            code="destination_peer_prohibited",
            reason="Connected peer is prohibited by network mode policy.",
        )
    if normalized_peer != normalized_selected:
        raise NetworkGatewayError(
            code="destination_peer_mismatch",
            reason="Connected peer did not match the approved destination.",
        )


def _gateway_reason(code: str) -> str:
    return {
        "destination_invalid": "Destination URL is invalid.",
        "destination_resolution_failed": "Destination resolution failed.",
        "destination_resolution_empty": "Destination resolution returned no addresses.",
        "destination_policy_denied": "Destination was denied by network policy.",
    }[code]
