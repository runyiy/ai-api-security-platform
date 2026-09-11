# Isolated backend CI partitions

This maintenance change starts at `4b0a37650df59db6a7708704fbbfe0e96a92e6aa`. It changes scheduling only. Existing application code, test assertions/fixtures, migrations, dependency versions and evaluation content remain unchanged. The handoff's hosted evidence is 832–918 seconds for serial pytest, with 476–527 seconds in the three W2 files; dependency installation was 7–8 seconds and initial migrations 1–2 seconds. Hosted performance for this change remains pending Review Project execution.

[The workflow](../.github/workflows/backend-tests.yml) retains pull requests to main and pushes to main, Python 3.12, dependency caching, PostgreSQL 16, read-only token permissions and bounded 20-minute jobs. There are no path exemptions, feature-push runs, retries, result reuse, xdist or test-wait changes.

The `collection` job compares three independent actual pytest collections: normal full collection, W2 selection and remaining selection. The ordered lists must match the partition rule, their union must equal normal collection and their intersection must be empty. The completeness job exports its full-list SHA256 as a same-run job output. Before selecting tests, each execution job must match that exact ordered full collection, so environment-dependent collection drift fails before fixtures execute. Duplicate IDs, missing/empty required W2 files, collection errors, ambient `PYTEST_ADDOPTS`, unknown partitions and unexpected empty shards fail closed. No fixed test-count ceiling or checked-in node list prevents future tests from being included.

