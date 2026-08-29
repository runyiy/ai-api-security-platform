# Security Model

## 1. Purpose

This document defines the security invariants for AI API Security Testing Platform.

The approved product and architecture constraints are recorded in [`architecture-decisions.md`](architecture-decisions.md). If roadmap text conflicts with either document, implementation must stop for an explicit architecture decision.

The platform is intended only for:

- localhost;
- self-controlled labs;
- self-owned servers;
- systems for which explicit testing authorization has been granted.

The platform must fail closed when authorization or scope cannot be proven.

Execution-capable v1 supports one trusted operator and local/private lab use. `execution_topology` defaults to `single_process`; explicit `multi_process` supports multiple FastAPI processes only for immutable exact-plan execution backed by shared PostgreSQL coordination. The legacy direct TestCase execute route is blocked before execution work in that topology. Public SRC or bug-bounty execution remains unsupported and operationally prohibited, and `external_public_authorized` remains blocked at runtime.

## 2. Trust Boundaries

The main trust boundaries are:

```text
User / Operator
    ↓
API / Service Layer
    ↓
Policy Engine
    ↓
Execution Plan
    ↓
HTTP Executor
    ↓
Authorized Target
```

Separate data/analysis paths exist for:

```text
TestRun → Analyzer → Finding → AI Analysis → Human Review → Report
```

AI is outside the execution trust boundary.

The trusted operator boundary and target-testing authorization boundary are separate. Control of the deployment does not authorize testing a Target.

## 3. Core Security Invariants

### 3.1 Default Deny

If a request cannot be positively matched to valid authorization and active scope, it must be denied.

No component may infer permission from the existence of a Target alone.

### 3.2 Target Is Not Authorization

A Target is an inventory/configuration object. It does not prove permission to test.

External execution must eventually require an AuthorizationProfile plus active Scope.

Wildcard program scope is also not execution authorization. A wildcard match may create an asset candidate, but every executable hostname must be reviewed and enrolled as an explicit Target.

### 3.3 Authorization Revision Selection

Every ExecutionPlan and execution must reference exactly one explicit immutable authorization revision. Permissions from separate grants or revisions must never be implicitly unioned.

Scope and platform policy may only narrow the selected authorization revision. They may never widen it.

This is the required target-state invariant and is mandatory before public execution. Until Milestone 4 is complete, the local/private-lab MVP uses the current mutable AuthorizationProfile as a transitional authorization object and does not claim immutable revision-level historical reproducibility.

### 3.4 Platform Host Allowlist

The executor may only reach hosts present in the platform-level allowlist.

Program scope cannot override the platform host allowlist.

### 3.5 Origin Match

The request origin must match the configured target origin unless an explicitly designed, reviewed future feature changes this behavior.

Unexpected cross-origin requests are denied.

### 3.6 Scope Match

Authorization requires matching:

- hostname;
- path;
- HTTP method;
- active scope state.

Ambiguous or unsafe path normalization is denied.

### 3.7 Execution-Time Revalidation

Policy must be checked immediately before each network request.

A previously generated TestCase or approved plan does not permanently authorize later execution.

### 3.8 Automatic Method Restriction

Automatic execution is GET-only for the current MVP and remains so until a dedicated reviewed milestone changes the policy.

Mutating methods must never become implicitly enabled because they exist in OpenAPI or a TestCase.

### 3.9 Redirects Disabled

HTTP clients used for authorized execution must not automatically follow redirects.

Redirect destinations must not bypass host/origin/scope enforcement.

### 3.10 Time and Size Limits

Network execution must enforce bounded timeouts and network-response-size limits. Persisted evidence has a separate, smaller storage limit and retention policy.

Unbounded body reads are prohibited.

### 3.11 Rate Limiting and Process Model

Requests must be rate limited at least per target. Later milestones may add program-level/global/concurrency limits.

Authorization does not imply unlimited request volume.

The exact-plan M8 stack uses PostgreSQL-backed rate reservations, claims and leases, canonical idempotency, progress/recovery, cancellation, kill switches, and advisory-lock network permits. These controls are process-safe for local/private exact-plan requests under explicit `multi_process` topology. The legacy direct TestCase execution path remains single-process-only and is fail-closed in multi-process mode. No background worker, scheduler, or automatic in-doubt recovery is implied; Redis is not mandatory for v1.

