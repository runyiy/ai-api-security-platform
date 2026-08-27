from threading import Barrier, Event, Lock, Thread
import ssl

import httpcore
import pytest

from app.network_safety.controller import NetworkExecutionController
from app.network_safety.gateway import NetworkGateway, NetworkGatewayError
from tests.network_safety.test_gateway import Connector, Resolver, Stream


def call_gateway(gateway: NetworkGateway, *, target_id: int = 1):
    return gateway.request(
        target_id=target_id,
        network_mode="private_local",
        method="GET",
        url="http://lab.test/x",
        headers={},
        max_response_bytes=2,
    )


def gateway_for(controller, *, resolver=None, connector=None) -> NetworkGateway:
    return NetworkGateway(
        controller=controller,
        resolver=resolver or Resolver(("127.0.0.1",)),
        connector=connector or Connector(Stream("127.0.0.1")),
    )


def test_default_state_allows_private_local_request() -> None:
    controller = NetworkExecutionController()
    assert call_gateway(gateway_for(controller)).status_code == 200
    assert call_gateway(gateway_for(controller)).status_code == 200


def test_global_and_target_switches_fail_before_dns_and_connect() -> None:
    for target_only, expected_code in (
        (False, "network_global_disabled"),
        (True, "network_target_disabled"),
    ):
        controller = NetworkExecutionController()
        resolver = Resolver(("127.0.0.1",))
        connector = Connector(Stream("127.0.0.1"))
        if target_only:
            controller.disable_target(1)
        else:
            controller.disable_global()
        with pytest.raises(NetworkGatewayError) as exc_info:
            call_gateway(gateway_for(controller, resolver=resolver, connector=connector))
        assert exc_info.value.code == expected_code
        assert resolver.calls == []
        assert connector.calls == []


def test_target_switch_is_isolated_and_enable_restores_only_target() -> None:
    controller = NetworkExecutionController()
    controller.disable_target(1)
    assert call_gateway(gateway_for(controller), target_id=2).status_code == 200
    controller.disable_global()
    controller.enable_target(1)
    with pytest.raises(NetworkGatewayError) as exc_info:
        call_gateway(gateway_for(controller), target_id=1)
    assert exc_info.value.code == "network_global_disabled"
    controller.enable_global()
    assert call_gateway(gateway_for(controller), target_id=1).status_code == 200


class RecordingSemaphore:
    def __init__(self, semaphore) -> None:
        self.semaphore = semaphore
        self.acquire_started = Event()

    def acquire(self, *, timeout):
        self.acquire_started.set()
        return self.semaphore.acquire(timeout=timeout)

    def release(self):
        self.semaphore.release()


@pytest.mark.parametrize("target_only", [False, True])
def test_waiter_rechecks_switch_after_permit_acquisition(target_only) -> None:
    controller = NetworkExecutionController(maximum_concurrency=1)
    recording = RecordingSemaphore(controller._permits)
    controller._permits = recording
    resolver = Resolver(("127.0.0.1",))
    connector = Connector(Stream("127.0.0.1"))
    gateway = gateway_for(controller, resolver=resolver, connector=connector)
    errors = []

    with controller.admission(99):
        recording.acquire_started.clear()
        thread = Thread(target=lambda: _capture_error(errors, call_gateway, gateway))
        thread.start()
        assert recording.acquire_started.wait(timeout=2)
        if target_only:
            controller.disable_target(1)
        else:
            controller.disable_global()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert errors[0].code == (
        "network_target_disabled" if target_only else "network_global_disabled"
    )
    assert resolver.calls == []
    assert connector.calls == []


class BlockingResolver(Resolver):
    def __init__(self):
        super().__init__(("127.0.0.1",))
        self.entered = Event()
        self.release = Event()

    def resolve(self, hostname):
        self.entered.set()
        assert self.release.wait(timeout=2)
        return super().resolve(hostname)


