# AI API Security Testing Platform — Level 3 Roadmap

## 1. Goal and source of truth

Build an authorization-aware API object-level authorization testing platform for self-controlled labs and, only after a dedicated release gate, explicitly authorized public SRC or bug bounty targets.

This roadmap is governed by:

- [`architecture-decisions.md`](architecture-decisions.md), which records approved product and architecture constraints;
- [`security-model.md`](security-model.md), which records mandatory security invariants.

The product is not a general-purpose website attack scanner. It remains focused on BOLA/IDOR until the authorization, execution, network, ownership, and evidence boundaries are safe and complete.

## 2. Current deployment safety status

Execution-capable v1 currently assumes:

- one trusted operator;
- `single_process` execution by default, with explicit `multi_process` support
  limited to local/private immutable exact-plan execution;
- self-hosted local/private lab usage.

Public SRC or bug-bounty execution is currently unsupported, is not an approved deployment mode, and is operationally prohibited. Completion of Milestones 0–2 does not provide public readiness. The current codebase does not yet provide the complete machine-enforced public-network readiness boundary and must not be assumed to prevent every possible public destination if configured with a public hostname. No real public SRC request may be intentionally executed until the Public SRC Readiness controls are implemented and satisfied. The target architecture must provide a machine-enforced release and execution gate before public use.

The `multi_process` topology is an explicit operator opt-in and is never inferred from worker count. It requires PostgreSQL shared coordination and disables legacy direct TestCase execution.

## 3. Non-negotiable principles

1. Authorization beats automation; default deny.
2. Trusted operator, Target existence, and wildcard matches do not prove authorization to execute.
3. Every execution selects exactly one authorization revision; grants are never implicitly unioned.
4. Scope and platform safety policy may narrow authorization but never widen it.
5. Policy and network safety are re-evaluated immediately before every request.
6. Automatic execution remains GET-only until a separately reviewed milestone changes it.
7. AI never owns or controls credentials, approval, policy, or HTTP execution.
8. Authentication material originates only from the constrained AuthenticationContext/credential-provider boundary.
9. Human approval binds an immutable bounded plan and never bypasses policy.
10. Generators and discovery metadata never execute requests.
11. Findings require human review before confirmation; only confirmed findings produce formal reports.
12. Secrets never appear in logs, evidence, findings, AI input, audit records, or reports.
13. Evidence retention is minimized, structured, bounded, and redacted by default.

## 4. Completed historical milestones

### Milestone 0 — Stable local MVP regression

Completed:

- deterministic local BOLA lab with secure and vulnerable modes;
- owner-baseline and cross-owner integration regression;
- reproducible pytest CI gate;
- regression coverage for current policy and executor safety behavior.

### Milestone 1 — AuthorizationProfile and Target binding

Completed:

- AuthorizationProfile model and CRUD;
- explicit Target binding and unbinding;
- fail-closed defaults.

The current mutable profile is an MVP predecessor to the immutable revision model in Milestone 4.

### Milestone 2 — Authorization-aware policy engine

Completed:

- AuthorizationProfile validity and automation checks;
- Scope enforcement;
- platform host allowlist and exact Target origin enforcement;
- safe path validation;
- automatic GET-only execution;
- execution-time policy revalidation;
- profile and shared per-Target rate limiting across execution and OpenAPI import;
- PolicyDecision authorization-profile and evaluation-time metadata.

These controls are necessary but do not establish public SRC readiness.

The requirement that every revision-aware ExecutionPlan and execution select exactly one immutable authorization revision is a target-state invariant and is mandatory before public execution. Until Milestone 4 is complete, the local/private-lab MVP uses the current mutable AuthorizationProfile as a transitional authorization object and does not claim immutable revision-level historical reproducibility.

## 5. Revised implementation milestones

### Milestone 3 — Credential domain

Separate identity from authentication mechanism without prematurely building a complex provider framework.

#### M3-01 — CredentialBinding domain foundation

Goal: separate `TestIdentity` from credential mechanism using only non-sensitive credential metadata.

The accepted conceptual boundary is:

```text
TestIdentity
    -> CredentialBinding
```

