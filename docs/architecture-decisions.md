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

M13-03 bounded excerpt clarification (Issue #110): the analyzer selects the first matching identifier field using its existing traversal order. When its compact JSON representation fits within 192 characters, the excerpt preserves that literal key exactly, including ordinary `id` and `{resource_type}_id` keys, and replaces the value with `[MATCHED_RESOURCE_IDENTIFIER]`. Existing Resource types can contain characters whose JSON escaping exceeds this bound. Only for that case, the excerpt uses this fixed field label:

```json
{"[MATCHED_RESOURCE_IDENTIFIER_FIELD]":"[MATCHED_RESOURCE_IDENTIFIER]"}
```

The fallback label denotes a matched identifier field whose literal key is omitted; it is not a response key or a truncated key. It contains no original key content, identifier value, siblings, or body data. Baseline and probe independently use the literal form or this fallback. Both forms remain canonical compact one-field JSON in the same immutable typed excerpt with extractor `bola_matched_identifier_field`, version `1`. This representation preserves the existing analysis outcome, confidence, structured evidence, 192-character storage bound, and append-once conflict behavior without changing Resource validation or existing rows.

M13-04 fingerprints record only SHA-256 digests and byte lengths of the exact persisted response strings encoded as UTF-8 (`None` means zero bytes), with no parsing, normalization, redaction, or truncation. They describe byte equality/integrity, not semantic equivalence, similarity, authorization, or a BOLA decision. Fingerprinting is independent of classification: typed metadata is available for non-finding outcomes to check an existing fingerprint on explicit reanalysis, but only POTENTIAL_BOLA may append a fingerprint. Any differing durable fingerprint fails with `finding_evidence_fingerprint_conflict` before evidence or Finding changes, including when changed bodies no longer satisfy the BOLA rule. Each fingerprint links to the exact structured evidence row and is part of the same atomic append as the Finding, structured evidence, and excerpt. Migration and reads do not backfill.

M13-05 similarity consumes only the immutable M13-04 fingerprint value. Comparator `sha256_exact_and_length_ratio`, version `1`, records exact digest equality and an integer byte-length ratio in basis points: both zero lengths yield `10000`; otherwise `min(lengths) * 10000 // max(lengths)`. It reads no response content and defines no normalization, threshold, or semantic equivalence. Neither equality nor the ratio affects classification, confidence, severity, or human review.

Similarity appends once to the exact fingerprint row, under the same savepoint as the Finding, structured evidence, excerpt, and fingerprint. The unique fingerprint FK is the final concurrency authority; differing existing metadata raises `finding_evidence_similarity_conflict` without changing any prior state. The M13-04 fingerprint conflict check remains first when source bodies change. Migration and reads never backfill; successful explicit same-pair reanalysis can append a missing similarity. `GET /findings/{finding_id}/evidence/similarity` returns only the linked row's ID, fingerprint FK, comparator metadata, equality flag, integer ratio, and timestamp. It exposes no digests or content and has no global listing.

M13-06 assigns one immutable, versioned retention-policy binding to each exact `FindingEvidenceRecord`. A frozen five-field policy constant supplies `policy_id = "m13_minimized_finding_evidence"`, `policy_version = "1"`, `retention_mode = "explicit_management_only"`, `automatic_deletion_enabled = false`, and `raw_response_body_retained = false`. These values are universal and independent of Finding severity, confidence or review, response/request content, Target/network mode, identity, Resource ownership, and AI.

M13 Finding evidence is structured, bounded, redacted/minimized by default; raw third-party response bodies are not copied into its tables. Automatic deletion is disabled. No TTL or expiry is claimed or calculated. `explicit_management_only` describes that current behavior and reserves any future deletion for a separate explicit retention-management feature. It does not imply an existing management workflow or deletion API.

The migration deterministically appends one v1 binding per existing structured evidence ID without reading TestRuns, Resources, or content, or changing any prior row. Downgrade removes only the retention-binding table. New POTENTIAL_BOLA analysis includes the binding in the same savepoint as Finding, structured evidence, excerpt, fingerprint, and similarity. The unique structured-evidence FK with `ON DELETE RESTRICT` is the final concurrency authority. Identical retries and reanalysis converge, preserving the original binding ID and timestamp; materially different stored policy fails closed with `finding_evidence_retention_policy_conflict` without mutating prior state or human review.

`GET /findings/{finding_id}/evidence/retention` resolves Finding, exact structured evidence, then its exact binding. It returns only binding ID, structured-evidence FK, the five policy fields, and `bound_at`. Reads do not append; no global listing, mutation/deletion API, scheduler, worker, or cleanup path is provided. This policy governs only M13 Finding evidence. `TestRun.response_body` is source execution data whose lifecycle is out of scope and unchanged. Retention metadata has zero effect on BOLA classification, Finding review, authorization, Scope, execution, or network permission.

M14-01 introduces a pure, bounded assertion-aware BOLA matrix planner over explicit, already-resolved access facts. Relationship and expected access remain independent: the planner never infers owner => allowed or non_owner => denied, and it preserves unusual explicit combinations. Insufficient/conflicting resolution or unspecified expected access yields no candidate. Only the exact auth type `anonymous` selects anonymous candidates; authenticated facts also require an explicit relationship. Supporting assertion IDs are preserved as provenance without choosing a winner.

This slice produces immutable planning candidates only. It is not yet connected to reviewed `EndpointResourceBinding` or TestCase persistence, and it adds no nested, query, or multiple-binding support. The existing owner-based generator and generation API remain unchanged and transitional until later M14 integration.

M14-02 adds a read-only, bounded BOLA matrix preview over an exact selected Endpoint, Resource, and ordered active identities. It copies only current persisted identity auth types, calls the unchanged M12 resolver for each exact Resource/Identity pair at the caller's explicit timezone-aware evaluation time, and passes every resolved fact to the unchanged M14-01 planner. Conflict and insufficient facts remain visible even when they produce no candidates. Structural validation precedes database reads; a clean caller Session is required, autoflush is suppressed, and the caller retains ownership of the read transaction. No truth is cached across calls.

This is a transient preview, not a persisted historical execution snapshot. Evaluation time governs assertion eligibility, not historical identity activity, auth type, or Endpoint metadata. Results are deterministic for the same selected persisted metadata and assertions; later changes can change a later preview. Same-Target validation establishes metadata consistency only, not a reviewed endpoint/resource binding or permission to execute. The service adds no HTTP route, TestCase persistence, or generation API integration; reviewed binding and execution integration remain separate future work.

M14-03 adds exact reviewed slot selection for one explicitly requested Endpoint and `EndpointResourceBinding`. The chosen row must belong to that Endpoint and currently be confirmed, regardless of confidence or provenance. Its exact path/query selector is revalidated with the existing parameter-name grammar and checked against the current flat path template or bounded query declarations. Ambiguous or malformed declarations fail closed; body bindings remain unsupported. The frozen descriptor contains only the two IDs, location, selector, and review state. It supplies no Resource association, ownership/access truth, request construction, or execution authority.

Selection uses minimal exact reads in a clean caller Session with autoflush suppressed and no cross-call cache. It represents transient current metadata, not a persisted immutable review revision, historical snapshot, or execution-time approval; changes after return remain possible, and future consumers must establish their own reviewed planning/execution boundary. Binding/preview composition, multiple-parameter assignment, and persistence remain separate future work. M14-03 adds no API or legacy generation integration and leaves all downstream execution and public-runtime restrictions unchanged.

M14-04 composes one exact Endpoint, 1–32 ordered explicit `(binding_id, resource_id)` proposals, ordered identity IDs, and an explicit timezone-aware evaluation time into immutable grouped slot previews. Structural validation precedes dependency work; both identity count and total requested assignment × identity cells are capped at 512, counting skipped facts and repeated Resources. All exact M14-03 bindings are validated before M14-02 access previews begin. Duplicate binding IDs or exact `(location, selector)` slots fail closed; the same name in path and query identifies distinct slots. Each distinct Resource is previewed once per call, with the same immutable preview reused for repeated assignments. There is no cross-call cache or partial result on failure.

Nested path positions and mixed path/query slots retain independently resolved facts and exact supporting provenance. Assignments are caller proposals: confirmed positions do not prove that a Resource belongs in a slot or that a task belongs to a proposed parent project. Unselected positions remain unassigned; no overall access verdict, complete rendered request, or executable authority is supplied. The composer performs no direct SQL and uses the existing services in the caller's clean read transaction with autoflush suppressed. Results are transient current metadata, not transactionally frozen historical execution snapshots; evaluation time governs assertion eligibility, not historical binding reviews or identity activity. Offline entry-point and consolidated matrix acceptance work remain later slices. This composition adds no API, persistence, or execution integration and does not change public-runtime restrictions.

M14-05 exposes exactly one bounded read-only planning operation, `POST /api/bola-matrix/preview`, around the unchanged M14-04 composer. POST transports explicit proposals to this platform only and never contacts a Target. Strict JSON IDs, explicit RFC3339 assertion time, ordered 1–32 assignments and 0–512 identities, and the 512-cell aggregate bound are validated before Session work. Actual input is capped at 65,536 bytes before parsing, regardless of Content-Length; duplicate keys, invalid encodings, non-finite values, extras, and query overrides fail with bounded sanitized errors. Async transport parsing precedes the normal synchronous/threadpool composer call in the request-owned Session.

Typed explicit output allowlists preserve independent relationship/access facts, candidate omissions, and exact assertion provenance. The complete UTF-8 serialization is capped at 4,194,304 bytes before sending success, with no truncation or partial response and `Cache-Control: no-store`. There are no business writes, new composition rules, credentials, outbound calls, or persistent preview state. Assignments remain proposals, and results remain transient current metadata without Resource-to-slot approval, parent-child membership proof, executable plans, or network permission. The trusted-operator/self-hosted deployment assumptions and all public-runtime restrictions remain unchanged. See [the local API example and contract](bola-matrix-preview-api.md); consolidated local matrix acceptance/compatibility closeout remains separate work.

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
