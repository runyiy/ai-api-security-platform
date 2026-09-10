# RA-02/W2：受限离线 observation intake

**v0.1.0 · IMPLEMENTED / PENDING_INDEPENDENT_REVIEW**。精确起点 `2575a34270fc53bddc75373afe220ba06a34883e`，远端 `origin/codex/ra-02-w1-intake-context` 已 fetch 核验；W1 已审阅/push、未合入 main，依据本次交接。W2 不签署 reviewer-PASS / RA-02 COMPLETE，不开始 W3，不 push。

采用已记录的 [DATA D1–D4 决定](research-assistant-adr-decisions.md#data-后续决定记录ra-02w1)，不改原 proposed 历史、其余 ADR、[产品支持范围](research-assistant-product-contract.md#5-请求形态支持矩阵)、[架构](architecture-decisions.md)或[安全模型](security-model.md)。本次只提供 **synthetic-only** 的本地操作者 API；不是 HAR 转换器、网络 importer、验证器或研究任务执行器。

## 1. 信任与值资格

调用链是 `operator → W1 project/context → maintenance gate → preparation registry → bounded JSON → transaction → observation receipt`。[W1 归属锁](research-intake-context.md#7-a592f7a-后的项目隔离修复)保持原样。每个 W2 service 先要求 clean Session、锁定精确 project/context；接受事务内检查 context 最新版本、合成数据资格、活跃 association，再共享锁 Target；runtime 时钟在这些锁等待之后采样，expiry/hold 不沿用等待前的时间。关闭/转移不能穿过这个检查窗口；已经关闭时不再读取 Target/revision/Scope。W2 不读任何 revision/Scope 内容，缺执行许可的合资格草稿仍可导入，执行准备仍由 W1 明确阻断。

Preparation 与导入文件分开。操作者通过固定 API 记录 `source/review` 的 `SyntheticReference`、`converter_version=synthetic_fixture_v1`、context_version、Target、资格有效期和 retention。文件不能创建 registry、改 W1 context 或宣称 verified。Preparation 不可原地编辑；新 `preparation_ref` 可用 `corrects_preparation` 关联旧记录，撤销保留原记录及时间。

当前资格范围刻意小于格式语法：

- 服务生成 `project_ref=project_{project_number}`；Target 必须是已审查的 `private_local`，origin 只准 `localhost` 或 loopback IP，不解析 DNS。其他 private 地址、真实项目主机不准入。
- Preparation、batch、entry、actor、resource 的 registry 值分别是 `preparation_N/batch_N/entry_N/actor_N/resource_N`，N 为 1–999999，无前导零。操作者先批准具体集合；不是导入时自动生成别名。
- 模板段仅可选 `synthetic/folders/projects/tasks/items` 和完整 `{resource_id}/{project_id}/{task_id}/{item_id}/{slot_1}…{slot_8}`（也可选根 `/`）；query 名只可选 `page/limit/resource_id/project_id/task_id/item_id/query_1…query_16`，且须在该 preparation 中精确登记。
- Registry 一次最多 128 个 batch/entry/actor/resource 值、16 个模板/query 名。`source/review` 只接受 W1 的固定 synthetic fixture ID/version，不收自由说明、签名文本、文件路径或 URL。

这些是关闭任意值域的合成资格约束，不能证明现实资料已脱敏。原始 HAR/body、headers/cookies、普通字段里的姓名/邮箱/token/canary、任意指令或未知字段整批拒绝；不先持久化再清洗。没有私有 profile 的开关或“备份已加密”自报放行字段。Opaque alias 不绑定 W3 Identity/Resource/slot，也不证明 membership、session health 或 allowed/denied 事实。

## 2. 格式、预算与错误

[DATA 2.2–2.3](research-assistant-adr-decisions.md#22-版本化-observation-字段白名单-p)仍是字段契约；[schema/parser](../backend/app/schemas/research_observation.py)是实现。全部字段必填、仅显式 nullable 可 null、strict integer 不接收 bool/float/数字字符串；未知字段、重复 key、BOM、非法 UTF-8、unpaired surrogate、非有限数/溢出、压缩、NDJSON、外部引用均拒绝。无 trim、转码修复或自动截断。

| 独立限制 | 当前执行方式 |
| --- | --- |
| input 262144 bytes | API 按实际 stream chunk 检查，超限在追加前拒绝；service 也检查 bytes 长度。不信 Content-Length，不写临时文件 |
| 单 entry 4096 canonical bytes | sorted keys、compact separators、UTF-8、无 LF；在字段校验前独立检查。达到预算不代表字段合法 |
| depth 5 / nodes 16384 | `BoundedJSON` 在每个值下降前计数；root=0、key 不计 node，拒绝后不继续递归解析 |
| entries 1–128 | 保留输入顺序；entry_ref 与 source_entry_index 各自唯一。128 条仍受总字节限制 |
| 时间 | RFC3339 20–32 字符、≤6 位小数、显式已知 offset；拒绝 naive、-00:00、闰秒、无效 offset。`observed_at <= prepared_at <= server intake_time`，比较 UTC，文件表示保留；数据库 TTL/hold 约束用精确 2592000 seconds，跨 DST 不改时长 |
| origin / template / labels | 分别 256 / 512 / 64 ASCII bytes；显式 canonical port 1–65535；模板最多 8 个不重名完整 slot；query 0–16、resource 1–8、object 0–8 且为 resource 子集 |
| operator metadata / response | preparation/lifecycle body ≤16384 bytes，仍过相同结构 parser；完整成功响应 ≤280000 bytes，错误 ≤256 bytes |

`GET` 是唯一导入 method；不是执行。`missing` capture 要求 null status、unknown media、空 object_labels；非空 object_labels 只在 complete/json_object/非 null status 时可记录，仍是未验证的捕获声明。HTML、truncated、expired/login_page/unknown、actor=null 保留不确定性；null actor 不是 anonymous。Query、多槽、非 object 观察不扩大初始 bridge 支持。

错误为固定 `{"status":"rejected","code":"observation_…"}`，无原文、offending key/value、路径、异常 repr 或 SQL 参数。未知字段用 `observation_field_not_allowed`；方法错误 `observation_method_unsupported`；预算 413、字段/时间 422、外项目/未审 Target/不可用引用统一 409 `observation_context_unavailable`。无原文日志或隔离文件。API 的意外异常统一 500 `observation_failed`。

## 3. 事务、来源与兼容

[service](../backend/app/services/research_observation.py)、[models](../backend/app/db/models/research_observation.py)新增五表：`research_observation_controls/preparations/records/payloads/events`。Project 经不可变 W1 context 固定；association、preparation、payload 使用 context 组合 FK，batch/preparation 在 context 内唯一。服务先锁 context，唯一键作最终约束。Target/alias 不自动 enrollment；无跨项目查找、共享缓存或 dedup。

接受前再次检查准备记录和 Target 归属；只对合资格的 canonical payload 取 SHA-256。Payload 与 provenance 分表，server 生成 ID、accepted_at、expires_at、版本和 `operator_import_unverified`；记录精确 preparation、entry 顺序/source index、更正关联。Digest 不描述原始文件，不证明访问真值，不进入全局日志。

同项目同 batch_ref、相同 canonical 内容/preparation/更正关联返回同一 receipt；whitespace/key 排序差异可收敛，ordered entries 不排序。内容、顺序或绑定变更不能覆盖原 batch。新批次重用 entry_ref 时必须显式提供实际含该 entry 的原 observation 的 correction 路径，不能借无关记录通过；新 capture 应使用新批准 entry_ref。Correction 是同项目软历史引用，旧元数据过期后消费者必须接受 source unavailable。Tombstone 保留期结束后不会无限记住旧幂等键。

整个接纳/更正/删除/hold/审计都在 savepoint 内，service 不 commit 外层事务；拒绝后 caller 即使 catch 再 commit，也没有写入前缀。API 在编码响应后才 commit。读操作也写固定元数据 audit；audit 或响应编码失败不能返回成功。没有 raw staging、原始数据 hash、TestRun、ExecutionPlan、verified assertion、observed_baseline 或 legacy 写入。

`read` 返回 receipt，只有 qualified available payload 才附 `payload`。held 默认无 payload；显式 human-review 还需仍有效的资格/归属。关闭、转移、暂停、资格撤销/变化返回 unavailable/quarantined 历史，不获取新项目 metadata。已 deleted/expired 的源不伪装为正常空 body；digest 和既有 entry 顺序保持，不得解释为 safe。旧 TestRun/M13 pair、fingerprint、review/report、五项 retention 常量均未修改。

## 4. 生命周期与操作责任

| 控制 | 本包行为与限制 |
| --- | --- |
| admission | 默认仅 synthetic。每个进程启动生成 recovery token；数据库中未完成该进程 reconcile 的 context 不能接纳/消费 payload。未知部署保证不转为私有许可 |
| expiry | 默认 2592000 秒，可缩短为 1–2592000；从 server accepted_at 计算，`now < expires_at` 才可用。读取/重试不续期 |
| deletion | 显式 delete 原子撤销普通可用性，maintenance 物理删除在线 payload row；active hold 阻止 purge。删除不改变 digest，也不删除旧执行/evidence |
| hold | 明确 synthetic reason、review、起止时间，单次 >0 且 ≤30 天；续期重新调用并记录审计。禁止对 expired/deleted/quarantined/missing payload 新建 hold；停止普通读，仅受限人工复核。Release/end 不重开 retention |
| incident | quarantine 撤销该记录可用性并取消 hold，secret/不合资格不能借 hold 留存；preparation revoke 停止相关消费；context suspend 停止整个新域消费/准入。Rejected batch 不会触碰已批准历史；发现既有资格事故由操作者立即 suspend/revoke/quarantine 并核对已知副本 |
| maintenance | 显式处理最多 1024 records/context，无 worker/scheduler；expiry/deletion 积压超过 24 小时仍有 payload 时，新的消费/准入 fail closed。离线或 audit 故障期间不声称已物理清理 |
| tombstone | 不可用且无 active hold 后保留 90 天；maintenance 到期移除无内容 provenance。Hold 结束后才起算有效计时；不因未来软引用无限保留。无引用的过期/撤销 preparation 也可在 90 天后清理 |
| logs | 只写固定事件码、scoped IDs、synthetic review、aware 时间及 hold 终点；maintenance 清除 ≥90 天事件。每 context 最多 8192 事件，超限拒绝成功操作；需先处理维护/审计故障，不能通过绕过 audit 返回成功 |
| recovery | 服务暴露前先 suspend、在隔离的恢复库重放本项目 `deleted_observation_ids`、reconcile expiry/hold/资格/完整性与积压，再开放。外项目/不存在 replay ID 整次拒绝；损坏 payload 在 reconcile 中 quarantine/purge |
| backups / export | 无实际备份/恢复基础设施操作、无 export API。DATA 的加密、最大 7 天副本/WAL/PITR 清单及删除重放证明仍是私有资料准入前置，本包不声称这些已部署。进程 token 不能检测同一进程内被外部替换的数据库；恢复前停服/隔离是操作者责任，私有资料仍无条件拒收 |

Preparation 最多 128、receipt 最多 1024/context，容量不足返回固定失败，不静默裁剪历史；可按资格到期维护。控制项只保存 W1 风格的 synthetic review 元数据，不把 W1 历史的关闭语义改成删除。数据库 row 删除不等于安全覆盖磁盘空页/WAL/外部副本，也不承诺 Python 内存零化。Single trusted operator/local 部署必须保持本机访问限制；本包没有 SaaS/RBAC、存储密钥平台或基础设施日志配置变更。

[增量迁移](../backend/alembic/versions/d8f0b2c4e6a8_add_bounded_research_observation_intake.py)为 `c7e9a1b3d5f7 → d8f0b2c4e6a8`，不 backfill 或读取原 body。空新域可降级；非空时先持五表 ACCESS EXCLUSIVE 锁再检查，抛出 `research_observation_populated_downgrade_blocked`。回退应用时保留新表并停用新入口；不得为回退擅自删除 payload/hold/history。实际数据处置和 backup inventory 仍需独立授权。

## 5. 最小合成操作示例

以下 API 均只访问本地平台。路径前缀为 `/api/research-projects/1/contexts/{context_id}/observations`；context/Target 先经 W1 显式建立。示例 fixture 时间来自测试固定时钟 **2031-04-03T12:00:00Z**，不是 API 可指定 server clock；实际调用须换成真实过去的 capture 时间与未来 ≤30 天的 preparation 有效期。未满足时间条件整批拒绝，不修正时钟。

1. `POST /maintenance`，body `{"action":"reconcile","review":{"kind":"synthetic_fixture","fixture_id":1,"version":1}}`。
2. `POST /preparations`，operator body 如下（target_id 替换为该 context 已审 Target）：

```json
{
  "preparation_ref": "preparation_1", "context_version": 1, "target_id": 1,
  "source": {"kind":"synthetic_fixture","fixture_id":1,"version":1},
  "review": {"kind":"synthetic_fixture","fixture_id":1,"version":1},
  "converter_version": "synthetic_fixture_v1", "data_eligibility": "synthetic",
  "retention_seconds": 2592000, "valid_until": "2031-05-03T12:00:00Z",
  "batch_refs": ["batch_1", "batch_2"], "entry_refs": ["entry_1", "entry_2"],
  "actor_refs": ["actor_1"], "resource_labels": ["resource_1", "resource_2"],
  "path_templates": ["/folders/{resource_id}"], "query_names": ["page"],
  "corrects_preparation": null
}
```

3. `POST /preparations/preparation_1/batches`，严格 `Content-Type: application/json`：

```json
{
  "format":"ra-observation", "version":"1", "project_ref":"project_1",
  "batch_ref":"batch_1", "preparation_ref":"preparation_1", "prepared_at":"2031-04-03T12:00:00Z",
  "entries":[{
    "entry_ref":"entry_1", "source_entry_index":0, "observed_at":"2031-04-03T04:59:59-07:00",
    "method":"GET", "origin":"http://127.0.0.1:58123", "path_template":"/folders/{resource_id}",
    "query_names":[], "actor_ref":null, "resource_labels":["resource_1"],
    "response":{"status_code":200,"media_kind":"json_object","capture_state":"complete",
      "object_labels":["resource_1"],"session_state":"unknown"}
  }]
}
```

[API 测试](../backend/tests/api/test_research_observations.py)实际断言：HTTP 200 + no-store，provenance=`operator_import_unverified`、availability=`available`、access_truth=`unknown`、execution_authorized=false。相同请求重试的 receipt 完全相同（服务时间统一 UTC）；GET `/{observation_id}` 返回该合资格 payload。随后 POST `/{observation_id}/delete` 返回 deleted，GET 无 payload 且 digest 不变；maintenance 返回 `purged_payloads=1`。这是数据接纳成功，不是漏洞/安全判断。

增加 `response.body` 或 `$ref` 得到 422/固定字段错误；改为另一项目路径得到统一 409；改 batch 内容但复用 batch_ref 得到 `observation_retry_conflict`。更正使用新 batch_ref，并 `POST /preparations/{ref}/corrections/{observation_id}`，原 receipt/内容不覆盖。POST `/{id}/hold` 要求 `review/reason/until`；`/{id}/human-review`、`/{id}/release`、`/{id}/delete`、`/{id}/quarantine` 要求 `review`。Preparation revoke 路径是 `/preparations/{ref}/revoke`。

此外，本次使用真实 server UTC 时钟（没有 API 时钟覆盖）运行同一路径的独占合成 TestClient 演示；资格有效期取运行时未来一天，capture 时间取运行时过去两秒。以下为实际观测摘要，不是未来运行输出：

```json
{"accepted_http": 200, "access_truth": "unknown", "after_delete": "deleted", "availability": "available", "clock": "server_utc", "execution_authorized": false, "forbidden_capability_calls": 0, "history_digest_preserved": true, "legacy_unchanged": true, "provenance": "operator_import_unverified", "purged_payloads": 1, "rejection": {"code": "observation_field_not_allowed", "status": "rejected"}, "retry_identical": true, "source_present": false}
```

## 6. 验收与复现

按[独占数据库 runbook](m14-offline-matrix-acceptance.md#reproducible-isolated-local-run)先显式配置并独立核验 PostgreSQL，再 import 应用。此次 Ubuntu WSL / Python 3.12.3 / PostgreSQL 16.15：database/user=`ra02_w2_test`，loopback `127.0.0.1:55454`，当前用户拥有的新目录 `/tmp/ra02-w2-test.8yZ3Ci/data`；独立 psql 验证初始 public 表 0、无其他 client backend、实际 database/user/address/port/version/data_directory。开发 requirements 已满足，DSN、临时 key 和测试日志在 Git 外，不依赖 `.env`。

| 验收面 | 实际测试位置 |
| --- | --- |
| 所有独立预算、恶意编码、duplicate/extra/ref、时间和不确定性 | [parser tests](../backend/tests/schemas/test_research_observation.py)：262144/262145、4096/4097、128/129、depth 5/6、nodes 16384/16385；预算测试与完整字段资格分开，不宣称到达结构上限的任意 JSON 都是合法 observation |
| 项目/registry/更正、无许可草稿、失败后 commit 不留前缀、zero capability sentinels | [service tests](../backend/tests/services/test_research_observation.py)、[fixtures](../backend/tests/research_observation_fixtures.py)、[API tests](../backend/tests/api/test_research_observations.py) |
| close/read/accept、hold/cleanup/delete、retry/suspend/recovery | Service 中真实双事务、`pg_blocking_pids` 确认等待，非 sleep 猜测；现有 W1 isolation 测试继续不变 |
| fresh + populated legacy/W1、pair/fingerprint/confirmed review/report、空降级/非空保护 | [migration tests](../backend/tests/migrations/test_research_observation_migration.py)。旧测试仅推进 latest head/明确后续表清单，保留固定历史 migration 与原断言 |

从 `backend/`、配置该专用测试环境后串行运行：

```bash
python -m alembic current
python -m alembic heads
python -m alembic upgrade head
python -m pytest tests/schemas/test_research_observation.py tests/services/test_research_observation.py tests/api/test_research_observations.py tests/migrations/test_research_observation_migration.py tests/services/test_research_context.py tests/services/test_research_context_isolation.py tests/api/test_research_contexts.py tests/migrations/test_research_intake_migration.py
python -m pytest tests/migrations tests/integration/test_m14_matrix_acceptance.py tests/api/test_finding_evidence_fingerprints.py tests/api/test_finding_evidence_retention.py tests/services/test_finding_analysis_concurrency.py tests/reports
python -m evaluation.ra01 verify
python -m pytest tests/evaluation
python -m pytest
python -m pip check
python -m alembic current
python -m alembic heads
git diff --check
```

最终串行验证结果（2026-09-10 UTC）：

| 命令/检查 | 实际结果 |
| --- | --- |
| 初始 Alembic current/heads/upgrade | fresh 无 revision；先升级既有 c7e9a1b3d5f7，再应用新增 d8f0b2c4e6a8；空新域往返成功 |
| 上述 focused + W1 命令 | **265 passed, 1 warning** |
| 上述 migration/M14/Finding/report compatibility 命令 | **155 passed, 1 warning**；最终全量再次覆盖这些测试及最终迁移 |
| `python -m evaluation.ra01 verify` | **VERIFIED**；freeze digest 仍 `692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a` |
| `python -m pytest tests/evaluation` | **79 passed** |
| `python -m pytest`（最终代码） | **2354 passed, 55 warnings**；无失败/跳过 |
| `python -m pip check` | No broken requirements found；本机 pip cache 不可写提示不影响检查 |
| 最终 Alembic current / heads | 均 **d8f0b2c4e6a8 (head)**，唯一 head |
| scoped diff、文档 JSON/相对链接/anchors、文件清单、`git diff --check` | 通过；W1 四个生产组件逐字不变，evaluation/freeze 无变更 |

失败记录保留：首次新 focused 为 **121 passed / 3 failed**，发现首次 receipt 与数据库重读的时区表示不同；修正为服务时间统一 UTC。扩展检查为 **233 passed / 1 failed**，新迁移测试误把正常 `UPDATE alembic_version` 视为 legacy 写入，精确排除该版本账本更新后通过，应用行完整快照断言保留。另有一次 compatibility 命令使用不存在的旧路径，未运行测试；核对实际测试路径后执行上方正确命令。初次生成脚本有 cwd 错误，空的新迁移草稿已重新生成/审查，不作为验证证据。没有跳过/弱化原测试或重试未变失败至绿；后续重跑针对新增更正、DST、锁等待 expiry 和字段边界的代码/断言。55 warnings 为现有 collection / TestClient 提示。

验证后仅停止本次创建的 `ra02_w2_test` 实例，确认其 `postmaster.pid` 已移除；没有停止其他数据库。W2 不修改 RA-01 evaluation corpus/labels/freeze；预算、provider accounting、session/baseline、INTENT 等未决项仍留在对应 ADR/工作包。没有 provider 性能、真实费用或私有数据 readiness 结论。

## 7. 完整 base-to-HEAD 文件清单

本包相对精确 W1 base 的全部 52 个文件如下。除新域注册外，既有生产代码不变；既有测试只调整 latest head、显式后续表集合/快照排除项，不放松历史能力断言。

```text
backend/alembic/versions/d8f0b2c4e6a8_add_bounded_research_observation_intake.py
backend/app/api/routes/research_observations.py
backend/app/db/models/__init__.py
backend/app/db/models/research_observation.py
backend/app/main.py
backend/app/schemas/research_observation.py
backend/app/services/research_observation.py
backend/tests/api/test_bola_matrix_preview.py
backend/tests/api/test_openapi_binding_candidates.py
backend/tests/api/test_openapi_body_binding_candidates.py
backend/tests/api/test_research_observations.py
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
backend/tests/migrations/test_resource_access_assertion_migration.py
backend/tests/migrations/test_resource_access_assertion_review_migration.py
backend/tests/migrations/test_safety_decision_audit_migration.py
backend/tests/migrations/test_stored_secret_migration.py
backend/tests/migrations/test_target_enrollment_provenance_migration.py
backend/tests/migrations/test_target_network_mode_migration.py
backend/tests/research_intake_fixtures.py
backend/tests/research_observation_fixtures.py
backend/tests/schemas/test_research_observation.py
backend/tests/services/test_asset_candidate_dns.py
backend/tests/services/test_bola_binding_matrix_preview.py
backend/tests/services/test_bola_binding_selection.py
backend/tests/services/test_bola_matrix_preview.py
backend/tests/services/test_research_observation.py
docs/research-assistant-roadmap.md
docs/research-intake-context.md
docs/research-observation-intake.md
```