CredentialBinding may eventually include fields equivalent to an identity reference, authentication type, source type, active state, and timestamps. Exact schema names belong in the implementation plan.

Initially expected values may include `auth_type = bearer` and `source_type = stored_secret`. CredentialSource may remain a small service/domain abstraction.

Explicitly out of scope:

- encryption or ciphertext storage;
- migration of existing bearer tokens;
- AuthenticationContext changes;
- API keys, cookies, or sessions;
- dynamic login/session acquisition;
- Vault/KMS;
- AuthorizationRevision, ExecutionPlan, or NetworkGateway.

#### M3-02 — Encrypted PostgreSQL stored-secret provider

Goal: add authenticated encrypted secret storage behind the CredentialBinding/source boundary.

Use vetted authenticated encryption, randomized nonces, external key material, a versioned envelope, and fail-closed tamper/wrong-key handling. PostgreSQL encrypted storage is the first provider, not the whole credential domain.

#### M3-03 — Bearer migration

Goal: create, update, and resolve bearer credentials through CredentialBinding and remove the plaintext bearer execution path after a safe migration/backfill path.

The existing `TestIdentity.credentials` JSONB field is transitional and must not remain the long-term credential model.

GitHub Issue #23 is superseded as written. Do not implement its orphan CredentialSecret shape or reuse its implementation branch for the replacement M3-01.

### Milestone 4 — Authorization revisions

Add immutable authorization revisions with explicit lifecycle state. A Target may have historical and future revisions, but each ExecutionPlan and execution selects exactly one revision. Never union permissions from separate grants or revisions. Scope only narrows the selected revision.

Historical plans and runs must be able to identify the exact revision that permitted the action.

### Milestone 5 — Execution planning and minimal safety audit

Add:

- immutable bounded ExecutionPlan;
- bounded PlanAction/request snapshots;
- stable plan digest;
- selected authorization revision and relevant policy context;
- minimal append-oriented records for plan, policy, and execution safety decisions.

TestCase remains reusable test intent. ExecutionPlan freezes the concrete action set. Full audit query/export is a later product capability.

### Milestone 6 — Network safety boundary

Introduce:

- explicit `private/local` and `external/public-authorized` modes;
- one centralized outbound NetworkGateway used by all network paths;
- canonical URL, scheme, origin, and port enforcement;
- DNS resolution and IPv4/IPv6 address classification;
- mode-specific private, loopback, link-local, metadata, reserved, and public address policy;
- DNS rebinding and policy-to-connect TOCTOU protection;
- actual destination/peer validation;
- global and per-Target kill switches;
- bounded connection concurrency;
- separate maximum network-response and stored-evidence limits.

This milestone is a blocker for public SRC execution. Hostname/origin checks alone are not sufficient.

### Milestone 7 — Human approval

Bind human approval to the exact immutable plan digest and bounded action set. Material mutation invalidates approval.

Approval is necessary when configured but never sufficient. Every action still undergoes immediate pre-network authorization, Scope, Target, credential, rate, kill-switch, and network validation.

### Milestone 8 — Shared execution coordination (complete)

Completed PostgreSQL-first coordination provides:

- process-safe rate reservations;
- execution claims and leases;
- idempotency;
- stuck-work recovery and cancellation semantics;
- multi-process execution readiness.

Deterministic real-OS-process tests cover the complete exact-plan stack against PostgreSQL and self-controlled localhost HTTP. Local/private exact-plan execution may use multiple FastAPI processes only when `execution_topology=multi_process`; the default remains `single_process`, and legacy `POST /test-cases/{id}/execute` is unavailable in multi-process topology. Redis is not required for v1.

Milestone 8 completion does not satisfy the separate Public SRC Readiness release gate. Public execution remains prohibited and `external_public_authorized` remains runtime blocked.

### Milestone 9 — API discovery

Add safe discovery features:

- explicit OpenAPI source URL;
- anonymous retrieval by default;
- optional explicitly selected documentation CredentialBinding later;
- no automatic TestIdentity or credential selection;
- versioned document/import provenance;
- bounded compressed/decompressed size, parser complexity, depth, endpoint count, and reference behavior;
- metadata import only, with no automatic request replay.

