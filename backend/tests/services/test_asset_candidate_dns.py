from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
import dns.exception
import dns.resolver
import pytest
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.db.models import (
    Endpoint,
    ExecutionPlan,
    OpenAPIImportRecord,
    PlanAction,
    Scope,
    Target,
    TestCase as StoredTestCase,
    TestRun as StoredTestRun,
)
from app.db.session import SessionLocal
from app.network_safety import destination
from app.network_safety.destination import AddressCategory
from app.services.asset_candidate_dns import (
    DNS_TIMEOUT_SECONDS,
    MAX_CNAME_HOPS,
    MAX_RESOLVED_ADDRESSES,
    AssetCandidateDNSResolverError,
    DnspythonAssetCandidateDNSResolver,
    classify_asset_candidate_dns,
)


class FakeResolver:
    def __init__(self, *, cnames=None, addresses=None, error=None):
        self.cnames = cnames or {}
        self.addresses = addresses or {}
        self.error = error
        self.cname_calls: list[str] = []
        self.address_calls: list[str] = []

    def lookup_cname(self, hostname: str) -> str | None:
        self.cname_calls.append(hostname)
        if self.error is not None:
            raise self.error
        return self.cnames.get(hostname)

    def resolve_addresses(self, hostname: str):
        self.address_calls.append(hostname)
        if self.error is not None:
            raise self.error
        return self.addresses.get(hostname, ())


def test_no_migration_and_only_focused_runtime_dependency() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert scripts.get_heads() == ["f3b5d7e9a1c2"]
    requirements = Path("requirements.txt").read_text().splitlines()
    assert [line for line in requirements if "dns" in line.lower()] == [
        "dnspython==2.8.0"
    ]


def test_no_cname_and_deterministic_ipv4_ipv6_deduplication() -> None:
    resolver = FakeResolver(addresses={
        "api.example.test": (
            "2606:4700:4700::1111", "8.8.8.8", "8.8.8.8",
            "2606:4700:4700::1111",
        )
    })
    decision = classify_asset_candidate_dns(
        "API.EXAMPLE.TEST.", resolver=resolver
    )
    assert decision.code == "asset_candidate_dns_public_only"
    assert decision.normalized_hostname == "api.example.test"
    assert decision.cname_chain == ()
    assert decision.terminal_hostname == "api.example.test"
    assert decision.resolved_addresses == (
        "8.8.8.8", "2606:4700:4700::1111"
    )
    assert decision.address_categories == (
        AddressCategory.PUBLIC, AddressCategory.PUBLIC
    )
    assert resolver.cname_calls == ["api.example.test"]
    assert resolver.address_calls == ["api.example.test"]
    assert not hasattr(decision, "allowed")


def test_multihop_idna_and_out_of_wildcard_cname_is_provenance_only(
    monkeypatch,
) -> None:
    def prohibited_match(*args, **kwargs):
        raise AssertionError("CNAME target was passed to wildcard matching")

    monkeypatch.setattr(
        "app.services.asset_hostname_rule.match_asset_candidate",
        prohibited_match,
    )
    resolver = FakeResolver(
        cnames={
            "api.example.test": "EDGE.BÜCHER.TEST.",
            "edge.xn--bcher-kva.test": "terminal.vendor.test.",
        },
        addresses={"terminal.vendor.test": ("10.1.2.3", "::1")},
    )
    decision = classify_asset_candidate_dns(
        "api.example.test", resolver=resolver
    )
    assert decision.code == "asset_candidate_dns_private_local_only"
    assert decision.cname_chain == (
        "edge.xn--bcher-kva.test", "terminal.vendor.test"
    )
    assert decision.terminal_hostname == "terminal.vendor.test"
    assert resolver.address_calls == ["terminal.vendor.test"]


def test_cname_cycle_and_hop_overflow_fail_closed_without_address_resolution() -> None:
    cycle = FakeResolver(cnames={
        "api.example.test": "edge.example.test",
        "edge.example.test": "API.EXAMPLE.TEST.",
    })
    decision = classify_asset_candidate_dns(
        "api.example.test", resolver=cycle
    )
    assert decision.code == "asset_candidate_dns_cname_cycle"
    assert decision.cname_chain == ("edge.example.test", "api.example.test")
    assert cycle.address_calls == []

    names = [f"hop-{number}.example.test" for number in range(MAX_CNAME_HOPS + 2)]
    overflow = FakeResolver(cnames={
        names[index]: names[index + 1]
        for index in range(MAX_CNAME_HOPS + 1)
    })
    decision = classify_asset_candidate_dns(names[0], resolver=overflow)
    assert decision.code == "asset_candidate_dns_cname_limit_exceeded"
    assert len(decision.cname_chain) == MAX_CNAME_HOPS
    assert overflow.address_calls == []