## 4. Authentication Security

### 4.1 Identity and Credential Are Separate

`TestIdentity` identifies who is acting. CredentialBinding and its source/provider identify how that identity is authenticated at execution time.

The existing `TestIdentity.credentials` JSONB field is transitional. Long-term credentials must not be owned as plaintext data by the TestIdentity ORM object.

The architecture may support encrypted PostgreSQL material, external secret references, and dynamic/expiring sessions. Initial implementation may support only encrypted PostgreSQL storage behind a small credential-source boundary; a plugin framework is not required.

### 4.2 AuthenticationContext Owns Request Authentication Material

Authorization-related headers must originate only from AuthenticationContext or its future secret-backed equivalent.

Test generators, AI modules, endpoint metadata, and arbitrary request payloads must not directly inject Authorization credentials.

### 4.3 Direct Authorization Header Injection Is Forbidden

If caller-provided request headers already contain Authorization, request construction must reject them rather than merge or override silently.

### 4.4 Secret Redaction

Sensitive material includes at least:

- Authorization;
- bearer tokens;
- cookies/session values;
- API keys;
- credential payloads;
- refresh tokens;
- secret custom authentication headers.

Sensitive material must not appear in:

- logs;
- TestRun request snapshots;
- API responses;
- analyzer reasons;
- findings;
- AI prompts;
- audit records;
- security reports.

### 4.5 Storage Hardening

Long-lived credentials must not remain indefinitely as ordinary plaintext application-visible JSON in the Level 3 design.

A future secret-storage milestone must provide encrypted storage or an external secret reference design behind CredentialBinding/CredentialSource. An orphan ciphertext table is not the credential-domain boundary.

## 5. Generator Security

The BOLA generator may:

- inspect endpoint metadata;
- inspect test identities;
- inspect resource ownership;
- generate candidate TestCases.

The generator may not:

- send HTTP requests;
- create or provide Authorization headers;
- alter Scope;
- alter AuthorizationProfile;
- bypass execution approval;
- call the HTTP executor.

Generated plans are untrusted input to the policy/execution layer and must be revalidated.

## 6. AI Security Boundary

AI may:

- summarize evidence;
- explain potential impact;
- estimate false-positive risk;
- suggest severity;
- suggest remediation;
- help draft report text.

AI may not:

- call or own the HTTP executor;
- create Authorization credentials;
- expand scope;
- approve execution;
- confirm findings;
- modify authorization policy;
- override deterministic policy decisions.

All AI input must pass through redaction/sanitization.

AI output is advisory, not authoritative.

## 7. Finding and Review Security

Rule-based analysis may produce a potential finding.

A potential finding is not a confirmed vulnerability.

Only a human review transition may mark a finding confirmed.

Formal report generation must require confirmed status.

This prevents automated analysis or AI output from being treated as final security truth.

## 8. Evidence Handling

Evidence should be sufficient to explain why a finding was created while minimizing unnecessary sensitive data retention.

The platform must prefer:

- relevant status codes;
- resource identifiers;
- selected/redacted response evidence;
- rule reasoning;
- baseline/cross-owner comparison metadata.

The platform should avoid storing unnecessary third-party or sensitive content.

Full third-party response bodies are not the default permanent evidence model. Network responses may exist transiently within a strict network size limit. Persisted evidence should be structured, bounded, redacted, provenance-bearing, and limited to material proof. Secrets must never be stored as evidence.

Baseline and probe evidence must identify their exact source runs/plans rather than selecting an unrelated latest run at analysis time.

## 9. Expected Access and Ownership Truth

Resource ownership and expected access are assertions with provenance. Recognized source classes include, in descending trust order:

1. human-verified assertions;
2. target-provided fixtures;
3. observed baseline behavior;
4. inferred candidates.

Assertions must carry confidence, verification state, and relevant observed/asserted timestamps. Observed access alone never silently proves exclusive ownership or that another identity should be denied.

## 10. Network Safety Requirements

Current executor protections must remain regression-tested:

- platform host allowlist;
- exact target origin validation;
- active hostname/path/method scope;
- no redirects;
- timeout;
- response-size limit;
- rate limiting;
- trust_env disabled where applicable;
- automatic GET-only execution.

Execution must distinguish explicit private/local and external/public-authorized network modes. Before any real public request, all outbound network paths must use a centralized boundary that includes:

