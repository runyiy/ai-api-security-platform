import ipaddress
import ssl

import httpcore
import pytest

from app.network_safety.gateway import NetworkGateway, NetworkGatewayError


class Resolver:
    def __init__(self, addresses):
        self.addresses = addresses
        self.calls: list[str] = []

    def resolve(self, hostname: str):
        self.calls.append(hostname)
        if isinstance(self.addresses, Exception):
            raise self.addresses
        return self.addresses


class Stream(httpcore.NetworkStream):
    def __init__(self, peer: str | None, response: bytes | None = None):
        self.peer = peer
        self.response = bytearray(response or b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        self.writes: list[bytes] = []
        self.closed = False
        self.server_hostname = None
        self.ssl_context = None

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        chunk = bytes(self.response[:max_bytes])
        del self.response[:max_bytes]
        return chunk

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.writes.append(buffer)

    def close(self) -> None:
        self.closed = True

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.ssl_context = ssl_context
        self.server_hostname = server_hostname
        return self

    def get_extra_info(self, info: str):
        if info == "server_addr" and self.peer is not None:
            return (self.peer, 443)
        return None


class Connector:
    def __init__(self, stream: Stream):
        self.stream = stream
        self.calls = []

    def connect(self, **kwargs):
        self.calls.append(kwargs)
        return self.stream


def request(*, addresses=("127.0.0.1",), peer="127.0.0.1", url="http://lab.test/x",
            mode="private_local", response=None):
    resolver = Resolver(addresses)
    stream = Stream(peer, response)
    connector = Connector(stream)
    gateway = NetworkGateway(resolver=resolver, connector=connector)
    result = gateway.request(
        network_mode=mode, method="GET", url=url, headers={"Host": "attacker"}
    )
    return result, resolver, connector, stream


def test_binds_one_complete_resolution_to_selected_connection() -> None:
    result, resolver, connector, stream = request(
        addresses=("127.0.0.2", "127.0.0.1"), peer="127.0.0.1"
    )
    assert resolver.calls == ["lab.test"]
    assert len(connector.calls) == 1
    assert connector.calls[0]["ip_address"] == ipaddress.ip_address("127.0.0.1")
    assert result.selected_ip == result.peer_ip == "127.0.0.1"
    assert b"Host: lab.test\r\n" in b"".join(stream.writes)
    assert b"attacker" not in b"".join(stream.writes)


@pytest.mark.parametrize(
    "addresses",
    [(), ("127.0.0.1", "8.8.8.8"), ("169.254.169.254",), ("bad",)],
)
def test_resolution_failure_or_any_prohibited_address_fails_before_connect(addresses) -> None:
    resolver = Resolver(addresses)
    connector = Connector(Stream("127.0.0.1"))
    gateway = NetworkGateway(resolver=resolver, connector=connector)
    with pytest.raises(NetworkGatewayError):
        gateway.request(network_mode="private_local", method="GET",
                        url="http://lab.test/x", headers={})
    assert connector.calls == []


@pytest.mark.parametrize(
    ("selected", "peer", "code"),
    [
        ("127.0.0.1", "127.0.0.2", "destination_peer_mismatch"),
        ("127.0.0.1", "169.254.169.254", "destination_peer_prohibited"),
        ("127.0.0.1", None, "destination_peer_unavailable"),
        ("::ffff:127.0.0.1", "::ffff:127.0.0.2", "destination_peer_mismatch"),
    ],
)
def test_peer_mismatch_or_prohibition_closes_connection(selected, peer, code) -> None:
    stream = Stream(peer)
    gateway = NetworkGateway(resolver=Resolver((selected,)), connector=Connector(stream))
    with pytest.raises(NetworkGatewayError) as exc_info:
        gateway.request(network_mode="private_local", method="GET",
                        url="http://lab.test/x", headers={})
    assert exc_info.value.code == code
    assert stream.closed is True


@pytest.mark.parametrize(
    ("selected", "peer"),
    [("127.0.0.1", "::ffff:127.0.0.1"), ("::ffff:127.0.0.1", "127.0.0.1")],
)
def test_ipv4_mapped_ipv6_equivalence_is_explicit(selected, peer) -> None:
    result, _, _, _ = request(addresses=(selected,), peer=peer)
    assert ipaddress.ip_address(result.peer_ip)


def test_https_preserves_logical_sni_and_verifying_context() -> None:
    _, _, _, stream = request(url="https://lab.test/x")
    assert stream.server_hostname == "lab.test"
    assert stream.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert stream.ssl_context.check_hostname is True


def test_redirect_is_returned_without_second_connection() -> None:
    response = b"HTTP/1.1 302 Found\r\nLocation: http://other.test/x\r\nContent-Length: 0\r\n\r\n"
    result, resolver, connector, _ = request(response=response)
    assert result.status_code == 302
    assert resolver.calls == ["lab.test"]
    assert len(connector.calls) == 1


def test_environment_proxy_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    result, _, connector, _ = request()
    assert result.status_code == 200
    assert len(connector.calls) == 1


def test_streaming_response_cap_closes_connection() -> None:
    response = b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabc"
    stream = Stream("127.0.0.1", response)
    gateway = NetworkGateway(resolver=Resolver(("127.0.0.1",)), connector=Connector(stream))
    with pytest.raises(NetworkGatewayError) as exc_info:
        gateway.request(network_mode="private_local", method="GET",
                        url="http://lab.test/x", headers={}, max_response_bytes=2)
    assert exc_info.value.code == "response_too_large"
    assert stream.closed is True
