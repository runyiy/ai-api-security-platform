# RA-02/W1：可信研究 intake context

> **记录状态（[PR #137](https://github.com/runyiy/ai-api-security-platform/pull/137)）：** synthetic-only intake context 已集成。下文基点、提交时 pending 状态、验证结果和包内停止指令是历史记录；旧停止点不约束后续已授权工作。契约/安全/验收要求仍有效，采纳按精确记录、当前进度按 [roadmap](research-assistant-roadmap.md#5-固定阶段与依赖)；集成不授予操作许可。

**research-intake-v1 · IMPLEMENTED / PENDING_INDEPENDENT_REVIEW**

基点为 reviewed W3 commit `dcdb50fd36c098173c2580389577bb59558c0982`，分支 `codex/ra-02-w1-intake-context`；W1–W3 未合入 main。本包依据 [roadmap RA-02/W1](research-assistant-roadmap.md#ra-02--任务规则受限离线观察和可用测试上下文) 与 [DATA 后续采纳记录](research-assistant-adr-decisions.md#data-后续决定记录ra-02w1)，只实现 synthetic 元数据输入和缺项展示。没有 observation importer、payload lifecycle、身份/Resource/slot 提议、credential 更新、bridge、执行入口、scheduler、provider、retrieval 或 CLI。

## 1. 操作者入口与边界

新增 API 供既有 single trusted operator 本地部署使用，不是 SaaS/RBAC 或新的用户认证边界。操作者在路径中明确选定 `project_number`，在创建请求中明确选择已有 Target 及关联 review 的合成引用；服务不会从全局列表、导入文本或模型建议自动登记/挑选 Target。

只接受整数标识、固定枚举及带版本的合成 fixture 引用：`{"kind":"synthetic_fixture","fixture_id":1,"version":1}`。引用按所属 context 保存，不做全局检索、跨项目复制、URL 获取或文件读取；它记录操作者的来源声明，**不证明所指 fixture、许可或批准真实存在**。`provenance=operator_recorded_unverified` 从服务生成，客户端不能提供 `verified`、approval state 或 authority。

规则内容限制为 `get_only`、`no_redirects`、`explicit_access_facts` 三种声明，每条保留独立 source/version。它们描述研究约束，不修改现有 policy，也不充当规则检索库。此 W1 不接收任意规则正文、用户名、资源值、URL、绝对路径、secret 或 PII；`purpose` 仅 `synthetic_bola_research`。`reviewed-minimized-private` 等资格被拒绝。把私有文本标成 synthetic 同样不能绕过固定字段/类型/枚举。

| 方法及路径（均在 `/api` 下） | 行为 |
| --- | --- |
| `POST /research-projects/{project_number}/contexts` | 一次事务创建 context、1–16 个已显式选择的 Target 关联及 version 1；201 |
| `GET /research-projects/{project_number}/contexts/{context_id}` | 重读最新版本；仅活跃 context/关联可评估当前许可元数据，否则 unavailable；200 |
| `POST /research-projects/{project_number}/contexts/{context_id}/versions` | `expected_version` + `correction_reference` + 完整 `intake`；追加新版本，保留规则、来源、预算和更正历史 |
| `GET /research-projects/{project_number}/contexts/{context_id}/versions/{version}` | 精确读取本项目版本，标明 latest/is_current；历史 intake 不变；仅仍有活跃归属时重读许可，非历史授权决定 |
| `POST /research-projects/{project_number}/contexts/{context_id}/close` | `expected_version` + `closure_reference`；关闭 context 并原子释放全部活跃关联，保留版本和旧归属 |

没有全局 list、删除、重新开启或执行 API。一个 project_number 唯一标识一个本地 context；关闭后要转移 Target，须在另一个显式项目 context 中重新 review/关联。现有 context 的更正不能替换 Target 集合或 association_review。所有精确读取/更正/关闭同时匹配 project_number 和 context_id；不存在与不属于本项目使用同一固定 404，绑定冲突使用固定 409，不返回另一项目 ID/内容。

## 2. 输入、预算与准备度

[严格 schema](../backend/app/schemas/research_context.py) 是完整字段契约；[路由](../backend/app/api/routes/research_contexts.py) 提供内联 OpenAPI request schema 与 typed response。body 所有字段必填，明确 nullable 才可 null，无隐式默认预算或类型转换。

| 限制 | 实现值及含义 |
| --- | --- |
| transport | 实际 UTF-8 body ≤16384 bytes，逐 chunk 计数，不信 Content-Length；仅 `Content-Type: application/json`、identity 编码；不接受 query 覆盖 |
| 严格解析 | 拒绝重复 JSON keys、额外字段、float/NaN/Infinity、BOM/无效编码、非法结构；受输入字节上限约束，递归解析失败统一拒绝；没有 HAR parser |
| 输出 | 完整 typed UTF-8 JSON ≤32768 bytes；超限整次失败，写入事务回滚；全部响应 `Cache-Control: no-store` |
| 标识与版本 | project_number / fixture_id：1–1000000；Target/revision/context ID：1–2147483647；来源/更正版本：1–10000；每 context 最多 10000 个版本 |
| 内容 | 0–3 个不重复 rule；1–16 个不重复 Target；固定 purpose、资格和 source kind，无自由字符串内容 |
| Target 预算草案 | target_requests：0–100；duration_seconds：0–1800；concurrency：0–1；rate_millirequests_per_second：0–1000。1000 表示 1 request/s，是本包保守输入上限，不放宽 revision/platform |
| Model 预算草案 | model_tokens 与 model_cost_microusd：各 0–1000000000 或 null；这是**整数记录容量**，不是批准的运行 cap。currency 固定 USD；accounting_version 固定 proposal_v1，不是 provider usage 计费协议 |
| 单位一致性 | requests=0 时，已知 time/concurrency/rate 只能 0；requests>0 时已知三项不可 0；model_tokens=0 时已知 cost 只能 0；partial unknown 保持 null |
| 许可读取 | 每 Target 最多读取 257 个 Scope，超过 256 则 `scope_limit_exceeded` 并阻止准备；这不是 Scope 匹配/网络许可 |

预算记录保留 `approval_reference`，但只表示**未验证声明**：此入口既不签署运行预算，也不解析批准文件。`budget_approval=unverified`、`budget_unapproved` 始终可见，即使字段全为零或提供引用，也不变成 approved。null 不当无限、零或缺省值；`budget_missing_fields` 区分未填写项与明确的零。未来 B/task 总 token/费用与可信 observer 的批准仍由 EGRESS/TASK 的依赖 gate 解决；没有复制 W2 的 synthetic 10240 tokens / 2000 microusd。

| 准备度字段 | 现有含义及限制 |
| --- | --- |
| `permission_missing` | 至少一个选择缺 revision/source、已存引用不可用（不存在或绑定/profile 不匹配）、context 已关闭/关联已释放、draft/superseded/revoked、未生效/已过期、Target 停用/非 private_local、GET/automation 不允许、Scope 缺失/超限或快照相关元数据变化 |
| `data_ineligible` | 固定结构的合成草稿可保存，但 `data_eligibility=unknown` 或 eligibility_reference=null 时不能进入后续准备；真实私有/敏感输入直接拒绝，不保存所谓隔离原文 |
| `budget_unapproved` | 本 W1 没有运行预算批准权；引用非批准证据。`budget_rate_exceeded` 仅对归属校验通过的 revision 比较 rate；无可用元数据时不参与比较，false 不表示预算通过 |
| `facts_missing` | W3 的身份、Resource/slot、独立业务事实及 session 仍未提供；规则缺失也未解决，不制造 allowed baseline |
| `execution_preparation_allowed` / `execution_authorized` | 始终 false；缺许可明确阻止准备，其他情况也不因元数据齐全而开放未实现的 bridge |
| `activity_limits` | Target requests=0、provider calls=0、provider cost=0；是此 intake 操作的能力边界，不是把未知未来预算归零 |

[服务](../backend/app/services/research_context.py) 先确认 context 未关闭且拥有该 Target 的活跃关联，再按明确的 Target/revision/profile 组合读取当前数据，绝不 union grants。非空 revision 必须等于 Target 当前绑定，且属于 Target 的 profile，才能读取其字段、记录 digest 或参与预算比较。创建/更正的外项目与不可用非空引用统一返回 409 intake_context_unavailable，原子回滚；revision=null 且 permission_source=null 的缺许可草稿仍可保存。已有不合关系的历史引用统一显示 unavailable，不根据外项目 revision 的存在、速率或状态区分。记录的是受限授权元数据的 digest（版本标识、origin、状态、时间窗、GET/automation/rate/审批要求和 Scope），不保存旧 description/notes、原始许可内容、凭据或响应 body；更正追加新的版本快照。相同 Session 也重新发出列级查询，不拿 ORM 旧值当许可。

`referenced_current` 仅说明目前选中的 revision/profile/Target 关联、基本时间/GET 状态及元数据 digest 相符。没有具体 action/path、身份、凭据、allowlist/DNS、kill switch 或精确审批判定，未逐条匹配 Scope；不是 authorized/allowed 的另一种拼法。读取是当前元数据观察，不是跨多个查询冻结的授权事务；后续执行仍必须按原规范立即重验一个 immutable revision、Scope、安全及适用 exact-plan approval。valid_until 为半开边界，时间来自服务 UTC clock；测试可注入 aware time，API 不接受 evaluation_time 覆盖。

## 3. 持久化、隔离与回退

新增 [三个模型](../backend/app/db/models/research_context.py)：`research_contexts`、`research_target_associations`、`research_context_versions`。只向这三个表写入；新关联到旧 Target 使用 RESTRICT FK，未修改旧 Target/profile/revision/Scope、Resource、assertion、TestRun、Finding、report 或 M13。

- `uq_research_target_active_context` 对 `released_at IS NULL` 的 Target 建 partial unique index，是跨连接并发的最终归属约束。context 内 Target 也唯一；没有仅靠“先查不存在”的竞态。
- 最新/历史读取及更正/关闭持有该 context row lock；实时评估另持有活跃 association 与 Target 的共享 row lock，直至 caller transaction 结束，避免归属校验后被关闭/转移或换 Target 绑定。service 调用方须及时结束事务；API 由 request-owned Session 清理释放锁。这些锁不是执行审批，也未把 revision/Scope 观察变成冻结的授权决定。
- 更正/关闭在 expected_version 不符、已关闭或版本耗尽时失败。成功更正追加 version，旧 intake/provenance 不覆盖。Target 归属更改必须走关闭和显式重新关联。
- service 要求 clean Session，在 savepoint 内完成全部写入，失败后即便调用者捕获错误再 commit 也不留下前缀状态；service 不 commit 外层事务。API 在响应校验/编码成功后提交外层事务。异常、解析错误和数据库约束失败只返回固定码，不回显参数、规则文本、secret、原始路径或 SQL。
- 本 W1 没有敏感存储/备份/hold/purge/export 保证，因此只允许固定形态的 synthetic 元数据。历史合成记录保留以供更正/审阅；关闭不是删除，也不是实现 observation 的 30/30/90/7 天生命周期。适用的敏感资料必须保持拒收，不能用已采纳 DATA 取代未实现的运维控制；W2 首次敏感持久化前仍须满足 DATA 条件。

[增量迁移](../backend/alembic/versions/c7e9a1b3d5f7_add_research_intake_context.py) 为 `b5d7f9a1c3e6 → c7e9a1b3d5f7`，只建新表/索引/约束，无旧数据 backfill/清理。空新域允许 downgrade；含任一新表数据时，持有三表 ACCESS EXCLUSIVE 锁后检查并抛出 `research_intake_populated_downgrade_blocked`，防止检查后并发写入又被 drop。应用回退可保留新表并停用新入口；不能用删 synthetic 历史的 SQL 当生产 rollback runbook。任何实际数据处置另需明确审批、盘点与恢复证据。

[迁移测试](../backend/tests/migrations/test_research_intake_migration.py) 在 fresh 独占 schema 和 populated legacy fixture 验证：新表集合/类型/FK/唯一性；旧行逐表精确一致；exact pair/fingerprint/confirmed review/report 读取与再分析保持；空新域往返；非空新域降级拒绝且原状态不变。旧迁移测试仅更新 latest head 和“后续表”清单；M13 retention 测试固定升级自己的 revision，继续验证旧 backfill/FK/源数据不变。

## 4. 合成 API 示例与实际输出

以下示例由本次独占测试库中的合成 fixture 经 TestClient 调用本地 ASGI API 得到；没有启动公网服务、访问 Target 或 provider。数值 ID 只对该次已清理的 fixture 有效。操作者实际使用须显式选择自己已审查的合成 Target/revision，不能复制示例 ID 当授权。完整测试见 [API tests](../backend/tests/api/test_research_contexts.py)。

实际创建请求：`POST /api/research-projects/1/contexts`，`Content-Type: application/json`：

```json
{
  "version": "research-intake-v1",
  "purpose": "synthetic_bola_research",
  "data_eligibility": "synthetic",
  "eligibility_reference": {
    "kind": "synthetic_fixture",
    "fixture_id": 1,
    "version": 1
  },
  "rules": [
    {
      "rule": "get_only",
      "source": {
        "kind": "synthetic_fixture",
        "fixture_id": 1,
        "version": 1
      }
    }
  ],
  "targets": [
    {
      "target_id": 3393,
      "association_review": {
        "kind": "synthetic_fixture",
        "fixture_id": 1,
        "version": 1
      },
      "authorization_revision_id": 337,
      "permission_source": {
        "kind": "synthetic_fixture",
        "fixture_id": 1,
        "version": 1
      }
    }
  ],
  "budget": {
    "target_requests": 10,
    "duration_seconds": 60,
    "concurrency": 1,
    "rate_millirequests_per_second": 500,
    "model_tokens": null,
    "model_cost_microusd": null,
    "currency": "USD",
    "accounting_version": "proposal_v1",
    "approval_reference": null
  }
}
```

实际返回 HTTP 201；下面是响应字段的精确摘录（省略重复的 intake 与服务时间字段）：

```json
{
  "context_id": 27,
  "context_version": 1,
  "permissions": [
    {
      "target_id": 3393,
      "authorization_revision_id": 337,
      "status": "referenced_current"
    }
  ],
  "missing_inputs": [
    "budget_unapproved",
    "facts_missing"
  ],
  "budget_missing_fields": [
    "model_tokens",
    "model_cost_microusd",
    "approval_reference"
  ],
  "budget_approval": "unverified",
  "execution_preparation_allowed": false,
  "execution_authorized": false,
  "activity_limits": {
    "target_requests": 0,
    "provider_calls": 0,
    "provider_cost_microusd": 0
  }
}
```

以同一 Target 在这个已创建 context 上执行以下后续请求，实际结果为：

| 场景与确切输入变化 | 实际结果 |
| --- | --- |
| 未批准预算：`POST /api/research-projects/1/contexts/27/versions`，body 的 expected_version=1、correction_reference 为上面的 fixture 1/version 1；intake 为完整创建输入，仅 budget.approval_reference 改为该引用 | HTTP 200，context_version=2；budget_missing_fields 只有 model_tokens/model_cost_microusd；budget_approval=unverified，missing_inputs 仍为 budget_unapproved/facts_missing，两个 execution 字段均 false |
| 缺许可：同一 versions 路径，expected_version=2、相同 correction_reference；intake 为原始创建输入，仅 targets[0].authorization_revision_id 与 permission_source 改为 null | HTTP 200，context_version=3；permission status=missing；missing_inputs 为 permission_missing/budget_unapproved/facts_missing，execution_preparation_allowed=false |
| 跨 context：`POST /api/research-projects/2/contexts`，body 为原始创建输入 | HTTP 409，完整 body 为 `{"detail":"intake_context_unavailable"}`；没有创建 project 2 的 context/关联或泄漏占用项目详情 |

同一次演示逐表比较 legacy 快照一致；结束后 fixture 清理自身合成记录。独立 API 回归还覆盖跨项目 GET/correction、关闭、迟发序列化失败回滚、无凭据 SQL、network/provider/executor sentinel 及拒绝输入的全表无变更。


## 5. 初始提交验证与剩余边界

按 [隔离 runbook](m14-offline-matrix-acceptance.md#reproducible-isolated-local-run)，使用 Ubuntu WSL、项目 `.venv` Python 3.12.3、PostgreSQL 16.15。应用 import/migration 前创建独占 native instance：database/user `ra02_w1_test`、`127.0.0.1:55452`，owned data directory `/tmp/ra02-w1-test.pHYnlr/data`；独立 psql 核验以上字段、版本、当前用户目录属主、初始 public 表数 0、无其他 client backend。DATABASE_URL 与临时 key 显式配置，未依赖 `.env` 选库。开发 requirements 已满足，无 dependency/config/.env 变更。日志/DSN/key 只在仓库外。

串行命令及最终结果（从 `backend/`，先载入上述独占测试环境）：

| 命令/检查 | 实际结果 |
| --- | --- |
| `python -m pip install --requirement requirements-dev.txt` | 已满足开发依赖 |
| `python -m alembic current` / `python -m alembic heads` / `python -m alembic upgrade head` | fresh 初始无 revision；唯一新 head c7e9a1b3d5f7；从空库逐级升级成功 |
| `python -m pytest tests/services/test_research_context.py tests/api/test_research_contexts.py tests/migrations/test_research_intake_migration.py` | 最终 **94 passed, 1 warning**；包含并发、回滚、合成错误不回显/不输出警告、typed OpenAPI、上下限及迁移证据 |
| `python -m evaluation.ra01 verify` | VERIFIED，freeze digest `692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a`；未重建语料或修改阈值 |
| `python -m pytest tests/evaluation` | **79 passed** |
| 下方 compatibility 命令 | 修正新 head/后续表预期后 **237 passed, 7 warnings** |
| `python -m pytest` | 最终 **2183 passed, 55 warnings**；121.91 秒，仅此次本地回归耗时，不是产品性能结论 |
| `python -m pip check` | No broken requirements found |
| `python -m alembic current` / `python -m alembic heads` | 均为 c7e9a1b3d5f7 (head) |
| 独立合成 TestClient 示例 | 四个上述响应已捕获；legacy 快照未变；已清理自有 fixture |
| 完整 diff、相对链接/anchors、源文件、schema/示例、`git diff --check` | 通过；规范架构/安全模型、W1 产品契约及 W2 frozen artifacts 保持原样 |

```bash
python -m pytest tests/migrations \
  tests/api/test_finding_evidence_pairing.py \
  tests/api/test_finding_evidence_fingerprints.py \
  tests/api/test_finding_evidence_retention.py \
  tests/reports/test_security_report.py \
  tests/api/test_resource_access_assertion_review.py \
  tests/api/test_resource_access_resolution.py \
  tests/integration/test_m14_matrix_acceptance.py \
  tests/services/test_plan_execution.py \
  tests/services/test_execution_plan_progress.py
```

验证过程保留失败事实：首次 focused 命令写文件路径与工作目录不匹配，未收集测试；修正命令路径后运行。首次 compatibility 为 **3 failed, 234 passed**，原因是两处旧迁移的“后续表”清单缺新三表，以及一处旧 head 断言；随后只修正这些 schema 基线，并查全其他同类 head 断言，保留原有业务与历史数据断言。不是跳过失败或重试未变代码至绿。首轮全套 2182 项通过后，从 warning 发现内部构造对象的序列化警告可能带值；关闭该序列化警告但继续严格验证，并加入拒绝不输出警告的测试，再跑上述最终 focused/full。既有 deprecation/collection warnings 保留，未引入依赖修补。

最终验证与示例完成于 2026-09-10 UTC；已仅停止本次创建的 PostgreSQL 实例，确认 postmaster.pid 移除。没有环境阻塞或未解决测试失败。


验证只证明本包元数据行为及既有回归。其他五项 ADR 仍未批准；规则/来源引用未验证为业务真值，预算引用未验证为运行批准，session/allowed baseline/可信 observer/B 与 task cap 不在本包解决。W2 parser/lifecycle 和 W3 身份/资源输入未实施；不宣告 RA-02 COMPLETE 或 W1 reviewer-PASS。提交后停止等待独立 Review Project，不 push。

## 6. 完整 base-to-HEAD 文件清单

截至本次隔离修复共 50 个文件；任务 base 中已有测试除精确 head、后续表清单和旧 retention 迁移切片定位外没有断言变更。

```text
backend/alembic/versions/c7e9a1b3d5f7_add_research_intake_context.py
backend/app/api/routes/research_contexts.py
backend/app/db/models/__init__.py
backend/app/db/models/research_context.py
backend/app/main.py
backend/app/schemas/research_context.py
backend/app/services/research_context.py
backend/tests/api/test_bola_matrix_preview.py
backend/tests/api/test_openapi_binding_candidates.py
backend/tests/api/test_openapi_body_binding_candidates.py
backend/tests/api/test_research_contexts.py
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
backend/tests/migrations/test_resource_access_assertion_migration.py
backend/tests/migrations/test_resource_access_assertion_review_migration.py
backend/tests/migrations/test_safety_decision_audit_migration.py
backend/tests/migrations/test_stored_secret_migration.py
backend/tests/migrations/test_target_enrollment_provenance_migration.py
backend/tests/migrations/test_target_network_mode_migration.py
backend/tests/research_intake_fixtures.py
backend/tests/services/test_asset_candidate_dns.py
backend/tests/services/test_bola_binding_matrix_preview.py
backend/tests/services/test_bola_binding_selection.py
backend/tests/services/test_bola_matrix_preview.py
backend/tests/services/test_research_context.py
backend/tests/services/test_research_context_isolation.py
docs/research-assistant-adr-decisions.md
docs/research-assistant-roadmap.md
docs/research-intake-context.md
```

## 7. a592f7a 后的项目隔离修复

本节对应对 reviewed HEAD `a592f7ac79f79e61c3bda1368115626318f3d138` 的两个 blocker 修复；任务 base 仍为 `dcdb50fd36c098173c2580389577bb59558c0982`。第 4–5 节保留初始提交的示例和验证历史，本节记录修复证据；不改变 DATA 批准、预算、W2/W3 或其他 ADR 的范围。

- 外项目 revision：原实现先读字段、后返回 mismatched，仍让外项目 rate 影响预算输出。现在先用 Target 当前绑定和 profile 限定 revision 查询；外项目和不可用非空引用的写入统一失败，读取旧记录统一 unavailable。对该不可用选择不哈希 revision 或查询 Scope，不产生部分 context/关联/version。人工可提交明确 null 的缺许可草稿，不能隐式选择另一个 grant。
- 关闭/转移：旧 context 保留自己的 intake、来源、版本和更正历史，但不再查询实时 Target/revision/Scope；每项 permission status=unavailable，permission_missing 与 budget_unapproved 保持，两个 execution 字段仍 false。没有合资格 revision 参与比较时 budget_rate_exceeded=false，表示未进行该比较，**不代表预算满足或安全**。活跃关联缺失/已释放但 context 未关闭的防御性路径也如此。
- 并发：latest/history 读先锁 context，再校验并共享锁定 association，最后共享锁定 Target、验证其 revision 关系。close/correction 使用同一 context 锁；创建先通过活跃归属唯一索引，再进入元数据读取。close/transfer 不能在归属检查与元数据使用之间完成；如 close 先持锁，读等待提交后只返回 unavailable。读事务不写 legacy 行，也不授予执行权限。

[服务隔离测试](../backend/tests/services/test_research_context_isolation.py) 使用两个不同 Target/profile/revision 的合成项目，覆盖外项目 rate 高/低与 revision 删除、ID 相同但 profile 不符、旧 latest/history、关联单独释放、无 legacy SELECT、拒绝写入后 caller commit 仍全表不变。它显式构造初始提交曾接受的 unsafe intake 作为测试历史，不迁移或改写任何真实历史。

[API 回归](../backend/tests/api/test_research_contexts.py) 覆盖相同泄漏面、固定错误码、缺许可草稿、完整旧响应除服务评估时间外不随外项目变化；现有 network/credential/executor sentinel 和 legacy 快照断言保留。并发测试用 `pg_blocking_pids` 观察真实锁等待，分别让读取或关闭先获得锁，并覆盖 latest 和 history；读取先行时精确暂停在关联检查后、Target SELECT 前，不靠时间睡眠猜测顺序。

本次于 2026-09-10 UTC 在 Ubuntu WSL、项目 `.venv` Python 3.12.3 / PostgreSQL 16.15 串行验证。应用 import 前新建独占实例 `ra02_w1_fix_test`（database/user）、`127.0.0.1:55453`、当前用户拥有的 `/tmp/ra02-w1-fix-test.HN01b1/data`；独立 psql 核验 database/user/address/port/version/data_directory、初始 public 表数 0、无其他 client backend。显式环境选择该库及临时 key，不依赖 `.env`；开发 requirements 已满足。验证后仅停止此实例并确认 postmaster.pid 移除；日志/DSN/key 留在仓库外。

| 本次命令 | 实际结果 |
| --- | --- |
| `python -m alembic current` / `python -m alembic heads` / `python -m alembic upgrade head` | fresh 初始无 revision，唯一 head c7e9a1b3d5f7，upgrade 成功；没有新迁移 |
| `python -m pytest tests/services/test_research_context.py tests/services/test_research_context_isolation.py tests/api/test_research_contexts.py tests/migrations/test_research_intake_migration.py` | **106 passed, 1 warning**；包括四个真实锁等待顺序/latest-history 组合 |
| `python -m evaluation.ra01 verify` / `python -m pytest tests/evaluation` | VERIFIED，freeze digest 仍为 `692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a`；**79 passed** |
| 第 5 节完整 migration/compatibility 命令 | **237 passed, 7 warnings**；保留 legacy pair/fingerprint/review/report、空/非空新域 rollback 证据 |
| `python -m pytest` | **2195 passed, 55 warnings** |
| `python -m pip check` | No broken requirements found |
| 最终 `python -m alembic current` / `python -m alembic heads` | 均 c7e9a1b3d5f7 (head) |
| 完整 diff、修复范围、链接/anchors、文件清单、`git diff --check` | 通过；相对 reviewed HEAD 仅六个修复相关文件，任务 base-to-HEAD 共 50 文件 |

本次没有测试失败、跳过或环境阻塞；首轮 focused 通过后，将并发暂停点加强到关联检查与元数据读取之间，再运行最终 focused/full，并非重试未变代码至绿。warnings 为既有 TestClient deprecation/collection 提示。

本次修复文件仅 service、其 service/API/fixture 回归、独立隔离测试及本文。没有新 migration/schema/route、W2 内容、运行预算批准或 reviewer-PASS；本地提交后停止，等待 Review Project，不 push。

W1 后续 gate：本次 W2 交接确认 `2575a34270fc53bddc75373afe220ba06a34883e` 已独立审阅/push，并已 fetch 核验。[W2 observation 实现](research-observation-intake.md)复用本节归属锁；本文件前述 W1 pending、W2 未实施等文字保留为原切片历史。
