import subprocess
import ssl
import sys
from threading import Event, Thread

import pytest
import httpcore
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from app.db.session import engine, network_coordination_engine
from app.network_safety.controller import NetworkExecutionController, NetworkExecutionDenied
from app.network_safety.gateway import NetworkGateway, NetworkGatewayError
from app.network_safety.postgres_controller import (
    ADVISORY_LOCK_NAMESPACE,
    PostgresNetworkExecutionController,
)
from app.network_safety.runtime import network_execution_controller, network_gateway
from tests.network_safety.test_gateway import Connector, Resolver, Stream
from tests.services.test_plan_execution_integration import approved_plan


@pytest.fixture(autouse=True)
def reset_global_control():
    with engine.begin() as db:
        db.execute(text(
            "UPDATE network_global_control SET global_enabled=true, "
            "maximum_concurrency=4, updated_at=clock_timestamp() WHERE id=1"
        ))
    yield
    with engine.begin() as db:
        db.execute(text(
            "UPDATE network_global_control SET global_enabled=true, "
            "maximum_concurrency=4, updated_at=clock_timestamp() WHERE id=1"
        ))


def controller(**kwargs):
    return PostgresNetworkExecutionController(bind=engine, **kwargs)


def subprocess_check(target_id: int) -> str:
    code = (
        "from app.db.session import engine; "
        "from app.network_safety.postgres_controller import PostgresNetworkExecutionController; "
        "from app.network_safety.controller import NetworkExecutionDenied; import sys; "
        "c=PostgresNetworkExecutionController(bind=engine); "
        "\ntry: c.check_enabled(int(sys.argv[1])); print('enabled')\n"
        "except NetworkExecutionDenied as e: print(e.code)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(target_id)], cwd=".",
        capture_output=True, text=True, check=True, timeout=10,
    )
    return result.stdout.strip()


def test_switches_are_cross_process_and_target_isolated(approved_plan) -> None:
    _, target_id, _, _ = approved_plan
    shared = controller()
    shared.disable_global()
    assert subprocess_check(target_id) == "network_global_disabled"
    shared.enable_global()
    shared.disable_target(target_id)
    assert subprocess_check(target_id) == "network_target_disabled"
    shared.check_enabled(target_id + 1)
    shared.enable_target(target_id)
    assert subprocess_check(target_id) == "enabled"


def _permit_process(target_id: int, wait: float = 5.0):
    code = (
        "from app.db.session import engine; "
        "from app.network_safety.postgres_controller import PostgresNetworkExecutionController; "
        "import sys; c=PostgresNetworkExecutionController(bind=engine,permit_wait_seconds=float(sys.argv[2])); "
        "print('started',flush=True)\n"
        "try:\n with c.admission(int(sys.argv[1])):\n  print('acquired',flush=True); sys.stdin.readline()\n"
        "except Exception as e: print(getattr(e,'code','error'),flush=True)"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code, str(target_id), str(wait)], cwd=".",
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )


def test_cross_process_cap_timeout_and_crash_release(approved_plan) -> None:
    _, target_id, _, _ = approved_plan
    with engine.begin() as db:
        db.execute(text("UPDATE network_global_control SET maximum_concurrency=1 WHERE id=1"))
    owner = _permit_process(target_id)
    try:
        assert owner.stdout.readline().strip() == "started"
        assert owner.stdout.readline().strip() == "acquired"
        contender = _permit_process(target_id, 0.1)
        assert contender.stdout.readline().strip() == "started"
        assert contender.stdout.readline().strip() == "network_concurrency_timeout"
        contender.wait(timeout=3)
        owner.terminate()
        owner.wait(timeout=3)
        with controller(permit_wait_seconds=1).admission(target_id):
            pass
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=3)


def test_cross_process_exact_cap_two_rejects_third(approved_plan) -> None:
    _, target_id, _, _ = approved_plan
    with engine.begin() as db:
        db.execute(text("UPDATE network_global_control SET maximum_concurrency=2 WHERE id=1"))
    owners = [_permit_process(target_id) for _ in range(2)]
    try:
        for owner in owners:
            assert owner.stdout.readline().strip() == "started"
            assert owner.stdout.readline().strip() == "acquired"
        contender = _permit_process(target_id, 0.1)
        assert contender.stdout.readline().strip() == "started"
        assert contender.stdout.readline().strip() == "network_concurrency_timeout"
        contender.wait(timeout=3)
    finally:
        for owner in owners:
            if owner.poll() is None:
                owner.stdin.write("release\n")
                owner.stdin.flush()
                owner.wait(timeout=3)


