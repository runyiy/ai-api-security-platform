# RA-05/W1 provider adapter — fake-only implementation

**IMPLEMENTED / PENDING_INDEPENDENT_REVIEW · 2026-09-11**

**Current correction:** independent review of `1a3e01c976c6606779abedf3071d3530a5667814` identified an authority-read timing gap despite its passing tests. The [P1 correction and validation record](#authority-read-timing-correction-p1) below supersedes its timing-coverage claim. Earlier results remain historical evidence, not acceptance of this correction.

Base: `386b08b2cca8f49a8ebeff14cc64e4a30d8b2037`; branch: `codex/ra-05-w1-provider-adapter`. Local HEAD/main, clean tree, origin `https://github.com/runyiy/ai-api-security-platform.git`, remote main and absence of the suggested branch were checked before branching. A read-only GitHub check also found no open PRs and confirmed main CI `34582003149` completed successfully at this base. No remote writes were performed.

The [adoption record](research-ai-provider-contract.md#w1-design-adoption-and-implementation-record) records the user's adoption of reviewed v0.1.0 / `29376dda7e0ffa99fe3f1947bb2c3e83df81c228`: P1–P6 and E1–E6 design constraints apply to **W1 fake-only implementation**. Actual account/key use, per-material egress/retention, operational budgets, paid calls and deployment remain unapproved. The synthetic receipt quantities are validation values, not spending approval. This record does not sign independent review, W1 acceptance or RA-05 completion, and does not start W2/W3.

## Implementation and trust boundaries

The independent [package](../backend/app/ai/proposals/__init__.py) has no route registration, database access, application settings discovery, Target authentication, execution, approval, Finding, policy mutation, subprocess or file-fetch capability. Existing `AIProvider.analyze`, `MockAIProvider`, Finding advice and routes remain byte-for-byte unchanged.

| Component | Implemented behavior |
| --- | --- |
| [Bounded codec](../backend/app/ai/proposals/codec.py) | Byte caps before decoding; depth and value-node checks during construction, including containers and excluding keys; duplicate keys, UTF-8/BOM/surrogates, non-finite numbers, malformed JSON and trailing material rejected with fixed codes. No coercion, truncation or repair. |
| [Proposal contract](../backend/app/ai/proposals/contract.py) | Exact input/output v1 fields, types, counts and byte limits; all three suggestion types, grounded refusal codes, per-call reference isolation and allowed candidate/rule combinations. Invalid entries reject the whole batch. Display consists of fixed templates, bounded opaque references and uncertainty enums; model prose never becomes facts or authority. Provider JSON Schema is only an additional shape constraint. |
| [Bindings and ports](../backend/app/ai/proposals/bindings.py) | Immutable local configuration, source/registry binding, current authorization snapshot and receipt types. Input digest binds exact payload bytes, registry, config/model/profile/usage/rate versions. Sources require independent synthetic classification, current eligibility, exact reviewed projection digest and seven distinct decision references. Context/project/source/decision/key versions are rechecked against a trusted current snapshot. These types are not wire APIs, persisted records or self-issued approval mechanisms. |
| [Responses adapter](../backend/app/ai/proposals/adapter.py) | Builds the fixed Terra/low/Responses request with no tools, fallback, streaming, storage, background operation or prompt cache. Independently projects final usage even for rejected output; missing/invalid/partial usage stays unknown/invalid. No reconciliation or budget refund is performed. Errors contain fixed codes only. |
| [Provider transport/auth boundary](../backend/app/ai/proposals/transport.py) | Fixed POST endpoint and exact Host, independent provider secret reference/version and bounded printable key bytes. One selected public address, one connect, verified TLS-context/hostname contract, peer check again at first write, no proxy selection/retry/redirect/compression. Raw HTTP headers, content-length/chunked framing and complete body are bounded independently. Absolute 30s, connect/TLS 3s and read-idle 5s limits include completed-operation checks and cancellation. |

`OpenAIProposalAdapter.propose_once(prepared=..., config=..., receipt=...)` returns a bounded `ProposalOutcome`: fixed error code or deterministic display, delivery (`not_sent`, `unknown`, `responded`), typed usage, and optional validated refusal enum. No raw response, reasoning, prompt, headers or secret is retained in that result. There is no success evidence, verdict, plan or approved action. A returned display is an immediate, transient view: later consumption must requalify current sources and configuration, not retain this result as authority.

Every default instance refuses. W1 transport accepts only explicitly injected concrete `MemoryResolver`, `MemoryConnector`/`MemoryWire` and `MemorySecret` dependencies; the HTTP framing, profile construction, qualification, callbacks, parsing and usage logic actually execute over those buffers. A live-mode transport refuses **before** secret/DNS/connect callbacks, and an arbitrary connector cannot label itself synthetic to use a fake receipt. There are no native DNS/socket/secret-file bindings or environment-based live enable switch. This is fake-only implementation evidence, not a claim that a real TLS connection, account policy or provider request succeeded.

`MemoryWire` represents TLS outcomes and scheduled I/O failures in tests. The transport supplies an actual certificate-verifying `SSLContext` and the fixed hostname, independently verifies peer identity and invokes the sending guard; fake certificate metadata is not real certificate evidence. Live I/O bindings and their deployment validation remain a separate live-enablement requirement, alongside the W2 gates below. No Target gateway or public execution flag was changed.

Exact reviewed projections and current independent qualification are the content allowlist. A small secondary recognizer also rejects obvious URL/email/token/instruction canaries; it is explicitly not a complete PII or prompt-injection detector. Review of independently authored synthetic material remains mandatory. Existing redaction cannot qualify Finding/TestRun/project evidence or its summary/hash for this protocol.

## Lifecycle, scheduling and W2 interfaces

The trusted `Authority.current(project, context, stage)` port must return current independently qualified state, not a stale snapshot supplied by a model or HTTP caller. The adapter checks it before input, after input construction, after admission/wait, after secret resolution/DNS/connect/TLS, within the sending critical section after the final peer check, before consumption/return and after observer work. It compares the full exact registry/configuration and independently checks half-open validity, source activity and clock order. Close/transfer, hold/delete/quarantine/revocation, publication/source versions, and configuration/key rotation invalidate the call. Post-send invalidation cannot erase already incurred usage.

The `Coordination` protocol is an obligation for later W2, **not a new ledger or scheduler implementation**:

- `admit(receipt, binding_digest, timeout)` independently verifies and consumes one exact receipt, owns shared concurrency/rate/call-count/budget enforcement and bounded waiting. W1 requires the matching synthetic account/configuration/usage/rate binding before invoking it.
- `sending(receipt, check)` serializes current source/config qualification and the observer's sending event with the first bounded write. DNS/TLS, admission waiting and response reading must occur outside its critical section. W1 exercises this ordering with injected scheduling hooks; no storage/locking architecture is selected here.
- `record(receipt, outcome)` receives only bounded terminal/usage information, with display and model refusal detail removed. `finish(receipt)` ends only the admission. Neither operation in W1 refunds tokens/cost or reconciles uncertain delivery. Observer/cleanup failure suppresses a successful display. Final source invalidation can suppress display after a usage observation; that observation remains valid as accounting input, not approval to consume the proposal.

The fake coordinator exists only in the tests. Its receipt-consumption set demonstrates no second send with the same call; it is not shared or durable coordination. No retrieval, escalation, reservation creation, account balance, reconciliation, crash recovery, cache, smoke or benchmark was added.

W1 maps OpenAI input totals, mutually exclusive cached/read-write input subsets, output totals including reasoning, and optional reasoning detail. `input + output` is the total; reasoning and cached counts are never added again. Counts reject bool/float/negative/overflow and inconsistent subsets/totals. Known over-cap usage is preserved rather than clamped. Nonzero cache in the adopted no-cache profile preserves known usage and refuses success. A proven pre-send failure reports known zero; a first-write attempt followed by failure/cancellation/timeout conservatively reports unknown. Missing usage is never zero. Refusal/malformed output with a valid final receipt retains known usage.

## Official profile check

Public official documentation was rechecked on **2026-09-11**, without sending repository or private material. The fixed model and low effort are listed on the [Terra model page](https://developers.openai.com/api/docs/models/gpt-5.6-terra). Explicit caching mode without breakpoints disables prompt-cache use, and cached/read-write counts are input subsets in the [prompt-caching documentation](https://developers.openai.com/api/docs/guides/prompt-caching). The [Responses reference](https://developers.openai.com/api/reference/resources/responses/methods/create) documents the adopted request fields and inclusive output limit; its Markdown representation was checked when the rendered reference failed to load. No observed discrepancy required changing the adopted model/profile. Documentation compatibility is not measured provider acceptance or performance.

## Validation and residual gates

All new fixtures are independently written synthetic inputs. No held-out/evaluator content was used to design tests. [W1 tests](../backend/tests/ai/test_proposal_adapter.py) exercise actual adapter and transport logic, with independent fail-on-call counters for sockets, DNS, subprocesses, Target gateway, Target credential resolution, execution, plan approval, Finding review, Scope/policy mutation and knowledge publication. Additional captured buffers and observer traces verify exact outgoing data and one-call behavior; `safe=true` is never an acceptance oracle.

| Adopted W1 acceptance area | Evidence |
| --- | --- |
| Valid types/refusal and legacy separation | All three types, four grounded refusal codes, four distinct suggestions, fixed display/profile assertions and unchanged legacy registration/protocol. |
| Encoding and limits | Exact and +1 input/proposal/request-envelope/response/header bytes; parser depth/nodes; candidate/rule/gap/suggestion counts; UTF-8 claim size; duplicate keys, BOM/surrogate/NaN/trailing material. Single-suggestion byte tests distinguish capacity from stricter enum validity; an independently passing cap never exempts semantic checks. |
| References, facts, data | Foreign/wrong-kind/cross-call/duplicate references, source aliases/versions/digests, invalid pairs, prohibited authority fields, whole-batch rejection, excluded source categories and injection/secret/PII canaries absent from capture/logs/results. |
| Permission/lifecycle | Disabled/missing permissions/receipt and fake-to-live rejection before I/O; lifecycle mutations at every applicable boundary, half-open source/context/authorization/receipt expiry, monotonic/wall-clock anomalies, config/key changes and observer-time changes. |
| Transport and failures | Destination/method/proxy overrides, DNS classes/8–9 count, peer rebinding/TLS mismatch, redirects/compression/header/length/chunk framing, slow drip/connect/idle/absolute deadlines, cancellation, refusal/incomplete/tools/multiple/unknown output, HTTP/usage errors and no retries. |
| Accounting handoff | Known final inclusive usage vs missing/invalid/unknown delivery, optional reasoning, preserved overage/cache-profile failure, observer failure and one-receipt replay denial. W2 ledger/atomic budget/reconciliation and W3 cache acceptance remain unimplemented. |

Validation uses environment-cleared, `.env*`-excluded backend copies and newly owned PostgreSQL 16.15 servers. Independent psql identity checks and `pg_controldata` agree on database/user/loopback host/port/data-directory/system identifier, UTF8, initially empty public schema and zero unrelated clients **before application imports/migrations/pytest**. Settings then assert no operator credential key. Current migrations are applied unchanged. Each shard is serial on its own PostgreSQL server; no default/shared/operator database is used.

README requires complete regression; a new provider security boundary also warrants it. Existing complete-suite synthetic loopback fixtures are confined to their owned test resources; no operator/deployed Target or provider is contacted. CI workflow, partitions and mandatory hosted gates are unchanged. The README's older M14 revision example is historical: the unchanged current migration chain upgrades to `6a94cbd3f825`.

Final validation artifacts are under the owned temporary root `/tmp/ra05-adapter-kj9tfjhk/`; they are local review evidence, not checked-in execution authority. Each source copy excludes `.env*`, and SHA256 comparisons match all seven new Python files to the working tree. All 468 pre-existing tracked backend/CI/review files remain unchanged from the exact base.

| Final check | Actual result |
| --- | --- |
| `python -m pytest tests/ai/test_proposal_adapter.py -q --tb=short` | **388 passed**, 5.33s. |
| `python -m pytest tests/ai tests/network_safety tests/auth tests/credentials -q --tb=short` | **557 passed**, 14.78s; two existing `TestIdentity` collection warnings. |
| `python -m ci_shards check --output collection.json` | **3654 total = 3266 baseline + 388 W1**, **117 w2 / 3537 remaining**, intersection **0**, complete union. Both runs must match ordered full-list SHA256 `89999e587b8e04c996ae17f52b22788775ea0bf56730c0bdeac9e472da6ba7e8`. |
| `python -m ci_shards run w2 --expected-sha256 <digest above>` | **117 passed**, 3537 assigned elsewhere/deselected, 62 existing warnings; pytest 422.15s, process wall 424.046s. This is the existing CI partition name, not RA-05/W2 implementation. |
| `python -m ci_shards run remaining --expected-sha256 <digest above>` | **3537 passed**, 117 assigned elsewhere/deselected, 62 existing warnings; pytest 581.66s, process wall 584.064s. Together the two serial shards passed all **3654** tests. |
| Aggregate / dependencies | Existing `ci_shards.py gate` exits **0** using the actual successful collection/shard results; `python -m pip check` passes. Hosted PR/main validation of this commit remains pending; no workflow was triggered. |
| Standard-library documentation checks | **106** local links/anchors across four changed documents; JSON examples, Decimal synthetic reservation/cost arithmetic, protected paths and whitespace pass. |

An earlier full run passed before the final profile/snapshot checks and observer minimization were added. The table records final-code validation; the earlier run is not substituted for it. No failed check was skipped, retried until green or weakened.

| Final owned validation server | Database/user | Loopback port | PostgreSQL system identifier |
| --- | --- | --- | --- |
| Focused/regression/collection | `ra05_test` | 45757 | `7684330284805050379` |
| Existing w2 shard | `ra05_w2` | 54163 | `7684330445962080435` |
| Existing remaining shard | `ra05_remaining` | 36971 | `7684330456435634386` |

Data directories are `<owned root>/<database>/data`. Final identity checks matched the independently verified initial identities and found zero unrelated clients. All three servers were stopped using their captured owned data directories, and their postmaster PID files are absent. Preliminary owned servers were also verified stopped; no unrelated resources were cleaned. Logs, collection lists, source hashes, public documentation snapshots and validation scripts remain in the owned roots for local review. `git diff --check` and the exact file-scope checks pass; no legacy application/test, migration, dependency, CI, review-rule or frozen evaluation file changed.

Live enablement remains blocked on independently reviewed native I/O/secret storage and account-specific policy, actual per-data retention/egress approval, operator budgets, proven total input-token upper bounds including provider overhead, W2 durable receipts/shared coordination/observer/reconciliation and their concurrency/lifecycle evidence. The output cap alone does not establish a complete spend cap. No operational decision is inferred from design adoption, fake receipts or passing local tests.

After local commit, stop for independent Review Project review. No push, PR, merge, branch cleanup or W2/W3 work is authorized by this record.

## Authority-read timing correction (P1)

This focused correction continues clean, unpushed branch `codex/ra-05-w1-provider-adapter` from reviewed HEAD `1a3e01c976c6606779abedf3071d3530a5667814`. Repository origin and actual remote main were reverified; main remains the task base `386b08b2cca8f49a8ebeff14cc64e4a30d8b2037`. Scope is the adapter boundary, appended synthetic regressions and this validation record. Protocol/profile, transport, lifecycle coordination, all existing tests, CI gates and operational restrictions remain unchanged.

Previously `_Boundary.check()` passed a wall time sampled before `Authority.current()` into interval validation. Time or cancellation consumed during the lookup could therefore allow an expired first write or final display/refusal. The corrected boundary checks time/cancellation before the lookup, performs **one** lookup, then samples fresh ordered monotonic/wall time and cancellation after it completes. It applies the absolute deadline and validates the returned snapshot's source, registry, authorization and receipt intervals using that completed-lookup time. It does not issue another authority lookup with another stale timestamp.

The same boundary path runs before input/secret access, at first write and at consumption/final return. The existing first-write coordination guard still encloses its final qualification and bounded write; no new lock/transaction spans network waiting. Expiry equality, deadline equality, clock rollback or cancellation suppress display and validated refusal detail. Pre-send rejection reports proven zero usage and performs no write; rejection immediately after sending preserves unknown delivery/usage; final rejection preserves already-known usage and its independent accounting observation.

**Deterministic failure detection:** 144 new parameter cases schedule changes *inside* `Authority.current()` at `before_input`, `before_secret`, `write_ready`, `after_send`, `before_consume` and `final_return`. They cover all four expiry scopes, an exact one-second expiry, a valid 0.5-second lookup, exact 30-second deadline, separate monotonic/wall rollback and cancellation, for both suggestions and protocol refusals. Assertions inspect actual memory secret/DNS/connect/write counters, lookup counts, display/refusal detail, delivery and usage. Existing fail-on-call guards still independently reject real sockets, Target calls and prohibited side effects. There are no sleeps, retries, skips or weakened assertions.

Before changing production code, the new cases ran against the reviewed adapter in a newly owned, independently verified PostgreSQL environment: **56 failed / 88 passed / 388 deselected**, the expected regression-detection result. The 48 within-window positive controls passed. That server was reverified and stopped. Artifacts remain under `/tmp/ra05-authority-red-l9aui91l/`; this deliberate failure is not counted as passing validation.

Corrected-code validation uses fresh environment-cleared `.env*`-excluded copies and newly owned PostgreSQL servers under `/tmp/ra05-authority-fix-2eplzue9/`, with the same independent identity/empty-schema/no-unrelated-client checks before application imports, migrations or pytest. Full regression is required for the corrected security boundary. Each complete shard remains serial on its own server; existing full-suite synthetic loopback fixtures stay confined to their owned resources. No real provider or operator/public Target is contacted.

| Correction check | Result |
| --- | --- |
| W1 module | **532 passed**, including all 388 existing cases and 144 additions; pytest 10.15s. |
| Affected AI/security regressions | `pytest tests/ai tests/network_safety tests/auth tests/credentials -q --tb=short`: **701 passed**, 27.41s; two existing collection warnings. |
| Collection/completeness | **3798 total = 3654 prior + 144 new**, **117 w2 / 3681 remaining**, intersection **0**. All prior node IDs remain in the same order. Ordered full-list SHA256: `8bd7162bd0f2e9be4928987f150a68d0455bf946fc615b526677d7280e9fbf84`; both execution shards must match it. |
| Existing w2 shard | **117 passed**, 3681 assigned elsewhere/deselected, 62 existing warnings; pytest **837.63s**, process wall **841.168s**. This partition name does not mean RA-05/W2 implementation. |
| Remaining local shard | **3681 passed**, 117 assigned elsewhere/deselected, 62 existing warnings; pytest **1252.42s**, process wall **1257.251s**. Together the shards pass all **3798** tests. |
| Aggregate/dependencies | The unchanged aggregate CLI exits **0** with the actual successful local collection/shard results; `pip check` passes. |
| Documentation/scope | **107** local links/anchors across the four related documents pass, as do JSON and whitespace checks. Only the adapter, appended tests and this record differ from the reviewed HEAD; the existing test file is an exact preserved prefix, and validated source hashes match all three copies. `git diff --check` passes. |
| Owned-resource cleanup | Final database identities were independently reverified with zero unrelated clients. All three final servers and the earlier regression-detection server are stopped, with absent postmaster PID files. Only owned resources were stopped; logs and identity/source/collection records are retained. |

Final database/user identities are `ra05_test` on loopback port **35257**, system identifier `7684338873715265548`; `ra05_w2` on **46589**, identifier `7684339170508308661`; and `ra05_remaining` on **51985**, identifier `7684339186036117716`. Data directories are `<correction root>/<database>/data`. The earlier regression-detection server was `ra05_test` on **54423**, identifier `7684338648645877772`, under its separately recorded owned root.

**Timing limitation:** the local remaining-shard process took approximately **21 minutes**, exceeding the hosted workflow's unchanged 20-minute job budget on this local machine. No test or workflow timeout was extended, no test was retried, and no cause is inferred from this single run. Passing local assertions and the aggregate CLI do not establish hosted timing/gate acceptance. Hosted PR/main validation remains pending; no push or workflow was triggered.

The correction remains **PENDING_INDEPENDENT_REVIEW**. Live enablement and all operational/W2 dependencies listed above remain pending. No provider/Target authorization, spending approval, W1 acceptance or W2/W3 implementation follows from this fix.
