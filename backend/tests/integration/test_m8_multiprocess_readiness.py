from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import sys
from threading import Event, Lock, Thread
import time

import pytest
from sqlalchemy import delete, func, select, text

from app.db.models import (
    ExecutionPlan,
    ExecutionPlanApprovalRecord,
    ExecutionPlanClaim,
    ExecutionPlanProgress,
    PlanAction,
    SafetyDecisionRecord,
    Scope,
    Target,
    TestCase,
    TestRun,
)
from app.db.session import SessionLocal, engine
from app.executors.http import ExecutionBlockedError
from app.api.routes.test_runs import executor as runtime_executor
from app.services.execution_plan_approval import recompute_persisted_plan_digest
from app.services.execution_plan_claim import ClaimHandle, ExecutionPlanClaimService
from app.services.execution_plan_progress import (
    ExecutionPlanProgressService,
    ExecutionProgressLostError,
)
from app.services.plan_execution import PlanExecutionService
from app.services.test_case_planning import create_test_case_execution_plan
from app.services.execution_plan_approval import record_plan_decision
from tests.services.test_plan_execution_integration import approved_plan


class LocalServerState:
    def __init__(self) -> None:
        self.lock = Lock()
        self.count = 0
        self.active = 0
        self.maximum_active = 0
        self.timestamps: list[float] = []
        self.request_entered = Event()
        self.release_response = Event()
        self.block_responses = False


@pytest.fixture
def localhost_server():
    state = LocalServerState()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with state.lock:
                state.count += 1
                state.active += 1
                state.maximum_active = max(state.maximum_active, state.active)
                state.timestamps.append(time.monotonic())
            state.request_entered.set()
            if state.block_responses:
                assert state.release_response.wait(timeout=8)
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            with state.lock:
                state.active -= 1

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, state
    finally:
        state.release_response.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def retarget_plan(
    plan_id: int, target_id: int, port: int, *, rate=100.0,
    hostname="127.0.0.1",
) -> None:
    with SessionLocal() as db:
        target = db.get(Target, target_id)
        action = db.scalar(
            select(PlanAction).where(PlanAction.execution_plan_id == plan_id)
        )
        scope = db.scalar(select(Scope).where(Scope.target_id == target_id))
        assert target is not None and action is not None and scope is not None
        target.base_url = f"http://{hostname}:{port}/api"
        target.network_mode = "private_local"
        target.authorization_revision.max_requests_per_second = rate
        action.url = f"http://{hostname}:{port}/api/projects/project%20100"
        scope.hostname = hostname
        scope.path_pattern = "/api/projects/*"
        db.flush()
        plan = db.get(ExecutionPlan, plan_id)
        assert plan is not None
        plan.plan_digest = recompute_persisted_plan_digest(db, plan_id)
        approvals = db.scalars(
            select(ExecutionPlanApprovalRecord).where(
                ExecutionPlanApprovalRecord.execution_plan_id == plan_id
            )
        )
        for approval in approvals:
            approval.plan_digest = plan.plan_digest
        db.commit()


def process_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["EXECUTION_TOPOLOGY"] = "multi_process"
    return environment


def execute_process(plan_id: int, *, synchronized=False):
    prefix = "print('ready',flush=True); sys.stdin.readline(); " if synchronized else ""
    code = (
        "import json,sys; from fastapi.testclient import TestClient; "
        "from app.main import app; " + prefix
        + "r=TestClient(app).post('/api/execution-plans/'+sys.argv[1]+'/execute'); "
        "print(json.dumps({'status':r.status_code,'body':r.json()}),flush=True)"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code, str(plan_id)], cwd=".",
        env=process_environment(), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def finish_process(process, *, timeout=12) -> dict:
    output, error = process.communicate(timeout=timeout)
    assert process.returncode == 0, error
    return json.loads(output.strip().splitlines()[-1])


def wait_for_lease_expiry(plan_id: int, timeout=3) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as db:
            expired = db.scalar(text(
                "SELECT lease_expires_at <= clock_timestamp() "
                "FROM execution_plan_claims WHERE execution_plan_id=:plan_id"
            ), {"plan_id": plan_id})
        if expired is True:
            return
        time.sleep(0.01)
    pytest.fail("claim lease did not expire")