- canonical URL, scheme, origin, and port enforcement;
- DNS resolution and IPv4/IPv6 address classification;
- mode-specific loopback, private, link-local, metadata, reserved, and public address policy;
- DNS rebinding and policy-to-connect TOCTOU protection;
- actual connected destination/peer validation;
- global kill switch;
- target kill switch;
- bounded concurrency;
- execution audit IDs.

Public/external mode must fail closed for localhost/loopback, private address ranges, link-local destinations, metadata services, and other prohibited address classes. These controls are public-readiness blockers, not optional late hardening.

## 11. AuthorizationProfile and Revision Security Requirements

When AuthorizationProfile is introduced, external execution should require all applicable conditions:

- profile exists;
- profile is active/valid for the current time;
- target is linked to the profile;
- automation is permitted when automatic execution is requested;
- HTTP method is allowed by program policy;
- configured program rate limit is respected;
- human approval is present when required.

Scope remains mandatory even when AuthorizationProfile is valid.

The mutable AuthorizationProfile is a transitional MVP model. Historical plans and runs must eventually reference the exact immutable authorization revision evaluated for that action.

## 12. Human Approval

Level 3 must provide an immutable bounded ExecutionPlan that can be reviewed before network execution.

The preview should identify at least:

- method;
- destination URL;
- target;
- test identity (without secrets);
- target resource;
- policy decision;
- expected authorization behavior.

Approval does not disable execution-time policy revalidation.

Approval must bind to the exact plan/action set, preferably through a stable digest. Material changes to the Target, authorization revision, identity/credential binding, resources, method, URL/template, network mode, or request count invalidate approval. Approval never bypasses authorization, Scope, platform safety, rate, kill-switch, credential, or network policy.

## 13. Audit Requirements

Minimal execution safety events must be recorded before public execution. Full audit search, export, and retention management may be added later.

Examples:

- policy denied;
- policy allowed;
- execution approved;
- execution started;
- execution failed;
- execution completed;
- finding confirmed;
- report generated.

Execution safety records must identify the selected authorization revision, plan/action, policy decision, relevant network decision, and execution result without storing secrets.

Audit records must never contain authentication secrets.

## 14. OpenAPI Retrieval

OpenAPI retrieval is anonymous by default. Authenticated retrieval may occur only through an explicitly selected documentation CredentialBinding and the same constrained AuthenticationContext used by other outbound requests.

The scanner must never automatically select a TestIdentity or credential. Authentication never widens Target authorization, authorization revision, Scope, origin, network mode, method, or rate policy. Retrieval remains GET-only, redirects-disabled, bounded, rate limited, and subject to the centralized network boundary.

## 15. External Testing Gate

Real public SRC or bug-bounty execution is currently unsupported and operationally prohibited. The current code does not yet provide the complete machine-enforced public-network readiness boundary. Before public use, the target architecture must enforce a release and execution gate that denies public execution unless all applicable readiness requirements are implemented and satisfied.

At minimum:

- one explicit immutable authorization revision is selected;
- scope is explicit;
- authorization is not expired;
- every executable hostname is an explicit Target;
- external/public network mode and the centralized DNS/IP/peer boundary are active;
- program automation constraints are understood;
- rate limiting is configured;
- the execution topology is compatible with the limiter/coordination design;
- dedicated test identities are used where applicable;
- CredentialBinding is active and secrets are protected;
- redirect/timeout/response-size protections are active;
- kill switches and bounded concurrency are active;
- an immutable bounded plan and required approval exist;
- execution approval policy is satisfied;
- minimal safety audit, evidence minimization, and redaction controls are functioning.

Failure of any mandatory check results in denial.

## 16. Superseded Credential Issue

GitHub Issue #23, "M3-01: add encrypted credential secret storage primitive," is superseded as written. Its authenticated-encryption requirements remain applicable to the replacement stored-secret provider milestone, but implementations must first establish the non-sensitive TestIdentity/CredentialBinding boundary. The old M3-01 implementation branch must not be reused for the replacement M3-01.

## 17. Regression Rule for Codex and Contributors

Any code change that touches policy, authentication, execution, findings, AI integration, reporting, or audit must preserve the invariants in this document.

If a requested feature conflicts with this security model, the implementation must stop and require an explicit architecture decision rather than silently weakening a guardrail.
