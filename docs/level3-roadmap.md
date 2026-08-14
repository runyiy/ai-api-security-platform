# AI API Security Testing Platform — Level 3 Roadmap

## 1. Goal

Build an authorization-aware API object-level authorization testing platform for localhost, self-owned labs, self-owned servers, and explicitly authorized environments such as bug bounty or SRC programs.

The Level 3 product is not a general-purpose website attack scanner. Its primary workflow is:

```text
Authorization Program / Rules
        ↓
Authorization Profile
        ↓
Target
        ↓
Scope
        ↓
API Import / Discovery
        ↓
Endpoint
        ↓
Test Identity
        ↓
Authentication Context
        ↓
Resource Discovery
        ↓
Resource Ownership
        ↓
BOLA Test Generator
        ↓
TestCase
        ↓
Execution Plan / Human Approval
        ↓
Policy Engine
        ↓
Authorized HTTP Executor
        ↓
TestRun
        ↓
Rule-Based Analyzer
        ↓
Potential Finding
        ↓
AI-Assisted Analysis
        ↓
Human Review
        ↓
Confirmed Finding
        ↓
Security Report
```

The MVP remains focused on BOLA / IDOR. New vulnerability classes are lower priority until the BOLA workflow is safe, complete, and externally usable in explicitly authorized environments.

## 2. Non-Negotiable Principles

1. Authorization beats automation.
2. Default deny.
3. Target existence does not imply authorization.
4. Policy is re-evaluated at execution time.
5. AI never owns or controls the HTTP executor.
6. Authorization material comes only from AuthenticationContext.
7. Automatic execution remains GET-only until a later milestone explicitly changes this policy.
8. Findings require human review before confirmation.
9. Only confirmed findings may generate formal reports.
10. Secrets must never appear in logs, findings, AI prompts, or reports.

## 3. Milestones

### Milestone 0 — Stabilize the Existing MVP

Goal: establish a reliable regression baseline for the current Target → Scope → OpenAPI → Endpoint → Identity → Resource → TestCase → TestRun → Finding → Report chain.

Required work:

- Add an official local BOLA lab with at least two test users.
- Add a secure mode and a deliberately vulnerable mode.
- Add end-to-end integration tests for owner baseline and cross-owner access.
- Add regression coverage for scope denial, invalid credentials, redirects, timeout, response-size limit, and rate limiting.

Done when:

- secure mode produces PASS for cross-owner access;
- vulnerable mode produces POTENTIAL_BOLA;
- the finding can be human-confirmed;
- a confirmed finding can generate a security report;
- the existing test suite remains green.

### Milestone 1 — Authorization Profile

Introduce an AuthorizationProfile model representing the source and limits of permission to test a target.

Candidate fields:

- id
- name
- program_name
- program_url
- authorization_type
- authorization_reference
- valid_from
- valid_until
- automation_allowed
- max_requests_per_second
- allow_get
- allow_post
- allow_patch
- allow_put
- allow_delete
- require_human_execution_approval
- notes
- created_at
- updated_at

Targets must belong to an AuthorizationProfile before external execution is allowed.

### Milestone 2 — Program Policy Engine

Extend policy evaluation beyond hostname/path/method scope.

Execution must validate:

- authorization profile exists and is valid;
- target is enabled;
- host is in the platform allowlist;
- request origin matches the target;
- hostname/path/method are allowed by active scope;
- automation is permitted;
- rate limits are respected;
- human approval is present when required.

PolicyDecision should be auditable and include at minimum:

- allowed
- code
- reason
- matched_scope_id
- authorization_profile_id
- timestamp or equivalent execution record linkage

### Milestone 3 — Secret and Authentication System

Expand AuthenticationContext support to:

- anonymous
- bearer
- API key
- cookie/session
- constrained custom header authentication

Do not permit arbitrary Authorization header injection.

Move long-lived credentials away from plain application-visible JSON storage into an encrypted secret design. Database records should store references and non-sensitive metadata where possible.

Secrets must be redacted from:

- logs
- API responses
- errors
- findings
- AI input
- reports

### Milestone 4 — API Import and Discovery

Support safe endpoint ingestion through:

1. configurable OpenAPI URL import;
2. offline OpenAPI JSON/YAML file import;
3. HAR import for endpoint discovery;
4. Postman collection import.

Imports should create or update endpoint metadata. They must not automatically replay arbitrary requests.

### Milestone 5 — Endpoint Intelligence

Expand resource binding beyond only `{resource_id}` forms.

Examples to support over time:

- `/orders/{order_id}`
- `/orders/{id}`
- `/orders/{uuid}`
- `/orders?id=123`
- nested resource paths

Introduce a ResourceBinding concept with fields such as:

- location
- parameter_name
- resource_type
- confidence