def crash_progress_process(plan_id: int, phase: str) -> dict:
    code = (
        "import json,os,sys; from app.db.session import engine; "
        "from app.services.execution_plan_claim import ExecutionPlanClaimService; "
        "from app.services.execution_plan_progress import ExecutionPlanProgressService; "
        "c=ExecutionPlanClaimService(bind=engine); p=ExecutionPlanProgressService(bind=engine); "
        "h=c.acquire(int(sys.argv[1]),'crashed-worker',lease_seconds=0.2); "
        "p.prepare_attempt(h); "
        "p.mark_network_started(h) if sys.argv[2]=='network_started' else None; "
        "print(json.dumps({'generation':h.fencing_generation}),flush=True); os._exit(0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(plan_id), phase], cwd=".",
        env=process_environment(), capture_output=True, text=True,
        check=True, timeout=5,
    )
    return json.loads(result.stdout.strip())


def create_second_approved_plan(plan_id: int) -> int:
    with SessionLocal() as db:
        action = db.scalar(
            select(PlanAction).where(PlanAction.execution_plan_id == plan_id)
        )
        assert action is not None
        second = create_test_case_execution_plan(
            db, test_case_id=action.test_case_id, credential_binding_id=None
        )
        record_plan_decision(db, execution_plan_id=second.id, decision="approved")
        db.commit()
        return second.id


def delete_extra_plan(plan_id: int) -> None:
    with SessionLocal() as db:
        db.execute(delete(SafetyDecisionRecord).where(
            SafetyDecisionRecord.execution_plan_id == plan_id
        ))
        db.execute(delete(ExecutionPlanApprovalRecord).where(
            ExecutionPlanApprovalRecord.execution_plan_id == plan_id
        ))
        db.execute(delete(ExecutionPlanProgress).where(
            ExecutionPlanProgress.execution_plan_id == plan_id
        ))
        db.execute(delete(ExecutionPlanClaim).where(
            ExecutionPlanClaim.execution_plan_id == plan_id
        ))
        db.execute(delete(TestRun).where(TestRun.execution_plan_id == plan_id))
        db.execute(delete(PlanAction).where(PlanAction.execution_plan_id == plan_id))
        db.execute(delete(ExecutionPlan).where(ExecutionPlan.id == plan_id))
        db.commit()


def test_same_plan_concurrent_processes_execute_one_local_request_and_replay(
    approved_plan, localhost_server
) -> None:
    plan_id, target_id, _, _ = approved_plan
    port, state = localhost_server
    retarget_plan(plan_id, target_id, port)
    workers = [execute_process(plan_id, synchronized=True) for _ in range(2)]
    for worker in workers:
        assert worker.stdout.readline().strip() == "ready"
    for worker in workers:
        worker.stdin.write("go\n")
        worker.stdin.flush()
    results = [finish_process(worker) for worker in workers]
    replay = finish_process(execute_process(plan_id))

    assert state.count == 1
    assert sum(result["status"] == 200 for result in results) >= 1
    assert replay["status"] == 200
    successful_ids = {
        result["body"]["id"] for result in [*results, replay]
        if result["status"] == 200
    }
    assert len(successful_ids) == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count(TestRun.id)).where(
            TestRun.execution_plan_id == plan_id
        )) == 1
        claim = db.get(ExecutionPlanClaim, plan_id)
        assert claim is not None and claim.owner_id is None