All remote retrieval uses the same policy, credential, rate, and NetworkGateway boundaries as other outbound requests. Authentication never widens authorization or safety policy.

### Milestone 10 — Wildcard asset enrollment

Support program wildcard rules and exclusions through a candidate workflow:

```text
program wildcard rule
    -> discovered asset candidate
    -> inclusion/exclusion validation
    -> CNAME and DNS/network validation
    -> human enrollment
    -> explicit Target
```

Every executable hostname must become an explicit Target. Wildcard matches never directly authorize execution.

### Milestone 11 — Endpoint and resource-binding intelligence

Support reviewed bindings for path, query, nested, and multiple resource identifiers. Bindings carry provenance and confidence. Low-confidence bindings do not silently become executable actions.

### Milestone 12 — Resource access assertions

Replace single timeless ownership truth with assertions carrying:

- provenance: `human_verified`, `target_fixture`, `observed_baseline`, or `inferred_candidate`;
- confidence;
- verification state;
- asserted/observed time and relevant validity information;
- expected access relation or decision.

Observed access never silently proves exclusive ownership or expected denial for another identity.

### Milestone 13 — Evidence engine

Introduce:

- explicitly paired baseline/probe provenance;
- structured selected evidence;
- bounded redacted excerpts;
- response hashes/fingerprints and similarity metadata;
- extractor/rule provenance and timestamps;
- an explicit retention policy.

Full third-party response bodies are not retained by default. Potentially sensitive identifiers, PII, and business data are stored only when materially necessary to prove a finding. Secrets are never evidence.

### Milestone 14 — BOLA matrix expansion

Expand pure plan generation only after resource assertions and evidence foundations exist. Add owner, cross-subject, anonymous, role/shared-access, nested-resource, query-binding, and multiple-parameter cases. Generators never send HTTP requests or supply credentials.

## 6. Later product maturity

After the safety and BOLA foundations:

- human review console and audited review transitions;
- AI advisory analysis with redacted allowlisted evidence;
- confirmed-finding reports;
- full audit query/export and retention management;
- additional offline/import formats;
- mutating execution only after a separate architecture and threat review.

## 7. Public SRC Readiness release gate

Public SRC or bug-bounty execution is unsupported and operationally prohibited until all applicable controls are implemented and tested. The completed architecture must enforce this release and execution gate in the machine boundary before public use. Required controls include:

- one valid immutable authorization revision selected per plan/execution;
- explicit Target and Scope;
- external/public network mode;
- centralized NetworkGateway with DNS, IPv4/IPv6, prohibited-address, rebinding/TOCTOU, port, and actual-peer enforcement;
- valid credential binding with no plaintext execution path;
- immutable bounded plan and required approval;
- immediate pre-network policy revalidation;
- rate and concurrency enforcement appropriate to the deployment topology;
- global and per-Target kill switches;
- minimal safety audit records;
- structured bounded redacted evidence and tested secret redaction;
- GET-only, redirects-disabled, bounded-timeout, bounded-response behavior;
- a successful self-owned public-server readiness exercise before third-party use.

Failure of any mandatory check results in denial.

## 8. Completion gates

### Gate A — Local lab

All core workflows and safety failures are reproducible against self-controlled fixtures. This is the only currently supported execution environment.

### Gate B — Self-owned public server

After all mandatory Public SRC Readiness controls applicable to the exercise are implemented and tested, validate the external/public network path against a public server owned and controlled by the operator. Readiness is determined by required security capabilities, not milestone number alone; unrelated capabilities are not prerequisites when they do not apply to the exercise.

### Gate C — Explicitly authorized public program

Only after Gate A, Gate B, and all applicable Public SRC Readiness requirements pass may the platform test an explicitly authorized third-party SRC/bug-bounty Target, within the exact selected authorization revision and Scope.

## 9. Development workflow

Implement one small reviewed issue at a time:

```text
Milestone
  -> small issue
  -> implementation and focused tests
  -> full regression suite
  -> self-review and diff review
  -> human review
  -> CI and merge
```

Each issue defines one concrete goal, security constraints, explicit exclusions, required tests, and done criteria. Architecture changes require human review before implementation.