def test_exact_cname_hop_limit_reaches_terminal_without_truncation() -> None:
    names = [f"exact-{number}.example.test" for number in range(MAX_CNAME_HOPS + 1)]
    resolver = FakeResolver(
        cnames={
            names[index]: names[index + 1]
            for index in range(MAX_CNAME_HOPS)
        },
        addresses={names[-1]: ("8.8.8.8",)},
    )
    decision = classify_asset_candidate_dns(names[0], resolver=resolver)
    assert decision.code == "asset_candidate_dns_public_only"
    assert decision.cname_chain == tuple(names[1:])
    assert decision.terminal_hostname == names[-1]


@pytest.mark.parametrize(
    "hostname",
    ("https://api.example.test", "bad host", "127.0.0.1", "::1"),
)
def test_invalid_initial_candidate_fails_before_resolver(hostname: str) -> None:
    resolver = FakeResolver()
    decision = classify_asset_candidate_dns(hostname, resolver=resolver)
    assert decision.code == "asset_candidate_dns_invalid"
    assert decision.normalized_hostname is None
    assert resolver.cname_calls == []


@pytest.mark.parametrize(
    "target",
    ("https://alias.example.test", "bad alias.test", "127.0.0.1", "::1"),
)
def test_invalid_cname_target_is_sanitized(target: str) -> None:
    resolver = FakeResolver(cnames={"api.example.test": target})
    decision = classify_asset_candidate_dns(
        "api.example.test", resolver=resolver
    )
    assert decision.code == "asset_candidate_dns_invalid"
    assert decision.resolved_addresses == ()
    assert resolver.address_calls == []


@pytest.mark.parametrize(
    ("addresses", "expected"),
    (
        (("8.8.8.8",), "asset_candidate_dns_public_only"),
        (("127.0.0.1", "10.0.0.1", "fd00::1"),
         "asset_candidate_dns_private_local_only"),
        (("169.254.169.254",), "asset_candidate_dns_prohibited"),
        (("192.0.2.1",), "asset_candidate_dns_prohibited"),
        (("0.0.0.0",), "asset_candidate_dns_prohibited"),
        (("224.0.0.1",), "asset_candidate_dns_prohibited"),
        (("8.8.8.8", "10.0.0.1"), "asset_candidate_dns_prohibited"),
        (("::ffff:10.0.0.1",), "asset_candidate_dns_private_local_only"),
    ),
)
def test_m6_address_category_combinations(addresses, expected) -> None:
    decision = classify_asset_candidate_dns(
        "api.example.test",
        resolver=FakeResolver(addresses={"api.example.test": addresses}),
    )
    assert decision.code == expected


def test_exact_m6_classifier_is_called_for_every_address(monkeypatch) -> None:
    original = destination.classify_address
    calls = []

    def tracked(address):
        calls.append(address.compressed)
        return original(address)

    monkeypatch.setattr(destination, "classify_address", tracked)
    decision = classify_asset_candidate_dns(
        "api.example.test",
        resolver=FakeResolver(addresses={
            "api.example.test": ("8.8.8.8", "2606:4700:4700::1111")
        }),
    )
    assert decision.code == "asset_candidate_dns_public_only"
    assert calls == ["8.8.8.8", "2606:4700:4700::1111"]


@pytest.mark.parametrize("addresses", ((), ("not-an-address",), "8.8.8.8"))
def test_empty_or_malformed_address_results_fail_sanitized(addresses) -> None:
    decision = classify_asset_candidate_dns(
        "api.example.test",
        resolver=FakeResolver(addresses={"api.example.test": addresses}),
    )
    assert decision.code == "asset_candidate_dns_resolution_failed"
    assert decision.resolved_addresses == ()


def test_address_overflow_fails_without_truncation() -> None:
    addresses = tuple(f"10.0.0.{number}" for number in range(1, 18))
    assert len(addresses) > MAX_RESOLVED_ADDRESSES
    decision = classify_asset_candidate_dns(
        "api.example.test",
        resolver=FakeResolver(addresses={"api.example.test": addresses}),
    )
    assert decision.code == "asset_candidate_dns_address_limit_exceeded"
    assert decision.resolved_addresses == ()
    assert decision.address_categories == ()


def test_address_overflow_stops_consuming_after_seventeenth_unique_result() -> None:
    consumed = []

    def addresses():
        for number in range(1, 30):
            consumed.append(number)
            yield f"10.0.0.{number}"

    resolver = FakeResolver(addresses={"api.example.test": addresses()})
    decision = classify_asset_candidate_dns(
        "api.example.test", resolver=resolver
    )
    assert decision.code == "asset_candidate_dns_address_limit_exceeded"
    assert consumed == list(range(1, MAX_RESOLVED_ADDRESSES + 2))


@pytest.mark.parametrize(
    "error",
    (AssetCandidateDNSResolverError("secret provider detail"), TimeoutError("secret")),
)
def test_resolver_failures_are_stable_and_sanitized(error) -> None:
    decision = classify_asset_candidate_dns(
        "api.example.test", resolver=FakeResolver(error=error)
    )
    assert decision.code == "asset_candidate_dns_resolution_failed"
    assert "secret" not in repr(decision)


