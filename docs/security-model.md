# Security Model

## 1. Purpose

This document defines the security invariants for AI API Security Testing Platform.

The platform is intended only for:

- localhost;
- self-controlled labs;
- self-owned servers;
- systems for which explicit testing authorization has been granted.

The platform must fail closed when authorization or scope cannot be proven.

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

## 3. Core Security Invariants

### 3.1 Default Deny

If a request cannot be positively matched to valid authorization and active scope, it must be denied.

No component may infer permission from the existence of a Target alone.

### 3.2 Target Is Not Authorization

A Target is an inventory/configuration object. It does not prove permission to test.

External execution must eventually require an AuthorizationProfile plus active Scope.

### 3.3 Platform Host Allowlist

The executor may only reach hosts present in the platform-level allowlist.

Program scope cannot override the platform host allowlist.

### 3.4 Origin Match

The request origin must match the configured target origin unless an explicitly designed, reviewed future feature changes this behavior.

Unexpected cross-origin requests are denied.

### 3.5 Scope Match

Authorization requires matching:

- hostname;
- path;
- HTTP method;
- active scope state.

Ambiguous or unsafe path normalization is denied.

### 3.6 Execution-Time Revalidation

Policy must be checked immediately before each network request.

A previously generated TestCase or approved plan does not permanently authorize later execution.

### 3.7 Automatic Method Restriction

Automatic execution is GET-only for the current MVP and remains so until a dedicated reviewed milestone changes the policy.

Mutating methods must never become implicitly enabled because they exist in OpenAPI or a TestCase.

### 3.8 Redirects Disabled

HTTP clients used for authorized execution must not automatically follow redirects.

Redirect destinations must not bypass host/origin/scope enforcement.

### 3.9 Time and Size Limits

Network execution must enforce bounded timeouts and response-size limits.

Unbounded body reads are prohibited.

### 3.10 Rate Limiting

Requests must be rate limited at least per target. Later milestones may add program-level/global/concurrency limits.

Authorization does not imply unlimited request volume.

## 4. Authentication Security

### 4.1 AuthenticationContext Owns Credentials

Authorization-related headers must originate only from AuthenticationContext or its future secret-backed equivalent.

Test generators, AI modules, endpoint metadata, and arbitrary request payloads must not directly inject Authorization credentials.

### 4.2 Direct Authorization Header Injection Is Forbidden

If caller-provided request headers already contain Authorization, request construction must reject them rather than merge or override silently.

### 4.3 Secret Redaction

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

### 4.4 Storage Hardening

Long-lived credentials must not remain indefinitely as ordinary plaintext application-visible JSON in the Level 3 design.

A future secret-storage milestone must provide encrypted storage or an external secret reference design.

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

The platform should prefer:

- relevant status codes;
- resource identifiers;
- selected/redacted response evidence;
- rule reasoning;
- baseline/cross-owner comparison metadata.

The platform should avoid storing unnecessary third-party or sensitive content.

## 9. Network Safety Requirements

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

Future hardening should include:

- DNS/IP validation;
- SSRF-oriented private/reserved network controls;
- global kill switch;
- target kill switch;
- bounded concurrency;
- execution audit IDs.

These additions must fail closed.

## 10. AuthorizationProfile Security Requirements

When AuthorizationProfile is introduced, external execution should require all applicable conditions:

- profile exists;
- profile is active/valid for the current time;
- target is linked to the profile;
- automation is permitted when automatic execution is requested;
- HTTP method is allowed by program policy;
- configured program rate limit is respected;
- human approval is present when required.

Scope remains mandatory even when AuthorizationProfile is valid.

## 11. Human Approval

Level 3 should provide an ExecutionPlan that can be reviewed before network execution.

The preview should identify at least:

- method;
- destination URL;
- target;
- test identity (without secrets);
- target resource;
- policy decision;
- expected authorization behavior.

Approval does not disable execution-time policy revalidation.

## 12. Audit Requirements

Important security events should eventually be recorded in append-oriented AuditLog records.

Examples:

- policy denied;
- policy allowed;
- execution approved;
- execution started;
- execution failed;
- execution completed;
- finding confirmed;
- report generated.

Audit records must never contain authentication secrets.

## 13. External Testing Gate

Real external execution remains disabled unless readiness requirements are satisfied.

At minimum:

- explicit authorization is recorded;
- scope is explicit;
- authorization is not expired;
- platform host allowlist is configured;
- program automation constraints are understood;
- rate limiting is configured;
- dedicated test identities are used where applicable;
- secrets are protected;
- redirect/timeout/response-size protections are active;
- execution approval policy is satisfied;
- audit and redaction controls are functioning.

Failure of any mandatory check results in denial.

## 14. Regression Rule for Codex and Contributors

Any code change that touches policy, authentication, execution, findings, AI integration, reporting, or audit must preserve the invariants in this document.

If a requested feature conflicts with this security model, the implementation must stop and require an explicit architecture decision rather than silently weakening a guardrail.