def test_pre_network_crash_allows_higher_generation_safe_takeover(
    approved_plan, localhost_server
) -> None:
    plan_id, target_id, _, _ = approved_plan
    port, state = localhost_server
    retarget_plan(plan_id, target_id, port)
    crashed = crash_progress_process(plan_id, "pre_network")
    wait_for_lease_expiry(plan_id)
    result = finish_process(execute_process(plan_id))
    assert result["status"] == 200
    assert state.count == 1
    with SessionLocal() as db:
        progress = db.get(ExecutionPlanProgress, plan_id)
        claim = db.get(ExecutionPlanClaim, plan_id)
        assert progress.fencing_generation > crashed["generation"]
        assert claim.fencing_generation == progress.fencing_generation
        assert claim.owner_id is None
    stale = ClaimHandle(
        execution_plan_id=plan_id, owner_id="crashed-worker",
        fencing_generation=crashed["generation"],
        lease_expires_at=datetime.now(timezone.utc),
        database_now=datetime.now(timezone.utc),
    )
    with pytest.raises(ExecutionProgressLostError):
        ExecutionPlanProgressService(bind=engine).mark_network_started(stale)
    with SessionLocal() as db:
        canonical = db.scalar(
            select(TestRun).where(TestRun.execution_plan_id == plan_id)
        )
        action = db.scalar(
            select(PlanAction).where(PlanAction.execution_plan_id == plan_id)
        )
        assert canonical is not None and action is not None
        test_case = db.get(TestCase, canonical.test_case_id)
        assert test_case is not None
        canonical_snapshot = (
            canonical.id, canonical.response_status, canonical.response_body,
            canonical.error_message,
        )
        service = PlanExecutionService(db=db, executor=runtime_executor)
        with pytest.raises(ExecutionBlockedError) as rejected:
            service._finish(
                claim_handle=stale,
                test_case=test_case,
                request_data={"stale": "must-not-persist"},
                target_id=target_id,
                revision_id=canonical.authorization_revision_id,
                plan_id=plan_id,
                action_id=action.id,
                outcome="succeeded",
                response_status=299,
                response_body="stale replacement",
                duration_ms=1,
            )
        assert rejected.value.code == "execution_plan_result_fencing_lost"
    with SessionLocal() as db:
        persisted = db.scalar(
            select(TestRun).where(TestRun.execution_plan_id == plan_id)
        )
        assert persisted is not None
        assert (
            persisted.id, persisted.response_status, persisted.response_body,
            persisted.error_message,
        ) == canonical_snapshot
        assert db.scalar(select(func.count(TestRun.id)).where(
            TestRun.execution_plan_id == plan_id
        )) == 1


def test_network_started_crash_remains_in_doubt_without_second_request(
    approved_plan, localhost_server
) -> None:
    plan_id, target_id, _, _ = approved_plan
    port, state = localhost_server
    retarget_plan(plan_id, target_id, port)
    crash_progress_process(plan_id, "network_started")
    wait_for_lease_expiry(plan_id)
    result = finish_process(execute_process(plan_id))
    assert result["status"] == 403
    assert result["body"]["detail"]["code"] == "execution_plan_in_doubt"
    assert state.count == 0
    with SessionLocal() as db:
        assert db.scalar(select(TestRun.id).where(
            TestRun.execution_plan_id == plan_id
        )) is None
        assert db.get(ExecutionPlanProgress, plan_id).phase == "network_started"


def test_cross_process_cancelled_plan_never_reaches_local_server(
    approved_plan, localhost_server
) -> None:
    plan_id, target_id, _, _ = approved_plan
    port, state = localhost_server
    retarget_plan(plan_id, target_id, port, rate=1.0)
    with engine.begin() as db:
        seeded = db.scalar(text(
            "INSERT INTO rate_reservation_states AS state (key,next_allowed_at) "
            "VALUES (:key,clock_timestamp()+interval '10 seconds') "
            "ON CONFLICT (key) DO UPDATE SET "
            "next_allowed_at=clock_timestamp()+interval '10 seconds' "
            "RETURNING next_allowed_at"
        ), {"key": f"target:{target_id}"})
    owner = execute_process(plan_id)
    deadline = time.monotonic() + 5
    generation = None
    while time.monotonic() < deadline:
        with engine.connect() as db:
            row = db.execute(text(
                "SELECT claim.owner_id, claim.fencing_generation, progress.phase, "
                "rate.next_allowed_at FROM execution_plan_claims AS claim "
                "JOIN execution_plan_progress AS progress USING (execution_plan_id) "
                "JOIN rate_reservation_states AS rate ON rate.key=:key "
                "WHERE claim.execution_plan_id=:plan_id"
            ), {"key": f"target:{target_id}", "plan_id": plan_id}).first()
        if (
            row is not None and row.owner_id is not None
            and row.phase == "pre_network" and row.next_allowed_at > seeded
        ):
            generation = row.fencing_generation
            break
        time.sleep(0.01)
    else:
        pytest.fail("normal plan worker did not enter the rate-wait window")
    cancel_code = (
        "import sys; from fastapi.testclient import TestClient; from app.main import app; "
        "r=TestClient(app).post('/api/execution-plans/'+sys.argv[1]+'/cancel'); print(r.status_code)"
    )
    cancelled = subprocess.run(
        [sys.executable, "-c", cancel_code, str(plan_id)], cwd=".",
        env=process_environment(), capture_output=True, text=True,
        check=True, timeout=8,
    )
    assert cancelled.stdout.strip() == "200"
    owner_result = finish_process(owner, timeout=15)
    assert owner_result["status"] == 403
    assert owner_result["body"]["detail"]["code"] == "execution_plan_cancelled"
    retry = finish_process(execute_process(plan_id))
    assert retry["status"] == 403
    assert retry["body"]["detail"]["code"] == "execution_plan_cancelled"
    assert state.count == 0
    with SessionLocal() as db:
        assert db.scalar(select(TestRun.id).where(
            TestRun.execution_plan_id == plan_id
        )) is None
        claim = db.get(ExecutionPlanClaim, plan_id)
        assert claim is not None
        assert claim.owner_id is None
        assert claim.fencing_generation == generation


