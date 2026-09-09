# AI API Security Testing Platform

An authorization-aware backend for testing Broken Object Level Authorization
(BOLA), often exposed through insecure direct object references (IDOR). It helps
an operator test whether an API lets one identity access another identity's
objects, while keeping testing authorization, expected access, execution, and
human confirmation separate.

## Current status and supported use

The supported deployment is **one trusted operator, self-hosted local/private
labs**. The platform has working local/private execution; it is not wholly
offline. It is not a general-purpose attack scanner, multi-tenant SaaS, or
autonomous AI attacker.

| Path | Current support |
| --- | --- |
| Local/private execution | Explicit authorized Targets and Scope, immutable exact plans, approval when required, and execution-time safety checks. |
| M14 offline matrix preview | The agreed M14-01 through M14-06 planning scope is complete. A bounded read-only API returns facts and candidates without contacting a Target. |
| External/public runtime | Unsupported and operationally prohibited. `external_public_authorized` remains runtime blocked; public SRC/bug-bounty use has separate readiness gates. |

M14 completion is recorded in [PR #129](https://github.com/runyiy/ai-api-security-platform/pull/129)
and [Issue #128's final record](https://github.com/runyiy/ai-api-security-platform/issues/128#issuecomment-5594300820),
merged as `d5f97ce23d68aeb998a48b987e621e9afc671945`. Historical pending/future
wording in earlier slice documents does not reopen that completed offline scope
or imply that later execution/public capabilities are complete.

## Key capabilities

| Area | Implemented behavior and evidence |
| --- | --- |
| Authorization and inventory | Targets, active hostname/path/method Scope, authorization profiles and immutable revisions with lifecycle transitions. See [authorization services](backend/app/services/authorization_revision.py) and [policy tests](backend/tests/policies/test_scope_policy.py). |
| Identity and credentials | Identities are separate from CredentialBindings. PostgreSQL stored secrets use authenticated encryption; bearer material reaches request authentication through AuthenticationContext. See [credential implementation](backend/app/credentials/stored_secret.py) and [authentication boundary](backend/app/auth/context.py). |
| Plans and execution | Bounded immutable plans/actions, digests, exact-plan approval, safety audit, and local/private execution. PostgreSQL coordinates rate reservations, claims/leases, fencing, idempotency, cancellation, recovery and network concurrency. See [plan execution](backend/app/services/plan_execution.py) and [real multiprocess regression](backend/tests/integration/test_m8_multiprocess_readiness.py). |
| Discovery and reviewed metadata | Bounded OpenAPI retrieval/import with provenance, anonymous by default or with an explicitly selected documentation CredentialBinding; no automatic replay. Asset hostname rules, candidate evaluation, DNS validation and human enrollment are metadata steps. Endpoint bindings have provenance, confidence and review state. See [OpenAPI route](backend/app/api/routes/openapi.py), [asset enrollment](backend/app/services/approved_enrollment_target.py) and [binding routes](backend/app/api/routes/endpoint_resource_bindings.py). |
| Access assertions and planning | Explicit Resource/Identity access assertions with verification, provenance and time eligibility feed the offline matrix. See [M12 resolver](backend/app/services/resource_access_resolution.py) and [M14 API contract](docs/bola-matrix-preview-api.md). |
| Findings and reports | Explicit baseline/probe pairing, deterministic potential-BOLA analysis, structured evidence, human review transitions and versioned Markdown reports for confirmed findings. See [pairing regression](backend/tests/api/test_finding_evidence_pairing.py), [review API](backend/app/api/routes/findings.py) and [report service](backend/app/services/security_report.py). |
| Advisory AI | A provider abstraction receives sanitized evidence. The [currently wired route](backend/app/api/routes/ai_analysis.py) uses `MockAIProvider`, not a live remote LLM integration. No AI provider API key is needed. |

### Access truth and offline matrix planning

`relationship` and `expected_access` are independent: owner does not imply
allowed, non_owner does not imply denied, and role/name labels supply no access
truth. Observed baseline access does not establish ownership. Candidate and
rejected assertions do not supply verified truth. Eligible verified conflicts
remain conflicts, without a latest-row or highest-confidence winner.

`POST /api/bola-matrix/preview` sends a request **to this platform only**. It
uses explicitly selected, currently confirmed path/query bindings, proposed
Resource assignments, ordered identities, and an explicit timezone-aware
RFC3339 `evaluation_time`.

| Bound | Limit |
| --- | --- |
| Assignments | 1–32 |
| Identities | 0–512 |
| Assignment × identity cells | At most 512, including repeated Resources and skipped facts |
| Actual request body | 64 KiB (65,536 bytes), before JSON parsing |
| Complete serialized UTF-8 response | 4 MiB (4,194,304 bytes), before success is sent |

Results preserve slot/identity order, independent facts and exact supporting
assertion IDs. A 200 response can contain conflict or insufficient facts with
no candidate; a request-wide 4xx failure returns no partial preview. Responses
are allowlisted and use `Cache-Control: no-store`.

This is transient, read-only, **non-executable planning**. It creates no
TestCase, ExecutionPlan, PlanAction or TestRun, renders no complete request,
and grants no execution permission. A confirmed slot is not Resource-to-slot
approval; nested positions do not prove parent-child membership.
`evaluation_time` governs assertions, not a historical snapshot of binding or
identity metadata. Body binding selection and automatic downstream execution
integration are unsupported. Legacy owner-based `/api/test-cases/generate/bola`
remains a separate transitional path; new matrix shapes are not automatically
executable.

### Evidence, human review and AI

Analysis uses an explicit exact baseline/probe pair, not an unrelated latest
run. M13 evidence separates structured selected facts, bounded redacted
excerpts, fingerprints of the exact persisted response strings encoded as
UTF-8, and digest-equality/byte-length comparison metadata. Equality or length
similarity does not determine BOLA classification or authorization.

Retention bindings record a versioned policy with automatic deletion disabled.
They provide no TTL, purge, deletion-management API or automatic cleanup, and
do not clear `TestRun.response_body`; that source data's lifecycle remains
separate. See the [evidence and retention contract](docs/architecture-decisions.md#expected-access-and-evidence).

Rule analysis produces potential findings. Only human review can confirm a
finding, and formal reports require confirmed status. AI suggestions remain
advisory: AI cannot control credentials, authorization, approval, Finding
confirmation or the executor. Existing report templates remain oriented to
the legacy cross-owner workflow; M14 preview does not automatically feed them.

## Architecture

These are separate paths through the existing backend, not an automatic pipeline
from matrix preview into execution:

```text
Metadata / offline planning
  Endpoint + confirmed bindings + proposed Resources + identities + explicit time
    -> matrix preview route -> composer / selector / Resource preview
    -> M12 resolver -> pure planner -> typed facts/candidates (stop here)

Authorized local/private execution
  TestCase intent + one AuthorizationRevision + Target/Scope
    -> immutable ExecutionPlan / PlanActions -> exact-plan approval when required
    -> execution service + PostgreSQL coordination
    -> policy revalidation + AuthenticationContext -> HTTP Executor
    -> NetworkGateway checks -> authorized local/private Target -> TestRun

Analysis and review
  Exact baseline/probe TestRuns -> analyzer -> potential Finding + M13 evidence
    -> optional advisory AI (currently MockAIProvider)
    -> human review -> confirmed Finding -> report service / Markdown
```

AI is optional to review/reporting and has no edge into network execution.
The default `EXECUTION_TOPOLOGY=single_process` supports legacy direct TestCase
execution. Explicit `multi_process` is limited to local/private exact-plan
execution using shared PostgreSQL coordination; it disables the legacy direct
execute route. Worker count is not auto-detected. There is no Redis dependency,
background worker queue, scheduler, frontend console, or platform login/RBAC.

## Technology stack and repository map

Python 3.12, FastAPI/Pydantic, SQLAlchemy with psycopg, PostgreSQL 16 and Alembic
form the backend. HTTPX/httpcore and dnspython support controlled network work;
cryptography supplies AES-GCM credential encryption. pytest covers unit, API,
migration, PostgreSQL integration, local lab and real-process behavior.

```text
backend/
  app/
    main.py             FastAPI application and router registration
    api/routes/         HTTP operations
    schemas/            Typed API input/output
    core/               Settings
    db/                 SQLAlchemy models and Session ownership
    services/           Planning, coordination, discovery, analysis and reports
    policies/           Scope and authorization policy
    auth/, credentials/ AuthenticationContext and encrypted stored secrets
    executors/          Execution and rate limiting
    network_safety/     Destination checks, gateway and shared admission
    scanners/           OpenAPI retrieval/parsing
    generators/         Legacy generation and pure matrix planning
    analyzers/, domain/ Analysis and domain values
    ai/, reports/       Advisory provider/redaction and Markdown rendering
  alembic/              Schema migrations
  tests/                Unit/API/integration/migration tests and synthetic labs
  requirements*.txt     Runtime and development dependency lists
  docker-compose.yml    PostgreSQL only; persistent development configuration
docs/                   Normative contracts and acceptance runbooks
.github/workflows/       Backend migration and pytest CI
```

## Local quick start

Use a trusted Linux/WSL shell with Git, Python 3.12 plus venv support, curl,
and PostgreSQL 16 server/client binaries installed. On Debian/Ubuntu the latter
normally live in `/usr/lib/postgresql/16/bin`; adjust that path for your install.
Run PostgreSQL as your ordinary non-root user. The example creates a new
disposable local lab instance; it is not deployment provisioning.

```bash
# From the directory where you keep repositories:
git clone https://github.com/runyiy/ai-api-security-platform.git
cd ai-api-security-platform/backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --requirement requirements.txt
# Uvicorn is not included in the repository requirements:
python -m pip install 'uvicorn==0.35.0'
export PATH="/usr/lib/postgresql/16/bin:$PATH"
```

Choose unused ports (55436 for this database, 8000 for the API below). If a
port is occupied, choose another and update the matching commands/URL; do not
stop someone else's service. Keep this shell's `local_pg_root` value for cleanup.
The password below is a public **synthetic local-lab password**, never suitable
for shared or production data.

```bash
# Still in backend/, in the same shell:
local_pg_root=$(mktemp -d /tmp/api-security-local.XXXXXX)
printf '%s\n' 'local_lab_only' > "$local_pg_root/password"
initdb -D "$local_pg_root/data" -U platform_local \
  --auth=scram-sha-256 --pwfile="$local_pg_root/password"
rm "$local_pg_root/password"
pg_ctl -D "$local_pg_root/data" -l "$local_pg_root/server.log" \
  -o "-h 127.0.0.1 -p 55436 -k $local_pg_root" -w start
PGPASSWORD=local_lab_only createdb -h 127.0.0.1 -p 55436 \
  -U platform_local platform_local
pg_isready -h 127.0.0.1 -p 55436 -U platform_local -d platform_local

export DATABASE_URL='postgresql+psycopg://platform_local:local_lab_only@127.0.0.1:55436/platform_local'
export ALLOWED_TARGET_HOSTS='localhost,127.0.0.1,::1'
export EXECUTION_TOPOLOGY='single_process'
alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Proceed only after each command succeeds and PostgreSQL reports accepting
connections. Set `DATABASE_URL` **before** importing the app or starting
Alembic/pytest. [Settings](backend/app/core/config.py) also read `.env` relative
to the process working directory; there is no committed `.env.example` to copy.
This fresh-clone example uses environment variables and creates no `.env`.

In a second terminal (any directory), check only the local platform:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health
curl --fail --silent --show-error --output /dev/null http://127.0.0.1:8000/docs
curl --fail --silent --show-error --output /dev/null http://127.0.0.1:8000/openapi.json
```

`/health` returns `{"status":"ok"}` and checks application availability only,
not database health or execution authorization readiness. `/docs` serves the
interactive API UI and `/openapi.json` its schemas. The Swagger UI's browser
assets use FastAPI's default CDN URLs; the HTTP smoke checks above fetch only
local responses and do not require a Target.

The empty database has no Targets, bindings, Resources, identities or assertions.
Use the [localhost preview curl example](docs/bola-matrix-preview-api.md) only
after selecting existing same-Target metadata: declared confirmed path/query
bindings, explicit Resources and active identities. Its IDs are **synthetic
placeholders**, not seeded records. Eligible verified assertions at the chosen
instant are needed for resolved access truth; missing truth can yield a valid
200 with insufficient facts and no candidates.

### Configuration and credentials

| Environment variable | Default / purpose |
| --- | --- |
| `DATABASE_URL` | Required. Explicit PostgreSQL URL; engine setup happens at import. |
| `ALLOWED_TARGET_HOSTS` | `localhost,127.0.0.1,::1`; comma-separated host allowlist, not testing authorization. |
| `EXECUTION_TOPOLOGY` | `single_process`; `multi_process` is an explicit exact-plan-only opt-in. |
| `CREDENTIAL_ENCRYPTION_KEY` | Unset by default. Required when storing/resolving encrypted bearer secrets, including credentialed execution or authenticated OpenAPI retrieval. |
| `CREDENTIAL_ENCRYPTION_KEY_VERSION` | `v1`; nonempty version used in the encrypted-secret binding. |

Basic startup, migrations and offline matrix preview do not need a credential
encryption key. Credential operations require Base64 text decoding to exactly
**32 random bytes** for AES-256-GCM; padded URL-safe Base64 is accepted. A raw
passphrase, hex text, or an arbitrarily sized key is not the format. Provision
key material privately through the environment; never echo, log or commit it.
Keep the original key and version for an existing encrypted database: changing
either casually makes stored secrets unavailable. This is not a key-rotation
runbook. See [cipher validation tests](backend/tests/credentials/test_stored_secret_cipher.py).

The checked-in [Compose file](backend/docker-compose.yml) starts **only
PostgreSQL**, uses a persistent named volume, and publishes 5432 without a
loopback-only binding. It neither deploys the entire platform nor supplies a
disposable test database. The native example above follows the isolation model
of the [acceptance runbook](docs/m14-offline-matrix-acceptance.md#reproducible-isolated-local-run)
without needing Docker.

## Tests and verification

**Use a separate, exclusively owned, disposable TEST PostgreSQL instance.**
pytest does not redirect itself to a test database. Migration tests perform
downgrades/upgrades and fixtures commit synthetic rows; never run them against
the quick-start/operator, default, shared or production database. Run serially.

Stop the API with Ctrl-C first. In `backend/`, with the venv active and the
PostgreSQL 16 binaries on PATH, create a second instance on a different unused
loopback port. These are synthetic test credentials only:

```bash
python -m pip install --requirement requirements-dev.txt
test_pg_root=$(mktemp -d /tmp/api-security-test.XXXXXX)
printf '%s\n' 'test_only' > "$test_pg_root/password"
initdb -D "$test_pg_root/data" -U platform_test \
  --auth=scram-sha-256 --pwfile="$test_pg_root/password"
rm "$test_pg_root/password"
pg_ctl -D "$test_pg_root/data" -l "$test_pg_root/server.log" \
  -o "-h 127.0.0.1 -p 55437 -k $test_pg_root" -w start
PGPASSWORD=test_only createdb -h 127.0.0.1 -p 55437 \
  -U platform_test platform_test
export DATABASE_URL='postgresql+psycopg://platform_test:test_only@127.0.0.1:55437/platform_test'

# Verify the intended test database/user/port before any tests:
PGPASSWORD=test_only psql -h 127.0.0.1 -p 55437 -U platform_test -d platform_test \
  -c 'SELECT current_database(), current_user, inet_server_addr(), inet_server_port();'
alembic current
alembic heads
alembic upgrade head
pytest tests/integration/test_m14_matrix_acceptance.py
pytest
pip check
git diff --check
```

An empty database initially has no current revision. After migration, current
and head must be `b5d7f9a1c3e6`. The focused suite exercises the real persisted
HTTP-to-resolver/planner chain, explicit truth/history, bounds and read-only
safety. Full regression includes synthetic localhost lab HTTP and real
subprocess coordination; no third-party/public Target is needed. Fixtures own
their setup and cleanup. The [acceptance runbook](docs/m14-offline-matrix-acceptance.md)
details repeatability and evidence. A passing suite is not public readiness or
a guarantee of exhaustive correctness.

[Backend CI](.github/workflows/backend-tests.yml) installs development
requirements on Python 3.12 with a fresh PostgreSQL 16 service, applies
migrations and runs full pytest for main pull requests and main pushes.

When finished, stop only instances identified by the variables captured above:

```bash
pg_ctl -D "$test_pg_root/data" -m fast -w stop
pg_ctl -D "$local_pg_root/data" -m fast -w stop
unset DATABASE_URL
```

These commands stop owned servers; they do not delete directories. Remove only
your own captured temporary directories when their synthetic data is no longer
needed. Do not use blanket SQL cleanup, delete shared volumes, or substitute
another server's data directory. Re-export the local URL before restarting the
local API after a test session.

## Security boundaries and limitations

- **Default Deny:** Target existence is not authorization. Each execution uses
  one immutable AuthorizationRevision; grants never combine across revisions.
  Scope and platform checks can only narrow that authorization.
- A mandatory host allowlist, exact Target origin and safe path checks remain
  required. Automatic execution is GET-only, redirects are disabled, and
  timeout/response size, rates and concurrency are bounded.
- Credentials enter requests only through AuthenticationContext. Human plan
  approval never bypasses immediate pre-network authorization, Scope,
  credential, kill-switch or network checks.
- Wildcard matches, DNS classification and asset enrollment do not grant
  permission to connect. A hostname allowlist is not the complete public
  DNS/IP/peer safety boundary and does not establish public isolation.
- `external_public_authorized` remains blocked. Public SRC Readiness, a
  self-owned public-server exercise and explicitly authorized third-party
  testing are separate gates; M14 completion satisfies none of them.
- The operator-facing API is not a public management/authentication boundary.
  Keep this local setup on loopback. Platform RBAC, a review console, remote
  LLM integration, retention/deletion management and broader product maturity
  remain outside the implemented scope described here.

## Documentation

- [Research Assistant v1 plan](docs/research-assistant-roadmap.md): proposed / planning-only roadmap for a low-touch, low-token authorized research assistant.
- [Level 3 roadmap](docs/level3-roadmap.md): goals, deferred work and public readiness gates.
- [Architecture decisions](docs/architecture-decisions.md): normative product and subsystem boundaries.
- [Security model](docs/security-model.md): mandatory safety invariants.
- [BOLA matrix preview API](docs/bola-matrix-preview-api.md): metadata prerequisites, copyable localhost example, schemas and errors.
- [M14 offline acceptance](docs/m14-offline-matrix-acceptance.md): capability evidence, compatibility limits and isolated test runbook.