Low-confidence bindings require human review before execution.

### Milestone 6 — Resource Discovery

Support:

- manually registered resources;
- observed candidate resources;
- API-discovered candidate resources.

Discovered ownership must begin as a candidate state. The platform must not silently promote uncertain ownership inference into trusted Resource records.

### Milestone 7 — BOLA Test Matrix

Expand generated cases to cover:

- owner baseline;
- cross-owner access;
- anonymous access;
- role-boundary access.

The generator only creates plans. It must never send HTTP requests.

### Milestone 8 — Execution Plan and Human Approval

Add an ExecutionPlan layer between generated test cases and HTTP execution.

Before execution the user should be able to review:

- method
- URL
- identity
- target resource
- expected result
- scope decision
- authorization profile

When required by policy, execution must remain blocked until approved.

### Milestone 9 — Executor Hardening

Preserve current protections:

- no automatic redirects;
- timeout;
- response-size limit;
- rate limiting;
- execution-time scope checks;
- GET-only automatic execution.

Add over time:

- DNS/IP validation;
- private/public network policy;
- connection and concurrency limits;
- global kill switch;
- per-target kill switch;
- execution audit IDs;
- request fingerprints.

### Milestone 10 — Evidence Engine

Do not treat HTTP 200 alone as proof of BOLA.

Evidence should consider:

- owner baseline success;
- cross-owner response;
- target resource identifier;
- status differences;
- relevant JSON fields;
- response similarity;
- semantic evidence;
- confidence and explainable rule output.

### Milestone 11 — AI-Assisted Analysis

AI is a Finding Assistant only.

Allowed responsibilities:

- explain the suspected issue;
- summarize security impact;
- estimate false-positive risk;
- suggest severity;
- suggest remediation;
- improve report wording.

AI input must pass through a redaction layer.

AI must not:

- execute HTTP;
- expand scope;
- provide authorization material;
- confirm findings;
- bypass human approval.

### Milestone 12 — Human Review Console

Finding states should support at least:

```text
potential → reviewing → confirmed
                     ↘ false_positive
```

Reviewers should see the endpoint, actor, resource owner, resource identifier, baseline, cross-owner result, analyzer reasoning, evidence, confidence, and optional AI analysis.

### Milestone 13 — SRC / Bug Bounty Report Output

Only confirmed findings may produce formal reports.

Reports should include:

- title
- summary
- affected endpoint
- prerequisites
- reproduction steps
- expected result
- actual result
- security impact
- evidence
- suggested fix

Authentication secrets must always be omitted.

Support Markdown and structured JSON first. PDF is a later concern.

### Milestone 14 — Audit System

Introduce immutable or append-oriented audit records for important actions, including:

- execution allowed / denied;
- execution started / finished / failed;
- finding confirmed;
- report generated.

Audit records must not contain secrets.

### Milestone 15 — External Authorized Testing Readiness

Before any real external target is used, require a readiness gate covering:

- valid AuthorizationProfile;
- explicit scope;
- unexpired authorization;
- automation policy understood;
- host allowlist configured;
- rate limit configured;
- dedicated test accounts prepared;
- credential storage hardened;
- GET-only policy functioning;
- redirects disabled;
- timeout and response-size limits functioning;
- kill switch functioning;
- audit logging functioning;
- redaction functioning;
- human approval functioning.

If any required check fails, external execution remains disabled.

## 4. Completion Gates

### Gate A — Local Lab

All core workflows and safety failures are reproducible with pytest/integration tests against a self-controlled lab.

### Gate B — Self-Owned External Server

Run the platform against a publicly reachable API owned by the developer and validate TLS, DNS, policy, rate limiting, authentication, audit, and redaction behavior.

### Gate C — Explicitly Authorized Program

Only after Gate A and Gate B pass should the platform be used in a real authorized bug bounty/SRC environment, and only within the exact program rules and scope.

## 5. Codex Development Workflow

Do not ask Codex to implement Level 3 in one task.

Use:

```text
Milestone
  ↓
Small GitHub Issue / Task
  ↓
Codex implementation
  ↓
pytest / integration tests
  ↓
Self-review and diff review
  ↓
Human review
  ↓
Merge
```

Each task must define:

- context;
- one concrete goal;
- security constraints;
- files to inspect;
- required tests;
- done-when criteria.

A good task changes one logical capability at a time.

## 6. Product Positioning

Preferred description:

> Authorized API Object-Level Authorization Security Testing Platform

or:

> AI-Assisted BOLA / IDOR Security Testing Platform for Authorized API Environments

Core characteristics:

- authorization-aware;
- scope-aware;
- identity-aware;
- resource-aware;
- policy-enforced;
- evidence-driven;
- human-reviewed;
- AI-assisted.
