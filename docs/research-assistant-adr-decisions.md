# Research Assistant ADR 决策材料与 RA-02 数据契约

> **RA-05/W2 current proposal (2026-09-11):** [W2 budget/send-observation contract v0.1.0](research-ai-budget-contract.md) is **DOCUMENTATION_ONLY / PROPOSED / PENDING_APPROVAL**, from verified main `658464c5ae45f058cbb3c6a0bdd8ad3f371ad114`. The user handoff records reviewed W1 `0e3b94a909ee01eba41c79a264f056665c0222ce`, PR #150 integration and all required local/PR/exact-main gates passing 3798 tests; Git trees match, gates were not rerun for prose. W1 P1–P6/E1–E6 design adoption stands. W2 B1–B8, including PostgreSQL/transaction/observer/recovery choices, remain proposed and need independent review plus User adoption before implementation. Operational approvals and real-provider acceptance remain pending; no W2 implementation, W3/cache or RA-06 authorization. Earlier records below are historical.

> **RA-05/W1 historical design adoption and implementation:** The user handoff records “采用”, observed 2026-09-11, for reviewed provider contract v0.1.0 / `29376dda7e0ffa99fe3f1947bb2c3e83df81c228`. [Adoption record](research-ai-provider-contract.md#w1-design-adoption-and-implementation-record): P1–P6 and E1–E6 design constraints apply to fake-only W1. [Implementation evidence](research-ai-provider-implementation.md) is IMPLEMENTED / PENDING_INDEPENDENT_REVIEW. Actual account/key/data/retention/budget/egress approvals and real-provider acceptance remain pending; Target restrictions are unchanged. W2/W3 have not started. Earlier pending text is preserved as history.

> **RA-05/W1 后续文档准备（2026-09-11）：** 从核验的clean main/remote main `d058cb82215c61b4c7811784abb24dc0ae069f80` 开始既有W1的 [proposal/provider契约 v0.1.0](research-ai-provider-contract.md)，补齐§7/§8，**DOCUMENTATION_ONLY / PENDING_INDEPENDENT_REVIEW**；P1–P6/E1–E6全部 **PROPOSED / PENDING_APPROVAL**。推荐不批准模型、数据、账号、支出或provider POST例外，adapter未实施，W1/RA-05未完成，W2未开始。DATA D1–D4、K1–K4、INTENT I1–I7既有采纳约束不变；下方旧C/P、pending和包内停止点保留为各基点历史。

> **RA-04/W3 后续集成证据：** 用户交接确认 reviewed `167853e2a7386088b3d915e12e2c2e383fa3effe` 经PR #147集成，独立本地、PR CI `34569913522`、main CI `34570935882` 各3266 passed。本包Git核验上述main与reviewed feature tree相同，未重跑suite或重新审计CI。仅建立有界合成本地演示验收，不授予production/public/provider readiness或其余待决ADR批准。

> **后续状态：** 下方 v0.1.0 的 proposed / pending 登记保留为 W3 原始历史。DATA D1–D4 的后续采纳证据见本节；其余五项 ADR 不受影响。

> **RA-04/W1 后续 INTENT 提案（documentation only）：** [INTENT 协议 v0.1.0](research-intent-contract.md)基于本地核验的 `bedc55395d2e5abf3479026fd201430b537baff2`，补齐 §6 的 session-health 来源/时间、pair/revision、凭据变化、immutable linkage 与 legacy 回退建议。I1–I7 全部 **PROPOSED / PENDING_APPROVAL**，文档 **PENDING_INDEPENDENT_REVIEW**；操作者选择先准备建议，不等于采纳建议或批准 bridge 实施。下方 v0.1.0/RA-01/W3 的原始登记及 DATA 后续采纳历史保留，不重解释其当时状态。

## INTENT 后续采纳记录（RA-04/W1）

- **精确材料：** [INTENT v0.1.0](research-intent-contract.md)，reviewed commit `6723cb5bfa62a10453f18f8158c25000a1997711`，I1–I7。
- **决定来源：** 本次用户W1 implementation handoff明确说明操作者采纳I1–I7，Review Project Tech Lead采用这些推荐作为实现约束；不附造身份、签名或批准时间。原pending/提案段落作为历史保留。
- **适用边界：** 仅批准既有W1有序实现；未授权Target/health请求、operator credential访问、私有数据或费用。其他ADR不受影响；I6规定的W2解释/可信健康证明依赖未实现时，新purpose执行继续关闭，不能以fixture、NOT_RUN或operator claim替代。
- **实现状态：** [bounded bridge实施记录](research-intent-bridge.md)，IMPLEMENTED / PENDING_INDEPENDENT_REVIEW；W2/W3和RA-04 COMPLETE不在本次声明范围。

## INTENT W2 continuation record

The current W2 handoff records reviewed W1 integration in PR #144 and authorizes the existing next package from main `c4d6750eb42af5556036419980a0eb312f892d78`. Adopted I1–I7 continue to constrain the [W2 verifier and dedicated dispatcher](research-response-verification.md). Historical W1-only/pending text remains historical; no approval signature or time is invented. The implementation replaces W1's unavailable production interpreter only with exact platform provenance, independent expectations and bounded current qualification. Legacy execution/analysis remain closed to new semantics. Actual Target requests, operator credentials/private data, provider costs, public execution and all other pending ADRs receive no new authorization. Independent W2 review and W3 remain outstanding.

## DATA 后续决定记录（RA-02/W1）

- **被采纳的确切材料：** 本文 **v0.1.0**，reviewed commit [`dcdb50fd36c098173c2580389577bb59558c0982`](https://github.com/runyiy/ai-api-security-platform/blob/dcdb50fd36c098173c2580389577bb59558c0982/docs/research-assistant-adr-decisions.md)，ADR-RA-DATA 的 **D1–D4 推荐方案**。
- **决定来源：** 本次用户交接明确记录：操作者在 Review Project 的决定请求下回复 **“采纳”**；Tech Lead 在同一交接中采用这些推荐作为 RA-02/W1 的设计约束。Codex 仅转录交接事实，不补造批准人签名、账号或未提供的消息链接。
- **观察时间：** `2026-09-10T00:56:57Z`，是确认被观察到的时间，**不是声称精确的消息发送时间**。
- **效力：** DATA D1–D4 从待决定转为本包适用的已采纳设计约束；准入前仍须实现适用控制。W1 只接收受限 synthetic context 元数据，拒绝私有/敏感资料；observation parser/payload lifecycle 留在 RA-02/W2。新执行源数据、旧 TestRun/M13 的边界依 D4 保持。
- **没有扩大的批准：** 不批准 INTENT / PROPOSAL / EGRESS / TASK / PUBLIC，不批准真实私有数据使用、Target 执行、provider 调用或支出，不签署 W1 reviewer-PASS 或 RA-02 COMPLETE。
- **实现与证据：** [RA-02/W1 intake 使用说明](research-intake-context.md)。原 W3 的 P/C 描述仍指其 exact base，不能倒读为本包代码事实；原“无批准”表格是采纳之前的历史。

**文档 v0.1.0 · RA-01/W3 · IMPLEMENTED / PENDING_INDEPENDENT_REVIEW**
**全部新架构建议：PROPOSED / NOT_APPROVED。RA-02 前置审批尚未满足。**

精确来源基点：`ac9a5ce3142232e86576b5d789d93b95508e259d`，对应已独立审阅并 push 的 W2 HEAD，尚未合入 main；该 gate 依据用户交接及本次远端 SHA 核验。W1/W2 原文、语料、标签、hash 和阈值保持不变。本文交付可审阅的选择、输入说明与验收要求，不签署 Tech Lead/操作者决定，不宣告 W3 或 RA-01 COMPLETE，不启动 RA-02。

[规范架构](architecture-decisions.md)与[安全模型](security-model.md)保持优先；[roadmap 第 6–8 节](research-assistant-roadmap.md)、[W1 第 5–7 节](research-assistant-product-contract.md)及 [W2 第 3–6 节](research-assistant-evaluation.md)是设计依赖。下文 **C** 是本基点已读代码/既有测试事实；**P** 是待批准、待实现的要求。示例是本包新写的文档样例，没有引用 held-out 案例、响应或答案。

现有 FastAPI/PostgreSQL、单 trusted operator、local-first 边界保持。Default Deny、Target 非授权、一次 execution 一个 immutable revision、不 union grants、Scope/safety 只收窄、mandatory allowlist/exact origin/safe path、即时执行前重验、GET-only/no redirects/有界执行及适用精确审批均不被数据许可放宽；认证材料只经 AuthenticationContext，AI 无 executor、shell、任意 fetch、凭据、审批、policy-write 或 Finding-confirmation authority。导入/响应/model output 始终为不可信数据，公网 runtime 继续 blocked。

## 1. 决策与批准登记

六个标识沿用 roadmap，不是新增工作包或已批准 ADR 编号。审批者须审阅本文精确版本和 commit，记录接受/拒绝/有条件接受、条件、理由、身份与 aware 时间；代码 review PASS、roadmap 持续授权、数据使用许可、Target 执行许可、provider 支出批准相互不能替代。

| 现有标识 | 本包建议与依赖 | 批准责任及最晚检查点 | 实际批准证据 |
| --- | --- | --- | --- |
| ADR-RA-DATA | 第 2–5 节的输入、隔离、资格、lifecycle 与兼容方案 | Tech Lead 批准字段/存储/迁移/兼容；操作者确认数据资格、保留期限及运维可行性。RA-02/W1 使用输入设计前确认适用部分；RA-02/W2 持久化前批准并实现全部适用控制 | **无；PENDING** |
| ADR-RA-INTENT | 第 6 节及后续 [INTENT v0.1.0 / I1–I7](research-intent-contract.md#9-待决定表)：精确协议、health120s/pair30s/intent300s、失效与兼容建议 | Tech Lead 批准协议/模型/兼容；操作者决定时间和操作可行性、确认业务事实。RA-04/W1 依赖代码前，health/verifier及预算依赖按I6检查 | **I1–I7 ADOPTED（本次W1用户交接，见后续采纳记录）；实际请求/凭据/数据/费用未授权** |
| ADR-RA-PROPOSAL | 第 7 节及后续 [W1契约§2/P1–P6](research-ai-provider-contract.md#2-proposal-protocol-p)：独立、无 authority 的 typed suggestion | Tech Lead 批准协议/消费边界；操作者确认解释与拒绝方式。RA-05 proposal 集成前 | **DESIGN ADOPTED for W1 fake-only（见页首记录）；operational approvals PENDING** |
| ADR-RA-EGRESS | 第 8 节及后续 [W1契约§3–7/E1–E6](research-ai-provider-contract.md#3-providermodel-比较与推荐-p)：默认关闭的独立 provider transport、资格与核算 | Tech Lead 批准边界/用量解释；操作者独立批准模型、账号、数据及费用。RA-05 provider 代码前；每次真实运行前再次核验许可 | **DESIGN ADOPTED for W1 fake-only（见页首记录）；operational approvals PENDING** |
| ADR-RA-TASK | 第 9 节及 [W2预算/观察提案](research-ai-budget-contract.md)：预算/观察与 M8 精确计划协调分工；PostgreSQL仍为推荐 | Tech Lead 批准状态/事务/恢复；操作者批准硬预算与审批操作。RA-06 实施前，若更早引入审批聚合则更早 | **无；PENDING** |
| ADR-RA-PUBLIC | 第 10 节：readiness、自有演练、第三方许可分开 | Tech Lead 批准控制和 go/no-go；操作者取得每项实际测试许可。RA-08 控制修改前；RA-08/09 各执行门槛单独审查 | **无；PENDING** |

RA-02 准备度是 **设计材料可供决定，依赖实现仍受阻**。D1–D4 是 ADR-RA-DATA 内的审阅项，不是新 ADR。建议逐项签署；批准记录为空时不得把默认推荐当成已批准选项。

| 审阅项 | 推荐方案 P | 有意义的替代方案与代价 | 本次未决内容 |
| --- | --- | --- | --- |
| D1 输入与资格 | `ra-observation/1`，仅 GET 元数据和极少响应事实；默认 synthetic，额外批准后才允许 reviewed-minimized-private | 接受完整 HAR 再清洗：原文进入进程/错误链风险更大，超出 RA-02 初始约定；只收状态码：更小但缺对象/身份上下文 | 字段、数值上限、私有字段资格须批准；不靠“已脱敏”自报通过 |
| D2 项目边界 | 新的本地 research context 与显式 Target 关联；新 intake/observation/映射/日志全部 project-scoped | 直接把 Target ID 当 project：简单但无法表达项目许可/隔离；引入租户/RBAC 平台：超出单操作者产品 | 推荐一个 Target 同时只归一个活跃 research context；跨 context 转移须停用旧映射并审核，不能复制私有观察 |
| D3 生命周期 | 新 observation payload 与最小 provenance/tombstone 分开；默认 30 天、显式提前删除、有界 hold、独立备份控制，详见第 4 节 | session-only 不持久化：可降低风险但不满足可恢复观察；长期留存：增加泄漏与删除负担；应用层逐项目加密：可加强密钥撤销，但需新的密钥/恢复设计 | 30/30/90/7 天建议及加密存储/逻辑删除余留风险须由操作者接受；需要更强擦除保证的数据先拒收 |
| D4 兼容边界 | RA-02 只新增 observation/intake 域，不搬迁/清洗旧 TestRun 或 M13；未来 execution-source lifecycle 另有版本门槛 | 把导入转成 TestRun 或就地清空旧 body：破坏 provenance/读取/指纹，不能采纳；旧数据显式迁移：需额外规范决定及完整兼容证据 | 本包不给旧 body 删除许可；新执行源数据的具体 schema/不可用语义仍须在 RA-04 敏感执行前批准实现 |

## 2. ADR-RA-DATA：输入和信任边界

具体问题：如何让有资格的本地捕获元数据可恢复、可追溯地供研究使用，同时阻止原文泄漏、跨项目混用和伪造执行/访问真值，并让删除与历史证据兼容？

### 2.1 当前实现与推荐的数据分域

| 数据域 | C：本基点事实 | P：去向及禁止转换 |
| --- | --- | --- |
| 导入观察 | 没有 RA observation/project intake 模型。现有 `OpenAPIImportRequest` 是 source URL 输入，`OpenAPIScanner.scan()` 会走受限网络 fetch，`OpenAPIImportRecord` 记录该链的 hash/provenance [C01] | 新 observation 为 **operator_import_unverified**，只读本地预处理文件；不调用 scanner、Executor 或 credential provider，不创建 TestRun/ExecutionPlan/Target/Endpoint/verified assertion |
| 将来执行 source data | exact executor 最终通过 `decode_response_body()` 把至多 64,000 bytes 解码为 TestRun 字符串；没有截断/删除可用性状态 [C02] | RA-04 前必须另定原始接收、最小化、证据提取、可用性及删除顺序，不能直接继承旧持久化作为新敏感数据策略 |
| 既有 TestRun 源数据 | `response_body` 是 nullable Text；`TestRunRead` 直接暴露它，计划 FK 唯一；report/AI 仍消费 source body [C02][C03][C07][C08] | 不迁移、不清空、不冒充已治理。不得把被删除源内容编码成旧 `None`/空串而声称仍可重分析 |
| M13 evidence/history | exact pair、structured evidence/excerpt/fingerprint/similarity/retention 通过单 savepoint 追加；FK/唯一性及冲突检查限制重写 [C04][C05] | 保留历史和五个 v1 常量；observation 不能作为 M13 baseline/probe 的替身。新来源若需被消费，必须先有明确版本分派和批准 |

**四种资格分开：** 有权持有原始捕获，不等于允许把它导入；允许本项目最小化持久化，不等于允许共享检索；允许共享的通用材料，不等于允许发给 provider；以上都不是执行许可。私有项目证据、access truth、秘密和保密报告不进入共享知识或模型输入。

### 2.2 版本化 observation 字段白名单 P

这是文档设计，**没有可执行 schema/importer**。`ra-observation/1` 是从已在平台外完成最小化的 HAR 提取而来的专用 JSON，不接受 HAR `log/entries/request/response.content.text` 的原始结构，也不实现 HAR 转换器。操作者先提供数据资格清单；未确认资格时不要把原始文件送入平台。

全部字段必填，只有标注 nullable 的值可为 null；strict integer 排除 bool/float/数字字符串，其他字段也不强制转换；未知字段在任何层级都拒绝。字符串不得自动 trim、Unicode normalize、截断或转类型。opaque label 用 ASCII `[a-z][a-z0-9_-]{0,63}`，且必须属于本项目预先批准的 label registry，不能用邮箱、用户名、真实资源 ID 或任意文本充当 alias。导入字段与后台生成的 provenance 字段严格分开。

| 层级/字段 | 类型及精确 P 限制 | 资格、来源与含义 |
| --- | --- | --- |
| 根 `format` / `version` | 字符串，分别严格等于 `ra-observation` / `1` | 不根据内容猜版本；与 W2 `ra01-evaluation-v1` 无互换关系 |
| 根 `project_ref` | opaque label，1–64 UTF-8 bytes | 必须等于操作者在可信 intake 上下文选中的项目；文件自报值不授予访问权 |
| 根 `batch_ref` | opaque label，1–64 bytes | 项目内幂等键；同键不同最小化内容为冲突，不覆盖旧批次 |
| 根 `preparation_ref` | opaque label，1–64 bytes | 指向本地已批准的数据资格/最小化记录：来源合法性、字段和值域、转换工具版本、人工审核、允许的 Target/alias、保留规则。不是 URL，不跟随文件路径或外部引用 |
| 根 `prepared_at` | aware RFC3339 字符串，20–32 bytes；最多 6 位小数秒 | 保留原表示并按 UTC 比较；不得晚于服务选定的 `intake_time`，不接受 naive、未知 offset `-00:00`、闰秒或自动时钟纠正 |
| 根 `entries` | 有序 array，1–128 entries | 空集拒绝；成功保留原顺序，不排序、丢行或选择“最佳”观察 |
| entry `entry_ref` | opaque label，1–64 bytes；批次内唯一 | 稳定引用，不是 TestRun/Endpoint/Resource 主键 |
| entry `source_entry_index` | strict integer，0–999999；批次内唯一 | 在操作者原始 capture 中的零基序号，由 preparation 记录解释；不证明源内容真实 |
| entry `observed_at` | 同上 aware RFC3339 | `observed_at <= prepared_at <= intake_time`；历史捕获可导入，但不表示当前会话有效 |
| entry `method` | literal `GET` | 其他方法整批 `observation_method_unsupported`；这只是 v1 导入选择，不更改 M14 可预览的方法或授权规则 |
| entry `origin` | ASCII 字符串，1–256 bytes | `http`/`https` + 小写 ASCII DNS/IPv4 或带括号 IPv6 + 显式十进制 port 1–65535；无 userinfo、路径、query、fragment、反斜线、空白或百分号编码。与已审查 Target 的 `(scheme,host,effective port)` 匹配，纯本地解析，不解析 DNS。初始资格限获准 `private_local` context |
| entry `path_template` | ASCII 字符串，1–512 bytes | `/` 起始；literal 段只用字母、数字、`_`、`-`，动态段只能是完整 `{name}`，name 为 `[A-Za-z_][A-Za-z0-9_]{0,63}`；最多 8 个互不重名占位符。拒绝 `//`、尾随 `/`（根 `/` 除外）、dot 段、`%`、`\\`、query/fragment。须精确匹配本项目已审查的无敏感值模板，不由原 URL 自动推断映射 |
| entry `query_names` | array，0–16 个互异 ASCII 名称，每个 1–64 bytes、同 name grammar | 仅批准的参数名，无值，无任意 headers/cookies/body。零 query 不需要补虚构参数 |
| entry `actor_ref` | opaque label 或 null | 仅选定 actor alias；null 表示未知，不当作 anonymous。具体 TestIdentity/credential 选择属于 RA-02/W3 人工上下文 |
| entry `resource_labels` | array，1–8 个互异 opaque labels | 指向本项目合资格资源别名。可保留多 slot 的 coverage，但不证明这些 Resource 属于 slot/父资源 |
| entry `response` | 严格 object，仅以下五个字段 | 不保存原始 body、摘要自由文本、header、跳转 URL、HTML 或脚本 |
| response `status_code` | strict integer 100–599 或 null | 捕获声称的观察；200/403/404 本身不能推出 allowed/denied |
| response `media_kind` | enum `json_object/json_array/html/text/other/unknown` | 分类元数据；不是 MIME header 原文，不执行/解析 embedded 内容 |
| response `capture_state` | enum `complete/truncated/missing` | complete 仅为捕获者声明，不证明对象/权限完整性；missing 必须 status=null、media=unknown、object_labels=[] |
| response `object_labels` | array，0–8 个互异 opaque labels，必须是本 entry resource_labels 子集 | 只是来源声明“看见了对应对象”；只有 complete + json_object + 非 null status 才允许非空。不是可信 object proof，更不是 M13 evidence |
| response `session_state` | enum `reported_healthy/expired/login_page/unknown` | 捕获时声称的状态，不是凭据或未来 session probe 的结果；expired/login_page/unknown 阻止后续确定性访问结论 |

`path_template` 的多槽、query_names 非空、json_array/HTML/truncated 只提供观察/覆盖或不确定性，绝不扩张 W1 的单个 builder-compatible resource path + GET + JSON object + 显式 anonymous/bearer bridge。body binding、任意 headers/cookies、browser login/MFA、mutating 自动执行仍排除。实际桥接资格还需 ADR-RA-INTENT；本格式不提供 request renderer。

### 2.3 大小、解析、资格与失败顺序 P

所有上限同时生效，恰好等于上限不因大小拒绝，但仍必须符合字段与数据资格；不是达到任一上限就必然接纳。

| 度量 | P 上限与计法 |
| --- | --- |
| 实际输入 | **262144 bytes**，包含 UTF-8 whitespace；读取至上限+1 即拒绝，不能信任声明长度。只接受未压缩本地 UTF-8 JSON；拒绝 BOM、无效 UTF-8、unpaired surrogate、NaN/Infinity/数值溢出、重复 key。无 gzip/zip/YAML/NDJSON 支持 |
| 单 entry | **4096 bytes**，按排序 key、紧凑 separators、UTF-8、无末尾换行的 canonical JSON 编码后计数；含全部字段及括号 |
| JSON 结构 | root 深度 0，每进入一个子值 +1，**最大深度 5**；所有 JSON 值（含 object/array，key 不另算节点）共 **最多 16384 nodes**；在有界解析中限制，不先无限递归再检查 |
| 批次 | **1–128 entries**，根总字节限制独立；128 个各 4096 bytes 的条目不可能整体通过 262144-byte 限制，不拆包自动规避 |
| 持久化内容 | 只持久化通过资格复核的 canonical allowlisted payload；单 entry 仍 ≤4096 bytes。raw HAR/原始文件副本、body、任意文件名/路径及未经资格复核的 digest **持久化为 0** |
| 错误响应 | UTF-8 JSON **≤256 bytes**，固定 `status/code` 与可选已验证整数 `entry_index`；只报首个错误，不回显 offending value、原文、解析异常、自由字段名或绝对路径 |

建议处理顺序：可信项目/许可/数据资格检查 → 有界字节读取/解析 → 严格字段/时间/长度检查 → registry 与项目/Target 检查 → 值域与 secret/PII/指令资格复核 → 生命周期准入 → 单事务写入最小化 payload 和 provenance。任何失败整批无 observation 写入；回滚失败或审计持久化失败阻止成功 receipt。只留合资格的固定错误码/计数，不创建含原文的“隔离文件”。这里的隔离指停止使用，**不是允许持久化危险原文**。

字段名过滤不足以保证值安全。必须结合预先批准的字段/模板/opaque label registry、synthetic 或逐字段 reviewed-minimized-private 资格、值级 canary/secret 检查；不确定时整批 `observation_data_ineligible`，由操作者在平台外重做最小化与审核。导入文件自称 sanitized/verified、输入命令、model output 均无资格提升效力；正常字段携带 PII 也拒绝。未经复核的原文不得进日志、异常追踪、重试队列、临时磁盘、备份、export、retrieval 或模型上下文；不能承诺 Python 内存被可靠零化，敏感配置还须控制 swap/core dump/调试采样风险，无法满足则仅允许合成输入。

### 2.4 独立 provenance 和 RA-02/W1 上下文 P

服务生成并绑定 `observation_id`、可信 project/Target 关联、`intake_time`、`format_version`、canonical payload digest、batch/entry/source index、preparation 审批引用、`provenance=operator_import_unverified`、`availability` 与适用生命周期版本。不得由文件指定 TestRun ID、ExecutionPlan ID、`verified`、`source_test_run_id` 或 M12 `observed_baseline`。hash 只描述获准最小化数据完整性，不证明原始 HAR/业务真值，也不做跨项目 dedup。

同项目同 batch_ref、相同 canonical 内容和 preparation 绑定重试只返回已有 receipt；任何内容、顺序或绑定改变都要求新 batch_ref 和更正关联，原记录不覆盖。服务须在接受事务内再次核验项目归属和 preparation 有效性，避免审核后转移 Target 的竞态。导入不改变已有 identity/resource/binding/assertion。

RA-02/W1 的可信 intake 上下文与 observation 文件分离：操作者选定 project、显式已审查 Target 集合、每次拟执行的单一 revision、规则来源/版本/使用许可引用、数据资格记录、hard budget 与用途；这些是人工输入的可审阅记录，不能由 import/prompt 覆写。准备度分别列 `permission_missing/data_ineligible/budget_unapproved/facts_missing`，缺许可可保留合资格的草稿元数据，但阻止执行准备，且不得因此接纳未获准数据。Target 的存在、Scope 匹配或 capture 成功都不补许可。

RA-02 自身 network/provider 消耗上限均为 0。未来执行上下文须有整型 request/time/concurrency 限额、精确 revision/platform rate，以及已批准的总 model token/费用值和币种/计量版本。W2 的 B runtime budget 和未来 task token/cost 仍为未决，不把 null 当无限/0；W2 10240 tokens / 2000 microusd 是 synthetic fixture 数，不可复制为运行额度。RA-02 可以明确显示这些缺项，不能标为 executable-ready。

## 3. 合成输入、拒绝与暂停示例

以下 P 示例假设操作者已在可信 context 选择 `reviewlab`，预先审核一个本地 Target origin、`/folders/{folder_id}` 模板、actor alias `reader_a`、资源 alias `folder_blue` 和 preparation `prep_local_a`。这些都是**未创建的说明性名字**；不是数据库 seed，不是 W2 案例。资格记录若不存在，同一份文件也必须拒绝。以 `intake_time=2031-04-03T12:10:00Z` 检查：

```json
{
  "format": "ra-observation",
  "version": "1",
  "project_ref": "reviewlab",
  "batch_ref": "capture_a",
  "preparation_ref": "prep_local_a",
  "prepared_at": "2031-04-03T12:05:00Z",
  "entries": [
    {
      "entry_ref": "entry_a",
      "source_entry_index": 0,
      "observed_at": "2031-04-03T05:00:00-07:00",
      "method": "GET",
      "origin": "http://127.0.0.1:58123",
      "path_template": "/folders/{folder_id}",
      "query_names": [],
      "actor_ref": "reader_a",
      "resource_labels": ["folder_blue"],
      "response": {
        "status_code": 200,
        "media_kind": "json_object",
        "capture_state": "complete",
        "object_labels": ["folder_blue"],
        "session_state": "unknown"
      }
    }
  ]
}
```

静态文档检查：上述 JSON 加一个末尾 LF 为 **747 bytes**，单 entry canonical 编码 **393 bytes**，共 **26 nodes**、最大深度 **5**；下面拒绝 receipt 加 LF 为 **77 bytes**。这些是文档尺寸检查，不是 importer 的运行结果。

预期设计结果：接受一条 **unverified observation**，保留顺序和来源，session 未知使未来消费方保留 NEEDS_INPUT；既不宣称漏洞，也不宣称安全。`05:00-07:00 = 12:00Z <= 12:05Z <= 12:10Z`。需要人工补 session/许可/业务 truth；不能因为 response 是 200 而自动创建 allowed 事实。未来是否请求 session health 由 ADR-RA-INTENT/TASK 决定，RA-02 不发送该请求。

| 新的说明性变体 | P 预期与人工责任 |
| --- | --- |
| 增加 `response.body`、`headers.Authorization`、`cookies` 或根 `source_url` | 整批 `observation_field_not_allowed`；不静默丢字段继续，不抓取 source_url。操作者重新提供合资格最小化文件 |
| 普通 `entry_ref` 值为 `person@example.invalid`，或 registry 外形似 token 的值 | `observation_data_ineligible` 或先发生的格式错误；占位邮箱为合成示意，拒绝不依赖真实敏感词库命中 |
| `origin` 为 `http://reader:pass@127.0.0.1:58123`，或 path 含 `../`、`%2f`、原始 query 值 | `observation_shape_invalid`；不得转码/修复成另一个“安全” URL 后接纳 |
| 增加 `$ref`、文件路径引用或 HTML 指令文本 | 未在 allowlist 的字段整批拒绝；`media_kind=html` 只保存枚举，object_labels 必须为空，不能带原文或执行指令 |
| project_ref 改为另一项目，actor/resource alias 归属不符，或 Target 尚未审查 | 统一 `observation_context_unavailable`，不泄漏另一项目是否存在，不自动登记 Target |
| 同 entry_ref/index 重复、同 batch_ref 不同内容、Naive observed_at、未来时间、prepared_at 早于 observed_at | 依次按解析/字段/时间/幂等 gate 拒绝；更正需新批次，不重写历史 |
| capture_state=truncated，object_labels=[]；或 actor_ref=null | 合资格元数据可保留为不确定观察，不能当 object proof / anonymous / safe；要求补充事实 |
| query_names 非空或多槽模板，所有名称均已获准 | 可保留 coverage；不是 RA-04 初始 bridge 支持，不静默生成执行计划 |
| 任一条超限或有不合资格内容 | 整批 observation 写入为 0；不留下已处理前缀或包含原文的排错文件 |

说明性拒绝 receipt（不是当前 API 输出）：

```json
{"status":"rejected","code":"observation_field_not_allowed","entry_index":0}
```

## 4. 项目、访问、保留与事件处理 P

下列控制是 **RA-02/W2 首次相应持久化前**的依赖，不得借 RA-08 延后。选择 synthetic-only 也要通过零网络、provenance 和隔离检查；私有最小化资料额外要求全部适用 lifecycle/部署证据。若操作者不批准 D3 数字，保持 PENDING，不能默认为无限留存。

| 面向 | 推荐行为、期限与失败边界 |
| --- | --- |
| 项目隔离 | 单 trusted operator 的本地逻辑隔离，非多租户安全平台。新域的写入/读取/查找/更正/删除/导出都从可信上下文固定 project，FK/唯一性使用项目组合键并验证 Target、alias 同归属；不能仅信文件中的 project_ref。默认没有全局观察搜索、共享缓存或跨项目复制 |
| 与旧全局 API 共存 | 现有 Target/Resource/TestRun 无 research project 隔离列；不得声称旧 API 已提供新项目访问控制。新流程不能经旧全局 list 接口获取别的项目资料；旧 operator 管理能力不被解释为 SaaS 用户隔离。关联迁移必须显式审阅，不猜归属，不移动旧行 [C03][C06] |
| 最小化先于写入 | 接收的是已最小化文件；再次校验字段和值资格后才写 payload、digest 和 provenance。禁止原文临时落盘、数据库 staging 原 HAR、原始 body/hash、异常参数自动采样；值级检查失败只记录固定码 |
| 访问/密钥 | 默认本地操作者访问；应用/数据库服务最小权限，备份加密并受本机访问限制。敏感 profile 启用前证明 PostgreSQL data/WAL、临时文件及备份所在存储具备适用加密/访问控制，日志/core dump/swap 不旁路。现有 credential cipher 只用于凭据，不能把其密钥直接当 observation 通用密钥 [C11] |
| retention | 新 observation payload 默认从 accepted_at 保留 **30 天 = 2592000 秒**，可按项目资格缩短；访问条件为当前时间严格早于 expires_at。到期即不可检索/导出/用于规划，不能等清理任务才失效。read 不延长有效期，恢复备份不重置 accepted_at |
| deletion | 操作者可提前请求本项目 payload 删除；先原子标记不可用并撤销索引/缓存资格，再在显式有界维护操作中删除 payload，保留最小 tombstone。在线时推荐 **24 小时内**完成逻辑清理；离线不宣称已物理清理，恢复服务须先处理积压，再接纳私有新数据。不为此引入 scheduler |
| hold | 仅适用于已合资格的最小化 payload；显式记录理由代码、审批引用、起止时间，单次最多 **30 天**，续期须重新决定，不能无期限默认 hold。hold 不恢复已删除 payload，不延长已产生 tombstone 的期限；hold 阻止 purge，但停止普通检索/export，仅受限人工复核；secret/不合资格原文不能以 hold 为由留存。到期或释放后已超过原 expires_at 的内容立即进入删除流程，不重开 30 天 |
| provenance/tombstone | 新域保留 opaque IDs、format/lifecycle version、已批准 payload digest、顺序、审核/可用性事件与固定 reason code；不得藏一份内容在“审计”。删除不改旧 digest；更正追加关联。推荐从 payload 不可用且无有效 hold 起再保留 **90 天**，之后删除无内容的元数据；依赖方须支持 source unavailable，不能因软引用无限留存 |
| backups/恢复 | 只纳入获准最小化数据，加密备份最长 **7 天**，清单列出已知副本、WAL/PITR 范围、导出归属和到期。删除记录在恢复前重放，恢复库在到期/删除/hold 检查完成前不能对服务开放；备份操作失败或副本无法定位时禁止继续接纳私有数据 |
| 删除保证的边界 | 删除 row 不等于覆盖磁盘空页、WAL 或外部副本；7 天为受管备份保留上限，不宣称介质取证不可恢复。部署须审阅这种加密存储/逻辑删除余留风险；需要按项目密码学擦除或不可回收介质证明时，先批准替代密钥/存储方案，未具备前该类输入不合资格 |
| logs | 不含原始 input/body/URL/query/secret/PII、原始路径、parser repr 或完整 exception；仅项目内 opaque intake ID、固定事件码、validated index、计数和 aware 时间，推荐最长 **90 天**。受限 provenance digest 不进入全局日志；读/删/hold/export 行为可审计，失败不返回成功 |
| exports | 默认关闭；每次显式选择本项目合资格、未过期且非 hold 的字段并检查接收资格，记录批准引用和副本清单。导出不带 raw body/credential 或其他项目数据，不自动外发/提交。外部副本不能远程撤销，无法接受副本保留约束的数据不导出 |
| incident/不确定性 | 发现 secret/PII、错误项目、破坏的资格链、审计/清理失败：停止本批及该受影响 context 的消费/外发，撤销新域可用性，记录固定码和受影响 opaque IDs；操作者排查已知副本/凭据更新需要，按已批准方案处置。不能在日志复制事故内容，不能自动清洗旧 TestRun/M13，也不能自动调用凭据/外部系统 |

这里的 observation 删除/hold 是独立新域的拟议能力，**不是修改 M13 `automatic_deletion_enabled=false`**。物理 schema、操作接口和竞态处理的实现在 RA-02 获批后安排进既有 W1–W3；不存在已交付的 cleanup API、worker 或 incident 工具。

## 5. 兼容、迁移/回退证据与 RA-02 验收映射

### 5.1 必须保留的现有行为 C

`FindingAnalysisService.analyze_test_run(test_run_id, baseline_test_run_id)` 只接受旧 cross-owner probe 和独立 `owner_baseline` TestCase，检查同 endpoint/resource、不同 actor、精确 IDs；它不选择 latest，也不靠当前 Resource.owner 再选 baseline。当前 `Resource.owner_identity_id` 非空、TestCase 四列唯一键不能承载任意新 intent 语义。[C04][C06] 这不等于已支持“owner+denied / non-owner+allowed”的新执行 pair；必须新版本而非改标签冒充旧模式。

M13 `fingerprint_response_pair()` 对**已持久化字符串** UTF-8 取 hash [C05a]，`None` 在旧指纹算法中表示零 bytes；相似度只消费指纹，不影响分类。把旧 body 改为 null、规范化 JSON、删字段或换成 placeholder 后重分析，会与原指纹冲突；既有测试断言 409 `finding_evidence_fingerprint_conflict` 且不变更历史 [C04][C05][T02]。删除策略不能把 source unavailable 偷换成真实空响应、构造新 baseline、重算旧指纹或回填 verified 事实。

正式报告在 `confirmed` 后读取 TestRun 并生成持久化 report_data/markdown；AI advisory 也读取 source body。现有 `sanitize_response_body()` 是 key-name redaction 与 16000-character 截取，普通 username 字段仍保留，不构成 PII/lifecycle 或项目隔离保障 [C07][C08][T05]。旧报告的“cross-owner/owner”措辞也不能直接拿来描述新合法共享/owner-denied 语义。

M13 常量继续为 `policy_id=m13_minimized_finding_evidence`、`policy_version="1"`、`retention_mode=explicit_management_only`、`automatic_deletion_enabled=false`、`raw_response_body_retained=false`；它们不为 TestRun source body 声明保留/删除策略。

### 5.2 分阶段兼容建议 P

| 变更面 | 推荐策略与验收证据（尚未实现） |
| --- | --- |
| RA-02 新域 migration | 新增 context/显式关联、intake/provenance、最小化 payload/availability/hold 元数据；最终表/约束设计由 D2/D3 审批。不得给旧 TestRun 自动分配项目、填入 imported plan/source FK、改旧 Resource owner 或触碰 M13 行 |
| upgrade | 在 fresh PostgreSQL 与带旧 TestRuns、paired/unpaired Findings、human review、reports、M12 reviews、M13 五类附属证据的数据集验证；逐行/字段/指纹比对既有内容未变，旧读取与再分析保持原结果。迁移不读取原 body 来 backfill observation/资格 |
| FK/append-only | 保留 TestRun→TestCase/Plan/Revision 与 evidence→两次 run 等 RESTRICT、计划唯一结果、Finding/evidence 唯一性；保留 append-once，以及六层记录在同一 savepoint 内的原子性。新 observation payload 可删除，但 provenance 关联不可要求改写旧 FK；引用已删观察的未来消费者返回不可用，不 cascade 删除旧证据 [C03][C05][T03] |
| 新 source unavailable | RA-02 新域区分 `available/expired/held/deleted/quarantined`，消费者先检查资格，再读 payload；到期/删除/隔离不可重分析为安全。将来 execution source 必须在进入旧 analyzer 前作明确版本分派，source unavailable 时返回有类型的不确定/不可重分析，不伪造空 body；已有 immutable evidence 可只读但注明无法重新验证来源 |
| 旧 source body | 本包与 RA-02 不删、不迁移。若实际业务要求治理旧内容，须停止该依赖，提出显式规范兼容决定和额外授权：列出所有旧读者、报告副本、fingerprint、hold、恢复与无源再分析行为。不是把工作默默推迟到公网审计 |
| rollback | 空新域可验证 downgrade 恢复旧 schema；含新数据时先停用 intake/新消费者，盘点/按资格处理 payload、hold、日志和备份，不用 destructive downgrade 静默丢数据。默认回退旧应用并保留不可被旧应用消费的新表；处理完数据且无 hold 后才由人工批准 schema downgrade。rollback 不恢复已删除源数据或重新启用过期许可 |
| 新敏感执行 source | RA-04 敏感执行前批准并实现专门 source lifecycle 与 unavailable 分派，测试“提取→持久化→删除/hold”的原子性及报告一致性。若仍使用旧 body 写入路径而未有适用控制，敏感执行必须保持关闭；RA-08 只能再审计，不能补这一前置缺口 |

### 5.3 对既有 RA-02 工作包的具体映射 P

| 工作包 | 必需输入/设计控制 | 必需正向、负向和故障证据 | 放行条件 |
| --- | --- | --- | --- |
| RA-02/W1 | 可信项目/Target 归属、许可和规则来源版本、数据资格记录、显式 hard budget、适用的 D1–D3 批准引用；只存合资格上下文元数据 | 有许可/无许可、过期或换 revision、不能 union grants；null/未批准预算保持缺项；仅输入无网络/凭据读取；越项目不能借可信 operator 自动通过 | Tech Lead/操作者批准适用 DATA 设计，缺许可不执行准备；敏感规则/上下文本身同样先满足 lifecycle，不能等 W2 才保护 |
| RA-02/W2 | 实现本格式受限解析、逐项目事务/幂等、值级最小化、retention/hold/delete/log/backup/export 准入和隔离；migration 先过兼容测试 | 262144/262145 input bytes、4096/4097 canonical entry bytes、128/129 entries、深度 5/6、nodes 16384/16385；格式合法性与各预算分开验证。重复/额外/外部引用/无效编码、普通键 PII、跨项目/未审 Target、异常回滚、清理/hold/恢复竞态；0 fetch、0 TestRun、0 verified assertion | 控制已实现并有负向证据，**首次相应敏感持久化前**通过，不接受仅“已批准、以后补实现” |
| RA-02/W3 | 人工选择 anonymous/bearer 与现有 credential 更新边界；Resource/slot 提议与 independent relationship/access facts；缺事实/session/membership 保留 NEEDS_INPUT | owner+denied、non-owner+allowed、合法共享、事实缺失/verified conflict、expired/login/MFA unknown；不制造 owner baseline；更正/review 保留历史；不从 observation 自动建 verified/observed_baseline；0 Target health requests | DATA 适用部分已通过；输出只是 RA-04 设计输入。INTENT 未批则不创建新 intent/plan/evidence；凭据只走既有 AuthenticationContext 域 |

M12 当前 review 会追加 human_verified 行、保留原 candidate；`observed_baseline` 只能由合资格真实 TestRun 生成 candidate，且 source FK 不为空 [C09]。建议 RA-02 的 observation 引用留在新上下文中，由操作者独立决定业务 facts 并调用现有人工 assertion 边界 [C09a]；不要把 imported ID 填入 source_test_run_id，也不增加第五种 provenance 来绕过既有约束。旧 Resource 要求非空 owner；owner 未知时只保留新上下文中的 Resource 提议/缺项，不能为了满足 NOT NULL 填入假 owner 或启动旧 owner-based generator。

## 6. ADR-RA-INTENT：显式访问语义、精确配对与 session

**RA-04/W1 后续材料：** [详细协议 v0.1.0](research-intent-contract.md)提供本基点实际调用证据（§1）、immutable core/摘要/plan link（§2–3）、health与精确时间窗（§4）、等待/变化失效规则（§5）、TestCase/M13/rollback选择（§6）及独立合成验收映射（§7）。下列原始 C/P 段落保留为 RA-01/W3 历史，不作为当前代码未经核验的证明。

**当前未决：** I1–I7 尚无批准。推荐数值为 **PROPOSED / PENDING_APPROVAL：health120秒、baseline完成至probe开始及最终pair消费30秒、intent300秒**，均以半开边界及更早依赖截止收窄。建议两单GET plan同revision、分别适用审批；凭据版本/身份/映射/事实/Scope/来源变化重建整对。当前缺少可信health receipt与运行预算核验，不能把RA-02的operator claim或unverified budget升格为执行资格。受控新TestCase类型、所有旧读者分派和保留数据的回退要求须Tech Lead明确决定；health解释器属RA-04/W2，提前依赖须按I6确认次序。本文仅提供review材料，不实施bridge、验证器或迁移；不改变其他ADR状态。

**W2 continuation:** the adopted I1–I7 constraints are implemented as recorded in [W2 verification](research-response-verification.md); the original C/P paragraphs below remain historical. No W2 reviewer PASS or stage completion is asserted.

**具体问题：** 如何把有来源的 access facts 和确认的 Resource-to-slot 关联变成可审批的单动作计划，并用合法 baseline 验证 probe，同时保留旧 cross-owner 证据与报告？

**C：** M14 `select_bola_binding()`/`preview_bola_binding_matrix()` 只返回当前 confirmed slot 和独立资源事实，不证明 membership、不创建计划 [C13][T06]。`PlanExecutionService.execute()` 要求恰好一个 GET、TestCase/Resource provenance，预先核验 actor/credential；等待后 refresh 重验 plan/revision/Scope/approval，不完整重取凭据或验证 session [C10][C11]。M12 使用 aware evaluation_time、asserted_at 资格、半开有效期，256 上限/257 拒绝；verified conflict 不按来源/置信度/最新时间选赢家 [C09]。

**推荐 P：** 另设版本化 immutable intent/context，描述同 project/Target 的精确 endpoint/template、单 resource slot 与人工映射证据、独立 relationship/expected_access、supporting assertion IDs/aware time、明确 baseline/probe actor 与各自 session 资格、credential binding 非秘密版本引用、单一 revision、data-policy version。bridge 按已审 intent 创建 **两个独立的精确单 GET plan，按各自 revision 要求分别获得适用审批**，绑定 intent digest；等待后重新校验全部适用关系与资格，变化则停止并要求新 intent/plan，不只检查 URL 未变。现有 policy_context 可存 JSON/hash 不等于已有这些校验。

baseline 必须有独立 current allowed 事实和合资格完整对象证据；probe 的 denied/allowed 与 owner/non-owner 无关。shared/non_owner+allowed 不能报漏洞；owner+denied 不能被强制当 baseline；没有 allowed 主体就 NEEDS_INPUT，不制造成功观察。资源证据不足、expired session、200 登录页、权限/MFA 未知均不记 safe。query/nested/multiple 继续只做 preview/coverage；body、任意 headers/cookies、browser login、mutating 与更广 renderer 继续排除。

**替代与兼容：** 继续强制 legacy owner-baseline 可少改表，但损失上述业务语义，必须显式缩小产品声明且不能改 oracle/分母掩盖；扩展旧 TestCase 唯一键/重解释 `owner_baseline` 则影响生成幂等、旧 reports 和 M13，风险更大。推荐新 intent/evidence 版本与明确消费分派，旧模型/FK/指纹/审阅保持不变；不能让旧 API 消费新类型后给出似乎正常的结果。

**待决/检查点：** Tech Lead 在 RA-04/W1 前批准 intent 存储/摘要联结方式、旧读者兼容和 pair/revision 规则；操作者确认 allowed baseline 选择及更新凭据步骤。session 健康证明的来源、最长有效时长、baseline/probe 最大间隔、等待后 binding/secret-version 变化如何使审批失效，**尚无批准数值或协议**，依赖代码前必须补齐。若需要 health GET，它本身也需授权、精确计划/适用审批并计入总请求，不自动访问 login/MFA。RA-02/W3 仅保存合资格事实和缺项；不得提前实施该 bridge。

## 7. ADR-RA-PROPOSAL：上游建议与既有 Finding advisory

**RA-05/W1后续材料：** [独立协议、字段/限额、opaque引用及确定性消费](research-ai-provider-contract.md#2-proposal-protocol-p)，P1–P6待决定；DOCUMENTATION_ONLY / PENDING_INDEPENDENT_REVIEW。下文保留原始原则/兼容记录，新材料不代表协议已采纳或实现。

**具体问题：** AI 可提供何种有限建议，如何拒绝越权、未知引用和不确定输出，而不把现有 Finding 后分析误认为自主发现？

**C：** `/findings/{finding_id}/ai-analysis` 直接装配 `MockAIProvider`；`AIAnalysisService.analyze_finding()` 先读取 Finding/TestRun/TestCase，再调用 `AIProvider.analyze()` 并记录 advice。Mock 依据已给状态/置信度分支，不是 live provider 或探测循环；该协议没有新候选 intent 的定义 [C08][T05]。

**推荐 P：** 上游 proposal 与旧 advisory 分成独立版本协议；只允许已选 context 中的 opaque references、规则版本/适用条件、有限建议类型、引用与 uncertainty/reason code。确定性消费者核对引用存在、项目/版本/资格、support envelope 和预算，未知或不可证明就拒绝/NEEDS_INPUT；模型不能补造 verified facts、plan digest、执行成功、human review 或费用。对象 ID 必须来自已选合资格数据，不让模型输出任意 URL、headers、工具名或 script。私有项目 evidence 不进入模型；只有独立批准的合成/通用化材料可用于 proposal。只有操作者与确定性 policy/execution 路径拥有原有权限。

**替代与兼容：** 把所有建议塞进现有 AIAnalysisResult 可少一个协议，但会混淆 Finding 后解释与上游计划；给模型 executor/任意 fetch 工具违反既有规范，不是可采纳替代。推荐保留旧路由和 FindingAIAnalysis 读取兼容，新增协议必须在 RA-05 的获批范围单独实现，不拿旧表文本作为新执行指令。

**待决/检查点：** Tech Lead 在 RA-05 proposal 集成前批准建议类型、精确字段/大小上限、版本协商、可用引用集合、拒绝码及 deterministic consumer；操作者审阅拒绝/解释的可用性。验收须包括恶意指令、跨项目/held-out 引用、虚构动作/事实/确认、超长/缺失/重复输出，且 execution/approval/policy-write/Finding-confirmation 为 0。当前不把这些未来协议上限借用成 W2 或 importer 的限制。

## 8. ADR-RA-EGRESS：provider 外发与实际用量

**RA-05/W1后续材料：** [官方provider/model比较与推荐](research-ai-provider-contract.md#3-providermodel-比较与推荐-p)、[数据/局部POST边界](research-ai-provider-contract.md#4-外发数据和独立-transport-p)、[凭据](research-ai-provider-contract.md#5-provider-账号与凭据-p)、[用量与最坏预留](research-ai-provider-contract.md#6-usage预算与未来-w2-接口-p)及E1–E6全部PROPOSED / PENDING_APPROVAL。下文“本包不选vendor/model”指原RA-01/W3历史；本次只准备推荐，实际选择、例外和支出仍待批准。

**具体问题：** 如何在 Target GET-only/public blocked 不变的前提下接入一个确切 provider，并证明数据、凭据和最坏成本都有边界？

**C：** 当前只有 MockAIProvider；现有凭据由 `BearerCredentialService.resolve_binding()` 和 `AuthenticationContext` 管理 Target auth，不能证明 provider secret/retention 适用。OpenAPI fetch 也是 Target 受控 GET，不是通用 provider transport [C01][C08][C11]。W2 `Ledger/score()` 能检查 synthetic 的调用集合与数值，却不采集现实的发送/消耗；`inclusive-input-output-v1` 只是该离线契约 [C14]。

**推荐 P：** 独立、默认关闭、固定 provider origin/endpoint/method 的 adapter，独立 account/secret 与数据资格；不继承 Target 许可、不放宽 Target 方法、不使用任意 URL/proxy/redirect。Provider API 若要求 POST，必须在该 ADR 中明确局部 transport 边界及网络/超时/字节/peer 控制，通过真实调用前的数据与费用批准；本包不选 vendor/model，也不产生支出。

调用前由可信 observer/预算层记录唯一 call ID、reserved 最坏 input+全部生成 output（含 reasoning）/费用、版本与 attempt；完成/失败/取消后对账。cached-input/cache-write 的包含关系和 reasoning 是否含在 output 必须按**届时所选 provider 官方定义**冻结映射，不能直接相加重复核算；embeddings 初期 0。没有 final usage 或 delivery 状态不明时保守保留余额，拒绝无法证明仍在预算内的后续调用，不以重试隐藏成本。私有项目 evidence/secret/access truth/保密报告不进入模型，普通键名通过旧 redaction 也不获准外发。

**替代与兼容：** 把 provider 当 Target 或直接换 Mock 实例会混用许可、secret 与费用域，不采纳；继续只用规则/Mock 可保留离线能力，但不能宣称真实 AI 比较完成。推荐 adapter 与旧 advisory/proposal 两个消费者显式分派，存储模型/设置/prompt/核算版本；旧 analysis/reports 不重新解释或改写。

**待决/检查点：** RA-05 provider 代码前由 Tech Lead 批准传输/密钥/数据 minimization、缓存 retention/account 边界、usage 适配与总生成量是否可硬限；操作者选定模型及费用账户。B 的有界上下文/预算、每项运行总 tokens/费用硬 cap、费率/币种/缓存计费均仍未批准；RA-07 实测前按当时官方资料和独立预算许可冻结，**缺失不是 0**。验收必须覆盖 omitted call、发送后超时/用量未知、cached/reasoning double count、恰好/超预算及数据 egress canary；真实调用/收费另批，本包上限为 0。

## 9. ADR-RA-TASK：预算、审批集合和恢复

**RA-05/W2 design preparation:** [v0.1.0 proposal](research-ai-budget-contract.md) maps deterministic preparation, exact receipts, short-transaction reservation/settlement, final-send qualification, independent observation and recovery to actual W1 ports. B1–B8 remain **PROPOSED / PENDING_APPROVAL**; PostgreSQL authority is assessed, not adopted. Review and User adoption are required before dependent W2 implementation, earlier than RA-06. This does not approve task/account caps, account evidence access, storage/retention, spending or real sends; M8 and the historical recommendations below retain their boundaries.

**具体问题：** 谁拥有任务状态、消费预算与观察账本，如何把有限精确 plans 组合成操作流程，而不把 M8 的 plan ownership 当作 research scheduler？

**C：** M8 PostgreSQL claims/fencing/progress/cancellation 管理单 plan；`prepare_attempt()` 只让 `pre_network` 接管，`network_started/in_doubt` 阻止盲重试，canonical TestRun 可直接返回。当前执行仍只有单 GET action；plan 存储上限 100 不代表多动作执行。旧 TestCase.status 只是兼容/UI 状态，没有 research-task token/cost 账本或 worker 生命周期 [C10][T07]。

**推荐 P：** 将来任务记录只组织显式有界的 plan ID/digest 集合及依赖，PostgreSQL 作为原子预算与观察状态的权威；M8 继续独占每个 plan 的执行 ownership。任务层记录 selected/supported/unsupported/unexecuted/inconclusive/blocked/missing，恢复不删失败/未知记录，不隐瞒 health 请求或模型失败。审批界面可批量呈现一个有限列表，但决定仍绑定每个精确 plan/digest；不得批准未来不断扩展的集合或把 W2 的 96 案例自动转成一个任务。

request/token/cost 在可发送前原子预留，observer 与结果声明分开；调用/动作按 attempt 唯一计量，已完成不因重读再扣一次，未知不释放。取消停止新增工作，不声称已经发送的请求/调用被撤销；仅证实 pre_network 且许可/审批/预算仍有效才可恢复，in-doubt 由操作者核实外部效果，无证明不重放。手工增加预算/恢复计划是新决定，不能重启被取消的旧 plan。

**替代与兼容：** 内存 task dict 不能跨进程/重启保证预算；用 TestCase.status 推断恢复会绕过现有 M8 fencing；自动把多个 action 放进一个可执行计划不受当前实现支持。推荐独立任务域引用旧 plans/canonical results，不新增 Redis、自动 worker 探测或放宽 single_process/multi_process 边界。

**待决/检查点：** Tech Lead 在 RA-06（或更早审批聚合）前批准状态机、budget/observer 事务边界、worker 生命周期、取消/失败语义和 local CLI；操作者批准总预算、有限审批呈现及恢复步骤。拟议 30 分钟/100 Target GET（含 health）/并发 1/不超 revision/platform rate ；C 每案例最多 2 次调用，每次最多 4096 input tokens / 1024 total generated tokens（适用时含 reasoning），每案例模型累计墙钟时间最多 60 秒，保持 W2 数值，**task 总 token/费用与 B cap 仍不能从 synthetic 额度推导**。可信 observer 怎样捕获“两份账本都漏了一个现实事件”、如何对账 provider 的 delayed usage，是批准前需验证的开放项。验收使用独立故障/多进程证据验证并发预留、崩溃各时点、取消和 in-doubt，不要求本包实现 scheduler。

## 10. ADR-RA-PUBLIC：分开的发布与测试许可

**具体问题：** 哪些端到端证据足以支持一次明确受限的公网启用，而非因本地回归或 ADR 文件存在就放行？

**C：** `PolicyEnforcedHTTPExecutor.execute()` 与 `OpenAPIScanner.scan()` 对非 `private_local` 主动阻断；gateway 绑定选定 IP/实际 peer、admission 和有界响应，但不能由此断言已审查全仓库所有 outbound paths [C01][C12]。M14 可离线处理 public-mode metadata，不产生网络权限 [C13][T06]。

**推荐 P：** 沿现有架构，默认拒绝保持到独立 readiness gate：完整 outbound 清单（Target、OpenAPI、未来 provider/其他消费者）、DNS/IP/port/canonical origin/peer/TOCTOU、secret/data lifecycle、限制/kill/audit/deployment/rollback 演练均有适用负向证据。再独立批准自有环境演练；第三方测试仍需专属 Target enrollment、revision/Scope/平台安全、身份、精确审批及程序约束，三者不能互相替代。

**替代与兼容：** 改一个 public 开关后继承本地 PASS、认为 wildcard/DNS 命中等于授权、或复用 provider 出口放行 Target 都不符合当前规范。推荐窄且可回退的版本门槛，公共模式无法证明控制时拒绝；保留 GET-only、no redirect、immutable single revision、即时重验、时间/字节/速率/并发及安全审计，不改变本地许可含义。

**待决/检查点：** RA-08 控制改动前由 Tech Lead 批准差距清单、门槛、停用/回退；自有演练由操作者明确批准，RA-09 每项第三方测试另取许可。实际公共环境、具体演练计划、provider 与 Target 所需网络边界差异均在届时审查；本包没有相关执行证据。DATA 对导入和 RA-04 敏感执行的前置控制不能等到此时补做。

## 11. 审阅结论模板与剩余决定

当前登记仅说明 **材料已形成**。建议 Review Project 先审查 complete base-to-HEAD，再由 Tech Lead/操作者对精确版本分别记录：

| 记录内容 | 当前值/待填写 |
| --- | --- |
| W3 文档与验证的独立审查 | PENDING；没有 Codex 代签 |
| DATA D1–D4 选择、例外与条件 | PENDING；须明确采纳/修改哪些字段、限额、资格、项目归属、30/30/90/7 天方案及删除余留风险 |
| RA-02 所需批准关联 | PENDING；需批准记录的确切引用、批准者、aware 时间、适用实现包及生效/撤回条件 |
| 其余五个 ADR | DEFERRED_TO_DEPENDENCY_GATE；已列出责任和检查点，未批准依赖代码 |
| W2 标签/阈值与真实运行预算批准 | 工程 review/push 已通过不等于这些批准；以独立明确记录为准，本文不推定已签署 |
| RA-01 stage exit / RA-02 进入 | 尚未满足；需综合 W1/W2/W3、适用 DATA 批准及验收，不以本包 commit 宣告 COMPLETE |

若 Reviewer 接受文档而对任一 DATA 前置选择尚无结论，保留该项 PENDING 并阻止依赖实现；无需更改既有 roadmap 次序，也不能以 standing authorization 绕过 architecture decision。

## 12. 证据边界与本次验证

本次直接读取了以上规范，以及下面列出的生产路径/模型/schema 和兼容测试；这是与六项决策直接相关的读取，不是全仓库 outbound、安全或 migration 审计。没有读取 W2 held-out 答案作为设计输入；既有 evaluator 在自己的测试边界读取/验证它们，不将内容输出作示例。C 引用均锁定本次 exact base；T 引用是既有断言，本次运行结果另记，不把它们说成未来控制已实现。

| 证据索引 | 已读直接来源与限制 |
| --- | --- |
| C01 | [OpenAPI scanner][C01]、[输入 schema][C01s]、[import model][C01m]：有 fetch/provenance 的旧链，不是离线 HAR importer |
| C02/C03 | [body 解码/写入 helper][C02]、[TestRun model][C03]、[TestRunRead][C03s]：有界字符串存储与旧读者，不包含 source unavailable 状态 |
| C04/C05 | [FindingAnalysisService][C04]、[exact evidence model][C05]、[fingerprint model][C05f]、[retention model][C05r]：精确 pair、原子追加、不可变值/RESTRICT 与冲突；无删除功能 |
| C06 | [TestCase 唯一性][C06]、[Resource owner/Target][C06r]、[Target model][C06t]、[Finding FKs][C06f]：旧所有权与关联容量不是新 intent/项目隔离支持 |
| C07/C08 | [SecurityReportService][C07]、[AI service][C08]、[AI route][C08r]、[Mock provider][C08m]、[redaction][C08d]：旧 source 消费/confirmed gate、非 live AI、有限脱敏 |
| C09 | [observed source/candidate service][C09]、[assertion/review constraints][C09m]、[append review][C09r]、[time resolver][C09t]、[manual schema][C09s]：provenance 不允许导入伪装；关系与 access 独立 |
| C10 | [exact execution][C10]、[plan digest/storage][C10p]、[approval][C10a]、[progress/recovery][C10g]：单 GET、精确审批与 M8；非 task scheduler |
| C11 | [Bearer credential service][C11]、[AuthenticationContext][C11a]：只有专属 auth 材料通道，不构成 source-data 加密或 session-health 证明 |
| C12/C13 | [Executor gate][C12]、[gateway][C12g]、[M14 composer][C13]、[slot selector][C13s]：保留实际限制，不由预览或局部网络控制宣称公网 readiness |
| C14 | [W2 scorer][C14]、[W2 contracts][C14s]：离线 synthetic 核算及待决项，本次不改 |
| T01/T02/T03 | [pairing tests][T01]、[fingerprint conflict tests][T02]、[retention migration tests][T03]：精确 pair/源变更拒绝/历史保持及 FK；不是未来迁移证明 |
| T04/T05/T06/T07 | [assertion review][T04]、[AI redaction boundary][T05]、[M14 acceptance][T06]、[M8 progress tests][T07]：未来消费者必须兼容的现有断言 |

本次验证于 **2026-09-10 00:08–00:11 UTC**（本地工作日 2026-09-09）完成，Ubuntu 24.04.2 / WSL2、项目 `.venv` Python 3.12.3、PostgreSQL 16.15。按 [M14 隔离 runbook](m14-offline-matrix-acceptance.md#reproducible-isolated-local-run) 创建独占 native 实例：database/user `ra01_w3_test`，`127.0.0.1:55451`，当前用户拥有新目录 `/tmp/ra01-w3-test.VfKPMU/data`。应用 import 前显式配置测试 DATABASE_URL、allowlist、topology 及临时加密变量，不使用 `.env` 选库；独立 psql 查询核验 database/user/address/port/version/data_directory、初始 public 表数 0、无其他 client backend，目录属主也单独核验。仅停止本次创建的实例，已确认 postmaster.pid 移除。

从 `backend/` 在上述环境中**串行**执行：

| 命令/检查 | 实际结果 |
| --- | --- |
| `python -m pip install --requirement requirements-dev.txt` | 所需开发依赖已满足；无 dependency 变更 |
| `python -m evaluation.ra01 verify` | PASS；freeze digest 保持 `692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a`；没有运行生成器或重写冻结文件 |
| `python -m pytest tests/evaluation` | **79 passed**；既有 evaluator 内部完整性测试不把 held-out 内容用于文档示例 |
| `python -m alembic current` / `python -m alembic heads` / `python -m alembic upgrade head` | 初始无 revision；唯一 head `b5d7f9a1c3e6`；upgrade 成功 |
| 以下 compatibility 命令 | **199 passed, 7 warnings** |
| `python -m pytest` | **2089 passed, 55 warnings**；包含全部既有后端回归 |
| `python -m pip check` / `python -m alembic current` | No broken requirements found；最终 `b5d7f9a1c3e6 (head)` |
| 完整 diff、链接/anchors、exact-base 引用/标识、数字/JSON 示例、`git diff --check` | 仅本文与 roadmap 三处导航/状态修改；W1/W2/全部 backend 文件保持原样；检查通过 |

```bash
python -m pytest \
  tests/api/test_finding_evidence_pairing.py \
  tests/api/test_finding_evidence_fingerprints.py \
  tests/api/test_finding_evidence_retention.py \
  tests/migrations/test_finding_evidence_retention_migration.py \
  tests/reports/test_security_report.py \
  tests/ai/test_analysis_redaction_boundary.py \
  tests/api/test_resource_access_assertion_review.py \
  tests/api/test_resource_access_resolution.py \
  tests/integration/test_m14_matrix_acceptance.py \
  tests/services/test_plan_execution.py \
  tests/services/test_execution_plan_progress.py
```

没有测试失败或环境阻塞；pip 的 home cache 不可写提示仅导致禁用缓存，pytest warnings 均来自既有测试。没有弱化/跳过失败或重试至绿。日志、DSN、临时密钥与检查脚本均留在仓库外；不提交新代码/schema/test/migration。上述 PASS 只说明现有回归及文档静态一致性，**不是新 DATA 控制实现证明、ADR 批准或 reviewer-PASS**。

[C01]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/scanners/openapi.py#L313
[C01s]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/schemas/openapi.py#L5
[C01m]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/db/models/openapi_import_record.py#L9
[C02]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/services/test_execution.py#L142
[C03]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/db/models/test_run.py#L17
[C03s]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/schemas/test_run.py#L10
[C04]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/services/finding_analysis.py#L61
[C05]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/db/models/finding_evidence_record.py#L9
[C05f]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/db/models/finding_evidence_fingerprint.py#L9
[C05r]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/db/models/finding_evidence_retention_binding.py#L9
[C06]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/db/models/test_case.py#L20
[C06r]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/db/models/resource.py#L15
[C06t]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/db/models/target.py#L21
[C06f]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/db/models/finding.py#L19
[C07]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/services/security_report.py#L41
[C08]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/services/ai_analysis.py#L43
[C08r]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/api/routes/ai_analysis.py#L32
[C08m]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/ai/mock_provider.py#L7
[C08d]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/ai/redaction.py#L37
[C09]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/services/observed_access_assertion.py#L17
[C09m]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/db/models/resource_access_assertion.py#L11
[C09r]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/services/resource_access_assertion_review.py#L16
[C09t]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/services/resource_access_resolution.py#L33
[C09s]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/schemas/resource_access_assertion.py#L15
[C10]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/services/plan_execution.py#L99
[C10p]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/services/execution_plan.py#L161
[C10a]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/services/execution_plan_approval.py#L43
[C10g]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/services/execution_plan_progress.py#L57
[C11]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/credentials/bearer.py#L159
[C11a]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/auth/context.py#L32
[C12]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/executors/http.py#L70
[C12g]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/network_safety/gateway.py#L99
[C13]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/services/bola_binding_matrix_preview.py#L75
[C13s]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/services/bola_binding_selection.py#L80
[C14]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/evaluation/ra01/scoring.py#L152
[C14s]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/evaluation/ra01/contracts.py#L1
[T01]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/tests/api/test_finding_evidence_pairing.py#L97
[T02]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/tests/api/test_finding_evidence_fingerprints.py#L208
[T03]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/tests/migrations/test_finding_evidence_retention_migration.py#L80
[T04]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/tests/api/test_resource_access_assertion_review.py#L145
[T05]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/tests/ai/test_analysis_redaction_boundary.py#L117
[T06]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/tests/integration/test_m14_matrix_acceptance.py#L189
[T07]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/tests/services/test_execution_plan_progress.py#L126

[C05a]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/analyzers/bola.py#L82
[C09a]: https://github.com/runyiy/ai-api-security-platform/blob/ac9a5ce3142232e86576b5d789d93b95508e259d/backend/app/api/routes/resource_access_assertions.py#L128
