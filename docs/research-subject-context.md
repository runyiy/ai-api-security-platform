# RA-02/W3：显式身份、Resource/slot 提议和业务事实上下文

> **记录状态（[PR #139](https://github.com/runyiy/ai-api-security-platform/pull/139)）：** synthetic-only 身份/Resource/事实提议已集成。下文基点、提交时 pending 状态、验证结果和包内停止指令是历史记录；旧停止点不约束后续已授权工作。契约/安全/验收要求仍有效，采纳按精确记录、当前进度按 [roadmap](research-assistant-roadmap.md#5-固定阶段与依赖)；集成不授予操作许可。

**research-subject-v1 · IMPLEMENTED / PENDING_INDEPENDENT_REVIEW**。精确 base `e4f4de9ebb230b7dcde9d696bbfb8d731c6f283c`，分支 `codex/ra-02-w3-identity-resource-context`；本次 fetch 核验 published W2 HEAD。W2 独立 review/push 已通过依据本次交接，未合入 main。本文不签署 W3 reviewer-PASS、RA-02 COMPLETE 或其他 ADR 批准。

依据 [RA-02 验收卡](research-assistant-roadmap.md#ra-02--任务规则受限离线观察和可用测试上下文)、[DATA 采纳记录](research-assistant-adr-decisions.md#data-后续决定记录ra-02w1)、[W3 映射](research-assistant-adr-decisions.md#53-对既有-ra-02-工作包的具体映射-p)与 [INTENT 未决门槛](research-assistant-adr-decisions.md#6-adr-ra-intent显式访问语义精确配对与-session)实现。本包新增 context/proposal 元数据，真实 Target 请求、DNS、provider、费用、secret resolution、ExecutionPlan/TestRun/Finding 创建均为零。

## 1. 输入与操作者边界

[严格 schema](../backend/app/schemas/research_subject.py)仅接受整数 ID、固定枚举、aware 时间及既有 `SyntheticReference`，不接收名字、真实对象值、任意 selector/URL/body、secret、私有材料或自由文本。W1 当前版本必须仍是合资格 synthetic，Target 必须是同 context 的活跃已审关联和 loopback/private_local。W1 缺许可草稿仍可记录元数据；此接口不做许可判断，不改变 W1 的 permission_missing 或未批准预算。

每个 proposal number 表示一个人工选择的主体/资源/位置上下文；可以明确缺 identity、Resource、owner 或 slot。`resource_id=null` 保留提议，不创建 legacy Resource，也不以假 owner 满足其 NOT NULL。`owner_identity_id` 是人工提议，不读取旧 Resource.owner 来补答案。已选 binding 只是位置引用，不构成 Resource membership/映射审批；`membership=operator_proposed` 及其引用也始终是未验证提议。

| 输入维度 | 行为与限制 |
| --- | --- |
| identity_choice | `missing`、`anonymous`、`bearer`；missing 必须 ID=null，其他须显式选同 Target、active 且 auth_type 完全相符的 TestIdentity。legacy `none` 不自动转换 anonymous，失败 bearer 不降级匿名 |
| credential_binding_id | bearer 可明确选择同 actor 的 active/stored_secret/bearer binding；null 是缺项。仅读取 binding 元数据及最新 secret version **ID**，不读取 credentials/encrypted_envelope、解密或构造 AuthenticationContext |
| session | `unknown/operator_reported_valid/expired/login_page/mfa_required/authentication_failed/not_applicable`。非 unknown/not_applicable 需独立合成来源引用及不晚于 server decision time 的 aware reported_at；not_applicable 只用于 anonymous。没有网络会话检查、健康 TTL 或 baseline/probe 时间间隔 |
| credential_update | `unknown/needed/operator_reported_updated/not_applicable`；前几项用于 bearer，其他 identity 只能 not_applicable。operator_reported_updated 不是 secret 存在/有效或 session 健康证明 |
| relationship / expected_access | 分别是 owner/shared/non_owner/unspecified 与 allowed/denied/unspecified，非 unspecified 需 fact_reference。保存在 `proposal` 中，服务生成 `operator_proposed_unverified`，不能填 verified/provenance/source_test_run_id |
| assertion_ids | 0–16 个独立选择的既有 M12 引用；精确匹配已核验 Resource/Identity。candidate、过期或不在当前支持集合的引用显示缺项，不能提升为 verified |
| sources | 0–8 个 `{observation_id, source_entry_index}`，仅软引用；不持久化 actor/resource aliases、session claims、payload、digest 或源内容副本 |
| review / correction | 既有固定 synthetic_fixture ID/version；记录操作者声明，不解释为预算、执行或架构审批证据 |

**业务事实输入/审阅复用旧边界：** 操作者在已确认的合成 Resource/Identity 上显式使用既有 `POST /api/resources/{resource_id}/access-assertions`；candidate review 使用 `POST /api/resources/{resource_id}/access-assertions/{assertion_id}/review` 和 `review_resource_access_assertion()` 的追加 human_verified 逻辑。然后在 W3 明确选择相应 IDs。W3 不代理或自动调用这些写入口、不从 observation 生成 candidate/verified/observed_baseline、不改历史 review。旧 operator API 是全局管理面，**没有变成项目隔离 API**；新流程不得用旧全局 list 寻找其他项目资料。

**凭据更新复用旧边界：** 仅在独立允许的合成账号上，操作者使用现有 `PUT /api/test-identities/{identity_id}/token`，由 `BearerCredentialService.update()` 与 `StoredSecretProvider` 保存加密版本；测试只使用本次专用数据库的 disposable synthetic secrets。token 不出现在 W3 请求/返回或本文示例。W3 后续读取发现版本 ID 改变则增加 `credential_changed`；操作者追加 correction 记录更新需要/声明，但 `session_health_unverified` 不会因此消失。未来执行仍只能经既有 AuthenticationContext，W3 不调用该构造/解析路径。

## 2. 事实与 NEEDS_INPUT 输出

[服务](../backend/app/services/research_subject.py)在项目核验后复用 `resolve_resource_access()`，按 server aware UTC decision time 读取**全部**合资格 verified facts，保留其 asserted_at、半开有效期及 256/257 上限。assertion_ids 不是过滤冲突的白名单；即使操作者只选一个 allowed ID，另一个 eligible denied 仍产生 conflict。不会按新旧、confidence 或 provenance 选赢家；不持久化 resolver 结果作为真值缓存。

输出把 `proposal` 和当前 `facts` 分开；facts 仅含 state、relationship、expected_access、supporting_assertion_ids，不带旧对象值、凭据或 TestRun body。`recorded_at` 是追加时间，`evaluated_at` 是本次元数据观察时间；历史版本读也检查当前资格，**不是过去许可/会话的快照**。

| 情况 | 实际可观察结果 |
| --- | --- |
| owner+denied、non_owner+allowed、shared+allowed 的独立 verified facts | 原样保留；不创建 owner-success baseline、请求或 Finding |
| 只有人工 proposal，或缺 Resource/identity/facts | proposals 可保存；facts 仍 insufficient/unspecified；identity_missing/resource_missing/owner_unknown/facts_missing 等可见 |
| eligible verified 冲突 | facts.state=conflict、保留全部 supporting IDs、facts_conflict；不把拒绝判断当安全 |
| bearer 过期、登录页、MFA、失败或未知 | 原 session 声明保留，相应 session_*、credential_update_needed 可见；始终有 session_health_unverified |
| slot candidate/rejected/body/元数据不合法 | slot_unavailable；query/nested/multiple-path-parameter、非 GET 或不匹配旧 `_id` grammar 则 preview_only_shape；这不是新 renderer |
| proposal 与当前 facts 明确矛盾 | proposal_fact_conflict；不覆盖任一事实或选择胜者 |
| 所有看似完整的字段 | 仍为 NEEDS_INPUT；budget_unapproved、intent_decision_pending、membership_unverified 保留，execution_preparation_allowed=false、execution_authorized=false |

W1 permission/revision/Scope/预算缺项仍由 [W1 原接口](research-intake-context.md#2-输入预算与准备度)提供；W3 不重新解释 permission 或 null budget。缺 allowed baseline、session 证明方法/TTL、pair 间隔、binding/secret-version 变化怎样影响未来 intent、预算/observer 等仍留给原 ADR 检查点。这个接口不是 RA-04 intent、plan、approval、verifier 或调度器。

## 3. 生命周期、隔离与事务

沿用 [W1 的归属锁](research-intake-context.md#3-持久化隔离与回退)及 [W2 的生命周期](research-observation-intake.md#4-生命周期与操作责任)：先锁精确 project/context，再验证活跃关联与 Target，然后锁精确同 Target identity、Resource、Endpoint、binding、assertion。关闭后 latest/history 直接 unavailable，不读取新归属项目的 legacy 元数据；外项目和不存在引用相同固定错误，不泄漏其存在、属性或数量。

Identity 元数据通过 `load_only(..., raiseload=True)` 加载；M12 resolver 的 db.get 复用这次已限定的 identity/resource，不为它加载 credentials。与旧 bearer update 共用 identity→binding 锁顺序；读取最新非秘密版本 ID 后持锁到外层事务结束。后续请求再重验，锁不构成任何执行审批。

Sources 在接受事务和每次消费时经 W2 read 重验：精确 context、preparation/Target、registry、recovery gate、原 expires_at、hold、revocation、payload 完整性及 entry index。expired/held/deleted/quarantined/revoked、context version 变化、关闭/转移或 source 已清理时，读取仅返回自己的版本 receipt，`availability=unavailable`、proposal/facts=null，不复制原内容兜底；创建/更正整体拒绝。当前合资格 hold 结束后可按 W2 原规则恢复可用，不重置 expires_at。

Correction 不能换 Target，也不能删除/替换前版已经依赖的 source 引用。不能以更正把被清理来源的内容复制成“独立”上下文。独立人工事实仍须有自己的合成引用及原 M12 边界；系统不从观察推断这种独立性。Source 使用软 ID，W2 90 天 tombstone 删除不受 W3 FK 阻碍；剩下的 W3 历史只是原提议、固定枚举与引用，不是恢复源内容的副本。

服务要求 clean Session，context 锁及 savepoint 覆盖核验、W2 source-read audit 和唯一的新域 INSERT；不 commit 调用者事务。API 在 typed response 校验、完整编码后才 commit。Source audit 满额/失败、版本冲突、输入/数据库或响应故障不返回成功，不留半个版本或审计前缀。W2 audit rotation、撤销 cleanup、最终 tombstone clock 修复逐字不变。无全局 list、export、自动清理/重试、provider 或网络入口。

## 4. API、容量和 migration

[路由](../backend/app/api/routes/research_subjects.py)在 `/api/research-projects/{project}/contexts/{context_id}/subjects` 下：

| 方法与后缀 | 输入 / 行为 |
| --- | --- |
| POST `/{number}` | 完整 SubjectInput，首次 version 1；已有 number 拒绝 |
| POST `/{number}/versions` | expected_version、correction_reference、完整 proposal；原子追加，旧版本不覆盖 |
| GET `/{number}` | 最新版本及当前资格/缺项 |
| GET `/{number}/versions/{version}` | 精确历史版本，当前资格重验；无历史权限假设 |

实际 UTF-8 body ≤8192 bytes，逐 chunk 检查；仅 application/json、identity encoding，拒绝 query 覆盖。复用 W2 BoundedJSON，拒绝 duplicate keys、额外字段、浮点/布尔 ID、BOM/无效编码、surrogates、NaN/Infinity/外部引用；结构在递归分配前限制为深度 5（root 0）、16384 nodes。服务再校验 constructed/mutated schema 对象，禁用含输入 repr 的序列化 warning。ID 1..2147483647；project 1..1000000；number/version 1..1024；每 context **共最多 1024 个版本行**，更正也计数，无静默裁剪或自动重试。数据库 canonical/proposal JSONB 另限 16384 bytes。完整 typed response ≤32768 bytes，固定错误 ≤256 bytes，所有响应 no-store。

新增一张 [research_subject_versions](../backend/app/db/models/research_subject.py) 表：proposal/version、原 W1 version、review/correction、时间与固定 provenance；context/Target 及 context/W1 version 使用组合 RESTRICT FK；proposal/version 组合唯一。只保存合资格合成元数据，保留 correction 历史；没有新敏感部署/备份保证，真实私有数据仍拒收。

[增量迁移](../backend/alembic/versions/e9a1c3d5f7b8_add_research_subject_context.py) `d8f0b2c4e6a8 → e9a1c3d5f7b8` 不 backfill 或读取/更新旧行。空 W3 表可 downgrade；非空持 ACCESS EXCLUSIVE 锁后抛出 `research_subject_populated_downgrade_blocked`，不删除有价值版本。应用回退须停用新入口、保留新表；实际历史处置另行盘点/批准，不提供 destructive cleanup。W2 payload/hold/tombstone、M12 review、TestRun、M13 五类证据、fingerprint、report 原样保留。

## 5. 合成操作者示例与验收

可复现入口是 [API 示例测试](../backend/tests/api/test_research_subjects.py) `test_synthetic_operator_example_with_existing_human_fact_review`，在 owned fixture 内通过现有 API 明确写入 non_owner+allowed，再调用 W3；随后人工 review 一个 conflicting candidate，检查原 candidate 不变及新的 conflict。没有模型自评、网络请求或 held-out corpus 材料。

下面的 `1..5` 只是显示完整请求形态的占位 ID；必须替换为同一个已审核的**合成** context/Target/actor/Resource/Endpoint/binding。测试使用实际生成的 IDs，不把这些数字当许可。

```json
{
  "format": "research-subject-v1",
  "context_version": 1,
  "target_id": 1,
  "identity_choice": "anonymous",
  "test_identity_id": 2,
  "credential_binding_id": null,
  "session_state": "not_applicable",
  "session_reported_at": null,
  "session_reference": null,
  "credential_update": "not_applicable",
  "resource_id": 3,
  "owner_identity_id": null,
  "endpoint_id": 4,
  "binding_id": 5,
  "relationship": "non_owner",
  "expected_access": "allowed",
  "fact_reference": {"kind": "synthetic_fixture", "fixture_id": 1, "version": 1},
  "membership": "unknown",
  "membership_reference": null,
  "assertion_ids": [],
  "sources": [],
  "review": {"kind": "synthetic_fixture", "fixture_id": 1, "version": 1}
}
```

若无 M12 verified facts，这个输入只记录提议，facts 仍 insufficient。上述测试先写入独立人类事实再选择真实 assertion ID，实际断言 HTTP 200、facts.expected_access=allowed、supporting IDs 精确匹配、status=NEEDS_INPUT、provenance=operator_proposed_unverified 和 execution_authorized=false。审阅另一个 denied candidate 后 facts.state=conflict，不能通过只选择旧 allowed ID 隐藏冲突。将 resource_id 改成其他项目或不存在 ID 均同一 409；sources 到 expires_at 精确边界后 latest/history 均 unavailable，内容不返回。

运行前必须按[独占 PostgreSQL runbook](m14-offline-matrix-acceptance.md#reproducible-isolated-local-run)配置并独立验证库，再 import 应用。不能直接使用 `.env`/默认/shared/operator 库。以下在 backend、项目 Python 3.12 venv 下串行执行：

```bash
python -m alembic current
python -m alembic heads
python -m alembic upgrade head
python -m pytest tests/api/test_research_subjects.py::test_synthetic_operator_example_with_existing_human_fact_review
python -m pytest tests/services/test_research_subject.py tests/services/test_research_subject_concurrency.py tests/api/test_research_subjects.py tests/schemas/test_research_subject.py tests/migrations/test_research_subject_migration.py
python -m pytest tests/services/test_research_context.py tests/services/test_research_context_isolation.py tests/api/test_research_contexts.py tests/migrations/test_research_intake_migration.py tests/services/test_research_observation.py tests/services/test_research_observation_lifecycle.py tests/api/test_research_observations.py tests/api/test_research_observation_lifecycle.py tests/schemas/test_research_observation.py tests/migrations/test_research_observation_migration.py
python -m pytest
python -m evaluation.ra01 verify
python -m pip check
python -m alembic current
python -m alembic heads
git diff --check
```

验收映射：schema/API 测试覆盖完整字段、8192/8193 bytes、16/17 assertions、8/9 sources、strict ID/时区与恶意输入；service 测试覆盖关系/访问独立、M12 冲突与256/257、source 生命周期/软引用清理、1024/1025容量、凭据版本变化、零能力/legacy snapshots；并发测试以 pg_blocking_pids 确认真实锁等待，覆盖 close、hold、更正及既有 token update。Migration 测试覆盖 fresh、populated legacy/W1/W2、旧 pair/confirmed review/report/指纹历史、组合 FK 及空/非空回退。故障测试使用 PostgreSQL 实际 INSERT 错误和响应编码异常验证原子性。

## 6. 完整变更清单

本包相对精确 W2 base 共 53 个路径；既有生产代码只有新 route/model 注册，原 W1/W2 service、凭据/M12/执行/M13 实现和规范原文不变。既有测试仅推进 latest head、后续表集合与 snapshot 排除新表，固定历史 revision 与原断言保留。

```text
backend/alembic/versions/e9a1c3d5f7b8_add_research_subject_context.py
backend/app/api/routes/research_subjects.py
backend/app/db/models/__init__.py
backend/app/db/models/research_subject.py
backend/app/main.py
backend/app/schemas/research_subject.py
backend/app/services/research_subject.py
backend/tests/api/test_bola_matrix_preview.py
backend/tests/api/test_openapi_binding_candidates.py
backend/tests/api/test_openapi_body_binding_candidates.py
backend/tests/api/test_research_subjects.py
backend/tests/api/test_resource_access_resolution.py
backend/tests/generators/test_bola_matrix.py
backend/tests/migrations/test_asset_candidate_dns_validation_migration.py
backend/tests/migrations/test_asset_candidate_evaluation_migration.py
backend/tests/migrations/test_asset_enrollment_decision_migration.py
backend/tests/migrations/test_asset_hostname_rule_migration.py
backend/tests/migrations/test_authorization_revision_lifecycle_migration.py
backend/tests/migrations/test_authorization_revision_migration.py
backend/tests/migrations/test_credential_binding_migration.py
backend/tests/migrations/test_endpoint_resource_binding_migration.py
backend/tests/migrations/test_exact_revision_execution_migration.py
backend/tests/migrations/test_execution_plan_approval_migration.py
backend/tests/migrations/test_execution_plan_migration.py
backend/tests/migrations/test_finding_evidence_excerpt_migration.py
backend/tests/migrations/test_finding_evidence_fingerprint_migration.py
backend/tests/migrations/test_finding_evidence_pairing_migration.py
backend/tests/migrations/test_finding_evidence_retention_migration.py
backend/tests/migrations/test_finding_evidence_similarity_migration.py
backend/tests/migrations/test_finding_structured_evidence_migration.py
backend/tests/migrations/test_observed_access_assertion_migration.py
backend/tests/migrations/test_openapi_credential_provenance_migration.py
backend/tests/migrations/test_openapi_decoded_provenance_migration.py
backend/tests/migrations/test_research_intake_migration.py
backend/tests/migrations/test_research_observation_migration.py
backend/tests/migrations/test_research_subject_migration.py
backend/tests/migrations/test_resource_access_assertion_migration.py
backend/tests/migrations/test_resource_access_assertion_review_migration.py
backend/tests/migrations/test_safety_decision_audit_migration.py
backend/tests/migrations/test_stored_secret_migration.py
backend/tests/migrations/test_target_enrollment_provenance_migration.py
backend/tests/migrations/test_target_network_mode_migration.py
backend/tests/research_intake_fixtures.py
backend/tests/research_subject_fixtures.py
backend/tests/schemas/test_research_subject.py
backend/tests/services/test_asset_candidate_dns.py
backend/tests/services/test_bola_binding_matrix_preview.py
backend/tests/services/test_bola_binding_selection.py
backend/tests/services/test_bola_matrix_preview.py
backend/tests/services/test_research_subject.py
backend/tests/services/test_research_subject_concurrency.py
docs/research-assistant-roadmap.md
docs/research-subject-context.md
```

## 7. 本次验证记录与剩余限制

在真实 Ubuntu WSL 使用项目 `.venv` Python 3.12.3 / PostgreSQL 16.15。应用 import 前独立核验新建 database/user=`ra02_w3_test`、`127.0.0.1:55457`、当前用户拥有的 `/tmp/ra02-w3-test.9w0Nzy/data`；初始 public 表数 0、其他 client backend 数 0，显式配置覆盖默认数据库，开发 requirements 已满足。临时 DSN、key、日志均在 Git 外。未访问或修改操作者实际 credentials。

| 实际检查 | 结果 |
| --- | --- |
| fresh Alembic current / heads / upgrade head | 初始无 revision；全部增量成功升级；最终唯一 head/current 为 `e9a1c3d5f7b8` |
| 初始服务 / 扩展 schema、API、并发 / 加入迁移与容量检查 | 分别 30、75、87 passed；无代码失败。constructed-model 测试出现含输入 repr 的 Pydantic warning 后，修正校验前的序列化以禁用该 warning；未弱化测试 |
| W1/W2/W3 合并 focused（新增 token 并发测试前） | **437 passed, 1 warning**；原 W1/W2 350 项继续通过 |
| 最终 W3 五份专项测试 | **88 passed, 1 warning**，含 token update 与 W3 元数据事务真实锁等待 |
| 文档中的独立 synthetic operator 示例 | **1 passed, 1 warning** |
| 最终 `python -m pytest` | **2527 passed, 55 warnings**；无失败/跳过。第一次全量 2526 passed 后新增 token 并发案例，再执行最终全量；不是重试未变失败至绿 |
| `python -m evaluation.ra01 verify` | **VERIFIED**，freeze digest 仍为 `692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a` |
| `python -m pip check` | No broken requirements found；本机 pip cache 不可写提示不影响结果 |
| scope、53 路径清单、文档 JSON、本地链接/anchors、完整 diff、`git diff --check` | 通过；W3 文档 14、roadmap 34 个本地链接/anchors。首次链接检查发现 W2 标题锚点拼写错误，已修正；无运行时变更 |

55 warnings 为既有 collection/TestClient 提示。验证后仅停止本次新建 PostgreSQL，并确认 postmaster.pid 消失；未操作其他实例。

剩余限制保持显式：synthetic-only；没有私有数据/部署/备份或 export 放行；没有会话健康证明、TTL、自动登录/MFA、baseline/probe 配对、intent/bridge、真实预算批准、provider 或执行。Credential binding/version 只说明本项目元数据关联，不说明 secret 可解密或 Target 接受它。W1 的 facts_missing 占位仍保留，W3 通过独立上下文接口展示细化缺项，不把多个 API 拼成执行就绪状态。旧全局 operator 管理入口未获项目隔离保证。本文与测试是实现方证据；W3 待独立 Review Project 审查，本地 commit 后停止，不 push、不开始后续包。
