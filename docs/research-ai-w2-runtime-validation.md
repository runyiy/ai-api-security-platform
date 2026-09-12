# RA-05/W2 — N1 runtime prerequisite evidence

**INTEGRATED / RUNTIME PREREQUISITE VERIFIED. Broader RA-05/W2 remains INCOMPLETE.** [PR #153](https://github.com/runyiy/ai-api-security-platform/pull/153) integrated reviewed feature `f5ac48e2a9b4f6ca3c85cd7923f930e1ee5b04b3` at exact main `eb69379910b99cf312cbc9b08f9a3c4b72088c9d`. This record establishes the reviewed synthetic runtime increment, not production readiness, complete W2 or operational permission.

## Adoption and scope

[B1–B8](research-ai-budget-contract.md#w2-design-adoption-record) and [concrete implementation contract v0.1.1](research-ai-w2-implementation-contract.md) retain their design and acceptance authority. The latter was reviewed at `302b1e581f4e0e559a0f16f924d1ca64ea985adb` and integrated through [PR #152](https://github.com/runyiy/ai-api-security-platform/pull/152).

The User subsequently replied **“采纳”** to N1 v0.1.1, including deployment-wide acceptance closure and explicit recovery before reopening. The later continuation handoff records **“采纳”** for mandatory Linux/bubblewrap isolation, subject to all four conditions:

1. Required isolation must fail when unavailable, with no skip, ordinary-subprocess fallback or in-process substitute.
2. Validate the complete sandbox configuration with adversarial ownership/control-boundary tests.
3. Database authentication/privileges must prevent administrative identity switching and server-side filesystem/program bypasses.
4. Demonstrate compatible database access in hosted CI without sharing the host network or weakening its gates.

No observation/message timestamp, signature, identity or approval ID is supplied for these subsequent decisions, and none is inferred. [B1–B8 adoption](research-ai-budget-contract.md#w2-design-adoption-record) remains effective. Design adoption supplies no operational account/key, private-data/retention/egress, spending, deployment, real-provider or Target approval.

The subsequent **“Adopted CI-only bwrap compatibility policy”** User handoff explicitly adopts the exact Ubuntu `bwrap-userns-restrict` profile and authorizes implementation without repeat approval. [The bounded adoption and implementation record below](#adopted-ci-only-apparmor-compatibility-policy) records its package/hash, job-wide executable scope and verified hosted integration. This is an adopted CI prerequisite, not W2 completion or operational authority.

The earlier same-UID probe showed a child could chmod/replace parent-created 0400 synthetic content. It motivated the adopted OS boundary; same-UID subprocess/file modes are not an acceptable isolation fallback. Historical probe details remain in the [integrated development record](https://github.com/runyiy/ai-api-security-platform/blob/f5ac48e2a9b4f6ca3c85cd7923f930e1ee5b04b3/docs/research-ai-w2-runtime-validation.md); temporary directories are not prerequisites for understanding or rerunning current validation.

## Implemented prerequisite and its limits

[Sandbox helper](../backend/tests/ai/n1_sandbox.py) executes a fresh Python interpreter through bubblewrap with explicit user/PID/network/IPC/UTS isolation and a new mount tree/session. It drops capabilities, disables further user namespaces, clears ambient configuration, mounts the interpreter/stdlib/dependencies and supplied code read-only, and supplies an empty writable `/tmp`. Only the owned database-socket and call-socket directories are visible; parent journal/witness/control directories and home/configuration trees are absent. It rejects code projections containing `.env*`, `.git`, `.codex`, symlinks or excess files/bytes. Descriptor inheritance is closed; stdout/stderr are bounded jointly and failures return fixed codes without raw child error text. Timeouts kill/reap the owned child; no fallback occurs.

The fixed `LD_LIBRARY_PATH=/runtime/native` binds the interpreter's declared library directory read-only. This supports shared Python installations outside `/usr` and was exercised by the successful hosted runs. No ambient `LD_*`, provider settings or administrative credentials enter the child.

[Owned PostgreSQL helper](../backend/tests/ai/n1_postgres.py) creates a separate native PostgreSQL 16 instance for the sandbox test. It never starts an installed/default service. Independent `psql` and `pg_controldata` checks establish database, user, loopback host, port, data directory, system identifier, UTF8, an empty schema and no other clients before schema work. Migration authority uses a generated password over owned loopback TCP outside the child. Unix authentication accepts only the restricted adapter identity/database with SCRAM, then rejects every other identity. Host authentication accepts only the migration role; the isolated child has no host-network path.

The restricted role has no superuser, role/database creation, replication, RLS bypass or inherited role privileges. PUBLIC database privileges and schema creation are revoked; the positive probe receives only SELECT/UPDATE on its fixture table. A separate parent-side test attempts Unix login with the **correct** migration password and verifies HBA rejection. That password is never supplied to the child. Grants for future actual W2 tables/functions remain to be implemented and reviewed.

[Adversarial tests](../backend/tests/ai/test_n1_isolation.py) check namespace separation, zero capabilities/no-new-privileges, nested-user-namespace denial, parent journal/witness read/write attempts (including `/proc/1/root`), parent-process root visibility, an intentionally inheritable journal descriptor, hidden control-socket access, read-only code, empty temporary storage and absent host loopback connectivity. Numeric PIDs can coincide in different namespaces; the assertion checks namespace/root identity rather than treating a matching number as parent access.

The child successfully updates one synthetic database counter and exchanges a bounded message on its explicitly mounted call socket. It fails twelve privileged SQL operations, three privileged/unknown reconnections and four unauthorized probe messages. The parent independently checks retained journal/witness contents, database effects, endpoint messages and absent control connections. These messages exercise a **probe endpoint**, not the adopted permit issuer or an actual W2 acceptance boundary. They do not prove immutable permit bindings, journal fsync/ack ordering, accounting conservation or guard-loss fencing.

## P2 initialization cleanup correction

The integrated [PostgreSQL cleanup helper/tests](../backend/tests/ai/test_n1_postgres.py) cover startup acknowledgement loss and failures before database creation. Ownership is pinned after initdb by directory device/inode and cluster system identifier; cleanup verifies the maintenance endpoint, exact process/data-directory identity and pidfd before signalling only that owned server. Bounded process exit, absent PID file and durable cluster shutdown must agree. These checks remain active under optimized Python. Unverifiable cleanup preserves the original error and reports `N1_OWNED_DATABASE_CLEANUP_UNRESOLVED`, without exposing credentials or claiming successful shutdown. Nine cleanup cases complement ten isolation cases; no default/shared service is stopped.

## Adopted CI-only AppArmor compatibility policy

The User's explicit adoption applies to **all `/usr/bin/bwrap` executions on the existing temporary hosted `remaining` job's runner**, not just the capability probe. It authorizes the exact Ubuntu `apparmor-profiles` **`4.0.1really4.0.1-0ubuntu0.24.04.7`** member `/usr/share/apparmor/extra-profiles/bwrap-userns-restrict`, profile SHA256 **`11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9`**, package SHA256 **`bdac5b74d884643653565c52ed7483c9582e646ff72cce8d95d0eb8467a3139c`**. The declared source ABI 4.0/offline feature target is distinct from the exported binary wire ABI. No broader runtime-policy authority or approval identifier is inferred.

### Focused implementation

[The policy helper](../.github/scripts/n1_apparmor_policy.py) uses isolated authenticated Ubuntu APT metadata, lists/cache and configuration for only the pinned artifact. It rechecks version, Package/Architecture, package/member/profile identity, extracts only the approved profile and installs no package scripts or unrelated profiles. Runner APT configuration is unchanged. Loading is restricted to root in a GitHub-hosted Ubuntu 24.04 job; the child never runs under sudo. Existing names, potentially matching attachments (including `/bin/bwrap`), optional local overrides, unknown evidence or inventory drift block loading, with no profile-name whitelist or replacement.

The observed global baseline is preserved exactly: AppArmor enabled **Y**, `apparmor_restrict_unprivileged_userns=1`, `apparmor_restrict_unprivileged_unconfined=0`. No sysctl is changed. The approved enforcing child restriction/capability denial is retained byte-for-byte. The actual capability probe must report **`bwrap//&unpriv_bwrap (enforce)`** and satisfy every namespace/capability/no-new-privileges/nested-userns/read-only/database isolation assertion. Post-test read-only policy/control verification runs whenever loading succeeded, including when the partition failed, and can fail the aggregate.

Preparation/load/verification/probe commands have bounded step and subprocess deadlines. The workflow retains independent PostgreSQL instances, serial tests within each shard, complete/disjoint collections and digest checks, four jobs and fail-closed aggregation. Missing tools or interfaces and further unsupported policy representations fail; they do not broaden this adoption.

## AppArmor loaded-policy identity correction

### Identity chain and fail-closed boundaries

[The guard](../.github/scripts/n1_apparmor_policy.py) uses the kernel's per-profile `raw_data`, `raw_sha256`, `raw_abi` links and `sha256` entry. It verifies that these links identify the same kernel raw-data directory, verifies the complete exported blob's hash and ABI, parses every serialized profile, then binds each current named kernel entry to its exact segment hash, exported attachment and mode. A multi-profile blob can retain segments which are no longer current; a segment alone never establishes that it is loaded. No existing source file or local recompile is substituted for loaded-policy evidence.

[The bounded binary reader](../.github/scripts/n1_policy_binary.py) follows the kernel wire definitions and computes each profile hash over the little-endian version plus the exact serialized profile segment. It interprets the compiled attachment DFA for `/usr/bin/bwrap` and `/bin/bwrap`; any packed accepting permission is conservatively treated as a possible conflict regardless of xattrs or attachment specificity. `<unknown>` remains unresolved until that evidence is verified. All unknown entries, including unrelated applications, are resolved/reported together in each bounded inventory pass. Reserved names, actual attachment matches, optional local overrides, unavailable evidence and mismatches remain blockers. An executable's unconfined label is not used to clear attachment conflicts.

The kernel interface behavior is documented in its [AppArmor filesystem implementation](https://github.com/torvalds/linux/blob/v6.8/security/apparmor/apparmorfs.c), [policy unpacker](https://github.com/torvalds/linux/blob/v6.8/security/apparmor/policy_unpack.c), [profile hashing](https://github.com/torvalds/linux/blob/v6.8/security/apparmor/crypto.c) and [DFA matching](https://github.com/torvalds/linux/blob/v6.8/security/apparmor/match.c). Raw-data reads expose the decompressed original load bytes. The exported raw ABI describes the last header of a complete blob; per-profile hashes retain their own full version. Namespace revisions are read once with nonblocking I/O because that interface is pollable rather than a regular read-to-EOF file.

For the new policy, the guard compiles the hash-verified approved text **once** with the actual hosted compiler and running-kernel features, checks the resulting names, enforcing modes and attachment matches, then submits **those exact compiled bytes** through `--binary --add`. It compares the loaded complete-blob and per-profile hashes with this compilation, including bwrap's own `<unknown>` representation. It records source identity, compiler version/executable hash and compiled identities. Local binary digests are never portable hosted allowlists.

An exclusively created root-owned `/run/n1-bwrap-policy-identity/` directory retains the successful job's compilation, complete unrelated-policy baseline, global controls and post-load revision. The final existing verification step checks this trusted baseline, both intended profiles, unrelated policies and revisions again. Existing state is not overwritten. No policy file is installed in `/etc`, no policy is replaced, and no sandbox payload runs under sudo. The approved restricted child profile, actual enforced child-label assertion, namespace isolation, capability dropping, no-new-privileges, nested-userns denial, read-only projections and restricted database sockets remain unchanged.

Inventories are limited to 4096 profiles and 64 MiB of raw bytes, with 8 MiB per blob, bounded wire depth/node/table sizes, parser command deadlines and the unchanged workflow's outer 30-/20-second load/verification deadlines. The reader supports the packed xmatch representation exercised by the real compiler (wire ABI 5–9); namespace-qualified/renamed blobs, explicit xmatch permission tables, differential xmatch encoding or other unsupported representations fail closed for review. Missing kernel binary-export/hash interfaces also block loading. Compiler/kernel differences are evaluated through actual bytes, not assumed equivalent. No unsupported representation grants an exception or changes a policy.

Kernel `policy` traversal is pinned by a descriptor, with securityfs/AppArmor filesystem-type and namespace identity checks. Canonical objects are opened beneath it with no-follow component traversal; bounded per-profile relative links select candidates whose device/inode/type identities must independently match kernel traversal before reading and after verification. This avoids pathname resolution of magic-link display text and unchecked prefix containment.

The raw ABI reader accepts exact `v5\n` through `v9\n` bytes and compares against the complete blob's last header; malformed, unsupported and mismatched values fail. The source/fixture cross-check covers raw bytes, newline-terminated lowercase raw/profile hashes, ABI, profile name/mode/attachment and pollable decimal revision. See [Linux AppArmor exports](https://github.com/torvalds/linux/blob/v6.8/security/apparmor/apparmorfs.c), [unpacking/last-header assignment](https://github.com/torvalds/linux/blob/v6.8/security/apparmor/policy_unpack.c), [hashing](https://github.com/torvalds/linux/blob/v6.8/security/apparmor/crypto.c) and [the focused tests](../.github/scripts/test_n1_apparmor_policy.py). Tests include real procfs magic-link analogues, source-derived field formats and independent profile-hash calculations; synthetic ABI-mutated blobs are not claimed to have been accepted by a kernel. Diagnostics emit bounded fixed operation/field/category labels and errno, without paths, link targets, evidence bytes or raw OS errors.

## Hosted gate and remaining work

| Verified evidence | Result and scope |
| --- | --- |
| [PR CI 34676651320](https://github.com/runyiy/ai-api-security-platform/actions/runs/34676651320/attempts/1) at reviewed `f5ac48e2a9b4f6ca3c85cd7923f930e1ee5b04b3` | Attempt 1 succeeded: 3817 tests = 117 w2 + 3700 remaining, complete/disjoint collection, actual policy loading, enforcing child confinement and post-test policy/control verification. |
| [Exact-main CI 34677275222](https://github.com/runyiy/ai-api-security-platform/actions/runs/34677275222/attempts/1) at `eb69379910b99cf312cbc9b08f9a3c4b72088c9d` | Attempt 1 succeeded with the same 3817-test and runtime gates. |
| Independent local policy/diagnostic validation | 46 tests passed for the reviewed feature; helpers are outside backend collection. No local AppArmor activation was used. |

The maintenance handoff supplies the independent-review conclusion. Read-only verification during documentation cleanup confirmed PR merge/head, both exact run SHAs, attempt=1, successful job conclusions, complete/disjoint counts, runtime child-success output and post-test verification logs. No CI retry or backend rerun was performed for prose. The ordered collection digest was `976894a7e17540f864924c159ebdf10ff376a99fe58cfd27fce5691b5f3c0031`.

**Remaining W2:** strict records/interfaces, Q1–Q4 preparation and genuine-publication retrieval, persistent reservations/events/settlement, W1 wrappers, independent permit/journal/witness authority, full lifecycle-writer integration and explicit recovery. **T1–T12, X1–X8 and Y1–Y5 (including both Y3 histories) are not established by this prerequisite.** No PostgreSQL G-session-loss/invalidator/stale-sender or process-crash accounting protocol is proved here. Input-token/hidden-overhead bounds, account billing/retention, actual budgets and all live operational gates remain unresolved; no provider/Target/private-data permission follows.

## Local validation

The development record contains earlier 3808-test and corrected 3817-node snapshots, targeted N1/cleanup/gate runs and owned-cluster identity/cleanup evidence. They are historical, with the source-snapshot limits retained in [the reviewed record](https://github.com/runyiy/ai-api-security-platform/blob/f5ac48e2a9b4f6ca3c85cd7923f930e1ee5b04b3/docs/research-ai-w2-runtime-validation.md). The integrated PR/main runs above supersede their pending-hosted status. Pure documentation validation requires no application imports or databases; future DB work still requires an independently verified, owned disposable TEST PostgreSQL environment with isolated configuration.

## Historical failed runs and lessons

<a id="hosted-bubblewrap-prerequisite-correction"></a>
<a id="correction-validation-local-only"></a>
<a id="hosted-loopback-restriction-investigation"></a>
<a id="what-the-evidence-establishes"></a>
<a id="authorized-diagnostic-increment"></a>
<a id="actual-local-validation"></a>
<a id="validation-and-remaining-hosted-gate"></a>
<a id="actual-local-validation-and-remaining-hosted-evidence"></a>
<a id="p1-policy-namespace-access-correction--local-pending-independent-review"></a>
<a id="p1-raw-abi-format-correction--local-pending-independent-review"></a>

All runs below failed on attempt 1 and were retained rather than retried to green. Later new commits supplied the corrections integrated by PR #153.

| Run | Failure and lesson |
| --- | --- |
| [34658362313](https://github.com/runyiy/ai-api-security-platform/actions/runs/34658362313/attempts/1) | Missing bubblewrap; ten isolation failures. Runner inventory is not executable/runtime evidence. Add authenticated distribution installation and a required capability probe. |
| [34669278020](https://github.com/runyiy/ai-api-security-platform/actions/runs/34669278020/attempts/1), [34670509334](https://github.com/runyiy/ai-api-security-platform/actions/runs/34670509334/attempts/1) | Loopback `RTM_NEWADDR` failed before child startup. Local WSL success did not establish hosted compatibility. Bounded diagnostics identified relevant controls; missing denial records did not prove the exact denying hook. Later successful adopted-policy runs do not retroactively create that attribution evidence. |
| [34672200107](https://github.com/runyiy/ai-api-security-platform/actions/runs/34672200107/attempts/1) | `<unknown>` attachment rejected before loading. Compiler output can preserve a DFA without its source attachment text; verify complete raw/profile identity and DFA behavior instead of source names or local binary allowlists. |
| [34674561578](https://github.com/runyiy/ai-api-security-platform/actions/runs/34674561578/attempts/1) | 123 profiles failed, zero raw bytes read. Strict pathname resolution cannot reproduce kernel magic-link traversal. Ordinary-directory fixtures concealed the defect; real magic-link regressions and descriptor identity checks were added. |
| [34675661127](https://github.com/runyiy/ai-api-security-platform/actions/runs/34675661127/attempts/1) | 5,691,101 raw bytes read; all 123 profiles failed `validate/raw_abi/malformed_evidence`, before loading/N1 execution. Kernel ABI is `v%d\n`, not a bare integer. Correct fixtures and strict last-header parsing; cross-check all consumed export formats together. The in-memory normalization diagnostic was counterfactual, not committed-source or hosted-load evidence. |

Local setup/cleanup lessons also remain: tool-session lifetime can terminate an owned server between commands; Unix-socket paths must fit the kernel limit; PID numbers alone do not identify processes across namespaces; signal delivery must be observed with bounded pidfd readiness. Historical failed/development attempts are not acceptance runs. The fixed reviewed record retains the detailed evidence without making temporary paths the current documentation interface.
