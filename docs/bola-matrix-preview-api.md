# Offline BOLA matrix preview

`POST /api/bola-matrix/preview` is a read-only planning operation for the
trusted operator of a self-hosted deployment. POST sends JSON to this platform
only. The operation sends no request to a Target and creates no TestCase,
ExecutionPlan, PlanAction, TestRun, assertion, or evidence.

Use existing local metadata:

- List Targets with `GET /api/targets`, then Endpoints with
  `GET /api/targets/{target_id}/endpoints`.
- List reviewed positions with
  `GET /api/endpoints/{endpoint_id}/resource-bindings`. Select exact, currently
  `confirmed` path/query bindings declared by that Endpoint. Candidate,
  rejected, unsupported body, or ambiguous positions fail closed.
- List Resources and identities with `GET /api/targets/{target_id}/resources`
  and `GET /api/targets/{target_id}/test-identities`. Select explicit Resource
  IDs and active identity IDs from the same Target. No identity or credential
  is selected automatically.
- Inspect existing assertions through
  `GET /api/resources/{resource_id}/access-assertions`. Resolved facts require
  applicable verified assertions at the explicit evaluation instant, under
  the existing M12 rules. Merely having a legacy Resource owner or an
  owner-like role/name does not supply access truth.

The IDs below are **synthetic placeholders**. Replace them with IDs from your
existing local metadata before expecting a successful preview. This example
does not create fixtures or retrieve anything from the Target:

```bash
curl --fail-with-body \
  'http://localhost:8000/api/bola-matrix/preview' \
  -H 'Content-Type: application/json' \
  --data-binary '{
    "endpoint_id": 1,
    "assignments": [
      {"binding_id": 2, "resource_id": 3},
      {"binding_id": 5, "resource_id": 6}
    ],
    "test_identity_ids": [4, 7],
    "evaluation_time": "2030-06-01T12:00:00Z"
  }'
```

For `/projects/{project_id}/tasks/{task_id}`, these assignments might select
two reviewed path positions. Mixed path/query positions are also supported.
An assignment remains an operator proposal: confirming a position does not
approve its Resource value or prove that a task belongs to a project.
Unselected positions remain unassigned. There is no complete request,
aggregate access verdict, membership proof, or permission to connect.

The response contains the exact endpoint, evaluation time, ordered identities,
and ordered `slots`. Each slot contains only its reviewed `binding` descriptor
and its independent Resource `preview`, including all `facts` and eligible
`candidates`. Relationship and expected access remain independent: owner+denied,
non_owner+allowed, shared+denied, and anonymous facts retain their explicit
meaning. Conflict and insufficient facts remain visible, along with exact
supporting assertion IDs, even when they produce no candidate. No winner or
access permission is guessed.

The preview is transient current metadata. Evaluation time governs assertion
eligibility, not historical binding review, identity activity, or auth metadata.
A fresh call can change after metadata changes. Repeated Resources may share an
immutable service preview within one call; there is no cross-request cache.
Every preview response has `Cache-Control: no-store`.

All four request fields are required. Top-level and assignment extras and all
query parameters are rejected. IDs must be JSON integers in `1..2147483647`;
booleans, floats, strings, null, and overflow are invalid. Arrays preserve
order: 1–32 assignments, 0–512 identities, and at most 512 assignment × identity
cells, including skipped facts and repeated Resources. Duplicate binding or
identity IDs and aggregate overflow produce the fixed schema error below.
An empty identity array still validates every selected binding and Resource.

Time must be an RFC3339 string with offset or `Z`, up to six fractional digits,
and an instant representable in UTC. Numeric epochs, dates alone, naive time,
and an implicit current time are not accepted. Timezone spelling may normalize;
the evaluation instant is preserved.

The actual body is limited to **65,536 UTF-8 bytes**, regardless of
Content-Length or chunking. Only `application/json`, optionally with UTF-8
charset, is accepted; Content-Encoding must be absent or `identity`.
Input is never decompressed. Invalid UTF-8/JSON, duplicate keys, non-finite
numbers, excessively nested/invalid shapes, and sensitive extra fields produce
sanitized errors without echoing field names, values, headers, or diagnostics.

The complete typed response is validated and serialized before success.
Its UTF-8 limit is **4,194,304 bytes**; nothing is truncated or partially
streamed. The exact nested schemas and error codes are available in the local
OpenAPI document at `/openapi.json` and the interactive `/docs`.

| Status | Meaning |
| --- | --- |
| 200 | Complete, allowlisted, non-executable preview. |
| 404 | Exact Endpoint, Resource, identity, or binding missing; original domain code retained. |
| 409 | Binding/access metadata conflict, inactive identity, duplicate semantic slot, or M12 assertion limit; original domain code retained. |
| 413 | `bola_matrix_preview_request_limit_exceeded`. |
| 415 | `bola_matrix_preview_media_type_unsupported`. |
| 422 | `bola_matrix_preview_invalid_request` for transport/schema errors, including duplicate IDs and aggregate overflow; mapped domain validation codes are retained if raised after valid parsing. |
| 500 | `bola_matrix_preview_response_limit_exceeded` for oversized serialization; otherwise `bola_matrix_preview_failed` for invalid dependency output, unclean Session, or internal failure. |

Both private/local and external/public-authorized metadata can be previewed.
This adds no account/authentication mechanism or public management boundary.
Public runtime remains blocked; Default Deny, exact Target origin, mandatory
allowlist, immutable execution revision, GET-only automatic execution, and
redirect restrictions remain unchanged. Consolidated matrix acceptance and
compatibility closeout are separate work; this operation does not declare M14
or public execution readiness complete.

For reproducible isolated tests and the current capability/compatibility evidence,
see [M14 offline matrix acceptance](m14-offline-matrix-acceptance.md).
