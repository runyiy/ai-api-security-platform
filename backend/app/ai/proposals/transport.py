"""Fixed provider HTTP boundary, exercised over injected in-memory I/O in W1.

There is deliberately no live socket, DNS or secret-file implementation. A
synthetic receipt cannot turn this into live transport. The same framing,
TLS/peer qualification, first-write callback and bounded reads run in tests.
"""
from dataclasses import dataclass, field
import ipaddress
import ssl
from typing import Callable

from .codec import require

HOST = 'api.openai.com'
URL = 'https://api.openai.com:443/v1/responses'
METHOD = 'POST'
PATH = '/v1/responses'


@dataclass(repr=False)
class MemoryWire:
    """Explicit fake stream. Hooks schedule faults; buffers never reach a socket."""
    response: bytes
    peer_ip: str
    peer_port: int = 443
    certificate_hostname: str = HOST
    certificate_valid: bool = True
    hook: Callable[[str], None] = lambda stage: None
    chunk_size: int = 4096
    writes: list[bytes] = field(default_factory=list, repr=False)
    reads: int = 0
    closed: bool = False
    offset: int = 0

    def start_tls(self, context, hostname, timeout):
        self.hook('tls')
        require(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname, 'TRANSPORT_DENIED')
        require(hostname == HOST and self.certificate_hostname == hostname and self.certificate_valid,
                'TRANSPORT_DENIED')

    def peer(self):
        self.hook('peer')
        return self.peer_ip, self.peer_port

    def write(self, data, timeout):
        self.hook('write')
        require(not self.closed, 'TRANSPORT_DENIED')
        self.writes.append(data)

    def read(self, maximum, timeout):
        self.hook('read')
        self.reads += 1
        size = min(maximum, self.chunk_size)
        require(size > 0, 'TRANSPORT_DENIED')
        value = self.response[self.offset:self.offset + size]
        self.offset += len(value)
        return value

    def close(self):
        self.closed = True



@dataclass(repr=False)
class MemoryResolver:
    addresses: tuple[str, ...]
    hook: Callable[[str], None] = lambda stage: None
    calls: int = 0

    def __call__(self, hostname, timeout):
        self.calls += 1
        self.hook('dns')
        require(hostname == HOST, 'TRANSPORT_DENIED')
        return self.addresses


@dataclass(repr=False)
class MemoryConnector:
    wire: MemoryWire
    hook: Callable[[str], None] = lambda stage: None
    calls: int = 0

    def __call__(self, address, port, timeout):
        self.calls += 1
        self.hook('connect')
        return self.wire


@dataclass(repr=False)
class MemorySecret:
    secret_ref: str
    version: str
    value: bytes = field(repr=False)
    hook: Callable[[str], None] = lambda stage: None
    calls: int = 0

    def __call__(self, ref, version, maximum):
        self.calls += 1
        self.hook('secret')
        require((ref, version) == (self.secret_ref, self.version), 'CREDENTIAL_UNAVAILABLE')
        require(type(self.value) is bytes and 1 <= len(self.value) <= maximum, 'CREDENTIAL_UNAVAILABLE')
        return self.value


def public_ip(value):
    try:
        require(type(value) is str and '%' not in value, 'TRANSPORT_DENIED')
        ip = ipaddress.ip_address(value)
    except ValueError:
        require(False, 'TRANSPORT_DENIED')
    require(ip.is_global and not ip.is_multicast and not ip.is_reserved
            and not getattr(ip, 'ipv4_mapped', None)
            and not getattr(ip, 'sixtofour', None) and not getattr(ip, 'teredo', None), 'TRANSPORT_DENIED')
    return str(ip)


def request_bytes(body, authorization):
    require(type(body) is bytes and len(body) <= 32768, 'INPUT_LIMIT')
    require(type(authorization) is bytes and 1 <= len(authorization) <= 8192
            and all(33 <= c <= 126 for c in authorization), 'CREDENTIAL_UNAVAILABLE')
    return (b'POST /v1/responses HTTP/1.1\r\nHost: api.openai.com\r\n'
            b'Content-Type: application/json\r\nAccept: application/json\r\n'
            b'Accept-Encoding: identity\r\nConnection: close\r\nAuthorization: Bearer '
            + authorization + b'\r\nContent-Length: ' + str(len(body)).encode('ascii') + b'\r\n\r\n' + body)


