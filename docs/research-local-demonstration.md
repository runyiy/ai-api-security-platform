# RA-04/W3：本地合成端到端演示

基点 `844581154700a57607987e059091c3c4de43b238`；分支 `codex/ra-04-w3-local-demonstration`。开始前 clean local main、fetch 后 origin/main 及远端 main 均与基点一致。当前交接记录 W2 经 PR #145、CI 分片经 PR #146 集成，PR/main 各 3228 tests passed；这是交接记录，不代签本包独立 review。本文与可执行验收属于既有 W3；不宣告 RA-04 COMPLETE，不开始 RA-05。

## 1. 操作者演示入口

[端到端验收](../backend/tests/integration/test_ra04_local_demonstration.py)是本次精简演示入口，[自有 fixture](../backend/tests/research_demonstration_fixtures.py)组合现有本地 API、W1 bridge 和 W2 dispatcher。没有新增 CLI、服务器管理面或应用接口。运行器显式模拟操作者逐项确认；这些决定仅批准当次随机 loopback 端口上的自有合成资源，不是任何部署的批准。业务事实和预期字段来自独立编写的 fixture，不能从实际响应推导。

遵守 [README 的隔离测试流程](../README.md#tests-and-verification)，在 Ubuntu WSL 用仓库 backend `.venv`。**pytest 不自动隔离数据库。** 必须新建且独占 PostgreSQL 16 server，在首次应用 import 前核验 database/user、loopback 地址/端口、data_directory、system_identifier、UTF8、空 public schema、无其他 client。用不含 `.env*` 的 backend 副本和 `env -i` runner，仅传入该实例 DATABASE_URL、仓库 venv PATH 和 LANG；断言 `settings.credential_encryption_key is None`。不要 source operator `.env`，不要使用默认/共享数据库或已部署 Target。测试自行生成临时 token/key；无需操作者输入凭据。README 中历史 head 说明不取代当前 `alembic heads`。

在该安全 runner 下，从隔离 backend 副本执行：

```bash
python -m alembic upgrade head
python -m pytest -q -s --tb=short tests/integration/test_ra04_local_demonstration.py
```

第二条就是完整可重放 walkthrough：每个场景从独立 fixture 开始，启动自己的随机端口 HTTP server，实际运行逐步 API 操作、核验结果并清理本场景。正向三例打印 scenario、exact intent/pair reference、四个真实 TestRun ID、request count 和 pytest 临时目录中的 `trace.json`。trace 包含输入/输出、M14 candidate/supporting assertion 引用、mapping/manifest/budget/contract/health selection、逐计划决定、core/link/plan digests、run/witness、pair 和历史查询；不含 token、key 或 raw response body。IDs、digests、端口与时间每次变化；应比较引用关系和结果，不复制前次 receipt 重放。本演示 `knowledge=null`，候选来自真实 M12/M14；不创建或发布规则，也不把无规则引用包装成 RA-03 validation。临时 trace 是本次测试记录，不是可导入的执行授权或通用报告。

运行前后记下上述实例身份；结束仅停止此次自有 PG server，HTTP fixture 自动关闭。不要拿本段命令直接操作运行中的 operator 数据库。完整回归另外使用 [两个独立 PostgreSQL server 的分片流程](backend-ci-shards.md#commands-and-counts)，保持每 shard 串行；不能只运行三个 W2 文件。

## 2. 可审阅的显式操作顺序

基础 fixture 只建立合成 Target/Scope/revision、intake/context、两身份和加密临时凭据的元数据；所有权字段不制造 access truth。既有 fixture 的旧 subject/manifest 不被当成新业务版本。新增业务对象 `91001`、健康对象 `80814`，模板 `/folders/{project_id}`；两个 actor A/B 明确使用 bearer。另有现存匿名语义回归，不能把 bearer 失败降级成匿名。以下路径前缀为 `/api/research-projects/{project}/contexts/{context}`；实际 payload 在 trace 和 fixture 的 `Demonstration` 方法中，引用均取上一操作真实返回值。

| 顺序 / 本地操作 | 必须看到的行为及人工责任 |
| --- | --- |
| `observations/preparations` → `observations/preparations/preparation_2/batches` | 独立合成输入与 source reference，经现有最小化/资格入口；仅提供对象形态来源。导入 observation 绝不是 TestRun 或 health。 |
| `subjects/4..7` | 明确 A/B、binding、业务与健康 Resource；保留独立 relationship/expected_access 和 assertion IDs。operator claim 不升级 session。 |
| `intents/mappings/3..4` | 人工分别确认业务/健康 Resource 到单一 path slot 的确切映射；preview 不能代替确认。 |
| `/api/bola-matrix/preview` | 当前 M12/M14 facts/candidates，完整数据库快照证明零写入、server count=0；没有跨调用 truth cache。 |
| `intents/manifests/2` → `intents/budget-decisions` | 四个角色、总量4、concurrency=1、500 millirequests/s、最多300秒；独立明确预算决定。缺预算不转换。 |
| `verification/contracts/2` | 独立确认 literal `record_id`、A/B 的 `subject_id` 及固定 interpreter 约束；来自 fixture 规范，不照抄被测响应。 |
| `intents/versions/1..2`，purpose=`health_baseline` / `health_probe` | 各自产生一个 health GET plan。创建返回 `execution_authorized=false`；不隐含批准、发送或健康。 |
| 每 health 的 `verification/plan-decisions` → `verification/execute` | 分别批准各自 exact digest，逐个显式发送；真实 AuthenticationContext、pinned secret version、gateway、M8、send/complete 时间和健康对象/身份解释产生 witness。HTTP200 单独不够。 |
| `verification/health-selections` → `intents/versions/3`，purpose=`business` | 显式选择两个真实 witness。重算输入与资格，产生 core → 两个独立单 GET plans → link；同 revision、不同 actor。 |
| 业务两次 `verification/plan-decisions` | baseline 与 probe 单独批准；预算、health、另一计划批准均不可互代。 |
| baseline `verification/execute` → probe `verification/execute` → `verification/pairs` | baseline 必须独立 allowed 且具完整对象证明；固定 exact intent/link/roles/plans/actions/runs，不选 latest 或替换 baseline。 |
| `verification/history/pair` / `history/execution` | 保留原证据，明确 `historical_not_revalidated`、`reusable=false`，无新资格期限。canonical execute/pair 重读收敛，server count 不增加。 |

实际 fixture 为可比较的测试元数据设当前 UTC；正向运行使用真实 UTC/monotonic、原速率等待及120/30/300秒上限。只有明确标识的负向边界测试提供时钟跳变，monotonic 仍递增；不增加或续期生产窗口。过期、hold/release 或依赖改变须按 [INTENT §5](research-intent-contract.md#5-等待变更与失效-p)重建，历史不能恢复执行资格。完整 W1/W2 记录见 [bridge](research-intent-bridge.md)、[verifier](research-response-verification.md)。

## 3. 场景与验收映射

| 场景 / 对应测试 | 预期结果、请求数及可见限制 |
| --- | --- |
| `test_local_walkthrough[safe-*]`：A non_owner+allowed；B owner+denied | 两次健康+baseline+probe共4 GET；B 返回完整明确业务403，`expected_denial`，不能把 owner 当 allowed baseline。 |
| `test_local_walkthrough[vulnerable-*]`：相同事实，fixture 故意漏掉 B 的业务授权检查 | 4 GET；B 读到精确对象，`suspected_violation`，仍无 confirmed Finding、无 legacy report。缺陷是独立 synthetic server 的行为差异。 |
| `test_local_walkthrough[shared-*]`：独立共享事实，A owner+allowed、B non_owner+allowed | 4 GET；对象可读为 `allowed`，不同主体访问不自动等于漏洞。 |
| `test_no_allowed_baseline_retains_facts_and_creates_no_plans` | 无 allowed 或明确 denied baseline，保留 B owner+denied；0请求、无部分 manifest/plans。API 返回 `intent_facts_missing` / `intent_baseline_missing`；操作者结论为 inconclusive/NEEDS_INPUT，而不是伪造一个 W2 pair。 |
| prerequisite / preview-is-not-authority | 缺预算、contract、health、missing mapping、binding 未确认、facts conflict、nested/membership 未证形态：0依赖请求，无部分成功。未知/unsupported 保持可见。 |
| insufficient-baseline / HTTP200 health | 登录HTML、截断JSON、array、错误对象、401、恶意instruction、错误身份或仅200：inconclusive；坏baseline最多3请求，probe计数0；坏health仅1请求，不能转换业务计划。没有执行响应文本。 |
| exact-plan-gates | 缺/撤销批准、取消、digest错误、probe先发：仅已有2 health；0业务请求。 |
| change-after-real-rate-wait | 凭据同字节新version、source hold/release、revision变化或取消在实际rate wait后：0 GET；恢复/重试仍不能偷换旧依赖。 |
| observed-expiry | H=120、B=30、I=300到达边界，再校正UTC、使用新请求/clock：无额外GET，旧intent拒绝，历史只读。 |
| pair-references-and-legacy-consumers | 反转roles、health冒充baseline拒绝；真实新run不得进入legacy analysis/observed truth/direct/exact execution fallback；4次原请求不增加，旧行未改写。 |

完整 acceptance 还复用现有边界套件，不能仅从本文件绿灯推导安全：W1 source generation/foreign references/limits/rollback/concurrency；W2 final serialization、±1µs、slow response、source recovery、credential/Scope/approval变化、M8 in-doubt和零重发；M8 multiprocess fencing/cancellation；M13原有source/fingerprint/retention/review/report读写和再分析；M14 transient/no-cache/未知nested成员/unsupported preview。全部 backend shards 会重新执行这些原断言。无 schema/application/migration/CI partition 改动，legacy uniqueness、历史raw body和frozen evaluation保持原样。新源只合成，遵守 [安全模型](security-model.md) 与 [架构约束](architecture-decisions.md)，没有真实私有数据、provider、付费调用或公网执行。

## 4. 实际验证记录

实际环境：Ubuntu 24.04.2 / WSL2、仓库 backend `.venv` Python 3.12。自有根目录 `/tmp/ra04-w3.z2vrt0hf`；`prepare.py` 为每实例生成不同随机 SCRAM 凭据、mode0700目录和清空环境的 `run` wrapper。首次应用 import 前 psql 独立核验下表身份、UTF8、空schema和0其他client，再确认 operator encryption key 缺省。所有运行使用排除 `.env*`/`.venv` 的代码副本，没有 operator DB、私有资料、public Target 或 provider 调用。

| 角色 | database / user | 端口 | PostgreSQL system identifier |
| --- | --- | --- | --- |
| W3 walkthrough / collection | `w3_focused` | 55511 | `7684131813920089776` |
| W1/W2/M8/M13/M14 | `w3_regressions` | 55512 | `7684133339860630080` |
| 完整W2 shard | `w3_w2` | 55513 | `7684134221281375012` |
| 完整remaining shard | `w3_remaining` | 55514 | `7684134226796614469` |

Data directories 为该根下 `<focused|regressions|w2|remaining>/data`。两个 full shards 各自运行原 migrations 后串行 pytest；不共享 PostgreSQL server。没有修改 CI partition helper 或 workflow。实际 normal base collection 为3228个node；当前完整集合3262，精确保留原3228并自动加入34个W3参数case。W2=117、remaining=3145，交集0；34个新增node全部在remaining。逐条有序比对的 base/current node lists 和分片清单保存在 `base-nodes.json`、`collection.json`、`new-nodes.json`；实际有序 full SHA256=`8c2bc87f67d6a341a1b55d204b2b8127d9c55febdb0ace6fc37621c8a8f3199d`。没有以旧总数截断collection。

以下为实际命令；每个wrapper只带自身 DATABASE_URL、venv PATH、LANG，git/文档检查在仓库根：

```bash
/tmp/ra04-w3.z2vrt0hf/focused/run python -m alembic upgrade head
/tmp/ra04-w3.z2vrt0hf/focused/run python -m pytest -q -s --tb=short tests/integration/test_ra04_local_demonstration.py
/tmp/ra04-w3.z2vrt0hf/focused/run python -m pytest -q -s --tb=short 'tests/integration/test_ra04_local_demonstration.py::test_local_walkthrough[shared-allowed]'
/tmp/ra04-w3.z2vrt0hf/regressions/run python -m pytest -q --tb=short \
  tests/api/test_finding_evidence_excerpts.py tests/api/test_finding_evidence_fingerprints.py tests/api/test_finding_evidence_pairing.py \
  tests/api/test_finding_evidence_retention.py tests/api/test_finding_evidence_similarity.py tests/api/test_research_intents.py \
  tests/api/test_research_verification.py tests/integration/test_m14_matrix_acceptance.py tests/integration/test_m8_multiprocess_readiness.py \
  tests/migrations/test_research_intent_lifecycle_migration.py tests/migrations/test_research_intent_migration.py tests/migrations/test_research_verification_migration.py \
  tests/schemas/test_research_intent.py tests/schemas/test_research_verification.py tests/services/test_plan_execution_integration.py \
  tests/services/test_research_intent.py tests/services/test_research_intent_concurrency.py tests/services/test_research_intent_gates.py \
  tests/services/test_research_intent_knowledge.py tests/services/test_research_intent_sources.py tests/services/test_research_response_semantics.py \
  tests/services/test_research_verification.py tests/services/test_research_verification_clock.py tests/services/test_research_verification_expiry.py
/tmp/ra04-w3.z2vrt0hf/focused/run python -m ci_shards check --output /tmp/ra04-w3.z2vrt0hf/collection.json
# w2 / remaining 以下两条并发，分别先由自己的wrapper执行 python -m alembic upgrade head
/tmp/ra04-w3.z2vrt0hf/w2/run python -m ci_shards run w2 --expected-sha256 8c2bc87f67d6a341a1b55d204b2b8127d9c55febdb0ace6fc37621c8a8f3199d
/tmp/ra04-w3.z2vrt0hf/remaining/run python -m ci_shards run remaining --expected-sha256 8c2bc87f67d6a341a1b55d204b2b8127d9c55febdb0ace6fc37621c8a8f3199d
# 每个实例在测试后执行 heads/current/check；以下两项无operator配置：
/tmp/ra04-w3.z2vrt0hf/focused/run python -m evaluation.ra01 verify
/tmp/ra04-w3.z2vrt0hf/focused/run python -m pip check
git diff --check
```

`measure.py` 保留实际argv、退出码、wall/CPU时间及原日志；`full_shards.py` 在focused成功后检查实际collection，启动两独立进程，最后用真实job结果执行原 `ci_shards gate`。运行walkthrough原样生成的三个trace位于pytest临时目录；最终shared trace来自单独重跑。三份最终trace另保存于自有根的 `traces/safe.json`、`vulnerable.json`、`shared.json`，供本地review，不依赖pytest自动保留目录。实例仅用于本包，不能把其随机IDs/批准搬到部署。

| 已执行检查 | 实际结果 |
| --- | --- |
| W3全部场景 | **34 passed**, 1 warning, **268.77s** pytest / 269.44s process wall。 |
| 最后共享fixture事实校正 | **1 passed**, 1 warning, **21.56s** / 22.27s wall；明确A owner+allowed、B non_owner+allowed，其余33例代码行为不变。完整shards再次覆盖最终34例。 |
| 相关W1/W2、M8、M13、M14及migration | **667 passed**, 13 warnings, **580.62s** / 581.63s wall。 |
| 最终W2 shard | **117 passed**, 3145分配给另一shard/deselected, 62 warnings；pytest **457.19s**，process wall **458.23s**。 |
| 最终remaining shard | **3145 passed**, 117分配给另一shard/deselected, 62 warnings；pytest **615.47s**，process wall **617.25s**；包含最终34个W3 cases。 |
| Collection / aggregate | exact union、0 intersection；collection wall **12.03s**。真实collection/w2/remaining结果均success，原aggregate gate退出0；collection至gate **631.15s**。不是hosted CI性能测量。 |
| Schema | 四实例 `python -m alembic heads/current/check` 均唯一 **`6a94cbd3f825`**、**No new upgrade operations detected**。两fresh shard upgrades通过；原fresh/populated/legacy/empty/refused-downgrade tests全部通过，没有新增或改变migration。 |
| Evaluation / dependencies | `VERIFIED`，freeze=`692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a`，保留原approval状态；`No broken requirements found`（pip仅禁用不可写cache）。 |

开发期结果不计作最终PASS：旧fixture默认时间和硬编码observation日期引起两次setup error；初次已实际发送的safe流程在新断言中遗漏manifest健康roles，1 failed；独立source fixture曾使用不在closed vocabulary的ref，26 failed，随后复用旧batch导致1 failed；改为新合资格preparation/batch/entry后33 passed / 1 failed，最后失败是新测试把binding状态写成非法 `proposed`，已用合法 `candidate` 验证拒绝。以上全部是新fixture/断言修正；没有改应用、旧断言或安全限制来取得绿灯。最终共享事实的小修正消除了fixture catalog ownership与A relationship标签的不一致；不改变预期allowed结果。


最终静态核验：466个原tracked backend/workflow文件与基点逐字节相同；两shard中的最终W3 fixture/test与仓库相同。新增文档本地链接/anchors、三份trace结构及源码语法检查通过，`git diff --check`通过。只改本页、roadmap小更新及两个新增测试文件；旧断言/fixtures、CI分片语义、依赖、frozen evaluation和应用均未变。

测试结束后逐个独立复核四实例的database/user/host/port/data_directory/system_identifier、0其他client及operator key缺省，然后执行 `/usr/lib/postgresql/16/bin/pg_ctl -D /tmp/ra04-w3.z2vrt0hf/<role>/data -m fast -w stop`，四个postmaster PID文件均不存在。HTTP fixtures自行关闭，仅清理本包资源；日志、原node lists、时间JSON和三份trace保留于自有根供review。无未解决测试失败；warnings为既有pytest model collection提示及Starlette/httpx弃用提示。

限制与停止：这是可重复的自有synthetic本地API/真实HTTP执行演示，不是部署授权、私有数据资格、无人值守操作、通用报告或公网就绪证明。No-baseline显示拒绝/NEEDS_INPUT及明确不确定性，不制造pair。Hosted PR/main CI和独立Review Project结论仍待执行；本包不宣告RA-04 COMPLETE。仅创建本地commit，**不push、不创建/合并PR、不开始RA-05**，等待Review Project。