After collection succeeds, `w2` and `remaining` run concurrently on different hosted runners, each with its own fresh PostgreSQL service, database identity and synthetic service credentials. **Never run them against different databases on the same PostgreSQL server**: process-safe M8 controls and tests can observe server-wide coordination. Their fixture-owned loopback servers, encrypted ephemeral credentials and coordination state are independent. Each shard applies the unchanged migrations and executes pytest serially, including migration and multiprocess tests in `remaining`. The security and execution boundaries remain those in [the security model](security-model.md#37-execution-time-revalidation) and [W2's implementation record](research-response-verification.md#3-dispatch-budgets-and-time).

The stable aggregate job/check is named `pytest`. It has explicit dependencies on `collection`, `w2` and `remaining`, runs with `always()`, and accepts only an exact set of successful dependency results. Failed, cancelled, missing, unknown or skipped dependencies cannot produce a successful gate. This follows [GitHub's dependency and conditional semantics](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-jobs). The workflow itself does not establish repository-enforced merge protection; this package does not change branch protection or rulesets. The handoff reports those protections were absent at audit time.

## Commands and counts

Follow [README isolation guidance](../README.md#tests-and-verification) before importing application code. Use newly owned PostgreSQL instances and environment-cleared copies excluding `.env*`; never use an operator database or ambient encryption key. From each copy's `backend` directory, after independently verifying its database identity:

```bash
python -m alembic upgrade head
python -m ci_shards check --output collection.json
# On two independent instances/runners, concurrently with each other:
python -m ci_shards run w2 --expected-sha256 "$FULL_COLLECTION_SHA256"
python -m ci_shards run remaining --expected-sha256 "$FULL_COLLECTION_SHA256"
```

Set `FULL_COLLECTION_SHA256` to `summary.full_sha256` in the freshly produced `collection.json`; CI obtains it directly from the completeness job output. Missing, malformed or mismatched digests reject execution.

`w2` owns exactly these paths; every other discovered test belongs to `remaining`:

- `tests/services/test_research_verification.py`
- `tests/services/test_research_verification_expiry.py`
- `tests/api/test_research_verification.py`

The helper preserves collected node IDs, parameter cases and their relative execution order. It changes no fixtures or test bodies. Each run emits its assigned count, exit code, elapsed seconds and pytest's 20 slowest durations. Collection prints counts, zero intersection and the full-list digest; its JSON file also retains all three exact lists. It is diagnostic evidence, never cached execution authority or reused across PR/main runs. The existing 3193 nodes are retained; the added helper tests are reported separately below.

## Local validation and timing

Validation uses Ubuntu 24.04.2 on WSL2 and the repository's backend virtual environment. Task-owned roots are under `/tmp/ci-shards.UsQegQ`; each runner sets only `PATH`, `LANG` and its unique synthetic `DATABASE_URL` via `env -i`. Before any application imports, independent psql checks assert database/user/port/data directory, unique PostgreSQL system identifier, UTF8, empty public schema and zero other clients. Settings then assert no operator encryption key. Serial and concurrent benchmark phases do not overlap. Both local shards use the same WSL host and read-only preinstalled Python dependencies; PostgreSQL instances, sockets, source copies, coordination and test credentials are separate. CPU and storage contention on that host can affect timings. This single-host comparison cannot measure hosted runner queuing, image pulling, cache downloads or billing.

| Validation role | Database/user | Port | PostgreSQL system identifier |
| --- | --- | --- | --- |
| Unchanged serial baseline | `ci_serial` | 55501 | `7684111756349581433` |
| Collection/helper checks | `ci_collection` | 55502 | `7684112531846755824` |
| Final W2 shard | `ci_w2_final` | 55505 | `7684116644568145622` |
| Final remaining shard | `ci_remaining_final` | 55506 | `7684116660175552297` |

Each data directory is `/tmp/ci-shards.UsQegQ/<role-directory>/data`, where the directories are `serial`, `collection`, `w2_final` and `remaining_final`. Source copies and environment-cleared `run` wrappers are alongside them. The serial source copy contains the unchanged starting commit and no new helper tests. The actual old node list equals the new normal collection after removing precisely the 35 helper nodes, including identical parameter IDs and order. Final collection: **3228 total = 3193 original + 35 helper**, **117 W2**, **3111 remaining = 3076 original + 35 helper**, intersection **0**. Ordered full-list SHA256: `745b5541c78d5603c0090394a2f6cd16fc03bc7c7b157540db6e239fee5f4eb9`.

Exact principal commands (each `run` wrapper supplies only its own verified database):

```bash
/tmp/ci-shards.UsQegQ/serial/run python -m alembic upgrade head
/tmp/ci-shards.UsQegQ/serial/run python -m pytest -q --tb=short --durations=20
/tmp/ci-shards.UsQegQ/collection/run python -m pytest -q tests/test_ci_shards.py
/tmp/ci-shards.UsQegQ/collection/run python -m ci_shards check --output /tmp/ci-shards.UsQegQ/collection/validated-nodes.json
# Each of the following owned shard wrappers applies `python -m alembic upgrade head` first.
/tmp/ci-shards.UsQegQ/w2_final/run python -m ci_shards run w2 --expected-sha256 745b5541c78d5603c0090394a2f6cd16fc03bc7c7b157540db6e239fee5f4eb9
/tmp/ci-shards.UsQegQ/remaining_final/run python -m ci_shards run remaining --expected-sha256 745b5541c78d5603c0090394a2f6cd16fc03bc7c7b157540db6e239fee5f4eb9
```

The temporary `prepare.py`, `measure.py`, `capture_base.py` and `parallel_final.py` harnesses and their JSON timing/node records remain in the owned root. `parallel_final.py` invokes the final collection check, then starts both migration-plus-pytest commands concurrently in separate processes and invokes the real aggregate CLI with their actual results. No serial full-suite execution overlaps that phase. Setup/collection/helper development checks accompanied part of the baseline; this is one local comparison, not a statistical or hosted-CI guarantee.

Helper validation covers new paths/parameter cases, missing paths, empty/duplicate/overlapping/incomplete or wrong assignments, collection errors, missing/unknown states, and real subprocess rejection of stale full-collection digests before any synthetic test body runs. All **35 helper tests passed** on final code. Aggregate CLI probes returned 0 only for complete success, and 1 for failed, cancelled, skipped and missing dependencies. `actionlint` **1.7.12**, YAML structural comparisons and `git diff --check` passed. All 206 existing test/fixture files, application files, migrations, dependencies and evaluation content match the base byte-for-byte.

During development, the new digest-drift test twice exposed an error-reporting bug (34 passed / 1 failed): pytest's final collection hook obscured the earlier mismatch error. The helper now preserves that original rejection. A preliminary shard invocation started despite the failed local prerequisite and was explicitly interrupted (exit 130); its partial timings are excluded. Its exclusively owned `w2`/`remaining` clusters on ports 55503/55504 were stopped, and final benchmarking uses the fresh clusters listed above. No CI retry, test-assertion relaxation or existing fixture change was introduced.

| Final check | Actual result |
| --- | --- |
| Unchanged serial full pytest | **3193 passed**, 62 warnings, pytest **863.09s**; process wall **865.38s**. |
| W2 shard | **117 passed**, 3111 assigned elsewhere/deselected, 62 warnings, pytest **436.60s**; process wall **437.42s**. |
| Remaining shard | **3111 passed**, 117 assigned elsewhere/deselected, 62 warnings, pytest **344.71s**; process wall **346.50s**. |
| Final collection check | All 3228 IDs accounted for exactly once; **8.08s** process wall. Both execution jobs matched its digest. A separate `GITHUB_OUTPUT` probe emitted that exact digest in the step-output format. |
| Concurrent migration-plus-test phase | **438.91s** wall; individual jobs **438.91s** and **347.98s**, with recorded overlapping monotonic start/end intervals. Each fresh database upgrade passed. |
| Collection → concurrent jobs → actual aggregate CLI | **447.17s** measured wall; gate **0.04s**, exit 0, all three real dependencies successful. |
| Schema checks | `python -m alembic heads/current/check`: single head/current **`6a94cbd3f825`**, no new upgrade operations, on serial/collection/both final shard instances. The original fresh/populated/legacy/empty/refused-downgrade migration tests all passed in `remaining`. |
| Frozen evaluation | `python -m evaluation.ra01 verify`: **VERIFIED**, unchanged `692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a`. |
| Dependencies | `python -m pip check`: **No broken requirements found**; unavailable pip cache was disabled. |

Local setup measurements (initdb, startup, database creation, copying and checking settings) were **2.04s** for serial, **1.58s** for collection, **1.25s** for W2 and **1.24s** for remaining. Migrations took **1.92s** serial, **1.92s** collection, **1.43s** W2 and **1.41s** remaining. Services and dependencies were provisioned before the continuous validation timer. Adding measured setup along the dependency graph gives an **accounted local elapsed estimate of 451.92s versus 869.33s**, **48.0% lower**, exceeding the local 25% target. The continuous validation timer itself was 447.17s; the accounted figure adds collection setup/migration and the slower concurrent shard setup. This distinction avoids presenting separately prepared services as a continuously timed hosted workflow.

Total accounted job time (sum across jobs, including those setup components) was **801.13 job-seconds versus 869.33**, **7.8% lower in this observation**. Python process-tree CPU was **638.43s** serial versus **327.42 + 244.61 = 572.03s** for the shard test processes; PostgreSQL background CPU is not included in those CPU figures. These are local resource proxies, not monetary billing measurements or a promised cost reduction. Three dependency installs replace one, adding approximately 14–16 installer job-seconds using the handoff's hosted timings; hosted checkout, service/image setup, setup-python/cache transfer, the extra gate runner and queueing remain unmeasured. One warm-host WSL comparison with 16 visible CPUs does not establish hosted p50/p95 or future scaling. Independent hosted PR and post-merge main validation/performance remain pending; no push or PR was created for this package.

Final checks validated every local documentation link/anchor and confirmed 461 protected application, migration, existing test, dependency and evaluation files byte-identical to the starting commit. The helper and its tests also match the copies used by both successful final shard runs. All four final database identities were independently rechecked with zero other clients and no operator encryption key, then stopped; all six task-owned validation clusters, including the two discarded preliminary instances, have no remaining postmaster PID file. Only task-owned services were stopped. Temporary logs, node lists and timing records remain under the owned root for review.