def test_shared_rate_reservations_order_full_plan_processes(
    approved_plan, localhost_server
) -> None:
    plan_id, target_id, _, _ = approved_plan
    port, state = localhost_server
    retarget_plan(plan_id, target_id, port)
    second_plan_id = create_second_approved_plan(plan_id)
    try:
        workers = [
            execute_process(candidate, synchronized=True)
            for candidate in (plan_id, second_plan_id)
        ]
        for worker in workers:
            assert worker.stdout.readline().strip() == "ready"
        for worker in workers:
            worker.stdin.write("go\n")
            worker.stdin.flush()
        results = [finish_process(worker) for worker in workers]
        assert [result["status"] for result in results] == [200, 200]
        assert state.count == 2
        assert len(state.timestamps) == 2
        assert abs(state.timestamps[1] - state.timestamps[0]) >= 0.35
        with engine.connect() as db:
            reservation = db.execute(text(
                "SELECT next_allowed_at > clock_timestamp() - interval '2 seconds' "
                "FROM rate_reservation_states WHERE key=:key"
            ), {"key": f"target:{target_id}"}).scalar()
        assert reservation is True
    finally:
        delete_extra_plan(second_plan_id)


def test_shared_network_cap_spans_full_plan_requests(
    approved_plan, localhost_server
) -> None:
    plan_id, target_id, _, _ = approved_plan
    port, state = localhost_server
    retarget_plan(plan_id, target_id, port)
    second_plan_id = create_second_approved_plan(plan_id)
    state.block_responses = True
    with engine.begin() as db:
        db.execute(text(
            "UPDATE network_global_control SET maximum_concurrency=1 WHERE id=1"
        ))
    first = execute_process(plan_id)
    try:
        assert state.request_entered.wait(timeout=5)
        second = execute_process(second_plan_id)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                progress = db.get(ExecutionPlanProgress, second_plan_id)
                if progress is not None and progress.phase == "network_started":
                    break
            time.sleep(0.01)
        else:
            pytest.fail("second plan did not reach network admission")
        assert state.count == 1
        assert second.poll() is None
        state.release_response.set()
        results = [finish_process(first), finish_process(second)]
        assert [result["status"] for result in results] == [200, 200]
        assert state.count == 2
        assert state.maximum_active == 1
    finally:
        state.release_response.set()
        for process in (first, locals().get("second")):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=3)
        with engine.begin() as db:
            db.execute(text(
                "UPDATE network_global_control SET maximum_concurrency=4 WHERE id=1"
            ))
        delete_extra_plan(second_plan_id)