@pytest.mark.parametrize("target_only", [False, True])
def test_waiter_rechecks_disable_after_permit(approved_plan, target_only) -> None:
    _, target_id, _, _ = approved_plan
    with engine.begin() as db:
        db.execute(text("UPDATE network_global_control SET maximum_concurrency=1 WHERE id=1"))
    shared = controller(permit_wait_seconds=2)
    with shared.admission(target_id):
        waiter = _permit_process(target_id, 2)
        assert waiter.stdout.readline().strip() == "started"
        if target_only:
            shared.disable_target(target_id)
        else:
            shared.disable_global()
    assert waiter.stdout.readline().strip() == (
        "network_target_disabled" if target_only else "network_global_disabled"
    )
    waiter.wait(timeout=3)


def test_missing_config_fails_closed_before_dns(approved_plan) -> None:
    _, target_id, _, _ = approved_plan
    resolver = Resolver(("127.0.0.1",))
    gateway = NetworkGateway(controller=controller(), resolver=resolver)
    with engine.begin() as db:
        db.execute(text("DELETE FROM network_global_control"))
    try:
        with pytest.raises(NetworkGatewayError) as raised:
            gateway.request(
                target_id=target_id, network_mode="private_local", method="GET",
                url="http://lab.test/x", headers={},
            )
        assert raised.value.code == "network_coordination_failed"
        assert resolver.calls == []
    finally:
        with engine.begin() as db:
            db.execute(text(
                "INSERT INTO network_global_control "
                "(id,global_enabled,maximum_concurrency,updated_at) "
                "VALUES (1,true,4,clock_timestamp())"
            ))


def test_database_failure_is_sanitized_before_dns(approved_plan) -> None:
    _, target_id, _, _ = approved_plan

    class FailedBind:
        def connect(self):
            raise RuntimeError("private database connection details")

    resolver = Resolver(("127.0.0.1",))
    gateway = NetworkGateway(
        controller=PostgresNetworkExecutionController(bind=FailedBind()),
        resolver=resolver,
    )
    with pytest.raises(NetworkGatewayError) as raised:
        gateway.request(
            target_id=target_id, network_mode="private_local", method="GET",
            url="http://lab.test/x", headers={},
        )
    assert raised.value.code == "network_coordination_failed"
    assert "private" not in raised.value.reason
    assert resolver.calls == []


class BlockingResolver(Resolver):
    def __init__(self):
        super().__init__(("127.0.0.1",))
        self.entered = Event()
        self.release = Event()

    def resolve(self, hostname):
        self.entered.set()
        assert self.release.wait(timeout=3)
        return super().resolve(hostname)


def test_cross_process_disable_during_dns_prevents_connect(approved_plan) -> None:
    _, target_id, _, _ = approved_plan
    resolver = BlockingResolver()
    connector = Connector(Stream("127.0.0.1"))
    gateway = NetworkGateway(
        controller=controller(), resolver=resolver, connector=connector
    )
    errors = []

    def request():
        try:
            gateway.request(
                target_id=target_id, network_mode="private_local", method="GET",
                url="http://lab.test/x", headers={},
            )
        except NetworkGatewayError as exc:
            errors.append(exc)

    thread = Thread(target=request)
    thread.start()
    assert resolver.entered.wait(timeout=2)
    code = (
        "from app.db.session import engine; "
        "from app.network_safety.postgres_controller import PostgresNetworkExecutionController; "
        "import sys; PostgresNetworkExecutionController(bind=engine).disable_target(int(sys.argv[1]))"
    )
    subprocess.run([sys.executable, "-c", code, str(target_id)], cwd=".", check=True)
    resolver.release.set()
    thread.join(timeout=3)
    assert errors[0].code == "network_target_disabled"
    assert connector.calls == []