def test_switch_during_resolution_is_rechecked_before_connect() -> None:
    controller = NetworkExecutionController()
    resolver = BlockingResolver()
    connector = Connector(Stream("127.0.0.1"))
    gateway = gateway_for(controller, resolver=resolver, connector=connector)
    errors = []
    thread = Thread(target=lambda: _capture_error(errors, call_gateway, gateway))
    thread.start()
    assert resolver.entered.wait(timeout=2)
    controller.disable_target(1)
    resolver.release.set()
    thread.join(timeout=2)
    assert errors[0].code == "network_target_disabled"
    assert connector.calls == []


class BlockingConnector(Connector):
    def __init__(self, parties: int):
        super().__init__(Stream("127.0.0.1"))
        self.release = Event()
        self.full = Event()
        self.lock = Lock()
        self.active = 0
        self.maximum = 0
        self.parties = parties

    def connect(self, **kwargs):
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            if self.active == self.parties:
                self.full.set()
        assert self.release.wait(timeout=3)
        with self.lock:
            self.active -= 1
        return Stream("127.0.0.1")


def test_real_threads_never_exceed_process_local_concurrency_cap() -> None:
    controller = NetworkExecutionController(
        maximum_concurrency=2, permit_wait_seconds=2
    )
    connector = BlockingConnector(parties=2)
    gateway = gateway_for(controller, connector=connector)
    start = Barrier(7)
    errors = []

    def worker():
        start.wait()
        try:
            call_gateway(gateway)
        except Exception as exc:
            errors.append(exc)

    threads = [Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    start.wait()
    assert connector.full.wait(timeout=2)
    assert connector.maximum == 2
    connector.release.set()
    for thread in threads:
        thread.join(timeout=3)
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert connector.maximum == 2


def test_concurrency_wait_timeout_is_bounded_and_sanitized() -> None:
    controller = NetworkExecutionController(
        maximum_concurrency=1, permit_wait_seconds=0.01
    )
    resolver = Resolver(("127.0.0.1",))
    with controller.admission(99):
        with pytest.raises(NetworkGatewayError) as exc_info:
            call_gateway(gateway_for(controller, resolver=resolver))
    assert exc_info.value.code == "network_concurrency_timeout"
    assert exc_info.value.reason == "Network concurrency permit wait timed out."
    assert resolver.calls == []


@pytest.mark.parametrize(
    "failure",
    ["dns", "peer", "peer_prohibited", "tls", "overflow", "timeout"],
)
def test_permit_is_released_after_every_failure_path(failure) -> None:
    controller = NetworkExecutionController(
        maximum_concurrency=1, permit_wait_seconds=0.01
    )
    if failure == "dns":
        failing = gateway_for(controller, resolver=Resolver(RuntimeError("secret")))
    elif failure == "peer":
        failing = gateway_for(controller, connector=Connector(Stream("127.0.0.2")))
    elif failure == "peer_prohibited":
        failing = gateway_for(
            controller, connector=Connector(Stream("169.254.169.254"))
        )
    elif failure == "tls":
        context = ssl.create_default_context()
        context.check_hostname = False
        failing = NetworkGateway(
            controller=controller,
            resolver=Resolver(("127.0.0.1",)),
            connector=Connector(Stream("127.0.0.1")),
            ssl_context=context,
        )
    elif failure == "overflow":
        response = b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabc"
        failing = gateway_for(
            controller, connector=Connector(Stream("127.0.0.1", response))
        )
    else:
        failing = gateway_for(
            controller,
            connector=Connector(
                Stream("127.0.0.1", read_error=httpcore.ReadTimeout("secret"))
            ),
        )

    with pytest.raises(NetworkGatewayError):
        if failure == "tls":
            failing.request(target_id=1, network_mode="private_local", method="GET",
                            url="https://lab.test/x", headers={})
        else:
            call_gateway(failing)
    assert call_gateway(gateway_for(controller)).status_code == 200


def _capture_error(errors, function, *args) -> None:
    try:
        function(*args)
    except NetworkGatewayError as exc:
        errors.append(exc)
