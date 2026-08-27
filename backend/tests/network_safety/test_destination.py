import pytest

from app.network_safety.destination import (
    AddressCategory,
    NetworkDestinationError,
    classify_address,
    evaluate_destination_policy,
    parse_canonical_destination,
)


class FakeResolver:
    def __init__(self, results=(), error: Exception | None = None) -> None:
        self.results = results
        self.error = error
        self.hostnames: list[str] = []

    def resolve(self, hostname: str):
        self.hostnames.append(hostname)
        if self.error is not None:
            raise self.error
        return self.results


@pytest.mark.parametrize(
    ("url", "scheme", "hostname", "port", "literal"),
    [
        ("HTTP://Example.TEST./path", "http", "example.test", 80, False),
        ("https://example.test/path", "https", "example.test", 443, False),
        ("https://example.test:8443/x", "https", "example.test", 8443, False),
        ("http://127.0.0.1/x", "http", "127.0.0.1", 80, True),
        ("https://[::1]/x", "https", "::1", 443, True),
    ],
)
def test_canonical_destination(
    url: str,
    scheme: str,
    hostname: str,
    port: int,
    literal: bool,
) -> None:
    destination = parse_canonical_destination(url)
    assert (
        destination.scheme,
        destination.hostname,
        destination.port,
        destination.is_ip_literal,
    ) == (scheme, hostname, port, literal)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.test/x",
        "https://user:secret@example.test/x",
        "https:///missing-host",
        "https://example.test:0/x",
        "https://example.test:99999/x",
        "https://[::1/x",
        "https://bad_host.test/x",
        "https://example.test\\@evil.test/x",
    ],
)
def test_canonical_destination_rejects_malformed_input(url: str) -> None:
    with pytest.raises(NetworkDestinationError):
        parse_canonical_destination(url)


@pytest.mark.parametrize(
    ("address", "category"),
    [
        ("127.0.0.1", AddressCategory.LOOPBACK),
        ("10.1.2.3", AddressCategory.PRIVATE),
        ("172.16.1.1", AddressCategory.PRIVATE),
        ("192.168.1.1", AddressCategory.PRIVATE),
        ("169.254.1.1", AddressCategory.LINK_LOCAL),
        ("169.254.169.254", AddressCategory.LINK_LOCAL),
        ("0.0.0.0", AddressCategory.UNSPECIFIED),
        ("224.0.0.1", AddressCategory.MULTICAST),
        ("192.0.2.1", AddressCategory.SPECIAL),
        ("240.0.0.1", AddressCategory.SPECIAL),
        ("8.8.8.8", AddressCategory.PUBLIC),
        ("::1", AddressCategory.LOOPBACK),
        ("fd00::1", AddressCategory.PRIVATE),
        ("fe80::1", AddressCategory.LINK_LOCAL),
        ("::", AddressCategory.UNSPECIFIED),
        ("ff02::1", AddressCategory.MULTICAST),
        ("2001:db8::1", AddressCategory.SPECIAL),
        ("2606:4700:4700::1111", AddressCategory.PUBLIC),
        ("::ffff:10.1.2.3", AddressCategory.PRIVATE),
        ("::ffff:169.254.169.254", AddressCategory.LINK_LOCAL),
        ("::ffff:8.8.8.8", AddressCategory.PUBLIC),
    ],
)
def test_ipv4_ipv6_address_classification(
    address: str,
    category: AddressCategory,
) -> None:
    assert classify_address(address) is category


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.0.0.2", "192.168.1.2"],
        ["::1", "fd00::2"],
    ],
)
def test_private_mode_allows_only_local_private_sets(addresses: list[str]) -> None:
    decision = evaluate_destination_policy(
        mode="private_local",
        url="https://lab.test/path",
        resolver=FakeResolver(addresses),
    )
    assert decision.allowed is True
    assert decision.code == "private_destination_allowed"


@pytest.mark.parametrize(
    "addresses",
    [
        ["8.8.8.8"],
        ["10.0.0.2", "8.8.8.8"],
        ["169.254.169.254"],
        ["fe80::1"],
        ["0.0.0.0"],
        ["224.0.0.1"],
        ["192.0.2.1"],
    ],
)
def test_private_mode_rejects_public_mixed_metadata_and_special_sets(
    addresses: list[str],
) -> None:
    decision = evaluate_destination_policy(
        mode="private_local",
        url="https://lab.test/path",
        resolver=FakeResolver(addresses),
    )
    assert decision.allowed is False
    assert decision.code == "private_destination_prohibited"


def test_resolver_results_are_canonical_and_deduplicated() -> None:
    resolver = FakeResolver(["::1", "10.0.0.2", "10.0.0.2", "127.0.0.1"])
    decision = evaluate_destination_policy(
        mode="private_local",
        url="https://LAB.TEST./path",
        resolver=resolver,
    )
    assert decision.allowed is True
    assert resolver.hostnames == ["lab.test"]
    assert decision.resolved_addresses == (
        "10.0.0.2",
        "127.0.0.1",
        "::1",
    )


@pytest.mark.parametrize(
    ("resolver", "code"),
    [
        (FakeResolver([]), "destination_resolution_empty"),
        (
            FakeResolver(error=OSError("synthetic DNS failure")),
            "destination_resolution_failed",
        ),
        (FakeResolver(["not-an-address"]), "destination_resolution_failed"),
        (FakeResolver(None), "destination_resolution_failed"),
    ],
)
def test_dns_empty_failure_and_malformed_results_fail_closed(
    resolver: FakeResolver,
    code: str,
) -> None:
    decision = evaluate_destination_policy(
        mode="private_local",
        url="https://lab.test/path",
        resolver=resolver,
    )
    assert decision.allowed is False
    assert decision.code == code
    assert "synthetic" not in decision.reason


def test_ip_literal_does_not_call_dns_resolver() -> None:
    resolver = FakeResolver(error=AssertionError("resolver must not be called"))
    decision = evaluate_destination_policy(
        mode="private_local",
        url="http://127.0.0.1/path",
        resolver=resolver,
    )
    assert decision.allowed is True
    assert resolver.hostnames == []


def test_external_classifier_requires_public_only_resolution() -> None:
    allowed = evaluate_destination_policy(
        mode="external_public_authorized",
        url="https://public.test/path",
        resolver=FakeResolver(["8.8.8.8", "2606:4700:4700::1111"]),
    )
    mixed = evaluate_destination_policy(
        mode="external_public_authorized",
        url="https://public.test/path",
        resolver=FakeResolver(["8.8.8.8", "10.0.0.1"]),
    )
    assert allowed.allowed is True
    assert allowed.code == "public_destination_classified"
    assert mixed.allowed is False
    assert mixed.code == "public_destination_prohibited"