def test_cross_process_target_kill_switch_is_terminal_and_replay_is_zero_network(
    approved_plan, localhost_server
) -> None:
    plan_id, target_id, _, _ = approved_plan
    port, state = localhost_server
    retarget_plan(plan_id, target_id, port, hostname="localhost")
    worker_code = (
        "import json,sys; from app.db.session import SessionLocal; "
        "from app.api.routes.test_runs import policy_engine; "
        "from app.executors.http import PolicyEnforcedHTTPExecutor; "
        "from app.executors.runtime import platform_rate_limiter; "
        "from app.network_safety.gateway import NetworkGateway; "
        "from app.network_safety.runtime import network_execution_controller; "
        "from app.services.plan_execution import PlanExecutionService;\n"
        "class R:\n"
        " def resolve(self,hostname):\n"
        "  print('dns',flush=True); sys.stdin.readline(); return ('127.0.0.1',)\n"
        "with SessionLocal() as db:\n"
        " run=PlanExecutionService(db=db,executor=PolicyEnforcedHTTPExecutor("
        "policy_engine=policy_engine,rate_limiter=platform_rate_limiter,"
        "network_gateway=NetworkGateway(controller=network_execution_controller,"
        "resolver=R()))).execute(execution_plan_id=int(sys.argv[1]))\n"
        " print(json.dumps({'status':200,'body':{'id':run.id,"
        "'error_message':run.error_message}}),flush=True)"
    )
    worker = subprocess.Popen(
        [sys.executable, "-c", worker_code, str(plan_id)], cwd=".",
        env=process_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    assert worker.stdout.readline().strip() == "dns"
    switch_code = (
        "import sys; from app.network_safety.runtime import network_execution_controller as c; "
        "getattr(c,sys.argv[1])(int(sys.argv[2]))"
    )
    subprocess.run(
        [sys.executable, "-c", switch_code, "disable_target", str(target_id)],
        cwd=".", env=process_environment(), check=True, timeout=5,
    )
    try:
        worker.stdin.write("continue\n")
        worker.stdin.flush()
        first = finish_process(worker)
        assert first["status"] == 200
        assert first["body"]["error_message"].startswith(
            "network_target_disabled:"
        )
        assert state.count == 0
        with SessionLocal() as db:
            progress = db.get(ExecutionPlanProgress, plan_id)
            canonical = db.get(TestRun, first["body"]["id"])
            assert progress is not None and progress.phase == "network_started"
            assert canonical is not None
            assert canonical.execution_plan_id == plan_id
            assert canonical.response_status is None
        subprocess.run(
            [sys.executable, "-c", switch_code, "enable_target", str(target_id)],
            cwd=".", env=process_environment(), check=True, timeout=5,
        )
        replay = finish_process(execute_process(plan_id))
        assert replay["status"] == 200
        assert replay["body"]["id"] == first["body"]["id"]
        assert state.count == 0
    finally:
        subprocess.run(
            [sys.executable, "-c", switch_code, "enable_target", str(target_id)],
            cwd=".", env=process_environment(), check=True, timeout=5,
        )


def test_different_exact_plans_keep_cancellation_claims_and_results_independent(
    approved_plan, localhost_server
) -> None:
    plan_id, target_id, _, _ = approved_plan
    port, state = localhost_server
    retarget_plan(plan_id, target_id, port)
    second_plan_id = create_second_approved_plan(plan_id)
    cancel_code = (
        "import sys; from fastapi.testclient import TestClient; from app.main import app; "
        "r=TestClient(app).post('/api/execution-plans/'+sys.argv[1]+'/cancel'); print(r.status_code)"
    )
    try:
        cancelled = subprocess.run(
            [sys.executable, "-c", cancel_code, str(plan_id)], cwd=".",
            env=process_environment(), capture_output=True, text=True,
            check=True, timeout=5,
        )
        assert cancelled.stdout.strip() == "200"
        blocked = finish_process(execute_process(plan_id))
        independent = finish_process(execute_process(second_plan_id))
        assert blocked["status"] == 403
        assert blocked["body"]["detail"]["code"] == "execution_plan_cancelled"
        assert independent["status"] == 200
        assert independent["body"]["execution_plan_id"] == second_plan_id
        assert state.count == 1
        with SessionLocal() as db:
            assert db.scalar(select(TestRun.id).where(
                TestRun.execution_plan_id == plan_id
            )) is None
            assert db.scalar(select(TestRun.id).where(
                TestRun.execution_plan_id == second_plan_id
            )) == independent["body"]["id"]
    finally:
        delete_extra_plan(second_plan_id)
