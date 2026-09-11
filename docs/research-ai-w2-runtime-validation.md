# RA-05/W2 — N1 runtime prerequisite evidence

**v0.1.0 · IN_PROGRESS / PENDING_INDEPENDENT_REVIEW. W2 implementation is incomplete.**

Base: `cff95ddc296136ca54efbf92a5c833d65c2e26c1`; branch: `codex/ra-05-w2-budget-coordination`. Clean HEAD/local main, actual remote main and origin were rechecked before continuing. There was no remote feature branch. This increment implements test-only sandbox/database prerequisites; it does not implement preparation, reservation/accounting, W1 coordination, the permit authority/journal, lifecycle writer integration or recovery.

## Adoption and scope

The User handoffs record independent review of [implementation contract v0.1.1](research-ai-w2-implementation-contract.md) at `302b1e581f4e0e559a0f16f924d1ca64ea985adb`, integration through PR #152 and first-attempt PR/exact-main gates passing 3798 tests. Git confirms this contract's base contents match the reviewed commit. These hosted runs were not rerun or independently audited here.

The User subsequently replied **“采纳”** to N1 v0.1.1, including deployment-wide acceptance closure and explicit recovery before reopening. The later continuation handoff records **“采纳”** for mandatory Linux/bubblewrap isolation, subject to all four conditions:

1. Required isolation must fail when unavailable, with no skip, ordinary-subprocess fallback or in-process substitute.
2. Validate the complete sandbox configuration with adversarial ownership/control-boundary tests.
3. Database authentication/privileges must prevent administrative identity switching and server-side filesystem/program bypasses.
4. Demonstrate compatible database access in hosted CI without sharing the host network or weakening its gates.

