# Research Assistant 产品与能力契约 — RA-01/W1

**文档版本：0.1.0 · 2026-09-09 · IMPLEMENTED / PENDING_INDEPENDENT_REVIEW**

| 版本与审查项 | 固定值 |
| --- | --- |
| 工作包 | RA-01/W1；仅规格与证据，不新增 runtime |
| 精确源码起点 | `cec06c5c5f497e0f277b29d8316fa9b7cf726725` |
| 实施分支 | `codex/ra-01-w1-product-contract` |
| 规划来源 | [roadmap 第 6 节 RA-01 验收卡](research-assistant-roadmap.md#ra-01--产品契约基线及可执行评测与-adr-规范)；Issue #132 / merged PR #133 仅为已完成规划 provenance |
| 冻结含义 | 固定本版本供独立审查；不是 reviewer-PASS、架构批准、执行许可或 RA-01 COMPLETE |
| 下一依赖 | 顺序保持 RA-01/W1 → RA-01/W2 → RA-01/W3；本次提交后停止，不开始 W2 |

[架构决策](architecture-decisions.md)、[安全模型](security-model.md)优先于本文；[Level 3 roadmap](level3-roadmap.md)的公网门槛继续有效。既有规范中的切片“later/pending”按历史理解；M14-01 至 M14-06 的已完成离线范围不重开。当前交接授权覆盖 W1，本次工作流为实施、验证、**本地 commit 后停止**，由独立 Review Project 审查并 push 精确批准的 HEAD。历史 NOT_AUTHORIZED/push 文字不覆盖当前交接，也不批准未决重大设计、真实 provider 支出或公网请求。

## 1. 产品边界与证据口径

产品服务**一位可信操作者、本地优先、自控 local/private lab 的 BOLA/IDOR 研究**，保留 FastAPI/PostgreSQL。可信操作者负责管理面和测试许可；拥有部署不等于获准测试任意 Target。当前不是多租户 SaaS、全漏洞扫描器、浏览器登录 agent 或自主攻击循环；没有研究任务 CLI、scheduler、平台登录/RBAC 或自动报告提交路径。[S01]、[S09]、[S16]、[S23] 这些是入口/调用路径的静态核对结论，不是完整部署安全认证。

本文统一使用四类证据，避免把未来要求写成现在的输出：

- **C（current/source）**：精确基线代码中的行为；每项能力在第 4 节关联源码、限制及测试。
- **T（test assertion）**：已存在测试里的合成输入、断言或 fake 结果；不等于真实 Target/真实 provider 验收。
- **O（observed）**：本次确实运行的隔离回归及结果，见第 8 节。测试通过只证明对应断言在该环境通过。
- **P（proposed）**：已规划的产品要求，W1 不实现；下文示意输入不是新 schema，示意结论不是运行产物。H（historical）仅指 README/roadmap/M14 runbook 中旧作者或 CI 记录，不冒充本次重跑。

现有最接近产品的两条路径是：显式 M14 离线预览，以及独立准备的 legacy TestCase/精确计划执行、分析和人工报告。**两者之间没有现成自动桥接。** 本合同冻结这个缺口，不用一张连续图掩盖它。

## 2. 输入、输出与人工责任

以下 P 要求具体化已有 roadmap 范围；W2 决定机器可读评测 schema 和评分，W3 处理指定 ADR。不得把本文表格当作可 POST 的 Research Assistant 请求。

| 输入 | 当前可用承载与限制（第 4 节证据） | P：操作者必须补充/确认 |
| --- | --- | --- |
| 许可与范围 | Target、Scope、AuthorizationRevision；TestCase planner 使用 Target 当前绑定的唯一 revision，不能在该 service 参数里任意覆盖 revision（C04/C06） | 一个项目的许可来源、有效期、自动化规则；显式选择并核对 Target/revision/Scope；不可合并 grants |
| 研究上下文 | Endpoint、Resource、TestIdentity、confirmed binding、M12 assertions（C01–C03） | 一种有界本地 observation、来源与数据资格；明确 Resource-to-slot 分配及业务关系；不是 import 即执行或 import 即 verified |
| 访问事实 | relationship 与 expected_access 分开；只有合资格 verified facts 参与解析（C02） | 谁在所选时点应 allowed/denied，来源和有效期；共享、撤销、owner-denied 不得由角色名称代填 |
| 身份/会话 | 当前 AuthenticationContext 支持 `anonymous`/`none` 和加密 bearer；M14 仅精确 `anonymous` 有匿名候选语义（C03/C07） | 初始桥接显式选 `anonymous` 或 `bearer`；凭据经既有边界输入，人工取得/续期，不提供任意 headers/cookies |
| 预算/停止 | 当前 per-Target rate、网络 admission、cancel/progress 是执行控制，不是任务总预算账本（C06/C08） | 请求/时间/速率/并发/模型 token/费用的明确上限；health/baseline/probe 全计入；未知消耗不按零处理 |
| 审批与验证 | plan digest 决策是 service 能力；Finding review 是人工 API（C05/C12） | 审完整的有限计划集合；实质变化重新规划并履行适用审批；核实会话、对象证据、业务影响及最终 Finding |

| 可观察输出 | C：当前实际输出 | P：Research Assistant 仍需提供 |
| --- | --- | --- |
| 覆盖/候选 | M14 typed `slots`、每个身份的 `facts`、合资格 `candidates`、supporting assertion IDs；没有整体访问结论（C01–C03） | 选定范围的覆盖/缺口台账；unsupported、unknown、unexecuted 都可见，不得记“安全” |
| 精确动作 | service 返回 ExecutionPlan/PlanAction，含 URL、actor/binding 引用、revision、digest、count；不返回 secrets（C04/C05） | 有界 candidate-to-intent/plan 转换及可审视图；baseline/probe 各一个单 GET 计划 |
| 执行记录 | execute API 返回 `TestRunRead`，包括实际状态、source body、duration/error、revision/plan 引用；失败也可能返回 error TestRun，不能只看平台 HTTP 200（C06/C10） | 版本化上下文、显式配对及 session-aware verifier；完整任务暂停/恢复与预算核算 |
| 分析/证据 | 分析 API 返回 `outcome/reason/confidence/severity/finding`；potential 才追加 M13 证据（C09/C10） | assertion-aware 证据包、缺失原因和适用性说明；独立访问真值不被旧 owner 假设覆盖 |
| AI/报告 | Mock 分析记录；confirmed Finding 的版本化 Markdown 报告（C11/C12） | 真实 provider/用量、通用 intent 报告与人工成本/外部反馈账本；均不是当前能力 |

人工审核点有不同含义：binding review 确认位置；access assertion review 确认访问事实；exact-plan approval 允许特定动作；Finding confirmation 确认漏洞。任何一个都不能替代其他点。P 正常流程应集中呈现有限精确计划和异常，已批准计划执行期间无需例行逐请求点击；当前尚无这个产品入口或任务状态机。

## 3. 真实调用图与缺失连接

图中的箭头表示源码调用或明确的持久化后消费；不同 HTTP 操作需要调用方主动发起。函数名定位见 S 引用，测试观察见 T 引用。

### 3.1 只读 M14 与 M12 解析

```text
POST /api/bola-matrix/preview
  _read_request → BOLAMatrixPreviewRequest 校验
  preview_matrix
    → preview_bola_binding_matrix
      → select_bola_binding（先校验全部明确选定 slots）
      → preview_bola_matrix（每个 distinct Resource 一次）
        → resolve_resource_access（每个明确选定 identity、显式 time）
        → plan_bola_matrix（接收全部 facts，仅合资格 resolved facts 生成候选）
    → BOLAMatrixPreviewResponse.from_preview → 有界 UTF-8 JSON → 返回，结束
```

调用链：[S02]、[S03]、[S04]、[S05]、[S06]、[S07]、[S38]；真实 HTTP/DB 的委托 spy 在 [T01] `test_independent_truth_nested_positions_and_real_pipeline` 断言 composer/selector/Resource preview/resolver/planner 次数及顺序，保留真实返回值。解析器还可由 `GET /api/resources/{resource_id}/access-resolution` 单独调用；observed assertion 派生和 candidate review 是另行主动发起的写操作，**不在 preview 内**。[S08]、[S28]、[S39]、[T15]、[T16]

此链不调用 credential、AI、Executor、Gateway；不创建 TestCase、ExecutionPlan、PlanAction、TestRun、assertion 或 evidence，不持久化 preview，不跨请求缓存。`evaluation_time` 只管 assertions 的资格；binding review、identity activity/auth metadata 都是当前读值，不能宣称历史快照或执行前冻结。confirmed slot 不证明 Resource 值经批准，也不证明 parent-child membership。T01 的零副作用 guards、全表/配置前后快照覆盖成功与失败；不是靠 POST 名称推断只读。

### 3.2 既有精确计划执行与后续证据

```text
可选 legacy 起点（独立 HTTP 操作）：
POST /api/test-cases/generate/bola
  generate_bola_cases → generate_bola_test_cases → TestCase 持久化

显式 service 调用（当前无已注册的计划创建/审批 HTTP 入口）：
TestCase + 显式 credential_binding_id（匿名为 None）
  create_test_case_execution_plan
    → build_test_case_url → detect_resource_binding
    → create_execution_plan → compute_plan_digest_v1 → plan/action + safety audit
必要人工决策 → record_plan_decision → validate_persisted_plan_integrity → exact digest record

独立 HTTP 操作：POST /api/execution-plans/{execution_plan_id}/execute
  execute_execution_plan → PlanExecutionService.execute
    → integrity + ONE GET + TestCase/Resource provenance
    → canonical TestRun 已有时直接返回（不重新发送）
    → 当前 Target/revision/必要 approval + BearerCredentialService.resolve_binding
    → build_authentication_context → apply_authentication_context
    → PostgreSQL claim/cancel/canonical 检查 → prepare_attempt
    → PolicyEnforcedHTTPExecutor.execute
      → ScopePolicyEngine.evaluate → PostgresRateLimiter.wait
      → refresh_authorization（renew claim、复核 integrity/revision/approval/Scope）
      → 再 evaluate + safety audit + private_local gate
      → before_network / mark_network_started（含 fencing/cancel 检查）
      → NetworkGateway.request
        → PostgreSQL admission / kill / permits
        → destination policy → 固定 IP 连接 → peer 校验 → 有界响应
    → _finish → canonical TestRun + safety audit + TestCase 状态；释放 claim

独立 HTTP 操作：POST /api/test-runs/{test_run_id}/analyze + baseline_test_run_id
  FindingAnalysisService.analyze_test_run → analyze_bola_run
    → potential 时原子追加 Finding / structured evidence / excerpt /
       fingerprint / similarity / retention binding
独立、可选：POST /api/findings/{finding_id}/ai-analysis → AIAnalysisService → MockAIProvider
人工：PATCH /api/findings/{finding_id}/review → review_finding
确认后独立：POST /api/findings/{finding_id}/reports → SecurityReportService.generate
  → render_security_report_markdown → SecurityReport
读取：GET /api/reports/{report_id}/markdown
```

定位：[S09]–[S27]、[S32]、[S33]、[S36]、[S42]；T03–T14。baseline 和 probe 必须分别准备、执行两个精确单动作计划，再显式选择两条真实 TestRun；一个存有两个 actions 的 plan 当前不能执行。plan 创建与 approval service 均 flush，由调用方负责事务提交；完成判据是 canonical TestRun，`_finish` 不把 progress 改成研究任务完成状态。不能虚构已有 API/CLI 完整流程。route 注册和全路径静态搜索未找到调用这两个创建/决策 service 的 HTTP route。[S01]、[S10]、[S11]、[S12]

旁路兼容：`POST /api/test-cases/{test_case_id}/execute` → `TestExecutionService.execute` 仍存在于 `single_process`；`multi_process` 在 route 入口拒绝。即使单进程，当 revision 要求人工审批时也必须走 exact plan。legacy direct TestRun 没有 execution_plan_id，不等于精确计划结果。[S09]、[S13]、[T04]

独立取消入口是 `POST /api/execution-plans/{execution_plan_id}/cancel` → `cancel_execution_plan` → `ExecutionPlanCancellationService.request_cancel`。它只在未跨网络边界时记录 durable cancellation；已有 canonical 或 network_started/in-doubt 返回 409，不声称撤回已发送的请求。[S09]、[S44]、[T08]

**缺失的 P 桥接：** M14 output → 经确认且版本化的 Resource-to-slot/访问/会话 context → candidate-to-intent/单动作 plans → assertion-aware verifier。目前没有上述消费者、版本化 intent 或研究任务编排；图 3.1 的结束处没有指向图 3.2 的自动边。M8 仅协调被显式请求执行的 plan，不是 discovery/research scheduler；AI 也没有反向执行边。

## 4. 当前能力与兼容性矩阵

下表 C 是当前实现，不是未来承诺。T 引用均固定到本次源码基线；其中的 named tests 已阅读相关断言，并包含在第 8 节的回归运行中。静态缺口判断和 mock 测试不得包装成真实端到端输出。

| ID / 能力、具名入口 | 精确基线来源 | T：具体证据/可见结果 | 限制与 P 依赖 |
| --- | --- | --- | --- |
| C01 M14 transport/composer：`preview_matrix`、`preview_bola_binding_matrix` | [S02] / [S03] / [S07] | T01 `test_fresh_metadata_and_late_failures_are_request_wide`：错误整体返回；T02 `test_real_four_mib_serialized_boundary`：fake composer、序列化结果加空白至 4 MiB/多 1 byte，断言成功/500 且无部分输出 | 只读瞬态；没有 plan。1–32 assignments、0–512 identities、最多 512 cells（重复/跳过照计）；输入 65,536 bytes、输出 4,194,304 bytes；超限不截断/不返部分结果 |
| C02 M12：`resolve_resource_access`、`derive_observed_access_assertion`、`review_resource_access_assertion` | [S06] / [S08] / [S28] / [S39] | T01 `test_history_complementary_dimensions_and_uncertainty`；`test_late_257_assertions_return_no_partial_matrix`；T15 `test_eligible_owner_baseline_derives_exact_candidate`；T16 `test_review_appends_exact_immutable_outcome` | verified 且 asserted_at ≤ time；valid_from/valid_until 非空时分别要求 valid_from ≤ time、time < valid_until；各维度独立合并，冲突不选最新/高 confidence/provenance 赢家。上限 256 eligible，257 失败。observed 仅生成 unspecified+allowed candidate；review 追加 human_verified 行、不改原 candidate；对 unspecified relationship 不补所有权 |
| C03 slot/facts：`select_bola_binding`、`plan_bola_matrix` | [S04] / [S05] / [S06] / [S38] | T01 `test_independent_truth_nested_positions_and_real_pipeline`：八种关系/访问组合；T17 `test_confirmed_body_never_reads_declaration_or_evaluates_pointer` | path/query 精确 confirmed 声明；body 拒绝。authenticated candidate 还需明确 relationship；仅 `anonymous` 走匿名语义，`none` 不自动映射。角色/名称不补事实，slot 不证明 membership |
| C04 legacy intent/planner：`generate_bola_cases`、`create_test_case_execution_plan`、`build_test_case_url` | [S10] / [S13] / [S14] / [S29] | T03 `test_valid_test_case_derives_one_get_action_and_scope_snapshot`：确定 URL/来源；`test_invalid_endpoint_resource_rendering_fails_closed`：多参数/类型/非 GET 无 plan | generator 使用 owner 字段和旧 expected statuses，可生成 PATCH/DELETE metadata；不能执行这些方法。builder 只认一个 `{<name>_id}`（首字母英文），类型须匹配且替换后无剩余 braces，不消费 M14 reviewed binding |
| C05 plan/approval：`create_execution_plan`、`record_plan_decision` | [S11] / [S12] / [S30] | T05 `test_approval_snapshots_exact_plan_digest`、`test_material_persisted_mutation_invalidates_integrity_and_approval`；T04 `test_multi_action_plan_fails_closed_before_executor` | 存储 1–100 actions、provenance 字段可空，均不表示可执行；execute 另限一个 GET 且完整 provenance。没有批量审批 UI；新的/变化动作不继承审批 |
| C06 execution/policy：`PlanExecutionService.execute`、`ScopePolicyEngine.evaluate`、`PolicyEnforcedHTTPExecutor.execute` | [S09] / [S15] / [S18] / [S19] | T06 `test_approval_revoked_during_rate_wait_blocks_gateway`、`test_scope_narrowing_during_rate_wait_blocks_gateway`、`test_external_public_mode_remains_blocked_before_gateway`；T18 `test_platform_allowlist_remains_mandatory` | Default Deny、唯一当前绑定 revision、Scope 只收窄；批准不绕过检查。返回 canonical 只是读旧结果，撤销后也不发第二次请求；未执行不能算安全 |
| C07 credentials：`resolve_binding`、`build_authentication_context`、`apply_authentication_context` | [S15] / [S17] / [S27] / [S37] | T19 `test_bearer_never_uses_legacy_plaintext_credentials`、`test_rejects_manual_authorization_header`；T07 `test_legacy_plaintext_only_bearer_identity_fails_closed` | AES-GCM stored secret，精确 active bearer binding；匿名无 binding。未做 JWT expiry/真实 session-health 探测、自动续期/MFA；preflight 读取凭据不等于等待后再次核验会话，见第 6 节 |
| C08 M8/network：`prepare_attempt`、`mark_network_started`、`NetworkGateway.request`、`_BoundNetworkBackend.connect_tcp` | [S16] / [S19] / [S20] / [S21] / [S42] | T08 `test_same_plan_concurrent_processes_execute_one_local_request_and_replay`：本地计数 1/一个 canonical；`test_network_started_crash_remains_in_doubt_without_second_request`：403、无新增请求；T20 redirect/peer/size 断言 | PostgreSQL rate/claims/leases/fencing/cancellation/permits；不是 worker 或任务预算系统。默认网络 cap 1,000,000 bytes、各 I/O timeout 5s；不是整个研究任务或整个响应绝对墙钟 deadline。in-doubt 不盲重放 |
| C09 精确分析配对：`FindingAnalysisService.analyze_test_run` | [S22] / [S24] / [S32] | T09 `test_invalid_pair_never_falls_back`：409；`test_older_exact_pair_ignores_newer_decoy_and_legacy_owner`：用指定旧 baseline；T07 本地 secure/vulnerable 两模式 | 只分析 `bola_cross_owner` + `owner_baseline` 类型、同 endpoint/resource、不同 actor 的明确 pair；不回读 owner 字段选 baseline。没有 M12 assertion/context/session 或 pair revision 相等检查，不能宣称已支持新语义 |
| C10 M13：`analyze_bola_run`、`_persist_retention_binding`、`read_finding_evidence` | [S13] / [S22] / [S24] / [S31] / [S32] / [S40] / [S41] | T10 `test_new_finding_exact_binding_allowlisted_read_and_retry`、`test_six_layer_savepoint_rolls_back_every_failure`；T11 `test_changed_source_body_conflicts_even_when_rule_no_longer_succeeds` | structured/excerpt/fingerprint/similarity/retention append-once；精确 UTF-8 source string（None 为零字节），无归一化；similarity 不判授权/分类。TestRun body 另存，64,000 原始 bytes 截取后 decode；retention 不删除它 |
| C11 advisory AI：`analyze_finding_with_ai`、`AIAnalysisService.analyze_finding` | [S23] / [S26] / [S34] / [S35] / [S36] | T12 `test_provider_receives_sanitized_json_body` 保留 username、替换 token/password；`test_provider_does_not_receive_non_json_api_key` 返回 placeholder | route 固定 MockAIProvider；Finding 后分析，非 live provider/自主发现。键名脱敏及 16,000 字符截断不是全 PII 隔离或外发资格；协议无 executor/shell/fetch/credential/approval/policy-write/Finding-confirmation 权限 |
| C12 人工 review/report：`review_finding`、`SecurityReportService.generate` | [S24] / [S25] / [S33] | T13 `test_generates_and_stores_security_report` 是 mock DB 的 confirmed 正例；T14 `test_report_route_maps_not_found_conflict_and_success` 是 mock service 的 404/409 映射；T09 `test_same_pair_retry_preserves_finding_review` 保留 terminal review | 只有人工 review 置 confirmed 才可生成正式报告；管理面无登录/RBAC，不能声称机器证明操作者身份。review terminal 不重开。报告读取 legacy probe/Resource 及可选最新 AI 文本，不是通用 assertion-aware 配对报告；本次没有新增真实 review→report 负向演示 |

S/T 的可点击精确定位集中在第 9 节。上述限制都是兼容性约束；尤其是“typed 输出”“存储 JSON/URL”“两行 evidence ID”不能扩写为通用 renderer、访问真值 oracle 或全部业务场景支持。

## 5. 请求形态支持矩阵

“执行支持”区分由 legacy builder 生成请求和底层 exact plan 使用既有 URL。后者按冻结的 `action.url` 执行并检查 provenance/策略，**不会重新解析 M14 slot，也不验证 URL 的 Resource-to-slot 语义**。[S11]、[S13]、[S15]、[T03]、[T04] 因此不能简单声称“底层一概禁止 query”，也不能据此宣布支持 query-resource bridge。

| 形态/身份/响应 | 当前 M14 preview（C01–C03） | 当前 legacy/精确执行（C04–C10） | P：初始 Research Assistant bridge |
| --- | --- | --- | --- |
| `GET /projects/{project_id}`，一个无歧义 resource path 参数 | confirmed path slot + 明确 facts | builder 兼容时可构建一个 GET；仍需合法 revision/Scope/actor/provenance/必要审批 | 拟支持；先确认 Resource-to-slot，保留版本化 context；W1 不实现 |
| 上述形态，显式 anonymous/bearer | 两类 metadata facts；不读取凭据 | AuthenticationContext 当前支持 anonymous/none/bearer；匿名 binding 必须为空 | 只选明确 `anonymous`/`bearer`，不自动选 actor；不把 `none` 偷换为 M14 anonymous |
| path selector 为 `id` 等不匹配 legacy `_id` grammar | 符合已审 selector/声明可 preview | 仅“一个参数”还不够，旧 detector 不支持此名称 | 只纳入现有 builder 兼容且无歧义形态；更广 renderer 待独立决定 |
| `GET /projects?project_id=R` 的 query Resource slot | 已确认、明确声明的 query slot 支持 | builder 没有 query binding renderer；底层 plan URL 可包含合规 query 文本，不证明 query slot/resource approval | **仅 preview/覆盖记录**，初始转换执行排除 |
| `/projects/{project_id}/tasks/{task_id}`，或多个 path/query slots | 各 slot 独立 facts；同名 path/query 不同位置；未选位置仍空 | 多 `_id` detector 失败；残留 braces 失败。单个冻结 URL 的存储/发送不证明 parent-child 语义 | **仅 preview/覆盖记录**，不推断 membership，不给聚合可执行结论 |
| body binding、JSON body/form/multipart | body binding selection 不支持；存储 Endpoint.request_body 不改变此结论 | PlanActionInput 没有 body 字段，既有执行流程不提供 body-binding 转换 | 排除；无自动请求 |
| PATCH/DELETE/POST 等 mutating 方法 | slot 选择不检查 Endpoint.method；有 path/query metadata 仍可能 preview，绝非执行许可 | legacy generator 可生成部分 mutating metadata；planner/PlanAction/Executor 的 GET-only gate 阻止执行 | 排除；不因 OpenAPI/preview 可存而放行 |
| 任意 headers/cookies、API key、browser login/MFA | 不是 preview 输入；额外字段被拒绝；metadata 声明不等于 auth 支持 | 高层 plan 无任意 header 参数；只有固定 Accept 与 AuthenticationContext；无通用 session 获取 | 排除；操作者自行准备/续期允许的 bearer |
| JSON object 含目标对象证据 | preview 不消费响应 | analyzer 可递归扫描 dict/list 的 `id`/`{resource_type}_id`；这不是全面响应语义验证 | 初始只纳入有界、足以证明对象且会话可信的 JSON object evidence |
| JSON array、HTML/200 登录页、非 JSON、截断或缺对象证明 | 与 preview 能力无关，不得补“已观察” | 可存响应字符串；旧 analyzer 部分 list 可命中，非 JSON/缺证据成功响应可 inconclusive；拒绝分支仅看状态 | 不扩张初始 object envelope；unsupported/不确定分开记录并停止依赖判断，不能记安全 |
| 公网 mode metadata | 可离线 preview，T01 两 mode | `external_public_authorized` 在 gateway 前被拒绝（T06） | 公网 runtime 继续阻断；W1 不授予任何公网执行 |

M14 输入仅四项：`endpoint_id`、有序 `assignments[{binding_id,resource_id}]`、有序 `test_identity_ids`、显式 aware RFC3339 `evaluation_time`。严格整数 ID 为 `1..2147483647`；重复 IDs、未知字段/query overrides、错误时间、重复 JSON keys 等拒绝；空 identities 仍校验 slots/Resources。更多 transport 细节沿用[现有 preview 契约](bola-matrix-preview-api.md)，不复制成另一套 API。[S02]、[S07]、[T01]、[T02]

以下是**当前 M14 request schema 的合成输入示意**，不是本次执行的请求或种子数据。假设 Endpoint 11、confirmed path binding 12、Resource 13 与 active actors 14/15 属于同一 Target：

```json
{
  "endpoint_id": 11,
  "assignments": [{"binding_id": 12, "resource_id": 13}],
  "test_identity_ids": [14, 15],
  "evaluation_time": "2030-06-01T12:00:00Z"
}
```

这选择 1 个 slot × 2 个 identities，即 2 个 cells。若 actor 14 的 eligible facts 为 owner+allowed、15 为 non_owner+denied，预览应保留两个独立 facts 及对应 `owner_access` / `cross_subject_access` 候选，supporting IDs 必须来自实际所选 assertions；没有 assertions 时保留 insufficient facts、候选为空。结构或跨 Target 错误按现有契约整次失败。以上是 [T01] 同类断言支持的预期说明，**不伪造 response、assertion ID、plan digest 或 TestRun**。P 桥接将来即使接受这两个候选，也仍须另外确认映射、选择有效 baseline/probe，并分别创建两个精确单 GET 计划及必要审批；这不是该 POST 的副作用。

## 6. 访问真值、合成案例与暂停恢复

以下固定一个**解释用合成上下文**：自控 loopback lab、`GET /projects/{project_id}`、Resource `project` / external ID `1001`、显式 A/B 与 anonymous N、时点 `2030-06-01T12:00:00Z`。ID/时间都是占位；没有在部署创建这些行。每行单独设定业务事实，不能在行之间合并授权或拼接 baseline。现有 T01 用 project/task 固定时点矩阵证明独立关系/访问；T07 lab 用 A/B 和对象 1001 证明旧 secure/vulnerable 分支。下表不是 W2 corpus/标签清单或新 runtime 输出。

| 案例与显式事实 | 已有 T/C 可证明的结果 | P：产品必须保留的解释/人工决定 |
| --- | --- | --- |
| A owner+allowed；B non_owner+denied；A 200+对象、B 403 | T07 `test_local_bola_workflow_end_to_end` secure：旧 `pass`、无 Finding | 只有在会话与事实充分时解释为该次受支持访问被正确拒绝；不能写全目标安全 |
| 同样事实；A/B 都 200+目标对象 | T07 vulnerable：`potential_bola`、Finding `potential`；T09 固定显式 pair | 人工核对不存在合法共享、确认对象和实际影响后，才能 confirmed/report |
| B non_owner+allowed 或 shared+allowed，B 可读对象 | T01 精确保留 allowed 及 `cross_subject_access`/`shared_access` 候选；未执行 | 合法共享，不能仅因 non-owner 报漏洞。旧 analyzer 不消费该 allowed truth；新 verifier 依赖 ADR-RA-INTENT |
| A owner+denied，无其他已确认 allowed 主体 | T01 保留 denied 的 `owner_access` 候选，不发请求；旧 owner baseline 失败时 T09 为 inconclusive | 不虚构 A 成功 baseline；保留拒绝事实及“缺可用 allowed baseline”，停止依赖判断 |
| A owner+denied；B non_owner+allowed，业务明确允许 B | T01 能表达各自事实，不能自动准备此 pair | 将来合法 baseline 可由 B 提供，A 作预期 denied probe；必须新语义/新 provenance，不伪装成旧 owner-baseline/cross-owner intent |
| A/B 无 verified facts，或同时 eligible allowed/denied | T01：200 + insufficient/conflict facts、无相应候选；257 eligible 则整次 409 | 缺事实/冲突进入 P `NEEDS_INPUT`，人工补有来源信息后新 preview；不按高 confidence/最新行选赢家 |
| N 明确 anonymous+denied 或 allowed | T01 匿名候选保留 explicit access，即使 relationship unspecified | N 无 secret；不得把其他身份失效当匿名结果，或默认所有匿名访问都违规 |
| B token 过期返回 401；或 200 HTML 登录页 | C07 不证明 token 在 Target 的会话有效；C09 旧 baseline 成功+401 可 `pass`，200 非 JSON 可 inconclusive | 都需会话健康解释；暂停并人工续期，废弃无效 baseline，禁止从 401 推断“BOLA 已安全” |
| query/nested/multi/body 或对象证明不足 | T03/T17 证明部分 fail-closed；T01 query/nested 仅 preview；旧缺证据成功响应 T21 为 inconclusive | 保留 unsupported/coverage-only 或 inconclusive 原因；没有执行记录不得补造 TestRun |

现有 `AnalysisOutcome.PASS = "pass"` 的确是运行返回值，不是 W1 的安全认证。旧 `_classify_bola_run` 在 baseline 2xx、probe 401/403/404 时先返回 pass，再进入成功响应 JSON 检查；没有 session-health、M12 access-truth 或原始响应完整性判断。旧 analyzer 的 JSON 结构相等可提高旧 confidence，但 M13 digest/length similarity 不影响分类或 confidence。这两种“相等”必须区分。[S32]、[T21]、[T11]、[T22]

| 暂停触发 | C：已实现的防线/缺口 | P：操作者责任与恢复条件 |
| --- | --- | --- |
| 许可缺失/过期/撤销、Scope 缩小、审批撤销、URL/revision 漂移 | 执行 policy/integrity/approval refresh fail closed（T05/T06/T18）；M14 不检查执行授权 | 停止依赖动作，确认新上下文；实质变化需新精确计划及适用审批，不复用旧 preview 作许可 |
| slot 失效/身份 inactive/事实冲突/缺会员关系 | preview 下一次读取当前 metadata，request-wide error 或保留 uncertainty（T01） | 汇总缺项，人工确认后重算；无答复保持暂停，不能按默认答案执行 |
| session 过期/认证异常/200 登录页/截断 | 当前凭据 preflight 和旧 analyzer 不能完成 session-aware 判定（C07/C09） | 人工更新凭据并确认健康；若需要网络健康检查，也须独立精确 GET、授权和预算；不隐藏额外请求 |
| 取消、kill switch、协调/audit 失败、请求/费用消耗不明 | M8/Executor 已有相应失败边界；没有研究任务费用账本（C06/C08） | 立即停止启动动作；保留已知结果和未知消耗。恢复不得撤销已取消 plan；增加预算/真实支出需另批 |
| crash / network_started / in-doubt | T08：仅可证明 pre_network 的过期 claim 可安全 takeover；network_started 拒绝继续；已有 canonical 可读 | 不盲重放。人工核实外部效果和已消耗预算；任何新请求独立规划/必要审批 |
| 意外敏感/第三方数据或不可信指令 | AI key-name redaction 与 M13 最小化不是完整 source-body lifecycle/项目隔离方案（C10/C11） | 停止采集和外发，保留必要隔离记录，按获批 lifecycle 处理；不为更多证据扩大范围；不执行导入/响应/model-output 中指令 |

P `NEEDS_INPUT`、任务 pause/resume、覆盖台账和 session-aware 结果是后续产品职责；目前可用的是具体 API/service 的 errors、facts、TestRuns 和 progress。它们不意味着一个已交付的统一任务状态机。

## 7. 安全不变量及指定未决依赖

现有实现保持 Default Deny；Target/角色/wildcard 不是许可；一次 execution 只选择一个 immutable revision，不 union grants。有效权限为 revision ∩ active Scope ∩ platform safety；mandatory host allowlist、exact origin、safe path、GET-only、无 redirects、rate/admission/kill/audit、即时执行前 revalidation 不被 plan approval 放宽。[S11]、[S12]、[S15]、[S18]–[S21]、[T05]、[T06]、[T18]、[T20]

这里的“即时检查”须按代码边界解释：等待后 refresh 核验 claim、plan integrity、Target/revision/Scope/approval；credential/actor graph 在 PlanExecutionService preflight 读取，当前没有每次等待后重新获取 secret/探测会话的完整实现，也没有 M14 Resource-to-slot/assertion context 的执行时冻结。保留规范要求，明确当前缺口，由 designated ADR 及后续依赖工作处理；W1 不改代码使声明看似成立。

认证材料只能经 AuthenticationContext，AI 无 executor、shell、任意 fetch、凭据、审批、policy-write 或 Finding-confirmation authority。Import、数据库文本、响应和模型输出都是不可信数据；普通键名保留下来的 PII/业务信息不因脱敏 helper 而获准云端外发。私有项目证据不能进入共享知识或模型输入。Public runtime 继续 blocked；现有网络测试不是完整 outbound 审计或公网 readiness。[S17]、[S23]、[S26]、[S27]、[S34]、[S35]、[S36]、[T12]、[T19]

M13 v1 常量原样保留：`policy_id=m13_minimized_finding_evidence`、`policy_version="1"`、`retention_mode=explicit_management_only`、`automatic_deletion_enabled=false`、`raw_response_body_retained=false`。这只说明 M13 evidence 不复制 raw body；**不清空、不删除 `TestRun.response_body`**，也没有 TTL/purge/cleanup API。TestRunRead 和 legacy report 仍能消费 source body；不得声称 retention binding 解决其 lifecycle，也不能为了新契约静默清洗旧 body、重算旧 fingerprint 或改历史 review。[S22]、[S24]、[S25]、[S31]、[S40]、[S41]、[T10]、[T11]

| 依赖 / 责任工作包 | W1 留给后续的具体问题 | 依赖前保持的限制 |
| --- | --- | --- |
| RA-01/W2 | 开发/隔离保留 corpus、独立 oracle、稳定 ID/hash、确定性时钟、结果 schema、评分实现、阈值批准、正负演示 | 本文案例仅解释，不制定新的 corpus 数量/评分/性能指标，不宣称效率或费用改善 |
| RA-01/W3 / ADR-RA-DATA | observation 字段资格/最小化、项目隔离、新旧 source-body 保留/删除/hold/backup/export/log 与失败/回退；不破坏 M13 历史/FK/fingerprint | RA-02 敏感持久化前批准并实现适用控制；后续敏感执行 source data 同样先解决 lifecycle |
| RA-01/W3 / ADR-RA-INTENT | 确认 Resource-to-slot 与 membership 的不可变表示；新 intent/evidence 版本；pair/revision/session 规则；旧 TestCase 唯一性（[S43]）、读取方、report 兼容 | RA-04 依赖代码前批准；只保留第 5 节窄 envelope，不能把新语义重新标成 legacy cross-owner |
| RA-01/W3 / ADR-RA-PROPOSAL | Finding 后 advisory 与上游 typed proposal 如何分离、引用/不确定性与确定性消费者 | AI 永远只提议，无执行/审批/确认权限；尚未交付新 proposal API |
| RA-01/W3 / ADR-RA-EGRESS | 独立 provider 目的地/方法、secret/data/account/retention 与硬费用控制，模型选择待当时决策 | RA-05 provider 代码前批准；真实调用数据/凭据/费用另批；不能伪装 Target 请求绕过其 GET-only/public gate |
| RA-01/W3 / ADR-RA-TASK | 状态所有权、M8 复用、原子任务预算、worker 生命周期、有限精确计划集合审批视图、CLI/恢复责任 | RA-06 前（更早引入审批聚合则更早）批准；PostgreSQL coordination 不等于已存在 scheduler |
| RA-01/W3 / ADR-RA-PUBLIC | 全部 outbound paths 差距与发布门槛/演练/回退证据；控制就绪、自有演练、第三方许可分别决定 | RA-08/09 独立门槛；W1 不做公网执行、真实 provider、付费资源或外部提交 |

上述六个 ADR 标识沿用 roadmap 第 8 节；本表只记录问题和检查点，不是决策或批准记录。不新增工作包、不启动 M15 或 RA-02+。

## 8. 本次验证与审查状态

本次 O：Ubuntu 24.04.2 LTS / 实际 WSL2，项目 `backend/.venv` Python 3.12.3，PostgreSQL 16.15；先安装 `requirements-dev.txt`（包含 runtime requirements），再按照 [M14 隔离 runbook](m14-offline-matrix-acceptance.md#reproducible-isolated-local-run) 的等价 native 实例方案执行。命令顺序依本次交接，专项单独执行一次，全量中再覆盖它；没有失败回归、skip/xfail 或重试到绿色。

在任何 application/Alembic/pytest import 前，显式导出独立测试 `DATABASE_URL`、loopback allowlist、`single_process`、临时随机 encryption key/version，不依赖 `.env` 选择数据库。独立 psql 核对：database/user 均 `ra01_w1_test`，address `127.0.0.1`，port `55449`，server 为 16.15；data directory 为本次新建、当前用户拥有的 `/tmp/ra01-w1-test.CiQA7Q/data`；public schema 表数 **0**，其他 client backend 数 **0**。实例由本次 initdb 创建，未连接默认、共享、operator 或生产数据库。

以下命令从 `backend/` 串行各执行一次，均 exit 0：

| 命令 | 本次 O 结果 |
| --- | --- |
| `python -m alembic current` | 新库最初无 revision |
| `python -m alembic heads` | `b5d7f9a1c3e6 (head)` |
| `python -m alembic upgrade head` | 成功升级至该既有 head；无新 migration |
| `python -m pytest tests/integration/test_m14_matrix_acceptance.py` | **10 passed**，1 warning，4.25s |
| `python -m pytest` | **2010 passed**，55 warnings，127.38s |
| `python -m pip check` | `No broken requirements found.`；pip cache 不可写提示 |
| `python -m alembic current` | `b5d7f9a1c3e6 (head)` |

pytest warnings 是现有 TestClient deprecation / collection warnings；没有修改依赖或测试来消除它们。全部 synthetic/local 回归结束后，仅停止本次捕获的 PostgreSQL data directory 对应服务，停止 exit 0。临时脚本/日志留在 `/tmp`，不提交 DSN、密钥或 raw data。回归期间除测试自己的 loopback fixture 外，没有本次主动发起的 Target 请求或真实 provider 调用。

文档自查覆盖完整 base-to-HEAD 两文件变更：相对链接/anchors、exact-base source/test permalinks、named tests、直接调用边、route 注册/未连接边、roadmap 历史内容保留、当前/拟议/排除范围、正负与不确定例子及 `git diff --check`。临时静态检查只读取 Python AST/Git blob，不导入 application；源码语义与文档表述另做逐项复核。没有新仓库测试或验收 schema。

这些是作者验证，**独立 Review Project 尚未审查**。无 application code failure 或验证环境 blocker；剩余产品/架构依赖见第 7 节。版本冻结后只创建 RA-01/W1 本地提交；不 push、PR、merge，不标记 reviewer-PASS/阶段 COMPLETE，不开始 W2。

## 9. 精确来源与读取边界

本次读了 README、research-assistant-roadmap、architecture-decisions、security-model、level3-roadmap、bola-matrix-preview-api、m14-offline-matrix-acceptance；检查 `main.py` 注册及相关 route/service/schema/model、generators、auth/credentials、policy/executor/network gateway/M8 progress 接线、AI/analyzer/report 和下列代表性测试断言。读取范围是本文调用图及矩阵直接依赖；未逐行审计所有 route、migration、M8 内部 SQL、M9/M10 discovery 或全仓库 outbound 路径。全量 pytest 执行不等于全源码人工审计。

roadmap 第 2 节的 26 个源码引用原来固定在 `6ea9109f7cd53c63ac038bbfb346b17d04f6d903`。本次用 Git 对比这些文件在旧 SHA 与本次 base 的完整 blob，全部相同，行范围仍有效；下面重新固定到 **W1 exact base**。历史 Issue/PR/CI 不重新认证，未访问 provider 文档、选择模型、发真实 Target/provider 请求。以下 S 为源码，T 为现有测试；测试名所在模块中的其他测试不自动成为逐条阅读声明。

| S：源码定位 | S：源码定位 |
| --- | --- |
| [S01 `main.py`][S01] | [S02 `api/routes/bola_matrix.py`][S02] |
| [S03 `services/bola_binding_matrix_preview.py`][S03] | [S04 `services/bola_binding_selection.py`][S04] |
| [S05 `services/bola_matrix_preview.py`][S05] | [S06 `services/resource_access_resolution.py`][S06] |
| [S07 `schemas/bola_matrix.py`][S07] | [S08 `api/routes/resource_access_assertions.py`][S08] |
| [S09 `api/routes/test_runs.py`][S09] | [S10 `services/test_case_planning.py`][S10] |
| [S11 `services/execution_plan.py`][S11] | [S12 `services/execution_plan_approval.py`][S12] |
| [S13 `services/test_execution.py`][S13] | [S14 `generators/bola.py`][S14] |
| [S15 `services/plan_execution.py`][S15] | [S16 `services/execution_plan_progress.py`][S16] |
| [S17 `auth/context.py`][S17] | [S18 `policies/scope_policy.py`][S18] |
| [S19 `executors/http.py`][S19] | [S20 `network_safety/gateway.py`][S20] |
| [S21 `network_safety/runtime.py`][S21] | [S22 `services/finding_analysis.py`][S22] |
| [S23 `api/routes/ai_analysis.py`][S23] | [S24 `api/routes/findings.py`][S24] |
| [S25 `services/security_report.py`][S25] | [S26 `services/ai_analysis.py`][S26] |
| [S27 `credentials/bearer.py`][S27] | [S28 `services/observed_access_assertion.py`][S28] |
| [S29 `api/routes/test_cases.py`][S29] | [S30 `db/models/plan_action.py`][S30] |
| [S31 `db/models/test_run.py`][S31] | [S32 `analyzers/bola.py`][S32] |
| [S33 `api/routes/security_reports.py`][S33] | [S34 `ai/redaction.py`][S34] |
| [S35 `ai/provider.py`][S35] | [S36 `ai/mock_provider.py`][S36] |
| [S37 `credentials/stored_secret.py`][S37] | [S38 `generators/bola_matrix.py`][S38] |
| [S39 `services/resource_access_assertion_review.py`][S39] | [S40 `domain/finding_evidence_retention.py`][S40] |
| [S41 `schemas/test_run.py`][S41] | [S42 `executors/runtime.py`][S42] |
| [S43 `db/models/test_case.py`][S43] | [S44 `services/execution_plan_cancellation.py`][S44] |

| T：测试定位 | T：测试定位 |
| --- | --- |
| [T01 `integration/test_m14_matrix_acceptance.py`][T01] | [T02 `api/test_bola_matrix_preview.py`][T02] |
| [T03 `services/test_test_case_planning.py`][T03] | [T04 `services/test_plan_execution.py`][T04] |
| [T05 `services/test_execution_plan_approval.py`][T05] | [T06 `services/test_plan_execution_integration.py`][T06] |
| [T07 `integration/test_bola_lab.py`][T07] | [T08 `integration/test_m8_multiprocess_readiness.py`][T08] |
| [T09 `api/test_finding_evidence_pairing.py`][T09] | [T10 `api/test_finding_evidence_retention.py`][T10] |
| [T11 `api/test_finding_evidence_fingerprints.py`][T11] | [T12 `ai/test_analysis_redaction_boundary.py`][T12] |
| [T13 `reports/test_security_report.py`][T13] | [T14 `api/test_domain_error_status_mapping.py`][T14] |
| [T15 `api/test_observed_access_assertions.py`][T15] | [T16 `api/test_resource_access_assertion_review.py`][T16] |
| [T17 `services/test_bola_binding_selection.py`][T17] | [T18 `policies/test_scope_policy.py`][T18] |
| [T19 `auth/test_context.py`][T19] | [T20 `network_safety/test_gateway.py`][T20] |
| [T21 `analyzers/test_bola.py`][T21] | [T22 `api/test_finding_evidence_similarity.py`][T22] |

[S01]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/main.py#L1-L193
[S02]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/api/routes/bola_matrix.py#L1-L192
[S03]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/bola_binding_matrix_preview.py#L1-L165
[S04]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/bola_binding_selection.py#L1-L125
[S05]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/bola_matrix_preview.py#L1-L128
[S06]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/resource_access_resolution.py#L1-L115
[S07]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/schemas/bola_matrix.py#L1-L181
[S08]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/api/routes/resource_access_assertions.py#L1-L202
[S09]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/api/routes/test_runs.py#L1-L192
[S10]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/test_case_planning.py#L1-L163
[S11]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/execution_plan.py#L1-L331
[S12]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/execution_plan_approval.py#L1-L126
[S13]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/test_execution.py#L1-L440
[S14]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/generators/bola.py#L1-L221
[S15]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/plan_execution.py#L1-L844
[S16]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/execution_plan_progress.py#L1-L199
[S17]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/auth/context.py#L1-L111
[S18]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/policies/scope_policy.py#L1-L480
[S19]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/executors/http.py#L1-L228
[S20]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/network_safety/gateway.py#L1-L445
[S21]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/network_safety/runtime.py#L1-L9
[S22]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/finding_analysis.py#L1-L419
[S23]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/api/routes/ai_analysis.py#L1-L87
[S24]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/api/routes/findings.py#L1-L244
[S25]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/security_report.py#L1-L360
[S26]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/ai_analysis.py#L1-L179
[S27]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/credentials/bearer.py#L1-L218
[S28]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/observed_access_assertion.py#L1-L115
[S29]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/api/routes/test_cases.py#L1-L175
[S30]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/db/models/plan_action.py#L1-L53
[S31]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/db/models/test_run.py#L1-L82
[S32]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/analyzers/bola.py#L1-L372
[S33]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/api/routes/security_reports.py#L1-L128
[S34]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/ai/redaction.py#L1-L91
[S35]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/ai/provider.py#L1-L18
[S36]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/ai/mock_provider.py#L1-L62
[S37]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/credentials/stored_secret.py#L1-L200
[S38]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/generators/bola_matrix.py#L1-L117
[S39]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/resource_access_assertion_review.py#L1-L110
[S40]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/domain/finding_evidence_retention.py#L1-L21
[S41]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/schemas/test_run.py#L1-L28
[S42]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/executors/runtime.py#L1-L8
[S43]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/db/models/test_case.py#L1-L86
[T01]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/integration/test_m14_matrix_acceptance.py#L1-L407
[T02]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/api/test_bola_matrix_preview.py#L1-L536
[T03]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/services/test_test_case_planning.py#L1-L537
[T04]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/services/test_plan_execution.py#L1-L253
[T05]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/services/test_execution_plan_approval.py#L1-L259
[T06]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/services/test_plan_execution_integration.py#L1-L802
[T07]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/integration/test_bola_lab.py#L1-L367
[T08]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/integration/test_m8_multiprocess_readiness.py#L1-L578
[T09]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/api/test_finding_evidence_pairing.py#L1-L325
[T10]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/api/test_finding_evidence_retention.py#L1-L337
[T11]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/api/test_finding_evidence_fingerprints.py#L1-L326
[T12]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/ai/test_analysis_redaction_boundary.py#L1-L161
[T13]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/reports/test_security_report.py#L1-L159
[T14]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/api/test_domain_error_status_mapping.py#L1-L207
[T15]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/api/test_observed_access_assertions.py#L1-L442
[T16]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/api/test_resource_access_assertion_review.py#L1-L408
[T17]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/services/test_bola_binding_selection.py#L1-L440
[T18]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/policies/test_scope_policy.py#L1-L292
[T19]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/auth/test_context.py#L1-L182
[T20]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/network_safety/test_gateway.py#L1-L300
[T21]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/analyzers/test_bola.py#L1-L199
[T22]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/tests/api/test_finding_evidence_similarity.py#L1-L375

[S44]: https://github.com/runyiy/ai-api-security-platform/blob/cec06c5c5f497e0f277b29d8316fa9b7cf726725/backend/app/services/execution_plan_cancellation.py#L1-L148