class StubDnspythonResolver:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []
        self.timeout = None
        self.lifetime = None

    def resolve(self, name, record_type, **kwargs):
        self.calls.append((name, record_type, kwargs))
        answer = self.answers[record_type]
        if isinstance(answer, BaseException):
            raise answer
        return answer


def record(**values):
    return SimpleNamespace(**values)


def test_dnspython_adapter_absolute_bounded_queries_and_family_fallbacks() -> None:
    no_answer = dns.resolver.NoAnswer()
    stub = StubDnspythonResolver({
        "CNAME": [record(target=SimpleNamespace(
            to_text=lambda: "EDGE.EXAMPLE.TEST."
        ))],
        "A": no_answer,
        "AAAA": [record(address="2606:4700:4700::1111")],
    })
    adapter = DnspythonAssetCandidateDNSResolver(
        resolver=stub, timeout_seconds=1.5
    )
    assert adapter.lookup_cname("api.example.test") == "EDGE.EXAMPLE.TEST."
    assert adapter.resolve_addresses("edge.example.test") == (
        "2606:4700:4700::1111",
    )
    assert stub.timeout == stub.lifetime == 1.5
    assert [call[1] for call in stub.calls] == ["CNAME", "A", "AAAA"]
    assert all(call[0].is_absolute() for call in stub.calls)
    assert all(call[0].to_text().endswith(".") for call in stub.calls)
    assert all(call[2] == {"search": False, "lifetime": 1.5} for call in stub.calls)

    reverse_stub = StubDnspythonResolver({
        "A": [record(address="8.8.8.8")],
        "AAAA": dns.resolver.NoAnswer(),
    })
    reverse = DnspythonAssetCandidateDNSResolver(resolver=reverse_stub)
    assert reverse.resolve_addresses("api.example.test") == ("8.8.8.8",)


@pytest.mark.parametrize(
    "failure",
    (dns.resolver.NXDOMAIN(), dns.exception.Timeout("secret timeout")),
)
def test_dnspython_adapter_sanitizes_nxdomain_and_timeout(failure) -> None:
    adapter = DnspythonAssetCandidateDNSResolver(
        resolver=StubDnspythonResolver({"CNAME": failure})
    )
    with pytest.raises(AssetCandidateDNSResolverError) as caught:
        adapter.lookup_cname("api.example.test")
    assert "secret" not in str(caught.value)


def test_dnspython_adapter_both_address_families_missing_fails_in_service() -> None:
    stub = StubDnspythonResolver({
        "CNAME": dns.resolver.NoAnswer(),
        "A": dns.resolver.NoAnswer(),
        "AAAA": dns.resolver.NoAnswer(),
    })
    decision = classify_asset_candidate_dns(
        "api.example.test",
        resolver=DnspythonAssetCandidateDNSResolver(resolver=stub),
    )
    assert decision.code == "asset_candidate_dns_resolution_failed"


def test_classification_has_zero_authority_or_application_network_side_effects(
    monkeypatch,
) -> None:
    tracked = (
        Target, Scope, Endpoint, OpenAPIImportRecord, ExecutionPlan, PlanAction,
        StoredTestCase, StoredTestRun,
    )
    target_id: int | None = None
    try:
        with SessionLocal() as db:
            target = Target(
                name=f"dns-observation-retained-{uuid4()}",
                base_url="https://retained.example.test",
                environment="test",
                network_mode="external_public_authorized",
            )
            db.add(target)
            db.commit()
            target_id = target.id
            before = {
                model: db.scalar(select(func.count()).select_from(model))
                for model in tracked
            }
            network_mode = target.network_mode
        allowed_hosts = settings.allowed_target_hosts
        allowed_host_set = settings.allowed_target_host_set

        def prohibited(*args, **kwargs):
            raise AssertionError("application network path was invoked")

        monkeypatch.setattr("socket.getaddrinfo", prohibited)
        monkeypatch.setattr("socket.create_connection", prohibited)
        monkeypatch.setattr(
            "app.network_safety.gateway.NetworkGateway.request", prohibited
        )
        monkeypatch.setattr(
            "app.network_safety.gateway.DirectTCPConnector.connect", prohibited
        )
        monkeypatch.setattr("httpcore.ConnectionPool.stream", prohibited)
        decision = classify_asset_candidate_dns(
            "api.example.test",
            resolver=FakeResolver(addresses={"api.example.test": ("8.8.8.8",)}),
        )
        assert decision.code == "asset_candidate_dns_public_only"

        with SessionLocal() as db:
            after = {
                model: db.scalar(select(func.count()).select_from(model))
                for model in tracked
            }
            assert db.get(Target, target_id).network_mode == network_mode
        assert after == before
        assert settings.allowed_target_hosts == allowed_hosts
        assert settings.allowed_target_host_set == allowed_host_set
    finally:
        if target_id is not None:
            with SessionLocal() as db:
                db.execute(delete(Target).where(Target.id == target_id))
                db.commit()