No observation/message timestamp, signature, identity or approval ID is supplied for these subsequent decisions, and none is inferred. [B1–B8 adoption](research-ai-budget-contract.md#w2-design-adoption-record) remains effective. Historical pending text describes earlier states; it does not reopen these decisions. Design adoption supplies no operational account/key, private-data/retention/egress, spending, deployment, real-provider or Target approval.

The temporary earlier proposal and same-UID probe remain at `/tmp/ra05-w2-ownership-probe-exlu4ip8/runtime-choice.md` and adjacent `result.json`. The earlier child successfully changed a parent-created 0400 file to writable and replaced its synthetic content. That historical failure motivated the adopted OS boundary; a same-UID subprocess/file-mode arrangement is not an acceptable fallback.

## Implemented prerequisite and its limits

[Sandbox helper](../backend/tests/ai/n1_sandbox.py) executes a fresh Python interpreter through bubblewrap with explicit user/PID/network/IPC/UTS isolation and a new mount tree/session. It drops capabilities, disables further user namespaces, clears ambient configuration, mounts the interpreter/stdlib/dependencies and supplied code read-only, and supplies an empty writable `/tmp`. Only the owned database-socket and call-socket directories are visible; parent journal/witness/control directories and home/configuration trees are absent. It rejects code projections containing `.env*`, `.git`, `.codex`, symlinks or excess files/bytes. Descriptor inheritance is closed; stdout/stderr are bounded jointly and failures return fixed codes without raw child error text. Timeouts kill/reap the owned child; no fallback occurs.

The fixed `LD_LIBRARY_PATH=/runtime/native` binds the interpreter's declared library directory read-only. This supports the layout needed by shared Python installations outside `/usr`; it is not a claim that the actual hosted setup-python interpreter has been tested. No ambient `LD_*`, provider settings or administrative credentials enter the child.

[Owned PostgreSQL helper](../backend/tests/ai/n1_postgres.py) creates a separate native PostgreSQL 16 instance for the sandbox test. It never starts an installed/default service. Independent `psql` and `pg_controldata` checks establish database, user, loopback host, port, data directory, system identifier, UTF8, an empty schema and no other clients before schema work. Migration authority uses a generated password over owned loopback TCP outside the child. Unix authentication accepts only the restricted adapter identity/database with SCRAM, then rejects every other identity. Host authentication accepts only the migration role; the isolated child has no host-network path.

The restricted role has no superuser, role/database creation, replication, RLS bypass or inherited role privileges. PUBLIC database privileges and schema creation are revoked; the positive probe receives only SELECT/UPDATE on its fixture table. A separate parent-side test attempts Unix login with the **correct** migration password and verifies HBA rejection. That password is never supplied to the child. Grants for future actual W2 tables/functions remain to be implemented and reviewed.

[Adversarial tests](../backend/tests/ai/test_n1_isolation.py) check namespace separation, zero capabilities/no-new-privileges, nested-user-namespace denial, parent journal/witness read/write attempts (including `/proc/1/root`), parent-process root visibility, an intentionally inheritable journal descriptor, hidden control-socket access, read-only code, empty temporary storage and absent host loopback connectivity. Numeric PIDs can coincide in different namespaces; the assertion checks namespace/root identity rather than treating a matching number as parent access.

The child successfully updates one synthetic database counter and exchanges a bounded message on its explicitly mounted call socket. It fails twelve privileged SQL operations, three privileged/unknown reconnections and four unauthorized probe messages. The parent independently checks retained journal/witness contents, database effects, endpoint messages and absent control connections. These messages exercise a **probe endpoint**, not the adopted permit issuer or an actual W2 acceptance boundary. They do not prove immutable permit bindings, journal fsync/ack ordering, accounting conservation or guard-loss fencing.

## Hosted gate and remaining work

The current [workflow](../.github/workflows/backend-tests.yml) exposes its PostgreSQL service only through TCP. The local arrangement above uses an additional, fixture-owned native PostgreSQL instance with its own Unix socket; it does not forward the CI service or share the host network. The official [Ubuntu 24.04 runner inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md) lists PostgreSQL 16.15, but that inventory is not runtime evidence for this test, namespace permissions or bubblewrap availability. Public source checked 2026-09-11; no repository/private material was transmitted.

**Required hosted evidence is pending.** The workflow runs for main pushes and PRs; there is no dispatch trigger. The authorized work remains local with no push/PR, so this session cannot execute the new prerequisite on a hosted runner. No CI configuration defect or minimum configuration change has yet been demonstrated. If the required native tools or full isolation are unavailable there, the test must fail and the concrete minimum runtime change must be reviewed; do not install a fallback, share host networking or disable host security controls.

W2 remains incomplete pending that evidence and the original implementation work: all strict records/interfaces, Q1–Q4 preparation and genuine-publication retrieval, persistent reservations/events/settlement, W1 wrappers, independent permit/journal/witness authority, full writer inventory and explicit recovery. T1–T12, X1–X8 and Y1–Y5 (including both Y3 histories) are **not established** by this increment. In particular, no real PostgreSQL G-session-loss/invalidator/stale-sender schedule or process-crash accounting schedule has been implemented or validated here. No additional adoption of B1–B8/N1 is requested.

## Local validation

The earlier migration failed with connection refused after its startup command ended; the startup log has no shutdown record or exit signal. Current diagnostics show that tool commands have separate PID namespaces. Keeping the new server owned by a persistent tool session resolves cross-command availability: a separate command recorded a different PID namespace, the same network namespace and successful migration to unchanged head `6a94cbd3f825`. The server remains available across subsequent validation commands. All application imports/tests use environment-cleared, `.env*`-excluded copies. No default/shared/operator database is substituted.

Owned primary validation root: `/tmp/ra05-w2-owned-jeqf7j8v`; database/user `ra05_w2_admin`, port **41921**, data directory `<root>/data`, system identifier **7684402204251807756**. The separately owned full `w2` shard root is `/tmp/ra05-w2-full-shard-f1lulx2q`; database `n1_synthetic`, user `n1_migration`, port **56157**, system identifier **7684405584356962316**. Sandbox tests create and stop their own additional, independently verified instances serially inside the assigned shard.

Development found two incorrect new assertions: numerical PID inequality across namespaces, and expecting only EPERM when disabled user namespaces can fail with ENOSPC. Both were corrected to test the intended isolation property. Neither a production assertion nor an existing test was weakened; the failing runs were not counted as acceptance.

| Check | Actual result |
| --- | --- |
| Targeted AI/security | `pytest tests/ai tests/network_safety tests/auth tests/credentials -q --tb=short`: **711 passed**, 22.75s, two existing collection warnings; includes all 10 new prerequisite cases. |
| Collection/completeness | **3808 total = 3798 previous + 10 new; 117 w2 / 3691 remaining; intersection 0**. Ordered full-list SHA256 `a87a414bf30f5d3f1f81c9d958c82a83404809de66f83bc8c868df4eb7277205`. |
| Full local regression | **117 w2 passed**, 457.89s pytest / 460.73s migration-plus-process wall; **3691 remaining passed**, 639.51s pytest / 641.56s process wall. Both have 62 existing warnings. All **3808** tests passed on the pre-follow-up helper snapshot; see final targeted validation below. Partitions, serial execution, separate servers and digest checks are unchanged. |
| Final targeted follow-up | **10 N1 tests passed**, 3.82s. After the full run, review corrected identical-object mount-overlap detection and added completed-exit deadline checking/fixed timeout errors, including child EOF without exit. These test-helper-only changes were validated by rerunning the affected module; the entire suite was not repeated. Final collection has identical ordered nodes/digest. |
| Migration/dependencies/aggregate | Fresh upgrades reach unchanged `6a94cbd3f825`; all existing migration tests passed in the full suite, `alembic check` reports no new operations, `pip check` passes, and the existing aggregate CLI returns **0** using actual successful local collection/shard results. |
| Documentation/scope | Standard-library checks pass **141 local links/anchors across five documents**, preserve historical JSON blocks exactly and account for all 3798 unchanged nodes plus 10 new nodes. `git diff --check` passes. Full-run and final-targeted source hashes are retained separately in `validated-source-hashes.json`; no single final-tree full-run claim is made. |
| Cleanup | Primary and w2-shard identities were reverified with zero other clients before stopping only their owned servers; postmaster PID files are absent. Nested sandbox fixtures verify their own identities and stopped state. The final identity checker was corrected to use `host(inet_server_addr())`, avoiding PostgreSQL text-cast `/32` formatting; no server identity was substituted. |
| Scope | Only new test helpers/tests and narrow adoption/evidence documentation. No application, migration, dependency, CI, evaluation or Review-rule edits. |

Logs, complete node lists, final identities and separate source-snapshot hashes remain in the two owned roots above. No application/CI/migration/evaluation/Review file changed. No push or hosted workflow was triggered.

These checks establish local prerequisite evidence, not W2 completion, hosted acceptance or live-provider readiness. Input-token overhead bounds, account billing/retention, actual budgets and all live operational gates remain unresolved and disabled. No provider/Target/private-data authorization follows from this record.
