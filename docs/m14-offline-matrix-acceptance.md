# M14 offline matrix acceptance and compatibility

This records local acceptance evidence for the **offline planning** candidate on
`feat/m14-06-offline-matrix-acceptance`, based on
`4988d792a97a264c26e3188aa21e08c2be0a03cb`. It adds tests and documentation only.
It is not an M14 COMPLETE declaration: Tech Lead remote review, exact-head CI,
and exact-main CI after merge remain required. No future CI result is implied.

The [acceptance module](../backend/tests/integration/test_m14_matrix_acceptance.py)
exercises the real persisted PostgreSQL and in-process HTTP path:

```text
POST /api/bola-matrix/preview
 -> existing M14-04 composer
 -> existing M14-03 binding selector and M14-02 Resource preview
 -> existing M12 resolver and M14-01 planner
 -> actual M14-05 typed JSON serialization
```

No component in that chain is replaced with a canned result. Delegating spies
observe calls and immutable in-call reuse. Fail-fast guards surround every
preview, including failures, and permit only the in-process client and test
PostgreSQL. No live Target server, documentation retrieval, DNS, credentials,
executor, or AI is needed.

## Acceptance scenarios

The following exact tests live in the acceptance module:

- **Truth/positions:** `test_independent_truth_nested_positions_and_real_pipeline`
  runs in both `private_local` and `external_public_authorized` metadata modes.
  Two Resources cover all eight owner/non_owner/shared/anonymous × allowed/denied
  combinations. Same-role identities have different explicit access. A
  privileged-looking admin/owner identity, also the legacy Resource owner, has
  no eligible assertion and receives no candidate. Its confidence-100 candidate
  and rejected assertions grant nothing. Exact facts, candidate kinds, supporting
  IDs, ordered bindings, Resources, identities, and repeated-Resource reuse are
  asserted independently of a second implementation call.
- **History:** `test_history_complementary_dimensions_and_uncertainty` evaluates
  four explicit instants around a fixed boundary. Complementary verified
  relationship/access dimensions combine; conflicting candidate/rejected rows
  are ignored. A future-asserted, backdated denial is excluded earlier, then
  produces conflict alongside the older low-confidence allowance. It does not
  win by recency or confidence. Inclusive asserted_at/valid_from and exclusive
  valid_until boundaries, insufficient facts, and unspecified access or
  relationship produce the explicitly asserted facts and candidate omissions.
- **Fresh failures:** `test_fresh_metadata_and_late_failures_are_request_wide`
  first succeeds, then observes fixture binding rejection, identity deactivation,
  a late missing Resource, a late wrong-Target Resource, or a duplicate semantic
  slot. It asserts the exact 404/409 code and an error-only response.
- **Late resolver limit:** `test_late_257_assertions_return_no_partial_matrix`
  completes the first Resource's real planner before the second Resource hits
  257 eligible assertions. The whole response is 409
  `resource_access_resolution_limit_exceeded`, without earlier slots.
- **Bounds/input:** `test_exact_512_cells_and_sanitized_rejections_before_sql`
  accepts two distinct path/query positions for the same Resource and 256
  unasserted identities: 512 facts and zero candidates. Adding one identity
  rejects all 514 cells before SQL, without deduplicating Resources or ignoring
  skipped facts. A credential-like extra field yields the fixed sanitized 422.

Every request compares all mapped database tables and application configuration
immediately before/after. Snapshots include nonempty assertions, all M13 evidence
layers, TestCases, ExecutionPlans, PlanActions, TestRuns, authorization profiles,
revisions, and scopes. Fixture factories populate these before the guard begins;
the preview cannot write, flush, commit, roll back business state, or take row
locks. Normal request-dependency Session cleanup remains allowed. Response field
allowlists, absence of raw/sensitive execution values, and `no-store` are checked.

## Capability and regression evidence

The scenario names below refer to the exact tests listed above. Earlier tests
remain unchanged, including the deterministic subprocess repair.

