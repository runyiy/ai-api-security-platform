# RA-05/W2 — concrete preparation and coordination contract

**v0.1.1 · DOCUMENTATION_ONLY / PENDING_INDEPENDENT_REVIEW · implementation not started**

Base: `e953ee08cb32e0ba9de0f3d196e7b5e82b9fe727`; branch: `codex/ra-05-w2-implementation-contract`. Clean HEAD/local main, actual remote main, origin and branch absence were verified before branching. The user handoff records PR #151 integration of reviewed `dce6d4d4bc49a5284b427d4e428dd901be6c50c7`, with PR/exact-main CI each passing 3798 tests on their first attempts. Those runs were not rerun or independently audited for this document.

The [W2 adoption record](research-ai-budget-contract.md#w2-design-adoption-record) governs B1–B8. This companion supplies their explicit pre-code details; it does not reopen their recommendations. **D** below means a concrete implementation detail awaiting independent review. **N1**, the separately identified fake observer mechanism in §6, is a new material choice, not a decision attributed to that adoption. There is no W2 implementation authorization until this concrete contract passes independent review and N1 is resolved. All account/credentials, data/retention/egress, spending, deployment and live-provider gates remain separate and closed.

## 1. Boundaries and current dependencies

[DATA](research-assistant-adr-decisions.md#data-后续决定记录ra-02w1), [K1–K4](research-knowledge-contract.md#k1k4-后续采纳记录ra-03w2), [INTENT](research-intent-contract.md#i1i7-后续采纳与-w1-实施记录), [W1 design](research-ai-provider-contract.md#w1-design-adoption-and-implementation-record) and [W1 implementation](research-ai-provider-implementation.md) retain their authority. Nothing here creates a plan, access assertion, health proof, Finding, Target request, secret store, worker or cache. The input ceiling remains 4096 and total output ceiling 1024 including reasoning; every call reserves at least 5120 tokens / 22528 microusd under the inherited rate snapshot. These are fake arithmetic quantities, not approved total task/account caps.

The concrete path is a synchronous internal library, with no new route. A **case** is an independently authored synthetic question in an immutable task manifest. Its question, keywords, candidate selection, actor modes, gaps and ordering must not be derived from project observations, facts or outcomes. Project lifecycle can prohibit the entire operation; it cannot choose a different provider-visible synthetic example. A rejected local eligibility check yields only a local fixed error, never a model gap. Caller text cannot supply decisions, time, account identity or a trusted manifest.

Source reads below are code-context observations at this base, not imports. In particular, [knowledge retrieval](../backend/app/services/research_knowledge.py) `retrieve` reads subject/facts and project cards, emits audits and uses `_applicable` with current facts. Forwarding its response, or calling its fact qualification on fabricated subject data, is prohibited. [W3 `qualified`](../backend/app/services/research_rule_validation.py) verifies exact evidence by rerunning its current validator; `_content` only accepts `category='rule'`. Consequently a mechanism-only card cannot meet this path's W3 proof requirement today. It remains unsupported; this contract neither opens a mechanism publication bypass nor changes evaluator cases/hashes.

## 2. B1 finite necessity and projection contract — D

### 2.1 Fixed table and precedence

Table version **`ra-w2-necessity/1`** has exactly four entries. Trusted `QuestionManifest` pins the table entry, the listed template version, exact source bindings for each dependency role and, where applicable, a synthetic candidate manifest. No prefix/semantic matching, model-written entry or implicit latest. Absent entries, unknown versions or wrong dependency kinds return `UNSUPPORTED`.

Local templates are complete constant strings:

- `access-basis/1`: “Ownership alone does not establish access. Supply independent access facts before execution.”
- `sharing-rule/1`: “Review the referenced sharing rule and its limitations. No project conclusion.”
- `single-priority/1`: “One eligible synthetic candidate remains for human review. Not executed.”
- `compare-priority/1`: “Review priority is unresolved between the declared synthetic candidates. Not executed.”

`explicit_access_facts` below is the exact existing RA-02 rule name; its intake `SyntheticReference` is only an unverified declaration. A separate trusted, versioned local-policy binding is required. It does not turn an intake reference into approval. R is an exact `reusable_synthetic` rule whose `ra-knowledge/1` claim is **`Sharing may allow non-owner access.`**, shape is `single_resource_path_get_json_object`, `requires_facts=true`, actor applicability covers every declared candidate, and whose complete approved projection is P below. No other claim substitutes for R in v1.

| Entry / question kind | Exact dependencies and trusted inputs | Rules step → one retrieval step → final deterministic outcome |
| --- | --- | --- |
| Q1 `explain_access_basis` | `explicit_access_facts` + `access-basis/1`; independently reviewed synthetic question; no candidates | Current policy binding suffices: `RULES_SUFFICIENT`, template only, no retrieval or model. Missing local policy proof: `NEEDS_INPUT/POLICY_PROOF_MISSING`. |
| Q2 `explain_sharing_rule` | R + P + `sharing-rule/1`; no candidates; manifest pins R even if not initially loaded | If the selected exact R/P is already qualified: `RULES_SUFFICIENT`. Otherwise one bounded exact-R retrieval; if qualified: `RETRIEVAL_SUFFICIENT`. Empty eligible result: `NO_ELIGIBLE_RULE`. Never escalates. |
| Q3 `prioritize_single` | R/P + `single-priority/1`; exactly one independently reviewed, complete synthetic candidate and its allowed pair with R | Selected qualified R/P suffices; otherwise one retrieval. Once qualified, `RULES_SUFFICIENT` or `RETRIEVAL_SUFFICIENT` by origin, with the one candidate reference. Zero model calls. |
| Q4 `prioritize_pair` | R/P + `compare-priority/1`; exactly two independently reviewed, complete synthetic candidates; exactly two allowed pairs, both with R; distinct candidate source versions | The rules template establishes no priority order. One retrieval is required even if R was initially selected, to qualify the pinned comparison context; eligible R/P still leaves the predeclared priority question unresolved. `PRIORITY_UNRESOLVED` is the only model-eligible outcome. Missing model/data/account/budget decisions: `AWAITING_APPROVAL`; no eligible R: `NO_ELIGIBLE_RULE`. Retrieval rank/ties never determine necessity. |

Q4 sends only W1 `task='prioritize_review'`, with those two candidates, R's one complete projection and **empty gaps**. This is advisory comparison, not an assertion that either synthetic actor may access an object. Valid W1 refusal and other permitted W1 suggestion types retain their existing interpretation; W2 never asks another model to repair them. The table deliberately uses less than W1's maximum 8 candidates / 4 rules / 8 gaps. The broader schema remains compatible; v1 table rejects extra candidates/dependencies instead of silently discarding them. A second case call needs a different predeclared question ID, independently qualified from its original manifest, never previous model output. The inherited two allocated calls/case, 60s cumulative call wall time, 30s/call, concurrency one and start spacing limits still apply.

Evaluation order is: strict version/schema/bounds → trusted ownership and lifecycle → independent authorship and policy/dependency consistency → missing-input checks → rules → at most one retrieval → completed-operation requalification → model permissions/reservation. Invalid/foreign/withdrawn selected references fail the whole operation; they are not a search miss. Missing inputs are a sorted, unique subset of the following **local** codes (maximum 8): `POLICY_PROOF_MISSING`, `SOURCE_BINDING_MISSING`, `PROJECTION_APPROVAL_MISSING`, `SYNTHETIC_REVIEW_MISSING`, `ACTOR_MODE_MISSING`, `INDEPENDENT_FACTS_MISSING`, `INDEPENDENT_EXPECTATION_MISSING`, `SHAPE_MISSING`. Unknown actor/shape or an incomplete independent synthetic scenario yields `NEEDS_INPUT`; an explicitly unsupported shape yields `UNSUPPORTED`. Mapping/session/real access questions have no table entry and cannot escalate. No missing-input code is converted into a provider gap.

### 2.2 Internal retrieval seam

**`retrieve_projected_v1(ReadContext, RetrievalRequest) -> RetrievalResult`**, internal only, replaces no public `QueryInput`, `QueryRead` or route. It shares audited qualification/ranking primitives, not the existing `retrieve` orchestration:

1. Validate exact consumer project/context/version, current DATA eligibility and recovery control through [context](../backend/app/services/research_context.py) and [observation](../backend/app/services/research_observation.py) ownership/lifecycle checks. Check `_control` and `_eligible_context` with fresh completed-operation time. Do not call `subject.read`, `_shape_gaps`, `_context_deadlines` or fact-dependent `_applicable` for provider selection. Necessary local restrictions return a Boolean denial outside the model-visible path; no subject/fact identifier enters this seam.
2. Under the catalog/context locks in §5, restrict the database predicate **before content loading/ranking** to `KV.scope='reusable_synthetic'` and the finite manifest selection. Map ExactSource into the current ExactRef by explicitly reconstructing only its four fields; the new record format is never passed into the old strict schema. Resolve selected exact refs without exposing foreign existence; reject duplicates instead of the public query's deduplication. For lineage closure, scan only shared synthetic metadata, bounded by 256 rows (257 fails); never load project bodies. Verify `_exact`, `Content` schema/content digest, exact purpose/category, every ancestor's immutable binding and no contamination/disable/withdrawal, using `_lineage_eligible` and explicit withdrawal checks for the selected row. Preserve the existing ≤128 shared-version capacity; no alternate unbounded catalog.
3. Every source and ancestor must be independently `synthetic_authored`. Qualify review/reuse/publication with `_publication` **and explicitly require** `operator_recorded`, `local_operator`, non-null genuine `ValidationRef`, and `qualified` against current validator and half-open proof windows. `_publication` alone admits test-only records and is insufficient. Shared synthetic consumption does not borrow the author's exited Target/project authorization; its independent synthetic lineage and current publication/decision provenance must remain valid. No automatic validation or publication is performed by retrieval.
4. Require a separate exact egress/projection binding (record below), with seven W1 decision roles, source/projection digest, content review and finite expiry. `Content.claim` alone omits applicability/examples/counterexamples and is not a complete projection. Unsupported mechanism/category or projection mismatch fails; no truncation, generative summarization or best-effort salvage.
5. For eligible rows only, use existing `_rank`: distinct lowercase `[a-z]+` claim words matching keywords + twice matching tags. Sort by `(-score, scope, knowledge_id, version, digest)`, retain positive scores only. `RetrievalRequest` pins purpose `offline_context_explanation`, keywords ≤8 ×32 ASCII lowercase, tags from the existing eight-value vocabulary ≤8, exact selected refs 1–32, `top_k=4`; Q2–Q4 fix keywords `["sharing"]`, tags `["sharing"]` and selection `[R]`. Table dependencies do not expand with top-k. Scan 256/257, selected 32/33, top-k 4/5 and the inherited byte limits are distinct checks.
6. Re-read selected exact sources/projections and all decision windows after ranking/encoding using completed-lookup wall/monotonic time; validate again after rendering, waiting, before secret, under first-write guard and at display consumption. Reject clock rollback, expiry equality or any changed generation/configuration/decision/key reference. Audits contain fixed code and scoped IDs only; no excluded counts, titles, scores, private digests or raw text. Any audit failure rejects the operation.

This is a required internal extraction, **not currently implemented**. No existing eligibility check may be lost merely because its function is unsuitable for synthetic-only input. Public retrieval retains its subject/fact/permission checks and outputs. The new seam needs its own genuine-publication and independent synthetic provenance tests; a fake certificate is not a production eligibility issuer.

### 2.3 Projection and independently authored examples

P's complete literal text is:

> Sharing may allow non-owner access. This synthetic rule requires independent access facts and a complete single-resource GET JSON-object scenario. A separately authorized sharing example can permit access; a non-sharing counterexample can deny it. Neither ownership nor this rule proves a project's access policy. Human review is required; nothing was executed.

P has schema `ra-w2-projection/1` and exact fields `{format, template, claim, applicability_codes}`; `template='sharing-complete/1'`; codes in fixed order `['single_resource_path_get_json_object','anonymous','bearer','independent_facts_required']`. P requires R to support both actors; a narrower actor card needs a different reviewed table/projection version, not automatic rewriting. P is independently authored review material, not an existing published card or actual approval. Its source binding includes R's exact content digest and all example/counterexample references; reviewers must verify that the literal covers the actual selected R's limitations. Any mismatch denies use even if the claim literal matches. Only P.claim and its codes enter the W1 rule item; P metadata stays local. **W1 compatibility:** `ProjectionBinding.projection_digest` is exactly SHA256 of W1 canonical `{claim, applicability_codes}`, without `ref`, format, template or domain prefix, as `validate_registry` requires. `template_digest` separately hashes the entire P record with domain `ra-w2-projection/1\n`. Candidate W1 projection digests similarly cover only `{shape, actor_mode}`. SourceBinding uses exact source ID/version/digest, category `rule` for R and `synthetic_catalog` for candidates, synthetic scope/data class, content_review=`independent_synthetic_reviewed`, the seven verified decision refs and half-open windows. Allocate one fresh k handle for R and two fresh c handles for the distinct candidate sources; both allowed pairs point to that same k handle. No source is duplicated into multiple handles.

The following strict record is a **table-test fixture**, not a request/receipt or authority. `qualified_fixture` means the document-check oracle assumes independently supplied qualified bindings; it never creates a validation/publication record. Real PostgreSQL tests must obtain genuine W3 evidence and separate publication using existing services; synthetic-test-only/NOT_RUN records still fail the new seam. Candidate shapes below are fixed independently authored scenarios, not renamed project material.

```json
{
  "format": "ra-w2-table-examples/1",
  "measurement_kind": "synthetic",
  "operational_approval": false,
  "cases": [
    {"id":"e1","entry":"Q1","binding":"qualified_fixture","candidates":0,"initial_rule":true,"missing":[],"outcome":"RULES_SUFFICIENT","retrievals":0,"model_eligible":false},
    {"id":"e2","entry":"Q2","binding":"qualified_fixture","candidates":0,"initial_rule":false,"missing":[],"outcome":"RETRIEVAL_SUFFICIENT","retrievals":1,"model_eligible":false},
    {"id":"e3","entry":"Q3","binding":"qualified_fixture","candidates":1,"initial_rule":true,"missing":[],"outcome":"RULES_SUFFICIENT","retrievals":0,"model_eligible":false},
    {"id":"e4","entry":"Q4","binding":"qualified_fixture","candidates":2,"initial_rule":true,"missing":[],"outcome":"PRIORITY_UNRESOLVED","retrievals":1,"model_eligible":true},
    {"id":"e5","entry":"Q4","binding":"qualified_fixture","candidates":2,"initial_rule":false,"missing":["INDEPENDENT_EXPECTATION_MISSING"],"outcome":"NEEDS_INPUT","retrievals":0,"model_eligible":false},
    {"id":"e6","entry":"Q4","binding":"foreign","candidates":2,"initial_rule":true,"missing":[],"outcome":"SOURCE_UNAVAILABLE","retrievals":0,"model_eligible":false},
    {"id":"e7","entry":"unknown","binding":"qualified_fixture","candidates":2,"initial_rule":true,"missing":[],"outcome":"UNSUPPORTED","retrievals":0,"model_eligible":false},
    {"id":"e8","entry":"Q2","binding":"no_match","candidates":0,"initial_rule":false,"missing":[],"outcome":"NO_ELIGIBLE_RULE","retrievals":1,"model_eligible":false}
  ]
}
```

These examples stop at the necessity stage, before operational permission/reservation evaluation. E4 would produce `AWAITING_APPROVAL` at that later gate with this fixture, not a send. `initial_rule` is a trusted manifest Boolean selecting the initial exact-rule check; it is not cached eligibility. E4's two synthetic candidates are anonymous and bearer instances of the fixed shape, independently reviewed with complete synthetic prerequisites. `model_eligible=true` indicates necessity only; this fixture's `operational_approval=false` cannot reserve or send. Additions of private-derived choices, third candidates, duplicate/foreign refs, mechanism-only cards, missing projection proof or model-written table entries must reject before provider preparation. Q2's rule claim contributes one keyword and one tag match, score **3**; ordering never uses candidate facts.

## 3. B8 versioned records and interfaces — D

### 3.1 Common encoding and bounded records

Port family **`ra-w2-ports/1`**; no public endpoint. Each serialized record has exact `format='ra-w2-<record-name>/1'` plus the fields below; names use lowercase kebab form of the record name. Unknown versions/fields, omitted required fields, duplicate keys, BOM, invalid UTF-8/surrogates, nonfinite/fractional counts, booleans-as-integers, trailing content, recursion or capacity excess reject as a whole. No coercion, repair, truncation or automatic upgrade. Use W1 codec semantics for value-node counting (containers included, keys excluded), with record-specific limits checked **before** allocation: metadata records ≤4096 bytes, depth ≤6, nodes ≤512; composite preparation/authority records ≤32768 bytes, depth ≤8, nodes ≤4096. Embedded W1 input/output retain their tighter 16384/8192, depth5/node1024 caps. Full rendered body retains 32768 bytes. All lengths count canonical UTF-8 bytes.

Notation: `Id` = 1–64 ASCII `[A-Za-z0-9_-]`; `Label` = 1–96 ASCII `[A-Za-z0-9_./-]` for versioned templates/rates/mappings; `Hash` = 64 lowercase hex; `N` = strict nonnegative int ≤2^63−1; `Gen` = 1–2147483647; `Ver` = 1–10000; `Time` = aware UTC serialized as `YYYY-MM-DDTHH:MM:SS.ffffffZ`; `Opt[T]` = required field, either T or null. Arrays retain declared order, reject duplicates, and reject excess before sorting/encoding. Every record is immutable; updates append events. Hash canonicalization is sorted-key compact UTF-8 with no floats; W2 core domain `ra-ai-budget/1\n`. W1 digest serialization is unchanged, including its existing timestamp `isoformat()` and sorted allowed pairs.

| Record | Exact fields after format; constraints |
| --- | --- |
| `Scope` | `task_id:Id, case_id:Id, project_ref:Id, context_ref:Id, context_version:Ver, context_generation:Gen, account_ref:Id, deployment_ref:Id, policy_id:Id, policy_version:Ver`; trusted mapping of project/context opaque refs to current repository integer IDs, no caller-chosen cross-project mapping |
| `ReadContext` | `scope:Scope, process_epoch:Id, deadline_at:Time, mono_deadline_ns:N, cancellation_id:Id`; composite, frozen, operation deadline ≤30s and task/source expiry; trusted clocks only |
| `RecoveryContext` | `read:ReadContext, key:ReservationKey, recovery_decision_id:Id, owner_id:Id, owner_generation:Gen`; composite; no send permission, explicitly authorized newer recovery generation |
| `RunContext` | `scope:Scope, call_ref:q_[0-9a-f]{32}, attempt:1, owner_id:Id, owner_generation:Gen, process_epoch:Id, started_at:Time, deadline_at:Time, mono_start_ns:N, mono_deadline_ns:N, cancellation_id:Id`; composite, ephemeral. Deadline ≤30s and earlier task/case/source/receipt expiry; monotonic values valid only in this process epoch |
| `ExactSource` | `scope:'reusable_synthetic', knowledge_id:knowledge-[1-9][0-9]{0,5}, version:Ver, digest:Hash`; same four fields as current `ExactRef`, narrower scope |
| `ProjectionBinding` | `source:ExactSource, projection_digest:Hash, template_digest:Hash, template:'sharing-complete/1', decision_refs:Id[7], validation_id:positive N, validation_digest:Hash, review_event_id:positive N, reuse_event_id:positive N, publication_event_id:positive N, valid_from:Time, expires_at:Time`; seven distinct refs in W1 possession/use/reuse/review/validation/publish/egress order; real proof equality verified, not inferred from ID presence |
| `CandidateRef` | `source_id:Id, source_version:Ver, source_digest:Hash`; exact immutable synthetic registry reference, no latest lookup |
| `SyntheticCandidateCertificate` | `source_id:Id, source_version:Ver, source_digest:Hash, shape:'single_resource_path_get_json_object', actor_mode:'anonymous'/'bearer', scenario_manifest_digest:Hash, missing:MissingCode[0..8], decision_refs:Id[7], valid_from:Time, expires_at:Time`; complete independent authored scenario bound locally, no observation/Target/fact input. Registry validates seven distinct decision roles and exact content review; references never self-approve |
| `QuestionManifest` | `scope:Scope, question_id:Id, entry:Q1..Q4, policy_version:'ra-w2-necessity/1', template:Label, initial_rule:bool, local_policy_ref:Opt[Id], rule:Opt[ExactSource], projection:Opt[ProjectionBinding], candidate_refs:CandidateRef[0..2], synthetic_review_refs:Id[0..2], missing:MissingCode[0..8], valid_from:Time, expires_at:Time`; composite; Q-specific cardinalities/dependencies as §2, exact content/version certificates resolved by trusted registry; no free text |
| `RetrievalRequest` | `question_digest:Hash, purpose:'offline_context_explanation', keywords:lowercase-word[0..8], tags:existing-Tag[0..8], selected:ExactSource[1..32], top_k:4`; composite; Q2–Q4 values pinned as above |
| `RetrievalResult` | `question_digest:Hash, bindings:ProjectionBinding[0..4], projection_refs:Hash[0..4], scores:N[0..4], evaluated_at:Time, expires_at:Time, audit_id:Id`; composite; parallel array lengths equal, scores1..24, order fixed; empty result still has finite request/context expiry |
| `Preparation` | `question_digest:Hash, state:PreparationState, missing:MissingCode[0..8], template:Opt[Label], references:Id[0..4], prepared_ref:Opt[Id], core_digest:Opt[Hash], evaluated_at:Time, expires_at:Time`; only unresolved-and-qualified preparation may contain `prepared_ref/core_digest`; ephemeral registry resolves exact `PreparedProposal`, `Configuration`, body digest, source bindings. The serialized record never contains raw context/body |
| `BindingCore` | `scope:Scope, question_id:Id, question_digest:Hash, call_ref:q_[0-9a-f]{32}, attempt:1, w1_binding_digest:Hash, payload_digest:Hash, body_digest:Hash, registry_digest:Hash, configuration_digest:Hash, config_revision:Id, secret_ref:Id, secret_version:Id, necessity_version:'ra-w2-necessity/1', retrieval_version:'ra-w2-retrieval/1', projections:ProjectionBinding[0..4], candidates:SyntheticCandidateCertificate[0..2], prompt_digest:Hash, schema_digest:Hash, model:'gpt-5.6-terra', profile:'ra-openai-responses/1', destination:'https://api.openai.com:443/v1/responses', method:'POST', reasoning:'low', input_limit:4096, output_limit:1024, reserved_tokens:N, reserved_microusd:N, rate_card:Label, usage_mapping:Label, currency:'USD', token_bound_evidence_ref:Id, measurement_kind:'synthetic', valid_from:Time, expires_at:Time`; composite, no own hash. Fixed no-retry/tool/cache/stream/fallback settings additionally bound by exact configuration/profile/body hashes |
| `ReservationKey` | `scope:Scope, call_ref:q_[0-9a-f]{32}, attempt:1, core_digest:Hash`; composite; complete key required for every lookup, not call ID alone |
| `Reservation` | `key:ReservationKey, reservation_id:Id, owner_id:Id, owner_generation:Gen, input_limit:4096, output_limit:1024, reserved_tokens:N, reserved_microusd:N, currency:'USD', rate_card:Label, usage_mapping:Label, expires_at:Time, measurement_kind:'synthetic'`; composite; reserves ≥5120/22528, exactly equal immutable core, no live receipt |
| `PreparationAuthorityRead` | `read:ReadContext, question_digest:Hash, stage:'before_rules'/'before_retrieval'/'after_retrieval'/'after_render'`; composite, no call/reservation exists yet |
| `PreparationAuthorityView` | `scope:Scope, question_digest:Hash, registry_revision:Gen, policy_binding_ref:Opt[Id], projections:ProjectionBinding[0..4], candidates:SyntheticCandidateCertificate[0..2], missing:MissingCode[0..8], valid_from:Time, expires_at:Time, observed_cancel_generation:Gen`; composite; source/decision proof remains mandatory for any present binding, missing proof never replaced with a successful Boolean |
| `AuthorityRead` | `run:RunContext, key:ReservationKey, stage:Stage, expected_w1_digest:Hash, expected_config_revision:Id`; composite; authority stages are all W1 eligibility stages in §4 plus `complete`; preparation uses its separate record/port |
| `AuthorityView` | `key:ReservationKey, owner_generation:Gen, snapshot_ref:Id, snapshot_digest:Hash, valid_from:Time, expires_at:Time, observed_cancel_generation:Gen`; composite; ephemeral read-only handle to exact W1 `AuthorizationSnapshot`, no success Boolean substituting for its validation |
| `Admission` | `key:ReservationKey, admission_id:Id, owner_generation:Gen, admitted_at:Time, expires_at:Time`; composite; one current account/deployment slot, one-second or stricter start spacing |
| `AcceptanceTicket` | `ticket_id:Id, key_digest:Hash, deployment_ref:Id, authority_epoch:Id, acceptance_generation:Gen, owner_generation:Gen, body_digest:Hash, expires_at:Time`; issued before final authority qualification; one use to issue one permit, never retagged after invalidation |
| `InvalidationContext` | `deployment_ref:Id, writer_id:Id, process_epoch:Id, deadline_at:Time, mono_deadline_ns:N, cancellation_id:Id`; trusted internal operation context, ≤30s; no invented task/call scope for existing lifecycle writers |
| `InvalidationRequest` | `operation_id:Id, operation_digest:Hash, deployment_ref:Id, writer_id:Id, action:'INVALIDATE_DEPLOYMENT', reason:'LIFECYCLE'/'CONFIGURATION'/'CANCELLATION'/'RECOVERY', deadline_at:Time`; ≤4096 bytes; operation digest binds the exact planned mutation and dependency references, no raw content; scope is always the entire deployment |
| `InvalidationAck` | `operation_id:Id, operation_digest:Hash, deployment_ref:Id, authority_epoch:Id, acceptance_generation:Gen, barrier_sequence:positive N, state:'CLOSED', disposition:'PENDING'/'COMMITTED'/'ROLLED_BACK'/'UNKNOWN', event_digest:Hash`; historical durable barrier receipt, not proof of zero usage or of a committed database mutation |
| `InvalidationResolution` | `operation_id:Id, operation_digest:Hash, deployment_ref:Id, barrier_digest:Hash, database_outcome:'COMMITTED'/'ROLLED_BACK'/'UNKNOWN', transaction_evidence_ref:Opt[Id]`; terminal outcomes require independently verified transaction evidence, UNKNOWN never permits reopening |
| `ReopenRequest` | `deployment_ref:Id, recovery_decision_id:Id, expected_authority_epoch:Id, expected_acceptance_generation:Gen, pending_set_digest:Hash, transaction_evidence_digest:Hash, qualification_digest:Hash, stream_closure_evidence_digest:Hash`; ≤4096 bytes; exact trusted evidence references, not caller assertions or permission to replay |
| `GateAck` | `deployment_ref:Id, authority_epoch:Id, acceptance_generation:Gen, barrier_sequence:positive N, state:'OPEN', event_digest:Hash`; acknowledges only deployment gate state, never source/task permission or old permit validity |
| `SendPermit` | `key:ReservationKey, permit_id:Id, admission_id:Id, owner_generation:Gen, guard_epoch:Id, authority_epoch:Id, acceptance_generation:Gen, ticket_id:Id, body_digest:Hash, marker_event_id:Id, observer_epoch:Id, expires_at:Time`; composite; one use, transient, no bearer secret or raw HTTP digest |
| `Observation` | `key_digest:Hash, call_ref:q_[0-9a-f]{32}, event_id:Id, observer_epoch:Id, authority_epoch:Id, acceptance_generation:Gen, sequence:positive N, owner_generation:Gen, kind:ObservationKind, permit_id:Opt[Id], body_digest:Hash, observed_at:Time, terminal:Terminal, usage:UsageView, evidence_digest:Hash`; ≤4096 bytes. No provider request ID in this fake-only version; future live correlation needs a versioned extension |
| `UsageView` | `state:'known'/'unknown'/'invalid', input_tokens:Opt[N], output_tokens:Opt[N], cached_input_tokens:Opt[N], cache_write_tokens:Opt[N], reasoning_tokens:Opt[N], total_tokens:Opt[N], mapping:Label`; W1 semantics, known requires I/O/C/W/total; R may be null. Unknown/invalid have all null counts; independently known partial lower bounds belong in accounting events, not invented final usage |
| `Settlement` | `key_digest:Hash, settlement_id:Id, final_event_id:Id, revision:Gen, state:'ZERO'/'KNOWN'/'UNKNOWN'/'CONFLICT', settled_tokens:N, settled_microusd:N, held_tokens:N, held_microusd:N, refund_tokens:N, refund_microusd:N, overrun_tokens:N, overrun_microusd:N, accounting_digest:Hash`; cumulative totals plus last settlement delta's refund/overrun; no clamping known actuals |
| `Completion` | `key_digest:Hash, completion_id:Id, outcome_code:Opt[W1Code], delivery:'not_sent'/'unknown'/'responded', usage:UsageView, settlement_ref:Opt[Id], display_state:'SUPPRESSED'/'ELIGIBLE_NOW', evaluated_at:Time, expires_at:Time`; completion records **persist only SUPPRESSED**, display eligibility is ephemeral and requalified. Failure completion may have expiry≤evaluated_at; it is evidence, never authority |

`PreparationState` = `RULES_SUFFICIENT/RETRIEVAL_SUFFICIENT/PRIORITY_UNRESOLVED/NEEDS_INPUT/NO_ELIGIBLE_RULE/AWAITING_APPROVAL/UNSUPPORTED/LIMIT_EXCEEDED`. Lifecycle/format failures are port errors instead of successful Preparation records. `ObservationKind` = `PERMIT_ISSUED/WRITE_ACCEPTED/STREAM_CLOSED/FINAL_USAGE/ZERO_PROVEN/COVERAGE_GAP/CONFLICT`; `Terminal` = `NONE/COMPLETED/INCOMPLETE/FAILED/CANCELLED/UNKNOWN`. A W1 `code` alone never determines Terminal. `Stage` excludes `after_read`: that W1 boundary checks time/cancellation without fetching eligibility.

### 3.2 Signatures, ownership and errors

All signatures below are **new internal contracts**, not existing callable APIs. `ReadContext` is a frozen per-operation value containing exact Scope, process epoch, monotonic/UTC deadlines and cancellation ID, with no call yet. `RecoveryContext` contains the same scoped identifiers, an explicit synthetic recovery decision ID, a newer owner generation, a separate bounded operation deadline ≤30s, and no send permission. Preparation and recovery may never restart the old call deadline. `CallRuntime` is a newly constructed per-call facade with immutable RunContext and explicit trusted Clock/CancelReader/Authority/Coordination/Observer dependencies; no singleton, thread-local implicit authority or reused “current request” object.

| Signature | Contract / result |
| --- | --- |
| `prepare_v1(read: ReadContext, question: QuestionManifest) -> Preparation` | Q table, at most one `retrieve_projected_v1`; for unresolved eligible data, freeze ephemeral W1 objects and full core before reservation. Still no secret/DNS/provider I/O |
| `reserve_v1(run: RunContext, key: ReservationKey, prepared_ref: Id) -> Reservation` | Resolve immutable exact core; atomic account+task reserve and consumed call slot; synthetic operator decision fixtures accepted only for fake mode |
| `lookup_reservation_v1(read: ReadContext, key: ReservationKey) -> Reservation` | Bounded fresh lookup by unique full key; identical existing key returns immutable receipt, mismatch rejects. Ambiguous reserve acknowledgement is resolved by lookup, never allocating another call |
| `read_preparation_authority_v1(req: PreparationAuthorityRead) -> PreparationAuthorityView` | Fresh scope/lifecycle/policy and exact synthetic registry lookup before any reservation; validate completed-operation time/cancellation and every returned binding. Missing dependencies remain enumerated local missing inputs |
| `read_authority_v1(req: AuthorityRead) -> AuthorityView` | Bounded fresh read; controller checks cancellation/deadline before and **after** completion and validates the returned full W1 snapshot at fresh wall time. Exact sources, decisions, ownership, configuration and key reference required |
| `admit_v1(run: RunContext, reservation: Reservation) -> Admission` | Conditional on owner generation; enforce task/case/account/deployment caps and rate, persist slot, include waiting time; no G/long transaction during wait |
| `begin_acceptance_v1(run: RunContext, admission: Admission, body_digest: Hash) -> AcceptanceTicket` | Under N1 mutex A, require OPEN and journal coverage; pin current acceptance generation **before** final database/source lookup. A ticket alone cannot write |
| `invalidate_v1(ctx: InvalidationContext, request: InvalidationRequest) -> InvalidationAck` | Under the same A as ticket/permit issuance and endpoint acceptance: close deployment, advance generation, durably journal pending operation/barrier, revoke all older outstanding tickets/permits, then acknowledge. Missing/ambiguous acknowledgement never authorizes the database mutation |
| `lookup_invalidation_v1(ctx: InvalidationContext, operation_id: Id, operation_digest: Hash) -> InvalidationAck` | Exact idempotent recovery lookup, no fresh invalidation ID or replay of the mutation. Unknown/absent evidence is not an acknowledgement |
| `resolve_invalidation_v1(ctx: InvalidationContext, resolution: InvalidationResolution) -> InvalidationAck` | Journal independently verified transaction disposition; reply remains CLOSED. This port cannot reopen acceptance or restore a permit |
| `reopen_v1(ctx: InvalidationContext, request: ReopenRequest) -> GateAck` | Explicit recovery only under G: bounded, independently verified transaction/stream/current-qualification evidence; under A compare expected closed epoch/generation and resolved pending set before durable OPEN. Lost ack prohibits caller continuation; this never restores old tickets/permits |
| `send_guard_v1(run: RunContext, admission: Admission) -> ContextManager[SendPermit]` | Acquire G, obtain AcceptanceTicket, requalify, durably mark SEND_INTENT, then obtain a permit only if the ticket's authority epoch/generation still equals OPEN authority; yield that exact permit; `finally` closes/revokes permit and releases G, not budget liability |
| `consume_write_v1(run: RunContext, permit: SendPermit, body: bytes) -> WriteAck` | Trusted fake byte-writing boundary, not model/application callback; body ≤32768 bytes, independently hash-checked against permit. Under A, require OPEN and exact current authority epoch/generation as well as owner/body/deadline; atomically consume permit and record endpoint acceptance under §6. `WriteAck={format, permit_id:Id, event_id:Id, sequence:positive N, accepted_at:Time}` |
| `observe_v1(read: ReadContext, event: Observation) -> ObservationAck` | Independent observer producer appends immutable evidence; result/accounting callback cannot manufacture it. Ack exact `{format, event_id:Id, sequence:positive N, event_digest:Hash}`; lost ack gives UNKNOWN, not resend |
| `reconcile_v1(ctx: RunContext or RecoveryContext, key: ReservationKey, observation_ids: Id[1..8]) -> Settlement` | Fetch trusted observations independently; validate exact bindings and final counts, atomic dual-scope settlement. Stale producer evidence can be consumed by recovery but cannot act as recovery owner |
| `complete_v1(ctx: RunContext or RecoveryContext, key: ReservationKey, outcome: bounded W1 ProposalOutcome) -> Completion` | Preserve outcome usage regardless of display qualification, ensure observer/accounting disposition, persist content-free completion. Requalify display only for live call context; recovery never displays old results |
| `cancel_v1(read: ReadContext, key: ReservationKey, decision_id: Id) -> CancelAck` | Set monotone cancellation latch first; under G execute §5.3 invalidate/ack before committing durable task-version cancellation; exact `{format, key_digest:Hash, cancellation_generation:Gen, state:'CANCELLED'}`. Never refund or reactivate task |

Named tiny acknowledgement records share the metadata bounds. All operations propagate the same non-increasing absolute call deadline/cancellation ID. Use monotonic elapsed time and fresh ordered UTC for half-open intervals; any rollback/epoch mismatch fails closed. SQL statement/lock and observer wait timeouts are ≤remaining operation budget; after every wait/read/commit acknowledgement sample again. Equality with any applicable expiry or deadline rejects sending/display. Cleanup is allowed after expiry solely to close/fence/release owned resources and retain liability; if its separately bounded ≤3s cleanup cannot prove release, quarantine connection/slot and enter recovery, never declare success.

Port failures are exact `{format:'ra-w2-error/1', code:PortCode, key_digest:Opt[Hash]}` with no exception text. Codes and W1 bridge mappings: `VERSION_UNSUPPORTED→VERSION_UNSUPPORTED`, `INVALID_RECORD→MALFORMED_OUTPUT`, `LIMIT_EXCEEDED→INPUT_LIMIT`, `SOURCE_UNAVAILABLE→SOURCE_UNAVAILABLE`, `CONTEXT_CHANGED→CONTEXT_CHANGED`, `CANCELLED→CANCELLED`, `DEADLINE_EXCEEDED→PROVIDER_TIMEOUT`, `AUTHORITY_UNAVAILABLE→CONFIG_UNAPPROVED`, `RESERVATION_UNAVAILABLE/OWNER_LOST→BUDGET_UNAVAILABLE`, `OBSERVER_UNAVAILABLE/COMMIT_UNKNOWN/CONFLICT→AUDIT_UNAVAILABLE`. Unknown bridge exceptions map to `PROVIDER_FAILURE`; mapping never changes independently known usage or refunds uncertain liability. The Label type does not permit another rate/model strategy: every rate_card equals existing W1 RATE_CARD and every usage_mapping/mapping equals USAGE_MAPPING; unknown versions fail. No new W1 code is added. Every mutation is idempotent only for identical full key/event content; changed digests reject and record conflict. None retries a provider send.

### 3.3 Synthetic record example

This independently authored example is X1's accounting projection, not an issued reservation or operational authority. Repeated-byte core/body/evidence/accounting digests are visibly synthetic placeholders; a real issuer must recompute them from exact content and trusted evidence. The key digest alone is recomputed here with `SHA256(UTF8("ra-w2-reservation-key/1\n") || canonical(key))`. For other record digests use that record's format plus newline as domain; BindingCore keeps the adopted `ra-ai-budget/1\n` domain, and W1 keeps its unchanged domain/serialization. The example does not assert that these sources, account, proof or send exist.

```json
{
  "format": "ra-w2-record-examples/1",
  "measurement_kind": "synthetic",
  "operational_approval": false,
  "key": {
    "format": "ra-w2-reservation-key/1",
    "scope": {
      "format": "ra-w2-scope/1",
      "task_id": "syntask_orchid",
      "case_id": "syncase_amber",
      "project_ref": "synproject_41",
      "context_ref": "syncontext_73",
      "context_version": 1,
      "context_generation": 1,
      "account_ref": "synaccount_a",
      "deployment_ref": "syndeploy_a",
      "policy_id": "synpolicy_a",
      "policy_version": 1
    },
    "call_ref": "q_a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1",
    "attempt": 1,
    "core_digest": "b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2"
  },
  "observation": {
    "format": "ra-w2-observation/1",
    "key_digest": "a80fe805b3f2452285cdc2c51e25f5d4aa027c2d64a379c21c8f148dc3116217",
    "call_ref": "q_a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1",
    "event_id": "synevent_4",
    "observer_epoch": "synobserver_1",
    "authority_epoch": "synauthority_1",
    "acceptance_generation": 7,
    "sequence": 4,
    "owner_generation": 1,
    "kind": "FINAL_USAGE",
    "permit_id": "synpermit_1",
    "body_digest": "c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3",
    "observed_at": "2032-02-03T04:05:06.000000Z",
    "terminal": "COMPLETED",
    "usage": {
      "format": "ra-w2-usage-view/1",
      "state": "known",
      "input_tokens": 2048,
      "output_tokens": 384,
      "cached_input_tokens": 0,
      "cache_write_tokens": 0,
      "reasoning_tokens": 128,
      "total_tokens": 2432,
      "mapping": "openai-responses-2026-09-11/1"
    },
    "evidence_digest": "d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4"
  },
  "settlement": {
    "format": "ra-w2-settlement/1",
    "key_digest": "a80fe805b3f2452285cdc2c51e25f5d4aa027c2d64a379c21c8f148dc3116217",
    "settlement_id": "synsettle_1",
    "final_event_id": "synevent_4",
    "revision": 1,
    "state": "KNOWN",
    "settled_tokens": 2432,
    "settled_microusd": 8704,
    "held_tokens": 0,
    "held_microusd": 0,
    "refund_tokens": 2688,
    "refund_microusd": 13824,
    "overrun_tokens": 0,
    "overrun_microusd": 0,
    "accounting_digest": "e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5"
  }
}
```

## 4. Compatibility with actual W1 order — D

Current [bindings](../backend/app/ai/proposals/bindings.py), [adapter](../backend/app/ai/proposals/adapter.py), [codec](../backend/app/ai/proposals/codec.py) and [transport](../backend/app/ai/proposals/transport.py) are preserved as the compatibility baseline. W2 uses a new call-scoped `Authority.current(project_ref, context_ref, stage)` wrapper and the unchanged `Coordination.admit/sending/record/finish` signatures. They translate to §3 using their frozen RunContext; passed project/context/receipt values must match it exactly. W2 wrappers and the fake write port always take the minimum of W1 remaining time and the original RunContext deadline; entering W1 later never restarts W2's allowance. W1 `ReservationReceipt` is projected from a verified W2 Reservation; `PreparedProposal.digest(config)` remains the W1 digest and is **not** replaced with `core_digest`. W2 core separately binds that digest, payload/body hashes, manifest/registry/projection versions, fixed prompt/schema/profile, config/key-reference versions, rates/currency, limits and expiries. Exact core serialization must include those fields in a versioned record; no self-hash, timestamps generated after reservation or secret/header material.

| Actual W1 stage/order | Concrete W2 obligation |
| --- | --- |
| `config.validate`, concrete `ProviderTransport.qualify`, receipt/digest checks | W2 preflights lookup/authority before adapter entry; preserve disabled/live zero-I/O rejection and exact type/profile dispatch |
| `before_input → input_document/validate_registry/request_body → after_input` | Call-scoped wrapper reads fresh authority. Confirm W1-computed body hash equals reserved body hash; same canonical payload, PROMPT and output_schema, no metadata injected into prompt |
| `Coordination.admit → after_wait` | `admit_v1`; receipt expiration checked after wait, no G held; failures prior to `admitted=True` need outer W2 completion because W1 will not call record/finish |
| `before_secret → after_secret → after_dns → after_connect → after_tls` | Same synthetic MemorySecret/Resolver/Connector boundaries and fresh authority; no Target authentication; missing budget/config/eligibility denies before secret/DNS |
| `sending(check=final_send) → sending → before_write → write_ready → mark_started → MemoryWire.write` | Guard wrapper acquires G, pins acceptance ticket before qualification, and durably marks/arms the same-generation permit before yield; final checks remain inside G. Existing `mark_started` is merely conservative local delivery state. **Required narrow extension:** W2-bound exact MemoryWire verifies/consumes its permit after the existing write hook and immediately before buffer append; missing/revoked/expired/wrong-generation permit cannot append |
| `after_send → reads(after_read time-only) → exchange finally close → project_usage → output validation → before_consume → before_return` | Release G after first write; preserve bounded reads/close. Independently observe final usage, including rejected/incomplete output; no SQL transaction during response wait |
| `record(stripped outcome) → finish → final_return` | `record` reconciles trustworthy evidence, never records display authority; `finish` releases admission only on proven closed/fenced sender, retaining unknown liabilities. `final_return` can still fail and preserves already-known usage |
| Outer `complete_v1` after `propose_once` returns | Records content-free disposition even if no W1 callbacks ran; validates current source/config/cancellation/time again before ephemeral display. Accounting remains accessible after expiry, with no stale source reconstruction. Re-reading completion never resends or grants display permission |

Two integration changes must be reviewed explicitly: (a) a typed, optional fake permit/observation port on the **existing concrete** MemoryWire, required whenever bound to W2 and absent on unchanged legacy W1 fixtures; never allow arbitrary subclasses/native wire types to evade `qualify`; (b) a bounded usage/terminal observation hook at the adapter's projection boundary, **before** later profile/proposal/authority failures, so W2 does not infer terminal state from final `code`. No raw response, headers, errors or reasoning enter the observation port. The independent fake endpoint in §6 supplies corroborating final usage even if that hook or both ledgers are deliberately omitted. No change to W1 model input/output fields, PROMPT, digest serialization, profile, default-disabled behavior or legacy Finding/Mock/routes is required. Missing W2 ports fail closed for W2-bound calls, while unchanged standalone W1 fake fixtures retain their current behavior and zero durable-accounting claim.

W1's `after_send` authority failure may precede envelope parsing and leave its outcome unknown. W2 may use separately proven fake-endpoint final usage for accounting without converting the W1 outcome into success. A final-return failure after `record` cannot erase recorded known usage. Unknown delivery with known final usage is accountable but never a successful display; unknown/invalid final usage retains the reserve even if a response arrived.

## 5. Logical persistence, transitions and guard inventory — D

### 5.1 Constraints and postings

Assess the adopted PostgreSQL recommendation as the fake-validation budget authority; no schema/migration is created. Logical relations:

| Relation | Required key/constraint and mutation authority |
| --- | --- |
| `budget_policy` / `budget_balance` | Unique `(account_ref, policy_id, policy_version)` and task-scoped counterpart; immutable caps/rate/currency/manifest digest, exact project/account ownership; no null cap admission. Balance stores settled S, held U and allocated call/time slots; H=B−S−U is signed derived data |
| `call_core` / `reservation` | Unique full ReservationKey and `(task_id,case_id,question_id)`; attempt=1, no reparent/reuse; unique reservation ID. At most two allocated calls/case and one unfinished case reservation. Freeze W1 digest, body/payload hashes, config/rate, source/decision/projection binding manifest and expiry atomically with reserve |
| `acceptance_barrier` / `invalidation_operation` (independent N1 journal) | Deployment gate plus authority epoch/generation and unique `(deployment_ref,operation_id)`/operation digest; monotone journal sequence, pending/committed/rolled-back/unknown disposition. Durable closure is required before the matching DB mutation; ≤64 pending operations. A database copy is diagnostic, not acceptance authority |
| `admission` / `send_marker` | Unique unfinished account and deployment slot, exact call+generation; one marker/call, one permit/marker. Owner changes use compare-and-swap expected generation; no automatic takeover at ADMITTED or later |
| `observation_copy` / `event` | Unique `(observer_epoch,sequence)` and `event_id`; identical duplicate is no-op, conflicting bytes append separate bounded conflict evidence. This database copy is not the independent observer |
| `settlement` / `posting` | Unique `(call,final_event_id,revision)` and `(settlement_id,scope_kind,dimension)`; one token and one cost posting for each of task/account, committed with settlement and reserve removal. Final totals bind rate/mapping; later correction requires explicit recovery decision and signed delta, never overwrites previous entries |
| `completion` / `conflict` | Unique content digest/event ID; terminal accounting and display qualification separate. No raw payload/content. Capacity failures pause scope and retain reservation; reserved recovery slots are not unlimited overflow storage |

All mutations validate exact owner generation and immutable key. A stale sender cannot mutate/release a successor's slot. Genuine stale observations remain evidence: only a separately authorized current recovery context may apply them. Conservation per task **and** account uses inherited equations; views are not summed as double spend. Known over-cap actuals post in full and block admission. Checked arithmetic overflow preserves the original bounded usage counts, blocks settlement/admission and reports an accounting coverage failure; it never clamps a cost into the integer range or converts it to zero. Unknown reservation retains at least its original reserve, increased for a higher independently known lower bound. Observations or known partial usage do not count twice as settled spend plus the same held amount.

| ID | From → to | Preconditions and atomic effects |
| --- | --- | --- |
| S1 | PREPARED → RESERVED | Current manifest/policy; lock account then task; check token/cost/call/time capacity, insert exact core/reservation/event and increment U/call slot together |
| S2 | RESERVED → ADMITTED | Current generation, qualification and admission/rate capacity; persist account/deployment slot; no send |
| S3 | ADMITTED → SEND_INTENT | Hold G; fresh authority; durable marker commit before permit; acknowledgement ambiguity prohibits write |
| S4 | SEND_INTENT → IN_DOUBT | Unknown delivery/usage, cancellation or crash/coverage gap; retain U, pause task/account; never replay |
| S5 | RESERVED/ADMITTED/SEND_INTENT/IN_DOUBT → ZERO_FINAL | Independently proven no accepted bytes and irrevocably closed/revoked old sender/permit; absence of callback is insufficient |
| S6 | SEND_INTENT/IN_DOUBT → USAGE_FINAL | Independently correlated final known usage, even after cancellation/expiry/rejected proposal |
| S7 | ZERO_FINAL/USAGE_FINAL → SETTLED | Atomically append settlement/postings, S += actual on first settlement (new actual minus prior actual on a correction), U -= held; record refund/overrun, no call-slot refund |
| S8 | Any persisted state → CONFLICT | Conflicting immutable binding or final evidence; append conflict, pause; preserve prior postings and at least conservative disputed liability, no double final charge |
| S9 | CONFLICT → ZERO_FINAL/USAGE_FINAL | Explicit recovery decision resolves evidence; append correction revision; use deltas against existing S/U, not a second full posting |

S5 explicitly permits resolution of an IN_DOUBT marker after independent zero proof; this elaborates adopted recovery, not expiry-based forgiveness. SETTLED is terminal for ordinary operations; S8/S9 are corrective history, not send reactivation. Account/task cancellation is a separate monotone state: it blocks S1–S3/display but permits bounded S4–S9 accounting. Duplicate identical events leave state, S and U unchanged. Elapsed time across process epochs is never reset into another 30s allowance.

### 5.2 G and actual writer inventory

D proposes deployment-wide session advisory **G `(73105,2)`**, disjoint from the current knowledge transaction key `(73103,2)` and intent dispatcher `(73104,context)`. G normally serializes cross-context shared-publication changes with provider first writes, but session loss removes that exclusion. N1's mandatory acceptance barrier (§5.3) provides the invalidation/acceptance ordering even then; G is not that barrier or a substitute for current row/phantom locks. Inventory scope is the dependencies read by this concrete synthetic-only path, with conditional extensions called out below; it is not a claim that all repository/outbound writers have been audited.

**Every active writer row below must execute the §5.3 barrier/ack protocol before any eligibility-changing commit**, including implicit invalidation from a read, correction, recovery or internal service commit. Paths capable of marking unavailable during a read close the barrier at outer entry, before their database locks. An unexpected eligibility mutation discovered under database locks requires rollback of that transaction before starting the barrier protocol; never call A while retaining those locks. Pure audit-only work may omit the barrier only when it changes no qualification dependency. If that classification is uncertain, invalidate the deployment; no dependency-specific exception is inferred. Conditional integrations must join the same protocol before inclusion.

| Writer / current code path | Current locks/transaction owner | Required integration before W2 qualification |
| --- | --- | --- |
| [Context](../backend/app/services/research_context.py) `create_context/correct_context/close_context` | Context row FOR UPDATE for corrections/close; association uniqueness for create, metadata share locks; routes own outer transaction | G before insertion or context lock; close+new-context association transfer both participate. Hold through outer commit/rollback; barrier before close/transfer/correction takes effect |
| [Observation](../backend/app/services/research_observation.py) `prepare/accept/lifecycle/revoke_preparation/maintain` | `_locked` context FOR UPDATE, nested savepoints, association/Target share locks where used; lifecycle includes hold/release/delete/quarantine; maintain changes recovery token/purges | G before context lock; include correction via accept and all recovery/audit rotation. No observation body is provider input, but context recovery and immutable unavailability still constrain use |
| Observation `read` / [subject](../backend/app/services/research_subject.py) `record/read` | Context lock, metadata share locks; reads can audit or record unavailable state | G at outer transaction entry where mutating/auditing; no late acquisition inside nested service. Subject changes are included conservatively, but subject data never affects model selection |
| [Knowledge](../backend/app/services/research_knowledge.py) `record/decide/rotate_audit/retrieve` | `_locked`: catalog advisory xact lock then context FOR UPDATE; retrieve additionally SHARE-locks assertion/revision/scope tables | G **before catalog**, including read paths that audit; decide includes review/reuse/publish/withdraw/disable. Keep public retrieval's fact locks intact |
| [W3](../backend/app/services/research_rule_validation.py) `validate_rule/submit_feedback/read_validation/read_feedback/review_feedback/invalidate_pending` | Catalog→context via knowledge; nested `review_feedback→decide→invalidate_pending`; append-only proofs/reviews | One outer G scope, nested calls borrow explicit guard token; include disable/lineage invalidation, no nested session-lock count leak |
| [Targets](../backend/app/api/routes/targets.py) network-mode/profile/revision updates; [revisions](../backend/app/services/authorization_revision.py) `create_revision/transition_revision`; [scopes](../backend/app/api/routes/scopes.py) creation | Target FOR UPDATE; revision transitions profile→ordered revisions; Scope insert takes table row-exclusive lock; some services commit internally | G before **any** existing lock/transaction for these mutations because context `_target`/readiness qualification can change. Wrap internal commit as well as route-owned transaction; no G acquisition from inside a locked profile/Target |
| [Authorization profile routes](../backend/app/api/routes/authorization_profiles.py) update/delete; context/Target creation and enrollment | Profile update FOR UPDATE; create/delete and enrollment have their existing transaction/uniqueness locks | G before mutations that change any selected Target binding/lifecycle. Enrollment has its own origin lock; order G first. Unrelated asset DNS/provider-free analysis is not granted egress or forced into W2 |
| New W2 configuration, decision/projection/source-certificate registry, budget policy, cancellation and recovery writers | **No implementation exists**; W1 Configuration/Registry are frozen values, not mutable storage authority | Mandatory G on every replacement/revoke/disable/version/owner/retention change; §5.3 barrier also applies without a successor admission; fixed current version pointers plus immutable history. Key rotation means version references only; no secret storage/credential reads added |
| Conditional local fact/credential integrations, **excluded from this v1 provider path** | `review_resource_access_assertion` locks source assertion; observed derivation locks TestRun; fact inserts need phantom protection; bearer update identity→binding | If a later design adds subject/fact/credential dependencies, enumerate these and Resource/Endpoint/binding/identity writers before allowing them: G first plus existing table/row locks. Current synthetic-only seam does not use them as selection inputs or claim G protects them already |

Required database lock order for a W2 operation: **G → catalog `(73103,2)` if needed → context IDs ascending → existing association/Target metadata locks → short budget account→task→call locks**. Before those database locks, an invalidator performs **G → A(close/journal/ack) → release A → existing database order**; the sender obtains its ticket in the same position. A is never held over a SQL transaction or database wait, and database locks are never held while requesting A. Final permit issuance/acceptance occurs only after short SQL transactions have ended. No operation holds budget row locks while acquiring catalog/context/Target locks. Final-send qualification and marker posting can be separate short transactions while G remains held. Legacy internal orders remain inside G; selected Target/profile services must not call back into catalog/context while holding their existing locks. Read-only W2 reconciliation uses account→task→call without G when it only appends evidence/settlement; cancellation/ownership/policy changes take G first. Nested services receive an explicit borrowed guard token, never blindly reenter session advisory locks. Route `with db.begin()` boundaries matter: acquiring G inside a savepoint and releasing before the route commits would be incorrect.

Admission/rate waiting, secret resolution, DNS/TLS and response reads hold neither G nor SQL transaction. G covers bounded final qualification, durable marker/observer acknowledgement and **first write only**; no SQL transaction spans the write. Use a dedicated, non-pooled-back-until-unlocked G connection; on exception roll back any open short transaction, revoke/fence permit, release G once, and close/discard a suspect connection. A lost/ambiguous unlock cannot be treated as a reusable pooled connection. Lock/statement timeout, cancellation or expiry after lookup prevents write; failed cleanup pauses admission. Cancellation latch is checked after lookup even when its durable writer is waiting for G. Durable cancellation winning G blocks marking; cancellation after marking retains uncertainty until zero/final proof. No transaction spans operator recovery. Eligibility-changing writers cannot commit first and revoke later: §5.3 applies even if the prior sender's G connection disappeared.

**Connection-loss limit:** release of G does not stop a paused process or socket. A lifecycle writer that subsequently obtains G must close and durably acknowledge the independent acceptance barrier **before** making its invalidation effective; it cannot rely on a later admission/takeover. The endpoint rejects the old acceptance generation even when owner generation and permit expiry are unchanged. A new owner additionally needs old-stream fencing/closure proof before freeing admission. An extra database read or result-generation comparison does not close the acceptance race. Without proof, admission and liability stay held; live socket fencing remains unproved.

### 5.3 Invalidation and endpoint acceptance — N1 correction

**Shared serialization point:** the independent fake permit authority owns one deployment-wide mutex **A**, an `OPEN/CLOSED/RECOVERY_REQUIRED` gate, an authority restart epoch and a monotone acceptance generation. Ticket issuance, permit issuance, invalidation closure and simulated endpoint acceptance all serialize under A. Acceptance includes the final gate/generation/permit/body/deadline check **and** acceptance recording in one critical section; it is not a database precheck followed by an unguarded write. No writer may bypass this authority and still report that provider-relevant invalidation completed.

**Affected set:** invalidate **every outstanding ticket and unconsumed permit for the deployment**, not a caller-supplied list, just the active admission, or only the invalidated source's direct references. This intentionally conservative superset covers source/ancestor/publication withdrawal, context close/transfer/hold, configuration/key-reference replacement, cancellation and every other inventory dependency, across accounts/tasks. Every ticket/permit carries deployment, authority epoch and acceptance generation; checking these at acceptance revokes the set without enumerating or truncating a dependency index. Already consumed permits remain observed events. Stale epoch/generation rejects with CONTEXT_CHANGED; unavailable/incomplete authority returns OBSERVER_UNAVAILABLE, without inventing new W1 codes. No new permit may be issued while CLOSED, including one requested by a stale process with an unchanged owner generation. Generation overflow closes the authority and requires recovery, never wraps or reuses a generation.

The required writer sequence is:

1. Acquire G within the bounded operation deadline. Resolve the intended mutation's immutable ID/digest; if identifying it requires a read transaction, finish that transaction before contacting A and revalidate its expected versions in the later write transaction. The protected lifecycle state has **not** yet changed. InvalidationContext supplies trusted monotonic/UTC deadlines and cancellation; all A/IPC/journal waits are bounded by its remaining ≤30s operation budget, with fresh checks after completion. Cancellation/timeout before mutation aborts the transaction path; once a barrier might exist it stays unresolved/closed until reconciliation, not automatically undone.
2. Call `invalidate_v1`. Under A, first deny further acceptance by setting CLOSED, advance acceptance generation, and append/fsync the operation ID/digest, barrier sequence and pending disposition **before acknowledgement**. On journal failure remain CLOSED/RECOVERY_REQUIRED. Release A. The returned exact InvalidationAck is the prerequisite for database mutation, not a success response for that mutation. Closure remains in force after A is released and even if G disappears. The linearization boundary is this closure critical section versus endpoint acceptance under A.
3. Only with a verified durable acknowledgement for this still-pending operation, perform the original eligibility-changing transaction in the existing lock order, binding the operation ID/digest to its transaction evidence. Persist the invalidation marker/outcome with the mutation where feasible; internal-commit services must be enclosed by this protocol too. The authority stays CLOSED throughout. A database commit makes invalidation effective only **after** endpoint exclusion is established. An acknowledgement cannot be reused for another mutation or a second transaction attempt.
4. After ending all database transactions, append COMMITTED, ROLLED_BACK or UNKNOWN disposition with `resolve_invalidation_v1`. A success response requires both a known committed mutation and a durable matching resolution acknowledgement; the gate remains CLOSED. Rollback, exception or lost acknowledgement never restores old permits. No response falsely says “withdrawn”, “cancelled”, “revoked” or “safe to continue” when completion is unresolved. Loss of acknowledgement after a known database commit reports reconciliation required, not a claim that the committed change was rolled back.
5. Reopening is an **explicit bounded recovery operation**, not writer cleanup, a watchdog or automatic retry. Under G, independently establish terminal disposition of **all** pending writer operations and that no old writer transaction can later commit (including lost-G/connection cases); unresolved database acknowledgement keeps CLOSED. Freshly qualify current lifecycle/configuration and ownership, close/fence old streams, then under A verify the same closed generation, no unresolved operations and complete journal/witness coverage before journaling OPEN. Database transactions finish before requesting A. Old tickets/permits remain permanently unusable; new tickets are pinned before new final qualification. A marked call cannot obtain another ticket or rearm its permit, including after a proven-zero resolution; any eligible new call still needs its distinct predeclared question/reservation and all existing call limits. No source or cancelled task is re-enabled by opening the deployment gate.

An identical operation ID/digest lookup returns its original barrier/disposition; conflicting content rejects and keeps the gate closed. Ack disposition is explicit: a terminal operation returns its existing outcome, never permission to execute its database mutation again. The CLOSED receipt proves the historical barrier, not current gate state; pending-operation checks and the unique database operation ID prohibit replay after later reopening. Concurrent invalidators may each close/advance the generation; their pending dispositions are separately retained. Recovery cannot reopen merely because one completed. Bound pending invalidations to **64 per deployment** as an N1 control-record capacity, separate from per-call event limits; exhaustion/journal uncertainty keeps CLOSED/RECOVERY_REQUIRED and refuses new mutation protocols without silently retiring pending operations. Barrier records contain no model/source bodies and are not fabricated per-call Usage records. Their effect on every call is derived from its bound generation; zero refunds still require that call's independent no-acceptance/closed-stream proof.

| Gate transition | Required condition; never inferred from G ownership |
| --- | --- |
| L1 OPEN → CLOSED | A closure wins, generation advances and pending barrier is durably recorded before acknowledgement; older tickets/permits cannot accept |
| L2 CLOSED → CLOSED | Additional invalidation advances generation/adds pending operation; a disposition or rollback records history but does not reopen |
| L3 OPEN/CLOSED → RECOVERY_REQUIRED | Authority-detected journal/coverage uncertainty or restart; no new ticket/permit/acceptance while authority cannot prove a consistent gate. A caller-only lost reply does not prove the remote gate changed (Y3b) |
| L4 CLOSED/RECOVERY_REQUIRED → OPEN | Explicit §5.3 recovery, complete barrier/witness coverage, every pending writer terminal/fenced, old streams closed, fresh qualification and matching closed epoch/generation; durable reopen acknowledgement required for continuation |
| L5 OPEN/CLOSED/RECOVERY_REQUIRED → same state | Exact duplicate lookup/disposition is idempotent; it cannot replay mutation, reset generation or recreate a consumed permit |

**Unavailable or ambiguous authority:** if invalidate never obtained a verified acknowledgement, the writer must not commit the eligibility-changing mutation. Return OBSERVER_UNAVAILABLE or COMMIT_UNKNOWN, retain liability, and stop new work pending exact-ID reconciliation. Independently proven endpoint shutdown establishes no future acceptance but does not itself authorize a missing-ack database mutation: recovery must first establish and durably acknowledge the closed barrier. A lost reply has two possible histories: closure happened (old permits reject), or no closure happened (an old acceptance may have won before invalidation). Do not claim zero acceptances in the second history or claim invalidation took effect. A locally received cancellation request likewise is not proof of remote fencing. If the invalidation IPC path is partitioned while acceptance remains reachable, a local stop flag cannot fence that remote endpoint; the mutation remains uncommitted/unconfirmed until the closed barrier is durably acknowledged. This availability tradeoff grants no extension of DATA retention deadlines; an operational shutdown/retention path remains a live-only design gate, not a capability proved by this fake-only protocol. If the authority process itself is unavailable, the required endpoint acceptance primitive is unavailable too: the child must not append a successful simulated send or substitute an in-process writer. Restart is CLOSED/RECOVERY_REQUIRED until durable barriers and independent witnesses reconcile; never restore an older OPEN generation. No automatic continuation follows an ambiguous barrier, database commit, resolution or reopen acknowledgement.

**Acceptance first:** an endpoint acceptance that wins A is recorded once and cannot be undone by the later closure. The writer then closes the gate before committing invalidation; consumed evidence/known final usage survives, incomplete usage retains reservation, and lifecycle/cancellation suppresses subsequent display and calls. Closure does not retroactively turn acceptance into pre-send zero, refund unknown cost or authorize replay. **Invalidation first:** closure wins A, advances generation and acknowledges; every later acceptance using an old ticket/permit rejects, whether or not G, the owner generation, a lease or any admission changed.

## 6. Observer, traces and separately identified mechanism

**N1 — PROPOSED, targeted decision required:** for fake-only process tests, use an independently owned parent test harness as permit authority and fake endpoint, with bounded IPC and an append-only, fsync-before-ack journal in its owned temporary directory. The adapter child process cannot mint permits, alter journal files or append endpoint acceptance records. A separate read-only test observer retains endpoint counts and journal high-water marks outside both database result/accounting callbacks. N1 also includes the §5.3 deployment-wide generation barrier shared by every invalidating writer and endpoint acceptance, its acknowledged close-before-commit ordering, and explicit recovery before reopening. This correction remains a targeted **PROPOSED** N1 mechanism; B1–B8 stay adopted. This is a concrete testing mechanism newly specified here; it is not an adopted live service, worker, Redis component, deployment or account-evidence channel. Independent review should assess this choice and the User should decide this **N1 only** if it remains material; no blanket B1–B8 approval is requested. An alternative test-only independently owned PostgreSQL observer server would add database resources and different shared-failure assumptions.

Fake journal records use Observation schema (≤4096 bytes/event, ≤64 unique events/call, last8 reserved for stop/recovery). IPC control frames ≤8192 bytes; a write frame has ≤4096 metadata bytes plus ≤32768 synthetic body bytes and length framing, total ≤40960 bytes, one in flight/call, bounded by remaining deadline. Raw body exists only in ephemeral fake endpoint memory, never the journal; HTTP headers and authentication bytes do not enter IPC. The exact MemoryWire verifies the non-secret body digest against the bound permit; the parent endpoint independently hashes and checks received body bytes before accepting them. Its acceptance is the W2 fake first-write event; the child writes list is a diagnostic mirror, not the observer. The independent endpoint records acceptance and scripted final usage from its separately authored fixture, not from W1 `code` or accounting state. Journal acknowledgement and simulated endpoint acceptance occur under A, the **same** authority mutex used for lifecycle/configuration invalidation closure and ticket/permit issuance; if process/storage failure makes their relation uncertain, the surviving evidence is IN_DOUBT. A journal entry alone can overstate a send; it never proves a no-send refund. The endpoint must parse a bounded frame and check OPEN, exact authority epoch/acceptance generation, and its permit/body/owner/deadline atomically under A before accepting bytes as a simulated provider request. IPC submission is not provider delivery. A crash after endpoint acceptance but before child mirror append remains post-send unknown, not zero.

N1 must have enforced process ownership/permissions, explicit immutable call+generation+body bindings, journal restart epoch, sequence continuity and bounded independently retained head/tail witnesses. The parent uses its explicitly injected trusted test clock and permit expiry; cross-process native monotonic readings are never compared. Deadline/cancellation updates and clock anomalies are independently scheduled by the harness and checked again on endpoint acceptance. Every relevant writer, not only takeover/recovery, calls invalidate_v1 and must receive its durable closure acknowledgement before its database invalidation can take effect. Closure and acceptance race on A: closure first rejects subsequent affected acceptance; acceptance first consumes the permit irrevocably and may incur unknown usage. Delayed IPC delivery cannot be mistaken for the order of acknowledged closure. The adapter's old generation cannot obtain or reuse a permit after revocation even if its DB connection disappeared. A wrapper/hook that merely sets `safe=true`, or a journal in the killed adapter process, is not this mechanism. No fake port may open a provider/Target socket. Whether the claimed independence survives the chosen IPC, filesystem and process kill schedule is a **future test obligation**, not established by prose or same-host fsync.

| Trace | Ordered events and required externally observable result |
| --- | --- |
| X1 success | S1 reserve `(5120,22528)` → S2 admit → G/authority → S3 marker commit/ack → permit journal ack → fresh write_ready → endpoint consumes once → G released → known final I=2048/O=384/C=W=0/R=128 → S6/S7 settle `(2432,8704)`, refund `(2688,13824)` → record/finish → final_return → complete/requalify. Display only if still eligible; one actual memory write |
| X2 proven zero | S1/S2 → cancellation before marker, or S3 marker with revoked permit before acceptance → independent zero/closed-stream proof → S5/S7 settle zero, refund full `(5120,22528)`; call slot consumed; secret counter depends on boundary, write count exactly0. Lost marker acknowledgement is first COMMIT_UNKNOWN, not immediate zero |
| X3 unknown | S3/permit → endpoint accepts once → adapter killed or final usage absent → S4; U remains `(5120,22528)`, scope paused, zero replay. Lost observer acknowledgement has the same conservative disposition even if the caller thinks no write occurred |
| X4 final cancellation | Endpoint final known usage → adapter record settles → cancellation or expiry occurs **inside** final_return authority read → W1 error/no display, usage preserved → complete stores SUPPRESSED. A 0.5s lookup within a 1s source window succeeds; expiry/deadline equality and wall/monotonic rollback reject |
| X5 duplicate/conflict | X1 event replay with identical ID/content → no postings/change. Same ID or another final receipt with different totals → S8, append conflict and pause; prior actual stays visible; disputed reserve retained. S9 requires explicit recovery evidence/decision and posts only adjustment delta |
| X6 guard-loss invalidation, no takeover | Sender passes write_ready with ticket/permit generation7 and pauses → G session ends → context-close or source-withdrawal writer acquires G → invalidate_v1 closes A gate and advances to8, durable ack → writer commits invalidation/resolution while CLOSED → same sender resumes with unchanged owner generation and unexpired permit7 → consume_write rejects. **No new admission occurs; endpoint acceptances=0**. Unknown accounting is retained until independent zero/closure proof. Separate takeover control still requires fencing before successor admission and stale finish cannot clear a successor |
| X7 common-mode omission | Independent endpoint accepts; deliberately omit result **and** accounting events/marker projection → journal/witness set difference exposes missing call; create scoped recovery liability from trusted core/permit evidence, pause, do not invent a settled zero. Also remove observer evidence: independent endpoint witness exposes a coverage gap. If **all** independent witnesses disappear, completeness is unknowable; freeze the account and declare that limitation |
| X8 delayed usage | X3 paused/cancelled call → trusted late final usage under RecoveryContext → S6/S7, actual posted once and U removed; task cancellation persists and old display stays suppressed. Uncorrelated provider totals or malformed/partial counts never settle |

### 6.1 Guard-loss protocol traces

These are independently reasoned **protocol-model expectations**, not runtime/IPC/PostgreSQL observations. Initial state: one qualified, unconsumed permit in OPEN authority generation7, owner generation1, one existing admission, reserve `(5120,22528)`, with no expiry/cancellation/clock change. Every schedule creates **zero new admissions**; owner generation stays1. `close` is the durable A barrier, `ack` its delivery, `commit` the protected database invalidation, and `resolve` the acknowledged CLOSED disposition. `accept` means an endpoint attempt, which may reject. No schedule silently performs zero settlement without independent zero proof.

| Trace | Independently expected ordering and result |
| --- | --- |
| Y1 invalidation first | Exact blocker: write_ready → pause → lose G → close/ack → commit → stale resumption. Zero endpoint acceptances, even with unchanged owner/expiry and no successor. Gate CLOSED; retain reserve pending zero proof. |
| Y2 acceptance first | G lost, but acceptance wins A before writer closure: one accepted event. Later close/ack/commit cannot undo it; known final usage settles normally, display remains suppressed, and replay with the consumed permit rejects. Missing usage instead retains its original liability. |
| Y3 acknowledgement ambiguity | Y3a: durable close but lost reply → no DB mutation, stale acceptance rejects. Y3b: request never reaches A → no DB mutation and old acceptance may win; do not claim revocation/zero. Both are unresolved, retain liability and stop new work pending evidence. |
| Y4 unavailable authority | Endpoint/authority unavailable → no closure acknowledgement, no DB mutation, no endpoint acceptance or child fallback. RECOVERY_REQUIRED on restart; no false success/automatic reopen. Control-channel-only partition is the Y3b uncertainty, not a proven zero. |
| Y5 unchanged positive control | No invalidation/G loss; unchanged authority and unexpired permit accept exactly once, known usage settles and current final qualification allows display. The barrier design does not permanently disable valid calls. |

```json
{
  "format": "ra-w2-barrier-traces/1",
  "measurement_kind": "synthetic_protocol_model",
  "runtime_fencing_tested": false,
  "initial_acceptance_generation": 7,
  "owner_generation": 1,
  "new_admissions": 0,
  "cases": [
    {
      "id": "Y1",
      "schedule": [
        "lose_g",
        "close",
        "ack",
        "commit",
        "resolve",
        "accept"
      ],
      "acceptances": 0,
      "invalidated": true,
      "gate": "CLOSED",
      "caller": "COMMITTED_CLOSED",
      "held_tokens": 5120,
      "held_microusd": 22528,
      "settled_tokens": 0,
      "settled_microusd": 0,
      "display": false
    },
    {
      "id": "Y2",
      "schedule": [
        "lose_g",
        "accept",
        "close",
        "ack",
        "commit",
        "resolve",
        "known_usage",
        "accept"
      ],
      "acceptances": 1,
      "invalidated": true,
      "gate": "CLOSED",
      "caller": "COMMITTED_CLOSED",
      "held_tokens": 0,
      "held_microusd": 0,
      "settled_tokens": 2432,
      "settled_microusd": 8704,
      "display": false
    },
    {
      "id": "Y3a",
      "schedule": [
        "lose_g",
        "close",
        "lost_ack",
        "blocked_commit",
        "accept"
      ],
      "acceptances": 0,
      "invalidated": false,
      "gate": "CLOSED",
      "caller": "UNRESOLVED",
      "held_tokens": 5120,
      "held_microusd": 22528,
      "settled_tokens": 0,
      "settled_microusd": 0,
      "display": false
    },
    {
      "id": "Y3b",
      "schedule": [
        "lose_g",
        "lost_request",
        "blocked_commit",
        "accept"
      ],
      "acceptances": 1,
      "invalidated": false,
      "gate": "OPEN",
      "caller": "UNRESOLVED",
      "held_tokens": 5120,
      "held_microusd": 22528,
      "settled_tokens": 0,
      "settled_microusd": 0,
      "display": false
    },
    {
      "id": "Y4",
      "schedule": [
        "lose_g",
        "authority_down",
        "blocked_commit",
        "accept"
      ],
      "acceptances": 0,
      "invalidated": false,
      "gate": "RECOVERY_REQUIRED",
      "caller": "UNRESOLVED",
      "held_tokens": 5120,
      "held_microusd": 22528,
      "settled_tokens": 0,
      "settled_microusd": 0,
      "display": false
    },
    {
      "id": "Y5",
      "schedule": [
        "accept",
        "known_usage"
      ],
      "acceptances": 1,
      "invalidated": false,
      "gate": "OPEN",
      "caller": "IDLE",
      "held_tokens": 0,
      "held_microusd": 0,
      "settled_tokens": 2432,
      "settled_microusd": 8704,
      "display": true
    }
  ]
}
```

Recovery additionally compares reservation/marker/observer/completion sets by exact scope and process epoch; equal result/accounting sets prove no completeness. Restoring an older database starts suspended, preserves independently known deletion/expiry and unknown liabilities, and requires reconciliation with surviving observer witnesses before new work. No old prepared payload is restored from ledger metadata. Capacity exhaustion, unknown journal tail, uncorrelatable evidence or contradictory final totals stop admission; deletion/rotation cannot silently erase unresolved history. A same-host fake harness cannot prove real provider acceptance, bill correlation, hidden input-token overhead, exclusive account use, native socket fencing, physical backup completeness or independent live failure domains. Those remain live-only gates with separately approved mechanisms/data/retention and no automatic account API calls.

## 7. Acceptance mapping and documentation validation

| Existing requirement | Concrete records/interfaces/transitions and later evidence |
| --- | --- |
| T1 | Q1–Q4, QuestionManifest, prepare/retrieve; E1–E4 and X1; independent query/write counters, deterministic source order |
| T2 | Strict §3 records, Q cardinalities, P completeness, scan/order; exact/+1 byte/node/depth/selection/candidate/event limits; reject malformed JSON and private/injected canaries with no payload/log leakage |
| T3 | ExactSource/ProjectionBinding/Scope, full writer inventory, requalification; foreign/stale/held/deleted/withdrawn/test-only publication and contamination checks; no private-derived selection |
| T4 | Reservation/Settlement/postings, S1/S7/S9, X1–X3/X5; Decimal conservation, exact cap/+1, null/zero/overflow, task/account isolation and full over-cap actuals |
| T5 | Unique keys and admission slots, S1/S2; barrier-controlled competing real PostgreSQL transactions, commit acknowledgement loss, one winner and rollback conservation |
| T6 | AuthorityRead/RunContext/G/A, InvalidationRequest/Ack, S3–S5, X2/X4/X6 and Y1–Y5; completed-lookup cancellation and all interval/deadline equalities; secret/write/display counters and retained usage |
| T7 | Permit/Observation, S3/S4, X2/X3; independent child-process kills at reserve/marker/ack/accept/usage/settlement boundaries, zero automatic replay |
| T8 | Owner generation plus independent acceptance generation, invalidate/consume_write/finish, X6/Y1/Y2; independently controlled DB-session loss and stale resumption; actual zero stale writes and no successor cleanup |
| T9 | Event uniqueness, reconcile/complete, S7–S9, X5/X8; duplicate/conflicting/delayed usage, known-over-cap corrections, unchanged display eligibility and retained unknowns |
| T10 | N1 journal/witness/endpoint, COVERAGE_GAP, X7; omit result, accounting, both, and observer; distinguish detected omission from fundamentally unavailable evidence |
| T11 | RecoveryContext/InvalidationResolution, bounded CLOSED-gate recovery/retention, X6–X8/Y3/Y4; restore stale DB/observer, fsync/ack ambiguity, cancellation race, capacity stop, manual recovery decisions and unchanged DATA clocks |
| T12 | W1 wrappers/exact MemoryWire extension/terminal hook; digest/profile/schema/legacy compatibility, fake-vs-native zero-I/O; full rendering token certificate missing/stale fails before secret/DNS. Synthetic counters cannot certify provider overhead or billing |

Independent implementation review must approve the concrete B1 table/projection and B8 record/signature/call-order design, check writer coverage/lock order and resolve N1. These close design specification gaps **for review**, not runtime evidence gaps. Later tests need newly owned verified disposable PostgreSQL and independently owned fake processes; none runs for this document. No application imports, migrations, backend tests, held-out inspection or operational calls are needed for prose.

**Historical v0.1.0 validation (before the P1 correction):** isolated standard-library validation (`env -i`, `python3 -I`) passed: **4 Markdown files / 130 local links and anchors**, two strict JSON blocks, eight necessity examples, field-set checks against **25 record definitions**, the 361-byte complete projection, key digest and Decimal conservation, nine transitions/five legal transition paths with replay exclusions, eight ordered trace entries/T1–T12 coverage, **42 static source references**, and 12 rejecting parser/schema controls. The adopted proposal body and earlier validation are preserved. `git diff --check` passed; the entire diff is this companion, the adoption append/header, and narrow ADR/roadmap updates. Checker and results are retained under `/tmp/ra05-w2-contract-docs-7njekgm8/`. These checks establish document consistency, not implemented transaction safety, process isolation, runtime schema correctness or live-provider guarantees. No application imports, backend tests, databases, provider/Target calls or credential access occurred.

## P1 guard-loss lifecycle correction

This documentation-only fix continues reviewed `dcafd0ca0ab03cb83f6f6f210e7f47edfdc560cc` on `codex/ra-05-w2-implementation-contract`; main/base remains `e953ee08cb32e0ba9de0f3d196e7b5e82b9fe727`. Clean branch/repository identity and actual remote main were reverified. The previous v0.1.0 wording required revocation before a successor admission but missed an invalidator committing after G loss with **no successor**. Its historical validation above did not establish safety for this case.

V0.1.1 binds invalidation and endpoint acceptance to N1's shared A boundary, requires durable deployment-wide closure before invalidation commit, pins tickets before qualification, and keeps uncertain acknowledgements/transactions closed or unresolved. Interfaces, writer inventory, X6, Y1–Y5 and T6/T8/T11 agree on the corrected order. B1–B8 and their recorded adoption are unchanged; the additional barrier mechanism remains explicitly proposed within targeted N1, with no invented approval.

Correction validation passed using isolated standard-library scripts (`env -i`, `python3 -I`): **130 local links/anchors across four documents**, three strict JSON blocks, 32 record definitions/field-set checks, existing necessity/transition/Decimal accounting checks and 12 rejecting parsing/schema controls. The added protocol model checked **six Y trace cases, all 70 order-preserving interleavings** of ticket/qualification/issuance/acceptance versus close/ack/commit/resolution, the five gate-transition definitions, pending-writer recovery and conservative accounting. It detected both the old no-barrier counterexample and a split check/write counterexample. These checks assume the specified A critical sections and durable evidence; they do **not** prove atomic IPC, fsync, process isolation or runtime fencing. Checker/model/results remain under `/tmp/ra05-guard-loss-docs-csf9mx4o/`. `git diff --check` passed; only this document differs from the reviewed HEAD, and historical B1–B8 adoption records are unchanged. No application imports, databases, backend tests, provider/Target calls or credentials were used.

Local commit then **STOP** for independent review; no push, PR, merge, branch cleanup, W2 code, W3/cache or RA-06 work.
