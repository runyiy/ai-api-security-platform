# RA-03/W2：有界、先资格过滤的知识检索

> **后续W3：** 本文保留W2实施/修复历史；[W3验证与反馈审核](research-rule-validation.md)新增真实证据加独立人工publish的有条件gate。W2的NOT_RUN/synthetic_test_only不成为W3证明；过滤、actor、来源和最终时间边界保持。

**实施记录 v0.1.0 · IMPLEMENTED / PENDING_INDEPENDENT_REVIEW**。源码基点 main `dcf3ec044157bb7db816368d5b7683a1b7edfb1d`；分支 `codex/ra-03-w2-knowledge-retrieval`。此文记录 W2 实现，不替代 [W1 知识契约](research-knowledge-contract.md)、[K1–K4 采纳记录](research-knowledge-contract.md#k1k4-后续采纳记录ra-03w2)、[roadmap RA-03](research-assistant-roadmap.md#ra-03--版本化知识已审查规则和受限检索)。普通 publication 未启用；本次自检不是独立 review、W3 validation、实际规则发布或 RA-03 COMPLETE。

## 1. 实际边界与证据

新增 [schema](../backend/app/schemas/research_knowledge.py)、[models](../backend/app/db/models/research_knowledge.py)、[service](../backend/app/services/research_knowledge.py)、[routes](../backend/app/api/routes/research_knowledge.py) 和三张知识表。`record` 只创建 candidate；`decide` 追加 review/reuse/withdraw/disable。`publish` 总是返回 409 `knowledge_publication_closed`，无启用开关、test-mode 参数、seed 发布或普通发布实现。

`retrieve` 的正向路径只用 [测试拥有的 publication fixture](../backend/tests/research_knowledge_fixtures.py) 验证：测试直接插入 `synthetic_test_only` 记录，返回同一标记。正常操作者不能通过 API 写入该标记或 W3 validation 引用，candidate/reviewed 不进入结果。`NOT_RUN` 不转换成 W3 成功。未来接入正式发布须通过 W3 独立验证及另一次人工决定，不可把测试 fixture 搬入部署。

读取现有 [W1 服务](../backend/app/services/research_context.py)、[W2 服务](../backend/app/services/research_observation.py)、[W3 服务](../backend/app/services/research_subject.py) 后复用其 project/context 锁、Target 归属、来源资格、M12 resolution 和缺项语义。旧接口及凭据 fixture 不改。阅读限于这些直接依赖、相应 models/schema/tests 和规范；未审计全仓库、读取实际凭据、真实私有材料或 held-out/oracle 内容。[评测冻结](research-assistant-evaluation.md) 只经 evaluator 内部 verify 检查。

合成准入有意严格：五类均有显式 category，但本表只接纳 `mechanism`、`rule`。`project_evidence` 留在原项目域，`external_triage` 不准入；独立 `counterexample` 卡需精确关联及 W3 配套协议，本包拒绝其独立持久化，规则只保存有界 `example_refs`/`counterexample_refs`。这些引用尚未经 W3 执行验证，不是 oracle。只接受 `synthetic_authored`、固定四条解释 claim 和八个 tag（以 schema Literal 为准），不接纳自由文本、脚本、URL、私人证据正文或“公开即合成”的重标记。

`project` 卡绑定当前 context version/Target，可有本项目 observation 软引用；`reusable_synthetic` 只允许独立 synthetic 根引用，必须有本版本 review/reuse/publication 有效窗口。共享根无项目来源依赖，不读取作者项目的 Target/permission，不输出作者 context/Target；作者退出不会把独立根变成项目证据。使用者自己的 context/subject 必须仍有效。带项目来源的内容不能通过本服务改 scope、自动复制或更正去掉旧 source 以获得共享；没有私人通用化入口。固定合成引用是操作者声明，review 是独立记录，不证明外部文件存在，更不构成权限批准。

## 2. 精确版本与决定历史

逻辑精确引用为 `(scope, knowledge_id, version, digest)`；project scope 还由请求可信 context 限定，共享 scope 的名称/version 全局唯一。`knowledge-N` 中 N 为 1..999999，version 1..10000。content 使用 `ra-knowledge/1` 的本包严格合成子集；UTF-8 JSON、sort_keys、无空白 separators、数组原序，SHA-256 仅计算合资格 content，绝不计算原始输入/源正文的原始哈希。来源引用中的 digest 指 W2 已批准 canonical payload，消费时核对，不保存 payload。

版本 1 没有 supersedes；版本 N 必须引用本拥有者同名/scope 的 N−1 精确 digest。更正不继承 review/reuse/publication，也不自动撤回旧版；不允许改变 Target 或移除先前来源。withdraw 禁止此版本再消费；disable 同时排除可追溯的 supersedes 派生链，链缺失/循环/损坏也拒绝。没有自动 latest 升级或退回旧版。原版本和事件均有数据库 UPDATE 拒绝 trigger；运行接口不提供删除。新域无敏感历史保留；原 observation 的删除独立进行。

事件由 server 记录 local_operator、aware 时间、exact digest/context version、review 合成引用、单调 sequence。客户端只提交 expected_sequence；不一致原子拒绝。review/reuse 窗口和 publication 测试记录均按 `valid_from <= now < valid_until` 判断；引用的 review/reuse 必须属于本 exact version、位于 publication 之前且仍有效。任意 withdraw/disable 均排除。重复 review 不是冲突赢家选择；必须是调用者明确指定序列的下一次事件，且不能绕过终止事件。审核不是 M12 human_verified，不能写 access assertion、Finding 或执行批准。

## 3. 查询、限额与结果

路由前缀 `/api/research-projects/{project}/contexts/{context_id}/knowledge`。本地 trusted operator API，沿用现有部署边界，不引入多用户认证或 SaaS 权限体系。所有操作为 POST、`application/json`、无 query 参数、无压缩编码；错误为固定 code，响应 `Cache-Control: no-store`。

| 路由 | 输入及行为 |
| --- | --- |
| `/versions` | context_version、target_id、完整 content、review；返回 exact reference、candidate、audit_id |
| `/decisions` | exact reference、expected_sequence、action、review、valid_from/valid_until；返回精确 event_id/sequence；publish 拒绝 |
| `/query` | 当前 context/subject 精确版本、固定 purpose、有界 keywords/tags/top_k/selected；返回精确内容/来源/正反例/决定引用及缺项 |
| `/audit-maintenance` | 显式合成 review 引用；仅轮换本 context 操作审计，允许容量恢复；不改版本/决定/观察 |

| 独立限额 | 已选值与超限行为 |
| --- | --- |
| 请求实际字节 | query 8192；其余 32768；流式计数，超限 413，无临时副本 |
| JSON 结构 | depth 5、nodes 2048，沿用 bounded parser 的编码保护；字节/节点限制约束解析工作量，不声称独立墙钟超时；重复键/BOM/非法 UTF-8/surrogate/NaN/浮点/多 JSON/未知字段拒绝；int 严格，bool 不作数字 |
| 内容/输出 | canonical content ≤16384 bytes；整个编码结果 ≤65536 bytes，不能截断后报告成功；DB JSONB content 文本另限32768，event body4096，audit review256 |
| 字段 | source/example/counterexample refs 分别≤16；tags≤8；actors≤2；每个 ref 使用 SyntheticReference 的 fixture_id/version 上限；所有额外字段拒绝 |
| 查询 | keywords≤8，每个1..32 ASCII小写字母；tags≤8；两者不能全空或各自重复；top_k 1..8；selected≤32，exact duplicates 去重 |
| 存储/扫描 | 每 owner context 总共128版本，共享目录另限128；每次只扫描自己+共享至多256，发现257拒绝，不能给部分结果；至多64个 card observation source 资格读取/初筛；subject 本身≤8个来源，每次按现有 W3 与格式检查重验 |
| fact资格窗口 | 对已核验的本项目Resource/identity，最多256条当前或未来可用的verified assertion窗口；查询LIMIT257，超限整次knowledge_scan_limit。排除已到期、空窗口及其他Resource/identity；不按proposal选中的assertion IDs过滤未来冲突 |
| 决定/审计 | 每版本16事件；review/reuse 最多占14，留下withdraw/disable位置。每 context4096条操作审计；满时普通操作失败，显式轮换退役最早512并原子写 rotate；未满仅退役≥90天的操作审计 |

操作审计不是永久证据档案：只留 context、code、时间、必要 review，绝不留 query/正文。轮换需要操作者明确请求，不静默丢记录；rotate 自身失败则全部恢复。版本/决定不轮换，4096/512 不改变 W2 observation 8192容量或其90天时钟。长期用尽版本/决定额度须审查后续迁移，不能自动扩容或删历史。没有私有数据备份/恢复部署保证，因此私有准入仍拒绝。

过滤次序：严格解析 → context/project/Target/当前subject/数据资格 → exact version/digest/污染链 → review/reuse/publication窗口 → 现有来源读与 applicability → score → top-k → 再验来源/事实/窗口 → 原子审计、编码、提交。

score = `|keywords ∩ claim中的ASCII小写词集合| + 2 × |tags ∩ content.tags|`，只保留正分，最大24。tie-break 为 `(-score, scope, knowledge_id, version, digest)`，字符串按 Python 字典序；不是自然数字序（knowledge-10 可排在 knowledge-2 前）。无 corpus、向量、模型、随机数、置信度或 latest 替换。固定状态下 matches 顺序、内容、精确引用相同；每次 evaluated_at/audit_id 是新的，不声称整个 JSON 字节相同。

返回 `ra-knowledge-retrieval/1`，含本项目 context/subject version、evaluated_at、audit_id、最早已核验 deadline `eligibility_until`，每条 match 的 exact ref/content、score、review/reuse/publication event IDs、实际 review_actor/publication_actor、test-only 标记。foreign/nonexistent 精确选择都得到无匹配而无排除项 ID/标题/数量/分数；foreign/nonexistent context 均固定 unavailable。普通结果有 `no_match`、`unsupported`、`needs_input`、`source_unavailable` 或 `matched_synthetic`。所有结果 `execution_authorized=false`、`ordinary_publication_allowed=false`，保留 budget_unapproved/intent_decision_pending 等。gap 和零匹配均不代表 safe。

机制和规则都先检查声明的 applicability.actors，再进入 ranking/top-k；机制只给 general_explanation，actor匹配时仍可解释事实缺项。需要事实的规则只对单一 path 参数、GET 及明确 actor 的当前上下文作解释。还必须有当前 subject 明确引用的合资格 observation：同一 endpoint template、无 query、单 resource label，声明完整 JSON object 且单 object label；缺失/unknown/truncated 返回 object_shape_missing，数组、query、多对象或不匹配 template 排除为 unsupported。每个关联来源都需通过，不挑一个有利观察覆盖其余记录。通过只说明已检查导入格式声明，始终保留 observation_shape_unverified，不把 alias 变成 Resource membership 或验证过的响应真值。W3 解析全部当前 eligible verified facts，不按请求挑选冲突赢家；owner+denied、non_owner+allowed、sharing 彼此独立。missing/conflict/shape/session 等缺项排除规则，机制可以解释缺项。bearer 即使 operator_reported_valid 仍有 session_health_unverified，W2 不发健康请求或发明 TTL，不能返回依赖健康的规则。JSON object/membership 是后续 bridge 约束；本包不验证实际响应、确认 Resource-slot membership 或准备 executable intent。

## 4. 事务、生命周期与迁移

每次服务先取得知识目录事务 advisory lock `(73103,2)`，再复用 W1 context 锁；跨项目共享 withdrawal 也走同一目录锁。这是低吞吐本地有界实现，所有知识操作串行，不是 scheduler。检索另以 SHARE 锁住 resource_access_assertions、authorization_revisions、scopes，防止 M12 新插入/复审或 permission/Scope 修改在消费中穿过；现有 subject 读取锁住 Target、Resource、slot、identity/credential非秘密元数据。表级锁会阻塞无关项目这些写操作，属于明确代价，未声称性能收益。

context close/transfer、subject correction、observation lifecycle 沿现有 context 锁串行。退出后旧 context 请求在读新拥有者 metadata 之前拒绝。每次重新读取当前subject和source，不缓存 permission；M12 facts 再核对；permission与fact窗口相对生成初始readiness/facts的时刻收集，不能因后续时钟已跨界而丢弃。最早deadline包含source/review/reuse/publication、revision的valid_from/valid_until，以及同一Resource/identity全部适用verified assertions的下一次资格变化（开始为max(asserted_at, valid_from)，结束为valid_until），包括proposal未选中的未来冲突。readiness使用同一服务时钟；审计后、服务model_dump后及API JSON编码后跨界即整次拒绝。无匹配响应也保留deadline；客户端须重新请求以得到边界后的事实/gap/match，不自动选冲突赢家。锁在响应编码完成后的 commit 才释放，语义为此消费事务的线性化结果；已返回结果不是未来操作的授权快照，后续消费者必须重验。

普通 observation read 不传 human hold review，held/deleted/expired/quarantined/revoked 来源不能消费。到期删除后来源软引用保留，但不返回卡兜底；hold 合法 release/end 后按 W2 当前状态重验，不修改 W2 clocks。知识无 ObservationRecord/Payload FK，不阻止授权 purge；不复制观察正文、不提升 operator_import_unverified、不写 observed_baseline。来源审计、知识审计、响应编码任一失败，保存点/外层事务回滚，不返回部分成功。

[迁移 f0b2d4e6a8c0](../backend/alembic/versions/f0b2d4e6a8c0_add_research_knowledge.py) 仅从 e9a1c3d5f7b8 新增 research_knowledge_versions/events/audit 及约束/index/trigger。context/version/Target 组合 FK 保护原始出处；不回填、不读取旧source bodies。降级先 ACCESS EXCLUSIVE 锁全部新表，任一非空即 `research_knowledge_populated_downgrade_blocked`。仅新域全空才撤销新增结构；实际有价值数据保留迁移，回滚应用并停用新入口，不能删历史以强行 downgrade。

## 5. 独立合成操作例与验收映射

以下 query 是本包新写、可在拥有的合成 fixture context/subject=1中使用的完整请求；路径context ID取实际 fixture 返回值，不推测真实数据库ID。

```json
{"context_version":1,"subject_number":1,"subject_version":1,"purpose":"offline_context_explanation","keywords":["sharing"],"tags":["access"],"top_k":8,"selected":[]}
```

[合成 API 示例测试](../backend/tests/api/test_research_knowledge.py) `test_operator_candidate_and_publication_refusal` 用新写 content fixture 提交完整 `/versions`：200 candidate。再提交 publish：409 knowledge_publication_closed，数据库快照不变。query 为200、matches=[]。这是测试断言的实际结果，不是普通规则已发布的演示。

测试专用发布案例：knowledge-1/version1、claim `Sharing may allow non-owner access.`、tags sharing/access，分别追加独立review/reuse及明确synthetic_test_only publication。上述 query 得分3，返回本 exact digest和三个事件ID；selected 同一ref两次只返回一次。版本2单独审查并保留supersedes，选v1不升级到v2。withdraw v1 后不返回v1；disable v1会排除其派生链，历史行不改写。实际 digest和ID由测试生成，本文不伪造部署记录。

| 验收点 | 已有新测试证据；边界 |
| --- | --- |
| 精确版本、去重、排名、无匹配、先过滤 | [service tests](../backend/tests/services/test_research_knowledge.py)：deterministic、high-rank canary、independent shared、each exact publication dependency、256/257 scan、128/129 storage、top_k8/9、selected32/33 |
| 生命周期及污染 | 同上 source_unavailability、contamination；[concurrency tests](../backend/tests/services/test_research_knowledge_concurrency.py)：read-first/mutation-first withdraw/hold/delete/close、close transfer、source/publication到期、hold release与90天purge |
| 事实与会话 | owner-denied/non-owner-allowed/shared、全部冲突、bearer多种会话声明；并发新增冲突 assertion 被锁住。只是消费限制验证，独立规则正反例执行仍属W3 |
| 原子失败与隔离 | 两个独立project/Targets的canary、foreign/nonexistent一致、审计写入/轮换失败、API编码失败；旧表快照及零能力guards |
| 输入与准入 | [schema tests](../backend/tests/schemas/test_research_knowledge.py)：字节、depth、node、类型、UTC半开窗口及未知/数组/query/多对象形态；API非法UTF8/重复键/extra/private/script固定错误；没有 held-out 内容 |
| 迁移兼容 | [migration tests](../backend/tests/migrations/test_research_knowledge_migration.py)：新空schema、填充legacy/W1/W2/W3、配对/指纹/review/report保留；空降级及非空拒绝 |

## 6. 修复前验证记录与仍需决策

按 [独占数据库 runbook](m14-offline-matrix-acceptance.md#reproducible-isolated-local-run)，使用 Ubuntu WSL、项目 backend/.venv Python 3.12.3、全新独占 PostgreSQL 16.15。应用导入前以 psql 独立核对 test database/user、127.0.0.1/独占port、本人0700 data directory、public schema初始空、无其他clients。完整验证在临时无 `.env` 的源文件副本执行，使用原项目 venv，`env -i` 只显式设置测试 DATABASE_URL、PATH、LANG；不依赖环境加密key，原W3 opt-in disposable encryption fixture保留。副本来自当前完整 tracked/new source，不重写冻结文件；DSN/日志/临时资源不进入Git。结束前再次核对同一owned实例及0个其他clients；已停止仅本次创建的实例并确认postmaster.pid移除。

实际命令均用上述项目 venv 的 `python`；数据库测试串行执行。初始 `python -m alembic current` 为空，`python -m alembic heads` / `python -m alembic upgrade head` 成功；最终 current/heads/upgrade head/current 均为 `f0b2d4e6a8c0`。

```bash
python -m pytest tests/services/test_research_knowledge.py tests/services/test_research_knowledge_concurrency.py tests/api/test_research_knowledge.py tests/schemas/test_research_knowledge.py tests/migrations/test_research_knowledge_migration.py --tb=short
python -m pytest --tb=short
python -m evaluation.ra01 verify
python -m pip check
python -m alembic current
python -m alembic heads
python -m alembic upgrade head
python -m alembic current
git diff --check
```

| 实际检查 | 结果及边界 |
| --- | --- |
| 最终新域聚焦 suite | **116 passed，1 warning，26.02s**；含真实并发和新迁移。先前新域+W1 isolation+W3 service/concurrency 联跑162 passed；最终完整suite也覆盖既有W1/W2/W3回归 |
| 最终完整后端 suite | **2642 passed、1 failed、55 warnings，182.94s**。失败为 `tests/integration/test_m8_multiprocess_readiness.py::test_shared_rate_reservations_order_full_plan_processes` 第425行：`reservation is True` 实际 False；SQL检查 `next_allowed_at > clock_timestamp() - interval '2 seconds'`。此前两进程HTTP200、两次请求以及间隔≥0.35s断言通过。根因未确定，不能认定环境问题或宣称全量绿色；未修改该M8测试/执行器/协调代码，未对该失败重试 |
| 先前全量检查 | 定稿前两次为2634/2637 passed、各55 warnings；随后补充actor输出、字节边界和形态过滤测试。这些历史绿色不替代上述最终失败结果 |
| 迁移兼容 | fresh、populated legacy/W1/W2/W3、历史pair/fingerprint/review/report保留、空降级/非空拒绝测试通过；旧测试仅适配最新head及新表清单，不删除原约束/历史断言 |
| 冻结验证 | `status=VERIFIED`；digest `692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a`；approval仍为PROPOSED_PENDING_REVIEW_AND_OPERATOR_APPROVAL，未变更corpus或标签 |
| 依赖检查 | `No broken requirements found`；只另有pip缓存目录不可写警告，未更改依赖或缓存权限 |
| 文档/范围 | 122个本地链接/锚点及历史源码permalink对象校验通过；完整query示例JSON/schema验证；43个已有文件修改+12个新文件，只有知识域/注册/迁移测试适配及相关文档；`git diff --check`通过。测试副本与当前全部改动backend文件逐字节相同，原W3加密fixture和两项相关service测试逐字节保持base |

开发中发现并修正了新域审计DB故障误归409、32个exact refs超出原4KiB草案query容量、会话测试缺少合格声明引用、形态测试重复entry及不合封闭词汇的preparation输入；各自修正后再验证，未放宽既有规范或跳过失败。此修复前记录中的最终M8失败仍保留供独立Review Project评估，本文不把它消除为“已知无害”；后续三项blocker修复验证单独记于§7。不以测试记录替代真实发布质量证据。W3 validation/反馈及普通publication gate仍未实现；INTENT/session/baseline、EGRESS、TASK预算等 pending ADR 不因本包改变。[架构](architecture-decisions.md)、[安全模型](security-model.md)、[产品范围](research-assistant-product-contract.md)、[观察生命周期](research-observation-intake.md)、[subject边界](research-subject-context.md)保持原权威；网络、模型费用、执行、实际私人复用始终未获本包授权。

## 7. 三项独立Review blocker修复（2026-09-10）

本次仅修复 permission到期、未来verified fact开始生效及mechanism actor过滤三个blocker；继续原分支，父提交为reviewed HEAD `b99258a48c27e9e5e59affecefc46b5b475d1ba4`，另建本地fix commit，不amend、不push、不merge。普通publication继续关闭，未开始W3，也未增加外部、凭据或Target操作。用户提供的reviewer全量结果为2643 passed，属于交接证据；§6的2642 passed/1 failed保留为原始历史，未修改M8行为或测试以消除记录。

新增[永久边界回归](../backend/tests/services/test_research_knowledge_boundaries.py)及[API编码回归](../backend/tests/api/test_research_knowledge.py)：permission在context创建前设置有效窗口，避免把permission snapshot变更误当到期；在ranking、query audit、service model_dump及API canonical编码中推进时钟，分别覆盖边界前1微秒、等于边界、后1微秒。未来denied assertion未被proposal选中，分别由valid_from及asserted_at控制开始；另验max起点、空窗口、当前fact到期、fresh read重算gap、无匹配响应deadline、其他Resource/identity及candidate隔离、256/257容量、actor先过滤再top-k，以及actor匹配的mechanism在facts_missing时仍能解释。服务失败后即使调用者commit，知识与来源审计也全部回滚；API失败不发送已编码的stale matches。

交接路径 `/tmp/review-ra03-w2-9fd4i5h7/test_independent_boundaries.py` 在本环境不存在；按交接复现重建永久测试。只在临时测试副本把service替换为reviewed HEAD后运行三个核心测试：permission精确到期/ranking、valid_from精确生效/audit、mechanism actor/top-k，实际 **3 failed，2.13s**，分别为未拒绝、未拒绝、错误机制赢得top-k。恢复修复service后进行下列验证，不修改workspace旧提交。

验证使用本人拥有、0700 data directory的全新PostgreSQL 16.15 UTF-8实例；独占loopback端口与专用测试database/user，导入应用前psql核对database/user/address/port/data_directory/encoding，public表数0、其他clients 0。源文件副本无`.env`，`env -i`只传测试DATABASE_URL、PATH、LANG，显式断言配置的operator encryption key为None；保留原W3 opt-in临时加密fixture。416个backend tracked/new文件与最终测试副本逐字节一致。初次SQL_ASCII实例在SQLAlchemy连接初始化失败，换成新UTF-8实例后迁移成功；没有因此调整应用行为。

在副本backend工作目录使用原项目venv的Python 3.12.3，数据库测试串行：

```bash
python -m pytest tests/services/test_research_knowledge.py tests/services/test_research_knowledge_boundaries.py tests/services/test_research_knowledge_concurrency.py tests/api/test_research_knowledge.py tests/schemas/test_research_knowledge.py tests/migrations/test_research_knowledge_migration.py --tb=short -q
python -m pytest --tb=short -q
python -m evaluation.ra01 verify
python -m pip check
python -m alembic current
python -m alembic heads
python -m alembic upgrade head
python -m alembic current
python -m alembic check
git diff --check
```

| 本次实际检查 | 结果 |
| --- | --- |
| 开发边界/API首轮 | 61 passed，1 warning，22.52s；随后补充无匹配响应回归 |
| 最终W2 focused | **163 passed，1 warning，52.01s**；含全部原116例及47例新增回归 |
| 最终完整backend | **2690 passed，55 warnings，257.15s**；本次M8测试通过，未修改其行为或对失败重试 |
| 迁移 | 新空UTF-8实例upgrade head成功；focused包含fresh/populated兼容、空降级/非空拒绝；最终current/heads/upgrade head/current均成功，head为f0b2d4e6a8c0；alembic check退出0 |
| 冻结 | VERIFIED；digest `692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a`，approval仍为PROPOSED_PENDING_REVIEW_AND_OPERATOR_APPROVAL |
| 依赖 | No broken requirements found；仅pip cache不可写警告 |
| diff及清理 | git diff --check通过；26个本地文档链接目标存在。最终仅5个知识域service/tests/docs文件变更。停止前再次核对同一owned实例及其他clients 0；已停止本次测试实例，日志确认shutdown完成且postmaster.pid已移除 |

本次自检不代替独立Review Project review；完成本地commit后停止，等待独立review。