| Capability | Current guarantee | Acceptance | Existing regression evidence |
| --- | --- | --- | --- |
| Owner | Owner may explicitly be allowed or denied. | Truth/positions | [M14-01](../backend/tests/generators/test_bola_matrix.py): `test_authenticated_preserves_independent_relationship_and_access` |
| Cross-subject | non_owner may explicitly be denied or allowed. | Truth/positions | [M14-02](../backend/tests/services/test_bola_matrix_preview.py): `test_real_independent_access_truth_and_unspecified_skips` |
| Anonymous | Exact anonymous auth metadata selects anonymous candidates, preserving access truth. | Truth/positions, History | [M14-01](../backend/tests/generators/test_bola_matrix.py): `test_only_exact_anonymous_auth_type_overrides_candidate_kind` |
| Role/shared access | Shared is assertion-backed; role/name labels supply no access truth or role-policy engine. | Truth/positions | [M14-05 HTTP](../backend/tests/api/test_bola_matrix_preview_integration.py): `test_nested_mixed_http_preserves_independent_access_and_provenance` |
| Nested positions | Exact project/task positions link to proposed Resources; membership is unproven. | Truth/positions | [M14-03](../backend/tests/services/test_bola_binding_selection.py): `test_exact_path_names_and_multiple_distinct_slots` |
| Query binding | Same name in path and query denotes distinct positions. | Truth/positions, Bounds/input | [M14-03](../backend/tests/services/test_bola_binding_selection.py): `test_query_requires_exact_name_and_location_not_path_text` |
| Multiple assignments | Order, explicit Resource linkage and immutable in-call reuse are preserved. | Truth/positions, Fresh failures | [M14-04](../backend/tests/services/test_bola_binding_matrix_preview.py): `test_real_nested_path_mixed_query_independent_access_and_exact_calls` |
| Historical truth/conflict | Explicit assertion time, complementary dimensions, uncertainty and provenance survive the full chain. | History | [M12](../backend/tests/api/test_resource_access_resolution.py): `test_independent_merge_duplicates_and_deterministic_support`, `test_time_boundaries_are_asserted_aware_and_half_open` |
| Fresh metadata/error scope | Rejection/deactivation is noticed; late failures have no partial preview. | Fresh failures, Late resolver limit | [M14-05 HTTP](../backend/tests/api/test_bola_matrix_preview_integration.py): `test_http_has_no_cross_request_cache`, `test_full_256_provenance_preserved_then_late_257_fails` |
| Bounds/transport | Requested cells include repeated Resources and skipped facts; transport errors are sanitized. | Bounds/input | [M14-05 transport](../backend/tests/api/test_bola_matrix_preview.py): `test_count_and_aggregate_bounds_with_repeated_resources_and_skipped_facts` (32/33, 512/513), `test_actual_request_limit_before_parser_and_no_further_read`, `test_exact_body_limit_missing_length_and_single_read` (64 KiB), `test_real_four_mib_serialized_boundary` (4 MiB) |
| No mutation/network/credentials/AI | Preview success and failure leave persisted state/configuration unchanged and cannot cross guarded boundaries. | All scenarios, both metadata modes | [M14-05 HTTP](../backend/tests/api/test_bola_matrix_preview_integration.py): `test_http_is_read_only_with_evidence_and_zero_forbidden_activity`, `test_unclean_request_session_is_not_flushed_or_discarded` |

## Compatibility and remaining limits

- `relationship != expected_access`. Role labels neither grant nor deny access.
- A confirmed position is not Resource-to-slot approval. Nested positions do
  not prove parent-child membership, and unselected positions stay unfilled.
- A candidate is not a complete rendered request, persisted TestCase,
  ExecutionPlan, or permission to connect. New matrix shapes are not
  automatically supported by downstream execution.
- The preview is transient current metadata. `evaluation_time` governs
  assertions, not historical identity activity or binding review state.
- Legacy `POST /api/test-cases/generate/bola` remains a separate transitional
  owner-based path. It does not consume this preview automatically. Its
  idempotency/concurrency evidence remains in
  [test_test_case_generation_concurrency.py](../backend/tests/api/test_test_case_generation_concurrency.py).
- Confirmed body bindings remain unsupported by M14 planning selection:
  [`test_confirmed_body_never_reads_declaration_or_evaluates_pointer`](../backend/tests/services/test_bola_binding_selection.py).
- Previewing external/public-authorized metadata changes no runtime controls.
  Actual public execution rejection remains covered by
  [`test_external_public_mode_remains_blocked_before_gateway`](../backend/tests/services/test_plan_execution_integration.py).
  Public SRC Readiness Gates B/C and their controls/exercises remain separate.
- Product consoles, AI advisory expansion, reporting maturity, and
  retention/deletion management remain separate work. The
  [roadmap](level3-roadmap.md), [architecture](architecture-decisions.md), and
  [security invariants](security-model.md) are not reduced by this acceptance.

## Reproducible isolated local run

Use Python 3.12, the dependencies in `backend/requirements-dev.txt`, and
PostgreSQL 16, matching the [backend CI setup](../.github/workflows/backend-tests.yml).
The database role must own the disposable database and be able to apply DDL and
create test schemas. Run serially with exclusive use of that database.