def test_permit_is_held_without_transaction_across_request(approved_plan) -> None:
    _, target_id, _, _ = approved_plan
    observed = []

    class InspectingConnector(Connector):
        def connect(self, **kwargs):
            with engine.connect() as db:
                row = db.execute(text(
                    "SELECT activity.state, count(*) OVER () AS locks "
                    "FROM pg_locks AS locks JOIN pg_stat_activity AS activity "
                    "ON activity.pid=locks.pid WHERE locks.locktype='advisory' "
                    "AND locks.classid=:namespace AND locks.granted"
                ), {"namespace": ADVISORY_LOCK_NAMESPACE}).first()
                observed.append((row.state, row.locks))
            return super().connect(**kwargs)

    gateway = NetworkGateway(
        controller=controller(), resolver=Resolver(("127.0.0.1",)),
        connector=InspectingConnector(Stream("127.0.0.1")),
    )
    gateway.request(
        target_id=target_id, network_mode="private_local", method="GET",
        url="http://lab.test/x", headers={},
    )
    assert observed == [("idle", 1)]


@pytest.mark.parametrize(
    "failure", ["dns", "connect", "peer", "tls", "overflow", "timeout", "success"]
)
def test_shared_permit_released_after_gateway_failure(approved_plan, failure) -> None:
    _, target_id, _, _ = approved_plan
    shared = controller(permit_wait_seconds=0.05)
    with engine.begin() as db:
        db.execute(text("UPDATE network_global_control SET maximum_concurrency=1 WHERE id=1"))
    if failure == "dns":
        gateway = NetworkGateway(controller=shared, resolver=Resolver(RuntimeError("secret")))
    elif failure == "connect":
        gateway = NetworkGateway(
            controller=shared, resolver=Resolver(("127.0.0.1",)),
            connector=Connector(RuntimeError("secret")),
        )
    elif failure == "peer":
        gateway = NetworkGateway(
            controller=shared, resolver=Resolver(("127.0.0.1",)),
            connector=Connector(Stream("127.0.0.2")),
        )
    elif failure == "tls":
        insecure_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        insecure_context.check_hostname = False
        insecure_context.verify_mode = ssl.CERT_NONE
        gateway = NetworkGateway(
            controller=shared, resolver=Resolver(("127.0.0.1",)),
            connector=Connector(Stream("127.0.0.1")), ssl_context=insecure_context,
        )
    elif failure == "overflow":
        response = b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabc"
        gateway = NetworkGateway(
            controller=shared, resolver=Resolver(("127.0.0.1",)),
            connector=Connector(Stream("127.0.0.1", response)),
        )
    elif failure == "timeout":
        gateway = NetworkGateway(
            controller=shared, resolver=Resolver(("127.0.0.1",)),
            connector=Connector(
                Stream("127.0.0.1", read_error=httpcore.ReadTimeout("secret"))
            ),
        )
    else:
        response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
        gateway = NetworkGateway(
            controller=shared, resolver=Resolver(("127.0.0.1",)),
            connector=Connector(Stream("127.0.0.1", response)),
        )
    request_url = "https://lab.test/x" if failure == "tls" else "http://lab.test/x"
    if failure == "success":
        gateway.request(
            target_id=target_id, network_mode="private_local", method="GET",
            url=request_url, headers={}, max_response_bytes=2,
        )
    else:
        with pytest.raises(NetworkGatewayError):
            gateway.request(
                target_id=target_id, network_mode="private_local", method="GET",
                url=request_url, headers={}, max_response_bytes=2,
            )
    with shared.admission(target_id):
        pass


def test_unlock_failure_invalidates_physical_connection(approved_plan) -> None:
    _, target_id, _, _ = approved_plan
    wrappers = []

    class WrappedConnection:
        def __init__(self, delegate):
            self.delegate = delegate
            self.invalidated_by_controller = False

        def scalar(self, statement, values=None):
            if "pg_advisory_unlock" in str(statement):
                raise RuntimeError("synthetic unlock failure")
            return self.delegate.scalar(statement, values)

        def invalidate(self, *args, **kwargs):
            self.invalidated_by_controller = True
            return self.delegate.invalidate(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.delegate, name)

    class WrappedBind:
        def connect(self):
            wrapped = WrappedConnection(engine.connect())
            wrappers.append(wrapped)
            return wrapped

    shared = PostgresNetworkExecutionController(bind=WrappedBind())
    with shared.admission(target_id):
        pass
    assert wrappers[-1].invalidated_by_controller is True
    with controller(permit_wait_seconds=0.2).admission(target_id):
        pass


def test_production_runtime_uses_postgres_controller() -> None:
    assert isinstance(network_execution_controller, PostgresNetworkExecutionController)
    assert not isinstance(network_execution_controller, NetworkExecutionController)
    assert network_gateway.controller is network_execution_controller
    assert network_execution_controller.bind is network_coordination_engine
    assert isinstance(network_coordination_engine.pool, NullPool)