class ProviderTransport:
    def __init__(self, *, resolver=None, connector=None, secret=None,
                 execution_kind='live', url=URL, method=METHOD, proxy=None):
        self.resolver, self.connector, self.secret = resolver, connector, secret
        self.execution_kind, self.url, self.method, self.proxy = execution_kind, url, method, proxy

    def qualify(self):
        # Before any resolver/secret/connector callback, regardless of receipts.
        require(self.execution_kind == 'synthetic', 'PROVIDER_DISABLED')
        require((self.url, self.method, self.proxy) == (URL, METHOD, None), 'TRANSPORT_DENIED')
        require(type(self.resolver) is MemoryResolver and type(self.connector) is MemoryConnector
                and type(self.connector.wire) is MemoryWire and type(self.secret) is MemorySecret, 'CONFIG_UNAPPROVED')

    def exchange(self, body, config, boundary, before_send, mark_started):
        self.qualify()
        boundary.check('before_secret')
        key = self.secret(config.secret_ref, config.secret_version, 8192)
        request = request_bytes(body, key)
        key = None
        boundary.check('after_secret')
        addresses = self.resolver(HOST, boundary.remaining(3))
        boundary.check('after_dns')
        require(type(addresses) is tuple and 1 <= len(addresses) <= 8, 'TRANSPORT_DENIED')
        selected = tuple(public_ip(v) for v in addresses)
        require(len(set(selected)) == len(selected), 'TRANSPORT_DENIED')
        # One address and one connect, never IPv4/IPv6 fallback or retry.
        started = boundary.monotonic()
        wire = self.connector(selected[0], 443, boundary.remaining(3))
        require(type(wire) is MemoryWire, 'PROVIDER_DISABLED')
        try:
            boundary.check('after_connect')
            require(boundary.monotonic() - started <= 3, 'PROVIDER_TIMEOUT')
            context = ssl.create_default_context()
            wire.start_tls(context, HOST, boundary.remaining(3))
            boundary.check('after_tls')
            require(boundary.monotonic() - started <= 3, 'PROVIDER_TIMEOUT')
            peer = wire.peer()
            require(peer == (selected[0], 443), 'TRANSPORT_DENIED')
            # The trusted coordination dependency serializes final lifecycle
            # checks and only this bounded first write, never the response wait.
            with before_send():
                boundary.check('before_write')
                require(wire.peer() == (selected[0], 443), 'TRANSPORT_DENIED')
                boundary.check('write_ready')
                mark_started()
                wire.write(request, boundary.remaining(3))
            request = None
            boundary.check('after_send')
            return self._response(wire, boundary)
        finally:
            wire.close()

    @staticmethod
    def _response(wire, boundary):
        def read(maximum):
            start = boundary.monotonic()
            result = wire.read(maximum, boundary.remaining(5))
            boundary.check('after_read', eligibility=False)
            require(boundary.monotonic() - start <= 5, 'PROVIDER_TIMEOUT')
            require(type(result) is bytes and len(result) <= maximum, 'TRANSPORT_DENIED')
            return result

        def exact(size):
            data = bytearray()
            while len(data) < size:
                chunk = read(size - len(data))
                require(bool(chunk), 'PROVIDER_FAILURE')
                data.extend(chunk)
            return bytes(data)

        def line(cap):
            data = bytearray()
            while not data.endswith(b'\r\n'):
                require(len(data) < cap, 'OUTPUT_LIMIT')
                data.extend(exact(1))
            return bytes(data)

        # Count raw status/headers/CRLF, before constructing header objects.
        status_line = line(16384)
        parts = status_line[:-2].split(b' ', 2)
        require(len(parts) >= 2 and parts[0] == b'HTTP/1.1' and len(parts[1]) == 3
                and parts[1].isdigit(), 'TRANSPORT_DENIED')
        status = int(parts[1])
        require(200 <= status <= 599, 'TRANSPORT_DENIED')
        used, headers = len(status_line), {}
        while True:
            item = line(16384 - used)
            used += len(item)
            if item == b'\r\n':
                break
            name, sep, value = item[:-2].partition(b':')
            require(sep and name and all(c in b"!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ" for c in name), 'TRANSPORT_DENIED')
            name = name.lower()
            require(name not in headers and all(c == 9 or 32 <= c <= 126 for c in value), 'TRANSPORT_DENIED')
            headers[name] = value.strip(b' \t')
        require(headers.get(b'content-encoding', b'identity') == b'identity', 'TRANSPORT_DENIED')
        require(headers.get(b'content-type', b'').lower() in
                (b'application/json', b'application/json; charset=utf-8'), 'TRANSPORT_DENIED')
        require(not 300 <= status < 400 and b'location' not in headers, 'TRANSPORT_DENIED')
        length, transfer = headers.get(b'content-length'), headers.get(b'transfer-encoding')
        require((length is None) != (transfer is None), 'TRANSPORT_DENIED')
        if length is not None:
            require(length.isdigit() and len(length) <= 5, 'TRANSPORT_DENIED')
            size = int(length)
            require(size <= 65536, 'OUTPUT_LIMIT')
            body = exact(size)
        else:
            require(transfer == b'chunked', 'TRANSPORT_DENIED')
            body = bytearray()
            for _ in range(8192):
                size_line = line(16)[:-2]
                require(size_line and all(c in b'0123456789abcdefABCDEF' for c in size_line), 'TRANSPORT_DENIED')
                size = int(size_line, 16)
                require(len(body) + size <= 65536, 'OUTPUT_LIMIT')
                if size == 0:
                    require(exact(2) == b'\r\n', 'TRANSPORT_DENIED')  # No trailers.
                    break
                body.extend(exact(size))
                require(exact(2) == b'\r\n', 'TRANSPORT_DENIED')
            else:
                require(False, 'OUTPUT_LIMIT')
            body = bytes(body)
        # Connection: close; no ignored suffix or length-lie/pipelined response.
        require(read(1) == b'', 'TRANSPORT_DENIED')
        return status, body
