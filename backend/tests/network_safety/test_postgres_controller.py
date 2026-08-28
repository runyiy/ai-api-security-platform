import subprocess
import ssl
import sys
import time
from threading import Event, Thread

import pytest
import httpcore
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from app.db.session import (
    create_network_coordination_engine,
    engine,
    network_coordination_engine,
)
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
    return PostgresNetworkExecutionController(bind=network_coordination_engine, **kwargs)


def subprocess_update(method: str, target_id: int) -> None:
    code = (
        "from app.network_safety.runtime import network_execution_controller as c; "
        "import sys; getattr(c, sys.argv[1])(int(sys.argv[2])) "
        "if 'target' in sys.argv[1] else getattr(c, sys.argv[1])()"
    )
    subprocess.run(
        [sys.executable, "-c", code, method, str(target_id)],
        cwd=".", capture_output=True, text=True, check=True, timeout=5,
    )


def subprocess_check(target_id: int) -> str:
    code = (
        "from app.db.session import network_coordination_engine; "
        "from app.network_safety.postgres_controller import PostgresNetworkExecutionController; "
        "from app.network_safety.controller import NetworkExecutionDenied; import sys; "
        "c=PostgresNetworkExecutionController(bind=network_coordination_engine); "
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


@pytest.mark.parametrize(
    ("method", "expected"),
    [("disable_global", "network_global_disabled"),
     ("disable_target", "network_target_disabled")],
)
def test_cross_process_switch_is_enforced_by_gateway_before_dns(
    approved_plan, method, expected
) -> None:
    _, target_id, _, _ = approved_plan
    subprocess_update(method, target_id)
    resolver = Resolver(("127.0.0.1",))
    connector = Connector(Stream("127.0.0.1"))
    gateway = NetworkGateway(
        controller=controller(), resolver=resolver, connector=connector
    )

    with pytest.raises(NetworkGatewayError) as raised:
        gateway.request(
            target_id=target_id, network_mode="private_local", method="GET",
            url="http://lab.test/x", headers={},
        )

    assert raised.value.code == expected
    assert resolver.calls == []
    assert connector.calls == []
    if method == "disable_target":
        other_gateway = NetworkGateway(
            controller=controller(), resolver=Resolver(("127.0.0.1",)),
            connector=Connector(Stream("127.0.0.1")),
        )
        other_gateway.request(
            target_id=target_id + 1, network_mode="private_local", method="GET",
            url="http://lab.test/x", headers={},
        )


def _permit_process(target_id: int, wait: float = 5.0):
    code = (
        "from app.db.session import network_coordination_engine; "
        "from app.network_safety.postgres_controller import PostgresNetworkExecutionController; "
        "import sys; c=PostgresNetworkExecutionController(bind=network_coordination_engine,permit_wait_seconds=float(sys.argv[2])); "
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


def test_gateway_slot_scan_rejects_and_releases_permit_acquired_after_deadline(
    approved_plan,
) -> None:
    _, target_id, _, _ = approved_plan
    with engine.begin() as db:
        db.execute(
            text("UPDATE network_global_control SET maximum_concurrency=4 WHERE id=1")
        )
    owners = [network_coordination_engine.connect() for _ in range(2)]
    for slot, owner in enumerate(owners, start=1):
        assert owner.scalar(
            text("SELECT pg_try_advisory_lock(:namespace, :slot)"),
            {"namespace": ADVISORY_LOCK_NAMESPACE, "slot": slot},
        ) is True
        owner.commit()

    clock = {"now": 0.0}
    attempts = []

    class SlowSlotConnection:
        def __init__(self, delegate):
            self.delegate = delegate

        def scalar(self, statement, values=None):
            if "pg_try_advisory_lock" in str(statement):
                attempts.append(values["slot"])
                statement = text(
                    "WITH delay AS MATERIALIZED (SELECT pg_sleep(0.01)) "
                    "SELECT pg_try_advisory_lock(:namespace, :slot) FROM delay"
                )
                result = self.delegate.scalar(statement, values)
                clock["now"] += 1.0
                return result
            return self.delegate.scalar(statement, values)

        def __getattr__(self, name):
            return getattr(self.delegate, name)

    class SlowSlotBind:
        _network_coordination_statement_timeout_ms = 1000

        def connect(self):
            return SlowSlotConnection(network_coordination_engine.connect())

    shared = PostgresNetworkExecutionController(
        bind=SlowSlotBind(), permit_wait_seconds=2.5,
        monotonic=lambda: clock["now"],
    )
    resolver = Resolver(("127.0.0.1",))
    connector = Connector(Stream("127.0.0.1"))
    gateway = NetworkGateway(
        controller=shared, resolver=resolver, connector=connector
    )
    try:
        with pytest.raises(NetworkGatewayError) as raised:
            gateway.request(
                target_id=target_id, network_mode="private_local", method="GET",
                url="http://lab.test/x", headers={},
            )
        assert raised.value.code == "network_concurrency_timeout"
        assert resolver.calls == []
        assert connector.calls == []
        assert attempts == [1, 2, 3]
        with network_coordination_engine.connect() as verifier:
            assert verifier.scalar(
                text("SELECT pg_try_advisory_lock(:namespace, 3)"),
                {"namespace": ADVISORY_LOCK_NAMESPACE},
            ) is True
            assert verifier.scalar(
                text("SELECT pg_advisory_unlock(:namespace, 3)"),
                {"namespace": ADVISORY_LOCK_NAMESPACE},
            ) is True
            verifier.commit()
    finally:
        for slot, owner in enumerate(owners, start=1):
            owner.scalar(
                text("SELECT pg_advisory_unlock(:namespace, :slot)"),
                {"namespace": ADVISORY_LOCK_NAMESPACE, "slot": slot},
            )
            owner.commit()
            owner.close()


@pytest.mark.parametrize("target_only", [False, True])
def test_gateway_waiter_rechecks_cross_process_disable_after_permit(
    approved_plan, target_only
) -> None:
    _, target_id, _, _ = approved_plan
    with engine.begin() as db:
        db.execute(text("UPDATE network_global_control SET maximum_concurrency=1 WHERE id=1"))
    waiting = Event()

    def signal_wait(delay):
        waiting.set()
        time.sleep(delay)

    waiter_controller = controller(permit_wait_seconds=2, sleep=signal_wait)
    resolver = Resolver(("127.0.0.1",))
    connector = Connector(Stream("127.0.0.1"))
    gateway = NetworkGateway(
        controller=waiter_controller, resolver=resolver, connector=connector
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

    shared = controller(permit_wait_seconds=2)
    with shared.admission(target_id):
        thread = Thread(target=request)
        thread.start()
        assert waiting.wait(timeout=2)
        subprocess_update(
            "disable_target" if target_only else "disable_global", target_id
        )
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert errors[0].code == (
        "network_target_disabled" if target_only else "network_global_disabled"
    )
    assert resolver.calls == []
    assert connector.calls == []


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
        _network_coordination_statement_timeout_ms = 1000

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


@pytest.mark.parametrize("operation", ["read", "write"])
def test_blocked_coordination_query_times_out_sanitized(
    approved_plan, operation
) -> None:
    _, target_id, _, _ = approved_plan
    bounded_engine = create_network_coordination_engine(
        coordination_timeout_seconds=0.1
    )
    shared = PostgresNetworkExecutionController(bind=bounded_engine)
    blocker = engine.connect()
    transaction = blocker.begin()
    blocker.execute(text("LOCK TABLE network_global_control IN ACCESS EXCLUSIVE MODE"))
    started = time.monotonic()
    try:
        with pytest.raises(NetworkExecutionDenied) as raised:
            if operation == "read":
                shared.check_enabled(target_id)
            else:
                shared.disable_global()
        elapsed = time.monotonic() - started
        assert elapsed < 0.75
        assert raised.value.code == "network_coordination_failed"
        assert raised.value.reason == "Shared network coordination failed."
        assert "statement timeout" not in str(raised.value).lower()
    finally:
        transaction.rollback()
        blocker.close()
        bounded_engine.dispose()


def test_statement_timeout_is_active_at_session_start_and_survives_commit() -> None:
    bounded_engine = create_network_coordination_engine(
        coordination_timeout_seconds=0.1
    )
    try:
        with bounded_engine.connect() as connection:
            assert connection.scalar(text("SHOW statement_timeout")) == "100ms"
            connection.commit()
            assert connection.scalar(text("SHOW statement_timeout")) == "100ms"
    finally:
        bounded_engine.dispose()


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
        "from app.db.session import network_coordination_engine; "
        "from app.network_safety.postgres_controller import PostgresNetworkExecutionController; "
        "import sys; PostgresNetworkExecutionController(bind=network_coordination_engine).disable_target(int(sys.argv[1]))"
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


def test_permit_spans_blocked_response_read_across_processes(approved_plan) -> None:
    _, target_id, _, _ = approved_plan
    with engine.begin() as db:
        db.execute(
            text("UPDATE network_global_control SET maximum_concurrency=1 WHERE id=1")
        )

    class BlockingReadStream(Stream):
        def __init__(self):
            super().__init__("127.0.0.1")
            self.read_started = Event()
            self.release_read = Event()

        def read(self, max_bytes, timeout=None):
            self.read_started.set()
            assert self.release_read.wait(timeout=3)
            return super().read(max_bytes, timeout)

    stream = BlockingReadStream()
    gateway = NetworkGateway(
        controller=controller(), resolver=Resolver(("127.0.0.1",)),
        connector=Connector(stream),
    )
    results = []

    def request():
        results.append(gateway.request(
            target_id=target_id, network_mode="private_local", method="GET",
            url="http://lab.test/x", headers={},
        ))

    thread = Thread(target=request)
    thread.start()
    assert stream.read_started.wait(timeout=2)
    contender = _permit_process(target_id, 0.1)
    try:
        assert contender.stdout.readline().strip() == "started"
        assert contender.stdout.readline().strip() == "network_concurrency_timeout"
        contender.wait(timeout=3)
    finally:
        if contender.poll() is None:
            contender.kill()
            contender.wait(timeout=3)
    stream.release_read.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert results[0].body == b"OK"
    with controller(permit_wait_seconds=0.2).admission(target_id):
        pass


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
        _network_coordination_statement_timeout_ms = 1000

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


def test_real_unlock_timeout_is_bounded_discards_session_and_releases_slot(
    approved_plan,
) -> None:
    _, target_id, _, _ = approved_plan
    bounded_engine = create_network_coordination_engine(
        coordination_timeout_seconds=0.1
    )
    with engine.begin() as db:
        db.execute(
            text("UPDATE network_global_control SET maximum_concurrency=1 WHERE id=1")
        )
    wrappers = []

    class SlowUnlockConnection:
        def __init__(self, delegate):
            self.delegate = delegate
            self.invalidated_by_controller = False

        def scalar(self, statement, values=None):
            if "pg_advisory_unlock" in str(statement):
                statement = text(
                    "WITH delay AS MATERIALIZED (SELECT pg_sleep(1)) "
                    "SELECT pg_advisory_unlock(:namespace, :slot) FROM delay"
                )
            return self.delegate.scalar(statement, values)

        def invalidate(self, *args, **kwargs):
            self.invalidated_by_controller = True
            return self.delegate.invalidate(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.delegate, name)

    class SlowUnlockBind:
        _network_coordination_statement_timeout_ms = 100

        def connect(self):
            wrapped = SlowUnlockConnection(bounded_engine.connect())
            wrappers.append(wrapped)
            return wrapped

    shared = PostgresNetworkExecutionController(bind=SlowUnlockBind())
    started = time.monotonic()
    try:
        with shared.admission(target_id):
            pass
        elapsed = time.monotonic() - started
        assert elapsed < 0.75
        assert wrappers[-1].invalidated_by_controller is True
        with controller(permit_wait_seconds=0.2).admission(target_id):
            pass
    finally:
        bounded_engine.dispose()


def test_production_runtime_uses_postgres_controller() -> None:
    assert isinstance(network_execution_controller, PostgresNetworkExecutionController)
    assert not isinstance(network_execution_controller, NetworkExecutionController)
    assert network_gateway.controller is network_execution_controller
    assert network_execution_controller.bind is network_coordination_engine
    assert isinstance(network_coordination_engine.pool, NullPool)
