# RA-04/W1：INTENT 协议与兼容性建议

**文档 v0.1.0 · PROPOSED / PENDING_APPROVAL · DOCUMENTATION_ONLY / PENDING_INDEPENDENT_REVIEW**

基点为本地核验的 clean `main`：`bedc55395d2e5abf3479026fd201430b537baff2`；分支 `codex/ra-04-w1-intent-contract`。用户交接记录 RA-03/W3 reviewed `91db91f59761b5683309062d9b7ac51536bfe75f` 经 PR #142 集成、main 2790 passed；这是交接证据，本包未重新运行 backend suite，也不代签 stage COMPLETE。本文在既有 RA-04/W1 内补齐 [ADR-RA-INTENT §6](research-assistant-adr-decisions.md#6-adr-ra-intent显式访问语义精确配对与-session)，不是新增工作包。推荐协议、数值、持久化名和结果码均待决定、待实现；文档审阅不等于操作者或 Tech Lead 采纳。

[架构授权/执行约束](architecture-decisions.md#authorization-and-execution)、[安全模型 §§3–4、8–13](security-model.md#3-core-security-invariants)、[产品契约 §§5–7](research-assistant-product-contract.md#5-请求形态支持矩阵)优先。本文不修改这些不变量。初始范围仍为 private/local 合成 Target、一个 builder-compatible 无歧义 resource path 参数、GET、完整 JSON object、显式 anonymous/bearer；query/nested/multiple 仅 preview，其他形态不转换。没有请求、凭据读取、实际批准或样本导入；示例为独立作者值，不能作为执行命令。

## I1–I7 后续采纳与 W1 实施记录

本次用户 **RA-04/W1 bounded conversion handoff** 明确记录：操作者采纳本文 **v0.1.0、reviewed commit `6723cb5bfa62a10453f18f8158c25000a1997711` 的 I1–I7**，Review Project Tech Lead 将其作为实现约束。Codex只转录该交接，不补造批准身份、签名或消息时间。原文PROPOSED/PENDING段落保留为该次提案历史；不再表示I1–I7设计未获采纳。

采纳允许既有W1有序实现，不批准实际Target/health请求、凭据访问、私有材料或支出，也不批准其余待决ADR。W1基于main `59390a480db133db9411908af05df37dd625fc91` 实现映射确认、有限manifest与独立预算决定、不可变core/两单GET计划/link及旧入口拒绝，详见 [W1实施与实际验证](research-intent-bridge.md)。**IMPLEMENTED / PENDING_INDEPENDENT_REVIEW**；生产W2解释/health证明入口仍明确拒绝，新purpose的审批/执行保持关闭。测试中的future-qualified envelope只是独立受控替身，不是运行时证据。§1及§8原代码/验证记录锁定原提案基点，不能倒读为本次实现事实；pair30秒实际运行/最终响应解释留在W2，未伪造执行时间。

## 1. 当前代码证据 C 与缺口

以下定位以本基点实际文件和调用方为准；不是沿用旧 ADR 的历史 C 标签。链接定位文件，反引号标识可直接检索。

| 当前链路 C | 本基点行为；对建议的限制 |
| --- | --- |
| [M14 route](../backend/app/api/routes/bola_matrix.py) → [composer](../backend/app/services/bola_binding_matrix_preview.py) `preview_bola_binding_matrix` → [selector](../backend/app/services/bola_binding_selection.py) `select_bola_binding` / [resource preview](../backend/app/services/bola_matrix_preview.py) | 瞬态只读；confirmed binding 只确认 selector，不证明 Resource-to-slot 或 membership。不存在 candidate→plan 调用。不得在 preview 内加写入或缓存 |
| [M12 resolver](../backend/app/services/resource_access_resolution.py) `resolve_resource_access`；[subject](../backend/app/services/research_subject.py) `_qualify` / `read` | 全部 eligible verified assertions、独立两维、冲突不选赢家，256/257 上限；subject 锁定非秘密 identity/binding/version ID，始终保留 membership/session 等缺项；operator_reported_valid 无 TTL、不是健康证明 |
| [planner](../backend/app/services/test_case_planning.py) `create_test_case_execution_plan` → [plan service](../backend/app/services/execution_plan.py) `create_execution_plan` | 选择 Target 当前 revision；真实 TestCase/Resource provenance；`compute_plan_digest_v1` 覆盖 policy_context/action/actor/binding/revision，但不自动绑定 secret version 或 INTENT。存储允许 1–100 actions，executor 恰好一个；policy_context ≤16384 bytes |
| [approval](../backend/app/services/execution_plan_approval.py) `record_plan_decision` / `is_plan_approved` | 追加 approved/revoked、绑定精确 digest，最新匹配决定生效；无独立 approval TTL，无研究预算证明。静态搜索 `backend/app`：创建/决定 service 无已注册 HTTP 调用方；未来本地入口须另实现 |
| [execute route](../backend/app/api/routes/test_runs.py) → [PlanExecutionService](../backend/app/services/plan_execution.py) `execute` | 检查一个 GET、非空 TestCase/Resource、当前 actor/Target/revision/适用 approval；已有 canonical TestRun 返回原结果不重发。`refresh_authorization` 在 rate wait 后重验 plan/revision/Scope/approval；凭据/headers 已在等待前构造 |
| [bearer service](../backend/app/credentials/bearer.py) `update` / `resolve_binding`；[provider](../backend/app/credentials/stored_secret.py) `store_secret`；[auth context](../backend/app/auth/context.py) | update 持 identity→binding 锁、追加 secret version；resolve_binding 按最高 version ID 读取，不按计划固定版本读取。认证只走 AuthenticationContext；最新 token 可用不证明 Target session 健康 |
| [HTTP executor](../backend/app/executors/http.py) `execute` → [gateway](../backend/app/network_safety/gateway.py) `request` | rate wait 后 refresh，随后 before_network/M8 标记，再 gateway admission、destination resolution/connection。5s timeout 参数并非整个等待/流式读取的绝对总时限；不能把早期 refresh 当最终资格边界 |
| [TestCase](../backend/app/db/models/test_case.py) / [TestRun](../backend/app/db/models/test_run.py)；[legacy analysis](../backend/app/services/finding_analysis.py) `analyze_test_run` | TestCase 四列唯一键仍在；run 必须有 TestCase。legacy probe 必须 bola_cross_owner，baseline 必须 owner_baseline、同 Endpoint/Resource、不同 actor、精确 run ID；不能把 owner+denied 重标来复用 |
| [M13 analyzer](../backend/app/analyzers/bola.py) `fingerprint_response_pair`；[report](../backend/app/services/security_report.py) `generate` | 指纹对已存字符串 UTF-8 精确取 hash，非语义或权限证据；源改变时再分析冲突。report 只从 confirmed Finding 生成且仍读旧 source body。没有通用 assertion-aware pair/report |

RA-02 [intake 预算/准备度](research-intake-context.md#2-输入预算与准备度)、[subject 生命周期](research-subject-context.md#3-生命周期隔离与事务)及 [observation lifecycle](research-observation-intake.md#4-生命周期与操作责任)继续有效。RA-03 [W2 最终时间边界](research-knowledge-retrieval.md#7-三项独立review-blocker修复2026-09-10)及 [W3 validation/publication](research-rule-validation.md#2-不可变证据与发布资格)提供依赖核验模式，不提供网络/session/verifier authority。

## 2. 不可变结构与摘要 P

建议协议 `ra-intent/1`，canonical codec `ra-json/1`；未知版本一律拒绝，不能 fallback legacy。一个 intent version 只描述一个 Resource 上两个不同 actor 的一次 baseline/probe pair。成功转换前没有可执行的草稿；缺项输出 NEEDS_INPUT（固定原因列表），不创建部分 plans。

| 核心字段组（全部进入 intent digest） | 确切含义 |
| --- | --- |
| `protocol`, `intent_id`, `version`, `supersedes`, `pair_id`, `created_at`, `expires_at` | 服务分配 ID、正整数版本和新 pair ID；首版 supersedes=null，后版指前版 ID/version/digest，不覆盖前版。pair_id 每个版本独有，不是任意跨 pair 的关联键 |
| `context` | project_number、context_id、intake_version、Target association ID、data-policy 精确版本；两个 subject 的 number/version；context correction/转移不能被旧版本“最新读取”替换 |
| `candidate` | exact M14 planner version + 选定 candidate kind/Resource/actor；同事务重算的受限投影 digest。若依赖知识，附 exact knowledge key/version/digest、contract、validation ID/digest、review/reuse/publication event IDs；只接受当前合资格真实 W3 证明及独立 publication，synthetic_test_only / NOT_RUN 不合资格。无 rule match 不生成这个规则候选，不捏造 rule 引用 |
| `request` | Target ID、精确 origin/network_mode、Endpoint ID/method/template、一个 confirmed binding ID/location/selector、Resource ID/type/external ID 的合资格 synthetic 值、builder version、最终 canonical URL。method=GET，location=path；无 query/body/自选 headers |
| `mapping_confirmation` | **新增人工确认记录** ID/version/digest、精确 context/Endpoint/template/binding/Resource/value、证据引用、实际 reviewer 决定和服务记录时间。确认“该值指这个 slot 的这个对象”，不只是选择下拉项，也不是复制 subject.operator_proposed。需要新确认时不可自行填写 operator 身份或 verified |
| `actors` | 固定 baseline、probe 顺序；每项 identity ID/auth_type/active 元数据摘要、credential_binding_id、credential_version_id（anonymous 两者必须 null）、精确 subject 引用、relationship、expected_access、supporting assertions 引用、health evidence 引用。baseline 必须独立 allowed；probe allowed/denied 明确；bearer relationship 不能 unspecified，anonymous 可 unspecified |
| `facts` / `sources` | 每 actor 全部 supporting ID 及其 assertion/review 状态、provenance、asserted_at/valid_from/valid_until 与受限字段 digest；不是只保存操作者选中行。observation 使用 context 内精确 observation_id/entry_index 和可用资格引用；不复制 payload 兜底。固定来源类型，禁止自由文本或把返回内容作为指令 |
| `authorization` / `limits` | 每角色一个精确 revision ID 和许可来源引用；推荐两者同 revision（§3）；Target/profile 关联和相关 Scope 集合摘要；精确预算决定引用、两计划各 request_count=1、总请求清单及绝对 deadline。null/unverified 预算不转换 |
| `interpretation` | 固定版本的 identity/object/denial interpreter 引用和经审核的 synthetic fixture contract digest；字段期望来自独立确认，不能从本次 response 反推。非表达式、无 SQL/脚本/任意 JSONPath 执行 |

`ra-json/1`：严格 UTF-8 JSON，拒绝 duplicate keys、BOM、surrogates、float/NaN/Infinity、额外字段；整数、bool、null 不强制互转。keys 按 Unicode code point 升序，separators=`(',', ':')`、ensure_ascii=false、无尾换行、不做 Unicode normalization；协议键及枚举 ASCII。时间先验证 RFC3339 offset 且转 UTC，统一六位微秒 `YYYY-MM-DDTHH:MM:SS.ffffffZ`；拒绝 naive/leap second/精度超过微秒。集合型引用按 kind/ID/version 排序并拒绝重复；actors/actions 保留指定角色/ordinal 顺序。canonicalization 是版本化规则，不是任意输入的“清洗”。

`intent_digest = SHA256(UTF8("ra-intent/1\n") || canonical(core))`，64 位小写 hex；core 不含自身 digest、plan/run/approval IDs 或派生 current eligibility。子记录同 codec，使用自己的协议名加换行作 domain separator。收到 caller digest 时独立重算，再核验数据库 exact reference 与当前资格；hash 不是来源真实性或授权签名。

**单向联结，避免循环 hash：** 先冻结 core → 创建 baseline/probe 各一个 ExecutionPlan → 每个 plan 的现有 v1 policy_context 增加非秘密 `research_intent={protocol,intent_id,version,intent_digest,pair_id,role}` → 现有 plan digest v1 自然覆盖该引用 → 同事务追加 `ra-plan-link/1`（intent digest、两个 exact plan ID/digest、action ID、role）。plan link 再独立取 digest；它不回填 core。每个 plan 唯一绑定一个 intent version/role；每版恰好两个 plan，各一个 GET，数据库唯一性/RESTRICT 是最终并发约束。审批视图必须展示并核验 core/link，而非仅显示 URL。不存在 link、role 重复、摘要不符、跨 context 或 unsupported protocol 都不可审批/执行。

拟限额（同样待批）：实际 request/core 各 ≤32768 UTF-8 bytes、完整输出 ≤65536 bytes，深度 ≤8/root=0、nodes ≤8192；URL ≤2048 characters 且最终字节上限仍适用；ID 为严格整数1..2147483647、version为1..1024；两个 actor、一个 slot、最多8 observation refs、每 actor 当前及未来相关 assertions 共≤256（第257条整次失败）、Scope≤256。每 context 最多1024 intent versions（包括失效/更正）、2 plan links/版、最多4 health receipts/版、最多16追加决定/版；满额拒绝，不裁剪历史。health interpreter 输入≤16384 bytes/depth4/nodes256、只消费白名单 scalar 字段；输出 receipt≤4096 bytes。所有查询有界，无跨请求真值缓存；新记录不得复制 source body 或秘密。limits 不是允许增加既有更小上限。

## 3. 配对、revision 与转换 P

推荐 **同 context/Target/Endpoint/template/binding/Resource/value、不同显式 actor、同一个当前 active immutable revision**。每 plan 仍独立选择、独立通过该 revision 和适用 exact-plan approval；两份批准不能互代、不能把两个单 GET 合成多 action plan。若只有不同 revisions 分别能覆盖两个 actor，则此 v1 pair 不支持，NEEDS_INPUT；换 revision 后整个 pair 重建，绝不 union。替代“每侧不同 revision”虽理论可分开授权，但权限/时间语境可比性需要新决定，本文不推荐初始纳入。

转换按有限顺序：重新核验 exact intake/subjects/knowledge → 重跑 M12/M14 → 人工确认精确映射和独立 allowed baseline → 核验 health/解释契约/预算 → 冻结 core、创建两 plan 和 link → 完整编码/最后时间检查 → 原子提交。创建和审批各自是显式本地操作，创建成功不代表已批准或执行。协议不提出 scheduler、AI 自动选择或审批聚合。

先执行 baseline，得到 exact canonical run + 版本化完整对象证明，才能放行指定 probe；probe 可先获适用批准但依赖门仍关闭。一次 pair 不“选 latest”，不能替换 baseline run、反转 roles、跨 revision/版本混配，或拿旧 baseline 补新的 intent。新增 `ra-pair-evidence/1` 绑定 link digest、两 plan/action/run IDs、真实网络时间、health receipts、事实/规则/interpreter 版本和不确定原因；append-once，重试同值收敛，异值整次冲突。baseline 网络失败或对象证明不足则不发送 probe；无合法 baseline 时保留 owner+denied 事实，NEEDS_INPUT。

## 4. Session 健康、解释与时间 P

**所有本节数值 PROPOSED / PENDING_APPROVAL：health 最长120秒；baseline 完成至 probe 开始及最终 pair 消费最长30秒；intent 最长300秒。** 120秒给本地人工逐 plan 审批和两个请求有限余量，30秒约束两侧场景漂移；300秒限制长期挂起的尚未配对意图。它们不是 Target 保证：撤销/变化可提前失效，不能续签时间戳来延长。更短60/15秒更保守但审批重做更多；更长300/60秒降低操作成本却增加未知 session 变化窗口。既有5秒网络 timeout 不证明总耗时≤5秒，不能据此免除绝对 deadline。

**可接受的 bearer 健康来源：** 拟由平台实际执行并持久化的单个明确 `health` GET run，绑定 exact plan/action/approval（若适用）、revision、actor、binding、实际使用的 secret version、服务端请求开始/响应完成时间、context/fixture interpreter、source eligibility；确定性解释为“此凭据在此时被这个合成应用识别为预先确认的主体”。必须有完整 JSON object 中独立核对的 identity marker、健康对象 marker 和明确 authenticated=true；预期值来自已审核 fixture 映射，不能来自待检 response 的自称。只允许有限 literal field selector 的固定解释器；200 只是必要 transport 条件之一。重复/歧义字段、错 actor/object、登录页、截断、redirect、MFA、401/403、未知 error envelope 均不合格。对象值是合成 fixture 本来提供的语义，不要求增加 Target API 或在请求里注入自定义 header。

人工 session claim、更新成功、owner 标签、M12 observed_baseline assertion、导入 observation、调用方“healthy=true”、未记录实际 credential version 的旧 TestRun，**都不能单独升级为健康证据**。同 context 合资格平台 health receipt 可以短期复用；必须重查 exact记录与全部依赖，不接受 caller拼造。anonymous 没有 bearer health：显式 auth_type=anonymous、binding/version=null、没有 AuthenticationContext auth material，并有合资格匿名对象/拒绝解释；这是 no-session 模式，不把失效 bearer 降级 anonymous。

**Health 的 bootstrap 与预算：** bearer health 无需先证明自己健康，但只可作为显式 `health` purpose，不能给 access verdict 或绕过许可。操作者选择独立 allowed 的健康 Resource 和已确认 mapping；仍限同 Target 下一个 builder-compatible resource path GET + JSON object，有真实 TestCase/Resource provenance。任意 `/health`、login/MFA endpoint 或通用 fetch 不在范围。每个 health 是自己的 exact 单 GET plan、适用 approval、1次请求、速率/取消/M8 gate；不能在 credential resolve 或 baseline/probe 内隐式调用。最多两个显式 health plans 加两个业务 plans，总清单≤4 requests、concurrency=1；复用既有 receipt 则本轮无需 health 流量。任何追加/刷新 health 都是新清单、重新批准适用计划/预算，不自动重试。health 只证明身份识别，不把健康 Resource 的 allowed 迁移成业务 Resource 的 allowed。

**独立bootstrap结构：** health计划先于business intent，不引用尚不存在的business digest。采用单独的拟议 `ra-health-intent/1` core（context、单actor/binding/version、健康Resource/mapping、单revision、exact GET、interpreter、有限清单/预算引用、created_at/expires_at）；无前置health引用、无baseline/probe事实推断，最长300秒准备窗口且仍受更早许可截止限制。以自身协议名加换行取摘要，policy_context标记该协议和role=health，`ra-health-link/1`只允许一个plan/action；受控TestCase类型沿§6但必须分派此明确协议。实际receipt另绑定health core/link/plan/run与send/complete/verified时间，随后business core才引用它。health与business使用不同link schema，禁止把health run当business baseline或让缺health的business core走bootstrap豁免。清单先明确最多两个health目的和两业务请求额度；业务exact plans在health完成后才生成并取得各自适用审批，前期预算不是这些未知digest的提前批准。

这明确暴露依赖：当前 executor 无 health-purpose/source-version 证明，RA-02 预算引用始终 unverified。W1 依赖代码获批后可实现结构/转换的拒绝路径；**直到适用 health interpreter 和可信有限预算核验实现，bearer 执行门仍关闭**。解释器属于既有 RA-04/W2，不在本次文档实施；若要求提前实现以便 W1 单独可执行，须 Tech Lead/操作者明确批准包内次序调整。只提出有限清单的人工预算决定，不提前实现 RA-06 task ledger；若要审批聚合/恢复调度，先满足 ADR-RA-TASK 的提前检查点。

**对象/访问解释：** baseline 的 allowed 来自独立当前 facts，baseline response 必须证明所选对象、完整且无认证失败；health 不代替它。probe denied+可信对象可读只能产生疑似违反预期，待人工确认；probe allowed+对象可读是允许访问，non_owner/shared 不变成漏洞。denied+明确业务拒绝且健康合格只说明这次请求被正确拒绝。本建议的拒绝解释还要求完整JSON error object中 `error="access_denied"`、精确对象marker，bearer时有匹配预先确认主体的identity marker及authenticated=true；无法区别业务拒绝与未认证拒绝时仍INCONCLUSIVE，健康receipt的新鲜度不能补这个缺口。401/MFA/模糊403不能记安全。owner+denied 可当 probe，由 non_owner+allowed 作 baseline。未知/冲突/缺 baseline 或对象不充分时 NEEDS_INPUT/INCONCLUSIVE；unsupported shapes 明确 UNSUPPORTED。不因两 body hash/长度相等认定权限；不解释自然语言指令。

**时间与精确边界：** 服务可信 UTC clock 记录 created/evaluated/health verified/send/complete/serialized 时间，实际执行侧采样 send（最终 pre-send gate）、complete（完整有界 body 读取后），不能拿 TestRun.created_at/HTTP Date/用户 reported_at 当网络时间。freshness 从 health **send** 起算，不从验证/落库时间开始；若读取期间已到期，不能生成合资格 receipt。跨进程持久时间使用可信服务器 UTC，单进程等待/耗时再用 monotonic；时钟回退、次序不可能或多进程时钟不能保证一致时拒绝，而非增加容差。输入允许合法 offset，统一微秒 UTC；不允许 caller 设置消费时钟。

令 `H = health.send_at + 120s`（再与该证据各依赖截止取 min），`I = intent.created_at + 300s`（再与显式更短 expires_at 取 min），`B = baseline.complete_at + 30s`。每次消费要求 `now < min(I, applicable H, permission/source/fact/approval-explicit-end, B if baseline exists)`。health.send≤complete≤verified≤消费；baseline.send≤complete≤probe.send≤probe.complete≤最终消费；等于任何 deadline 已无资格，没有 grace period。健康两侧都必须覆盖最终 pair 消费（不只覆盖各自 send）。baseline–probe 定义为 `0 ≤ probe.send − baseline.complete < 30s`，且最终编码/提交也必须 `< B`；慢响应不可在时间窗外得到合资格结论。

M12 窗口仍是 `max(asserted_at, valid_from若有) ≤ now < valid_until若有`。下一次相关 verified assertion 开始生效（包括未在旧 supports 内的新冲突）也是截止：取当前及未来相关行的最近 start/end，并在每个边界重新 resolve 全部行。不能只检查旧支持集合的 expiry，不能到期后删除依赖来使结果看似完整。本建议不新增独立approval TTL：现有approved记录的可用性由最新决定及上述intent/依赖截止共同收窄；只有另有明确且已核验的批准截止时才加入min，未提供不能凭空填值。当前资格 deadline 是派生响应字段，不改变原 core；历史审阅不延长执行/重分析资格。

## 5. 等待、变更与失效 P

任何暂停、人工审批等待、rate/claim/permit 等待、DNS/连接准备之后，都要使用 fresh metadata 和 server clock 重验全部依赖；准备请求前必须比较 pin 的 binding/version 并通过 AuthenticationContext 取得**该 exact version**，不能自动升级到最新。现有 resolve_binding 无此契约，不能声称复用就已经解决。

拟执行 gate 在最终可发送位置与同域更新协调：沿既有 catalog→context→Target→identity→binding 的兼容锁序，锁相关行/集合以防 assertion/Scope phantom，核验完整快照和 future transitions；credential update/review/lifecycle 更改不能插入“核验→请求发送”的空隙。不得持数据库事务跨人工/rate 等长等待。连接/permit 准备后若仍会等待，必须再次 gate；实现在首次可能发送请求字节处的短临界区线性化，保留 M8 fencing/cancel/in-doubt 语义，遇到 deadline 阻断。不能以当前 before_network（早于 gateway admission）替代此要求。此处规定验收边界，不改变或批准公网 network redesign。

| 变化/故障 | 精确后果和恢复 |
| --- | --- |
| credential binding/source/active、最新 secret version ID 改变（即使 token 字节相同）、identity auth_type/active/Target 或身份映射改变 | 两侧整个 intent/pair 失效，未发送 plans 不可用；旧批准保留历史但不再足够。人工更新/重新确认 identity，重取 health、重读 subject，创建新 intent version+两 plans/link+各适用批准；旧 baseline 不复用 |
| Resource value/type/Target、Endpoint/template/method、binding selector/review、mapping evidence 改变/撤回 | 新人工 mapping confirmation，新的上下文/intent/plans/适用审批；禁止“URL相同所以继续” |
| context correction/关闭/转移、来源过期/hold/delete/quarantine/revoke、knowledge disable/validation 或 publication 不再合资格 | 依赖 intent 不可用；不能移除 source、换成副本或降级为无规则继续。保存 tombstone/ref，合法恢复后也需新 intent/plans；污染更正须新 rule version 和 W3 qualification |
| assertion 新增/更正/review/时间 start/end 变化，支持集合或两维结果变化（即使仍 allowed） | 重算 facts，旧 intent/plans 不可用；缺项/冲突先解决，再新版本/计划/审批。半开边界不延续旧结果 |
| 许可 expiry/revoke/supersede、Target 当前 revision/profile/origin/mode、相关 Scope 集合或 platform policy 改变 | 即刻停止；即使扩大 Scope 也不恢复旧 snapshot。选单一当前合法 revision、更新 intake permission snapshot，新 intent+两 plans+适用批准；不合并新旧许可 |
| health/intent/B 到期、暂停超时、clock anomaly | 旧 intent 永久不可继续执行；新 health（如需）、新 intent/两个 plans/适用批准。没有后台续期，不只替换 expired receipt |
| 单纯 approval revoked（其他依赖全同且未到期） | 对应 plan 不可执行；人工可对同 exact digest 追加新的 approved 决定，另一侧仍需自身当前批准。无需修改 core；若任一依赖变了，必须走新 intent/plans，不能重新批准旧 digest 来绕过 |
| 取消、claim lost、in-doubt，或网络后结果/审计/编码失败 | 遵守 M8。已发送无法事务回滚；保留真实 progress/canonical run，不隐藏计数或自动重发。没有合资格 pair success；恢复只能读原 canonical 结果并重验资格，无法确定发送状态须人工处置 |
| 创建/审核/证据追加时 audit、schema/输出编码失败，或编码间跨 deadline | 新域 savepoint/外层事务整体回滚，不返回预编码的 stale success；每次最终消费保留 gap/match/fact/deadline 一致。网络已发生时只回滚本次新证据写入，不能谎称请求未发生 |

历史 intent/link/run/审批/evidence 可按其来源访问边界只读显示“当时记录、当前不可复用”；过期 TTL 不抹掉原结论/人工 review。重新分析属于新的消费，要重验并追加新版本化结果，不能重写旧 evidence 或选另一个 baseline。服务序列化和 API 完整编码后、提交前均检查资格；发送已编码响应后时钟当然继续走，消费者仍须遵守带回的 deadline，不能把200当未来执行许可。

## 6. 兼容、增量存储与回退 P

推荐新增 immutable intent versions、mapping confirmations、health receipts、plan links、pair evidence 和追加决定记录；exact ID/digest、组合 context FK、唯一角色/plan/pair、RESTRICT、append-once 冲突检查。source软引用沿 RA-02 lifecycle，不用 FK 阻止 payload/tombstone 清理；不复制数据补不可用来源。不修改旧 migrations，不 backfill/relabel 旧 TestCase/Run/Finding/M13/report。

**需要明确批准的 TestCase 选择：** 当前 executor/TestRun 要真实 TestCase，四列唯一键又不能表示每个 intent version。推荐保留键，新增受控 `test_type=ra_intent_get_v1` 的真实 provenance TestCase（endpoint/actor/resource/type 幂等复用）；`ownership_relation=unspecified`、`expected_statuses=[]` 只作中性存储，访问真值全部在 intent，不从这些字段推断。多版本和 role 由伴随 link 区分，case.status 继续是 legacy 非权威摘要，不能作为 pair 状态。Resource 仍必须是合资格现有记录，不能伪造其 NOT NULL owner 来创建缺事实对象。没有可用 Resource/TestCase provenance 就 NEEDS_INPUT。

这**不是已经兼容**：旧直接执行路由并不因陌生 test_type 自动拒绝，单放 JSON digest 也不会启用重验。未来必须在 direct TestCase execute、普通 plan creation/approval、exact execute、analysis/report 所有入口识别此类型，只有有完整 typed link 的新 dispatcher 可处理；剥掉 policy_context/link 必须失败，不能落入 legacy。Tech Lead 须批准中性值和完整 reader 清单。替代是新 action/run provenance 域摆脱 TestCase 约束，但会改 roadmap 的“真实 TestCase”联结及 executor/reader 契约，需明确架构/产品决定，不能在实现时偷换。

| 表面 | 当前→建议兼容行为 |
| --- | --- |
| legacy generator/TestCase 唯一性与 direct execute | 旧 owner/cross-owner 生成、幂等、single_process 行为不变；multi_process 仍 exact-plan-only。新类型在旧 direct 路径明确 unsupported，不能根据 ownership_relation 猜 intent；未知类型不归类为已支持 |
| plan readers/approval/execution | legacy 无新类型/标记/link 的 plan 保留 v1 digest/历史读取/适用策略。新类型必须三者一致并验证 protocol/core/link；未知 protocol 只显示受限不可执行元数据。审批页显示 actor、mapping、业务预期、health时间、预算、exact digest；不展示 secrets |
| pairing/TestRun | 保留 exact canonical plan→run 唯一性与 TestCase/Resource FK。新伴随记录增加 role/intent/version/secret-version/真实send-complete时间，旧 run 不 backfill 假时间/health。一个原始 run 不被挪到另一个 pair |
| analyzer/Finding/report | legacy 显式同对象、不同 actor 的 owner_baseline/cross-owner path 原样。新 pair 只给版本化 assertion-aware evidence，不喂旧 analyzer、AI advisory 或旧 report，绝不自动 confirm Finding。新通用报告仍 RA-07；真适用 legacy 语义时可另走原有已确认 legacy 流程，不“转换”新记录 |
| M13 evidence/fingerprints/review | 旧原始字符串、None语义、六层原子追加、指纹冲突、retention常量、human review、report字节保留。新证据使用新域/版本，不重算或冒用M13 v1，不更改旧 source body。source unavailable 明确不可重分析，不能替换为空字符串 |
| source lifecycle | 初始只 synthetic。新敏感执行源在写入前仍须 ADR-DATA 适用 lifecycle 批准/实现；目前 TestRun body 路径不是这种保证。本文没有授予敏感执行或清洗旧 body 的权限 |

未来 migration 验收：fresh upgrade、包含旧计划/审批/paired和unpaired run/Finding/confirmed review/M12/M13/report 的 populated upgrade，逐字段/摘要证明旧数据未变；legacy读取/再分析同结果；新增exact FK/唯一性/并发原子性；空新域安全 downgrade，含新 metadata、marker TestCase 或新关联时持锁后拒绝 destructive downgrade。

**安全回退不是直接启动旧二进制：** 停新入口和全部执行入口、排空/盘点M8（in-doubt不重放），保留新表及历史；只能退到认识新类型并拒绝执行的兼容版本后恢复 legacy execution。若退到本基点旧应用，必须保持执行管理面不可调用，仅使用经过核验的 legacy只读路径，因为旧代码可能发送新marker case/plan；不能依赖一个旧应用不认识的 feature flag。删除新行或拆link来让旧版本继续执行不属于回退方案。测试上述拒绝后，另行人工授权处置数据才可能 downgrade；不得为回退改写证据。

## 7. 独立合成例与验收映射 P

所有数值/IDs 是本文新写的虚构 fixture，未创建记录、未读取 held-out。示例 origin `http://127.0.0.1:18743`，模板 `/records/{record_id}`，Resource 703/value `7042`；假定已独立人工确认该映射。下面是**两actor输入片段，不是完整可提交core或真实审批**，只展示角色与独立两维；余下必填字段由§2规定，不能靠省略来通过 gate。

```json
{"actors":[{"role":"baseline","identity_id":812,"auth_type":"bearer","relationship":"non_owner","expected_access":"allowed","credential_binding_id":912,"credential_version_id":1012},{"role":"probe","identity_id":811,"auth_type":"bearer","relationship":"owner","expected_access":"denied","credential_binding_id":911,"credential_version_id":1011}],"resource_id":703,"protocol":"ra-intent/1"}
```

独立 fixture interpreter `synthetic-record-object/1` 只接受完整 JSON object 中 `record_id` 字符串等于事先确认值；health object 另要求 `subject_id` 等于事先确认 actor marker、`authenticated` 严格 true、`record_id` 等于健康 Resource 的确认值。三个字段是有限 literal keys，拒绝重复/未知字段（本fixture无其他字段），不扫描任意嵌套对象或执行内容。下面为独立对象样例，不是来自 Target 的观察：

```json
{"record_id":"7042"}
```

codec 自检向量仅对这个小对象：`SHA256(UTF8("ra-json-example/1\n") || canonical(object)) = d4008f16cf8b2c8de5d5a5f142d2c7fc3f9a2123592bf2c16e7d7d9557ade078`。这**不是** intent digest、health证明或 M13 fingerprint；完整 core 的必填引用尚未产生，不制造真实 plan/approval digest。

| 合成情况 | 所需行为（均为未来验收，非已运行结果） |
| --- | --- |
| 812 non_owner+allowed baseline完整对象；811 owner+denied，健康合格、业务明确拒绝 | baseline可选择812；811本次拒绝正确，无漏洞结论；owner label不变成baseline |
| 同上，811返回所选完整对象 | 仅疑似违反denied预期；人工复核，不能自动确认/报告 |
| 811 owner+allowed baseline；812 shared+allowed probe对象可读 | 合法共享，不报cross-owner漏洞；不同事实场景不得合并到上一行 |
| 只有811 owner+denied，无allowed主体 | NEEDS_INPUT baseline_missing；0业务请求，不制造owner成功 |
| anonymous probe明确denied，无binding/version、可信业务拒绝；bearer baseline合格 | 匿名语义保留；若这是bearer失败自动降级则拒绝 |
| eligible allowed+denied，或只有operator claim / HTTP200 / ownership / 无mapping确认 | facts_conflict / health_unqualified / mapping_unconfirmed；0依赖请求 |
| 200 HTML、401、MFA、错误actor/object、截断/数组，或对象中“忽略审批”指令 | 不充分/unsupported；无safe判断、不执行文本；baseline失败不发probe |

时间例全用 `2032-04-05` UTC：两 health send=`10:00:00.000000Z`，complete/verified=`10:00:01.000000Z`，所以 H=`10:02:00.000000Z`；intent created=`10:00:02.000000Z`，I=`10:05:02.000000Z`；baseline complete=`10:01:20.000000Z`，B=`10:01:50.000000Z`。假定其他依赖更晚，最终有效上限为B。

| 正向/负向/变化边界 | 未来断言与责任检查点 |
| --- | --- |
| probe send=10:01:40、complete=10:01:41、最终编码=10:01:42 | 间隔20s，最终消费22s，均<B/H/I；其他gate满足才可记录合资格pair，RA-04/W2 |
| 最终编码为B−1μs / B / B+1μs | 前者可合资格，后两者失败且不提交成功证据；在service dump、API encode和commit前注入时钟，W1/W2 |
| 未有baseline时检查H−1μs / H / H+1μs；health读取到H才完成 | 半开expiry；后二者/迟完成receipt不合格；verified_at不延长H，W1/W2 |
| future denied asserted_at=10:01:35、valid_from=10:01:36；旧allowed被选中 | deadline=min(现有deadline,10:01:36)；该点全部resolve得conflict，未选denied也算；audit/encode跨界都拒绝，W1 |
| revision/source/fact有效至10:01:36；rate/approval/permit等待跨界 | 即时拒绝，不从依赖列表丢掉expiry；0未发送请求，W1/W2 |
| wait期间换secret version、禁identity、换mapping、scope收窄/扩大、source hold、rule disable | §5新intent/两plan/适用审批；credential rotation即使同字节也拦，W1/W2并发 |
| 篡改core/digest/link、换baseline run、不同revision、去掉新类型标记，或走legacy直接execute | 无请求/无新evidence；原记录/approval/review不变，W1/W3兼容 |
| 无预算证明、health暗中重试、audit失败、容量+1、无法确认已发送 | fail closed；网络前0请求，网络后保留M8真实计数/in-doubt，无自动重发，W1/W3 |
| `2032-04-05T03:01:40-07:00` 与 `10:01:40Z` | 同canonical UTC值；naive/future health/逆序/clock回退拒绝，W1/W2 |

可复用既有测试（本包只静态读取、**未执行**）：[M14 acceptance](../backend/tests/integration/test_m14_matrix_acceptance.py) `test_independent_truth_nested_positions_and_real_pipeline` / `test_late_257_assertions_return_no_partial_matrix`；[plan](../backend/tests/services/test_plan_execution.py) `test_required_exact_approval_executes_and_missing_or_revoked_blocks`、多action/完整性拒绝；[wait integration](../backend/tests/services/test_plan_execution_integration.py) approval/revision/Scope变化和fencing；[credential](../backend/tests/credentials/test_bearer_credentials.py) highest-version/exact-binding；[subject concurrency](../backend/tests/services/test_research_subject_concurrency.py) `test_credential_update_waits_for_w3_metadata_transaction`；[W2边界](../backend/tests/services/test_research_knowledge_boundaries.py)、[编码边界](../backend/tests/api/test_research_knowledge.py)；[M13冲突](../backend/tests/api/test_finding_evidence_fingerprints.py) `test_changed_source_body_conflicts_even_when_rule_no_longer_succeeds`。它们证明现有局部行为，不证明新INTENT协议；上表缺口需未来独立 fixtures、exact-boundary/变更等待/网络计数测试和migration/rollback集成测试。RA-04/W3仍须按roadmap完成本地端到端验收；本包没有替代它。

## 8. 本包实际验证与限制

实际在 Ubuntu 24.04.2 LTS / WSL2、backend `.venv` Python 3.12.3 完成以下检查。只读取本地源码/文档和测试断言；未import应用、访问数据库、解析凭据、调用provider/Target或抓取链接。

| 实际命令/检查 | 结果 |
| --- | --- |
| `git status --short`、`git branch --show-current`、`git rev-parse HEAD`、`git rev-parse main`（创建分支前） | clean main，两SHA均为本页基点；随后新建文档分支。`git log -4 --oneline`及`git merge-base --is-ancestor 91db91f59761b5683309062d9b7ac51536bfe75f HEAD`核对本地W3集成历史；未fetch |
| 仓库根目录：`backend/.venv/bin/python /tmp/check_ra04_intent_docs.py`（临时脚本，不入repo） | PASS：91处本地链接、15处本地anchor、21个代码symbol及创建/审批service实际生产caller集合；两个JSON片段结构/独立两维、上列domain-separated摘要、UTC换算/半开边界/未来fact起点、三文档pending/数值一致性。80处外部历史链接仅识别，未fetch或验证远端可用性 |
| `cd backend` 后：`env -i PATH=/home/runyiy/projects/ai-api-security-platform/backend/.venv/bin:/usr/bin:/bin LANG=C.UTF-8 python -m evaluation.ra01 verify` | **VERIFIED**；freeze digest=`692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a`；仍为PROPOSED_PENDING_REVIEW_AND_OPERATOR_APPROVAL。仅evaluator在自身完整性边界读冻结数据，未查看held-out样本/标签来构造提案 |
| `git diff --check`；文件范围/最终staged diff检查 | PASS；仅本提案、ADR decisions、roadmap三文档；冻结evaluation、应用/测试/schema/migration/config/依赖无改动 |

未运行应用测试、数据库/migration、网络或端到端验证；用户给出的2790 passed不是本包运行结果。静态检查不证明新方案已实现或可安全执行；future acceptance/migration/rollback、独立Review Project结论及I1–I7决定仍待完成。此次文档验证不改变冻结阈值、ADR规范或现有测试。

## 9. 待决定表

每项均 **PROPOSED / PENDING_APPROVAL**。Review Project先审精确文档commit；Tech Lead/操作者记录逐项采纳/替代/条件及真实身份、aware时间和精确版本引用后才满足对应检查点。本表不填造签名、时间或approval IDs。

| 决定项 | 推荐 | 替代/代价 | 责任决定者与实施检查点 |
| --- | --- | --- | --- |
| I1 immutable protocol/digest/link | §2 `ra-intent/1`、domain-separated digest、单向plan-link、exact provenance/限额 | 只存policy_context：缺强制消费与版本约束；整个新plan格式：迁移更大 | Tech Lead；W1转换/持久化代码前 |
| I2 session证据/解释 | §4平台health run+独立identity/object契约；claim不升级；匿名无session | 仅人工声明：不能满足资格；若fixture不支持健康对象则bearer暂停 | Tech Lead批准协议，操作者确认fixture身份/业务事实及更新流程；W1接口及W2解释依赖前 |
| I3 时间 | health120s、pair30s至最终消费、intent300s、半开、可信时钟 | 60/15更易过期；300/60漂移风险更高；任一变化仍提前失效 | Tech Lead+操作者；时间常量/等待实现前 |
| I4 pair/revision/invalidations | 独立allowed baseline、同revision、变化重建整对、精确版本凭据和最终gate | 不同revision须额外可比性决定；只重建一侧易混合语境 | Tech Lead；操作者确认baseline/mapping；W1桥接前 |
| I5 TestCase/旧读者/回退 | §6新受控type+中性字段+伴随版本；所有入口分派，回退保留数据且拒绝新执行 | 独立action/run provenance更纯粹但改变真实TestCase联结约束；重标legacy不可接受 | Tech Lead批准模型及reader/rollback清单；W1 migration/dispatcher前 |
| I6 health bootstrap/预算/包顺序 | health各自1计划/审批/预算，总清单≤4；W2解释器/可信预算前保持执行关闭 | 为使W1独立执行而提前做解释器需显式次序决定；通用health renderer超范围 | Tech Lead+操作者；任何health/业务请求实现依赖前；若引入聚合则先ADR-TASK |
| I7 source/evidence兼容 | synthetic-only新域，source不可用不伪空，保留M13/旧body；通用报告RA-07 | 复用旧raw-body敏感路径不满足lifecycle，须保持关闭 | Tech Lead+操作者；新source persistence前及任何敏感执行前另满足DATA检查点 |