The repository does **not** automatically redirect pytest to a test database.
`app/core/config.py` reads `DATABASE_URL` and falls back to `backend/.env`;
the engine is created at import time. Export the test URL **before** starting
Alembic or pytest. The checked-in `backend/docker-compose.yml` uses the
persistent `security_platform` database on port 5432 and a named volume; its
presence does not make that database disposable. Never point these commands at
a shared/default/operator database. Existing regression migration tests perform
downgrade/upgrade operations, and many fixtures commit their synthetic setup.

One isolation option is a new disposable container with no shared/named volume.
The following credentials are public **synthetic test credentials only**.
Use an unused loopback port; a collision should be resolved by choosing another
test port, not by stopping an existing service.

```bash
# From backend/, after installing requirements-dev.txt in .venv:
source .venv/bin/activate
m14_test_container=$(docker run --detach --rm \
  --publish 127.0.0.1:55436:5432 \
  --env POSTGRES_USER=matrix_test \
  --env POSTGRES_PASSWORD=matrix_test \
  --env POSTGRES_DB=m14_matrix_test \
  postgres:16)
export DATABASE_URL='postgresql+psycopg://matrix_test:matrix_test@127.0.0.1:55436/m14_matrix_test'
docker exec "$m14_test_container" pg_isready -U matrix_test -d m14_matrix_test
```

Proceed when pg_isready reports accepting connections. An independently
provisioned, fresh native PostgreSQL 16 test instance/database is equivalent.
No running FastAPI server or live Target is required:

```bash
alembic current
alembic heads
alembic upgrade head
pytest tests/integration/test_m14_matrix_acceptance.py
pytest tests/integration/test_m14_matrix_acceptance.py
pytest
pip check
git diff --check
```

On a new empty database, `alembic current` initially has no revision; after
upgrade, current/head must be `b5d7f9a1c3e6`. The focused suite has ten cases.
Both consecutive runs must pass; this checks cleanup/repeatability, not
retry-to-green. On failure, inspect the exact failing expectation; do not
skip, weaken it, or change production under this tests/docs issue.

Expected categories are 200 with exact resolved candidates, 200 with retained
conflict/insufficient/unspecified facts and omitted candidates, and request-wide
404/409/422 with a fixed or mapped detail code and no slots. Uncertainty in
otherwise valid data is not a transport failure or guessed denial. Every
preview uses `Cache-Control: no-store`; POST contacts only the platform.
Each slot identifies its confirmed position and proposed Resource. Its facts
retain one resolution per selected identity, with relationship and access as
independent dimensions. Candidates contain only the planner's eligible subset;
supporting assertion IDs identify the exact eligible evidence behind the facts.
The existing [localhost curl example](bola-matrix-preview-api.md) explains how
an operator selects existing metadata IDs; this suite does not seed a deployment.

The focused module owns synthetic rows through the existing approved-plan and
evidence factories plus a nested matrix fixture. Its teardown removes only
assertions/bindings belonging to those fixture graphs, then the owning factories
remove their own rows. IDs may advance between runs; expectations use captured
IDs and fixed assertion/evaluation instants, with no sleeps. Full regressions
also own any ephemeral loopback lab servers and subprocesses they require.
Neither command needs third-party Target traffic.

If you created the disposable container above, stop **that captured container
only** after testing; `--rm` removes its own disposable storage:

```bash
docker stop "$m14_test_container"
```

Do not substitute a shared container/database or use blanket SQL cleanup.
Generated test logs, DSNs, credential material and raw data are not committed.

## Candidate validation record

The candidate's local validation used Python 3.12.3 and a fresh, exclusively
owned `m14_matrix_acceptance` database on a temporary native PostgreSQL 16
instance, separate from the default database. After initial database bootstrap,
the prescribed commands ran in order without a failed run or automatic retry:

| Command | Actual result |
| --- | --- |
| `alembic current` | `b5d7f9a1c3e6 (head)` |
| `alembic heads` | `b5d7f9a1c3e6 (head)` |
| `alembic upgrade head` | Passed; no new migration |
| `pytest tests/integration/test_m14_matrix_acceptance.py` | 10 passed, 1 warning, 3.54 s |
| Same focused command, consecutive second run | 10 passed, 1 warning, 3.58 s |
| `pytest` | 2010 passed, 55 warnings, 105.36 s |
| `pip check` | No broken requirements found |
| `git diff --check` | Passed |

Warnings concern existing TestClient deprecation and pytest class
collection. No production defect was found by acceptance. Existing production,
migration and historical test files remain unchanged. Remote review and both
CI gates remain pending for this candidate.
