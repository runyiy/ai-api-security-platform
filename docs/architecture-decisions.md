# Approved Level 3 Architecture Decisions

This document records the approved architecture constraints for the Level 3 product. It is normative together with `security-model.md`. The roadmap must be interpreted through these constraints.

## Product and deployment boundary

Version 1 is a single-operator, self-hosted/local product. It is not a multi-tenant SaaS, so tenant, workspace, organization-membership, and platform-user RBAC foundations are intentionally deferred.

The operator's control of the deployment does not authorize testing arbitrary systems:

> trusted operator != authorization to test arbitrary targets

Execution-capable v1 assumes one trusted operator and local/private lab usage. The bounded `execution_topology` setting defaults to `single_process`. A trusted operator may explicitly select `multi_process` for local/private exact-plan execution across multiple FastAPI processes. Public SRC or bug-bounty execution remains unsupported, is not an approved deployment mode, and is operationally prohibited. `external_public_authorized` remains blocked at runtime. No real public SRC request may be intentionally executed until the separate Public SRC Readiness controls are implemented and satisfied.

Under explicit `multi_process` topology, only the immutable exact ExecutionPlan execute/cancel routes are supported for execution. The legacy direct TestCase execution endpoint is unavailable because it is outside the shared M8 ownership model; it remains available under the default `single_process` topology. PostgreSQL is the mandatory shared dependency for rate reservations, claims and fencing, canonical results, recovery, cancellation, kill switches, and network concurrency. Worker counts are never auto-detected. Redis is not required. KMS, Vault, and other external secret managers may be added later as optional credential-provider adapters.

## Authorization and execution

A Target may have historical, future, active, superseded, expired, or revoked authorization revisions. Every ExecutionPlan and execution must select exactly one explicit authorization revision. Permissions from separate grants or revisions are never implicitly unioned.

This is the required target-state invariant and is mandatory before public execution. Until Milestone 4 is complete, the local/private-lab MVP uses the current mutable AuthorizationProfile as a transitional authorization object and does not claim immutable revision-level historical reproducibility.

Scope is an additional restriction:

```text
selected authorization revision
        INTERSECT
active Target Scope
        INTERSECT
platform safety policy
```

Scope and platform policy may narrow authorization but never widen it.

Human approval applies to an immutable, bounded ExecutionPlan and exact action set, preferably identified by a stable plan digest. A material mutation, including a changed Target, URL, method, identity, credential binding, authorization revision, resource set, or request count, invalidates approval.

Approval never bypasses policy. Every approved action must still pass immediate pre-network authorization, Scope, Target, credential, rate, kill-switch, and network-safety validation.

## Network modes

Execution has two explicit network modes:

- `private/local`: local, lab, or self-owned private destinations under an explicit private-network policy;
- `external/public-authorized`: explicitly authorized public SRC or bug bounty destinations.

These modes must never be inferred ambiguously or used as fallbacks for one another.

Before public execution is enabled, all outbound requests must pass through a centralized network boundary that validates the canonical URL, DNS results, IPv4 and IPv6 address classes, ports, selected network mode, and actual connection destination. Public mode must fail closed for loopback/localhost, private ranges, link-local destinations, metadata services, and other prohibited address classes. DNS rebinding and policy-to-connect time-of-check/time-of-use gaps must be addressed.

## Identity and credentials

`TestIdentity` answers "who is acting?" A credential answers "how is that identity authenticated now?" The existing `TestIdentity.credentials` JSONB field is transitional.

The minimal direction is:

```text
TestIdentity
    -> CredentialBinding
    -> credential source/provider
    -> short-lived AuthenticationContext
```

The architecture must eventually support application-stored encrypted credentials, externally managed references, and dynamic/expiring sessions. Version 1 may implement only encrypted PostgreSQL storage. `CredentialSource` may initially be a small domain/service abstraction; a polymorphic plugin framework is not required.

OpenAPI retrieval is anonymous by default. Authenticated retrieval may later use an explicitly selected documentation CredentialBinding through the same constrained AuthenticationContext boundary. The scanner must never select a TestIdentity or credential automatically.

## Expected access and evidence

Resource ownership and expected access are assertions with provenance, not timeless facts. Supported provenance categories include, in descending trust order:

1. `human_verified`
2. `target_fixture`
3. `observed_baseline`
4. `inferred_candidate`

Assertions require confidence, verification state, and relevant observed/asserted timestamps. Observed access never silently proves exclusive ownership or expected denial for every other identity.

Evidence retention defaults to data minimization. Full third-party response bodies are not the intended permanent evidence model. Persisted evidence should be structured, bounded, redacted, provenance-bearing, and limited to what is materially necessary to explain a finding. Secrets must never be persisted as evidence.

## Wildcard asset enrollment

Wildcard program domains are discovery and enrollment rules, not execution authorization:

```text
wildcard rule
    -> candidate asset
    -> inclusion/exclusion validation
    -> DNS/network validation
    -> explicit Target enrollment
    -> Scope and execution planning
```

Every executable hostname must be an explicit Target.

> wildcard match != permission to execute

## Superseded M3-01 issue

GitHub Issue #23, "M3-01: add encrypted credential secret storage primitive," is conceptually superseded and must not be implemented as written. Its authenticated-encryption requirements remain useful, but an orphan `CredentialSecret` table is not the accepted credential-domain foundation.

The replacement sequence is:

1. M3-01 — CredentialBinding domain foundation;
2. M3-02 — encrypted PostgreSQL stored-secret credential provider;
3. M3-03 — migrate bearer AuthenticationContext off plaintext `TestIdentity.credentials`.

The old M3-01 implementation branch must not be reused as the new M3-01 branch.

M3-01 contains only non-sensitive credential metadata. It explicitly excludes encryption, ciphertext storage, bearer-token migration, AuthenticationContext changes, additional authentication mechanisms, dynamic sessions, Vault/KMS, ExecutionPlan, AuthorizationRevision, and NetworkGateway.
