# RA-03/W1：知识分类、版本与人工审核发布契约

**文档 v0.1.0 · RA-03/W1 · IMPLEMENTED / PENDING_INDEPENDENT_REVIEW**。这是已形成的设计材料；以下知识域字段、状态和接口义务均为 **PROPOSED / NOT_APPROVED / NOT_IMPLEMENTED**，不是现有 API/schema 或发布记录。

精确源码基点：`4ac9b284029a515766d7532a0381be86bea7ce09`。本次 fetch 核验本地 HEAD 与 `origin/codex/ra-02-w3-identity-resource-context` 相同、工作树干净后创建 `codex/ra-03-w1-knowledge-contract`。RA-02/W3 独立 review/push 已通过依据用户交接，未合入 main；不据此代签 stage COMPLETE、知识决定或运行许可。

任务依据为 [RA-03 验收卡](research-assistant-roadmap.md#ra-03--版本化知识已审查规则和受限检索)。[架构](architecture-decisions.md)、[安全模型](security-model.md)优先；[DATA 采纳记录](research-assistant-adr-decisions.md#data-后续决定记录ra-02w1)只批准其精确历史版本的 D1–D4，不批准本契约的分类/复用选择或其余五个 ADR。本包只改本文与 roadmap，不建立知识表、迁移、运行入口或新 ADR 编号。

## 1. 当前证据与阅读边界

下表 **C** 是本基点实际代码和已读测试断言；没有在本次重新运行数据库测试。**P** 是后续实现义务。源码永久链接固定到上述基点；测试链接指同一基点文件，不能把历史测试绿色解释为知识系统已通过验收。

| 现有边界 C / 精确入口 | 已读代码与证据 | 对知识消费者的限制 P |
| --- | --- | --- |
| W1 `read_context`、`_authorization_metadata`、`_readiness` | [源码][C1]；[使用契约](research-intake-context.md#2-输入预算与准备度)：归属校验先于 revision/Scope/rate 读取，关闭后 unavailable；固定三种 rules 与 SyntheticReference 是未验证声明 | 不是已审规则库；permission、reuse、预算各自独立，`execution_authorized=false` 不得被知识命中覆盖 |
| W2 `read`、`_live_preparation`、`maintain` | [源码][C2]；[生命周期及最终时钟](research-observation-intake.md#9-e3da1a0-后的已记录不可用时间修复)：普通读不返回 held payload，资格/到期重验、软引用可最终不可用 | 观察 `operator_import_unverified` 不等于事实。知识引用不能延长 payload/tombstone 时钟或保存副本兜底 |
| W3 `record`、`read`、`_qualify`、`_result` | [源码][C3]；[测试][T1] `test_source_lifecycle_suppresses_latest_and_history_without_copy`、`test_all_verified_conflicts_resolve_without_selected_id_filter`：提议/事实分开，来源失效隐藏 proposal/facts，更正不能剥离原 sources | 输出始终 NEEDS_INPUT；没有 intent、健康证明或发布能力。消费者保留精确 context/proposal version，但每次重验当前可用性 |
| W1/W3 归属与事务 | [W3 并发测试][T2] `test_closed_transferred_history_never_reads_new_project_metadata`、`test_real_transactions_serialize_context_sources_and_corrections`；[事务契约](research-subject-context.md#3-生命周期隔离与事务) | 新读者必须从可信 project/context 进入并在消费事务重验；不能用旧全局管理 API 探查别的项目。锁不是跨请求许可快照 |
| M12 `resolve_resource_access` | [源码][C4]：全部 eligible verified assertions、aware time、asserted_at、半开有效期；256 上限，257 拒绝；两维独立、冲突不选赢家。[T1] 验证 owner+denied、non_owner+allowed、shared+allowed | 规则的“适用”不是某个 Resource/actor 的 allowed/denied 事实；不得按排名、confidence、时间或 provenance 压掉冲突 |
| M12 人工 assertion 创建/review | [route][C5] `create_resource_access_assertion`；[service][C6] `review_resource_access_assertion` 追加 human_verified review，保留 candidate；[API 测试][T3] `test_synthetic_operator_example_with_existing_human_fact_review` | 人工显式走原边界；知识 review/publication 不调用或代理这些写操作。观察 ID 不得填 source_test_run_id，不增加 provenance 捷径 |
| Finding review / report | [route][C7] `review_finding`；[service][C8] `SecurityReportService.generate` 要求 confirmed；[M13 规范](security-model.md#m13-explicit-retention-policy) | published rule、正例或 external accepted 均不确认 Finding。保留旧 review/pair/fingerprint；retention binding 不删除 TestRun source body |

实际阅读限于上述服务/route、直接相关 RA-02 合成测试与下列规范的适用部分：[产品 §§5–7](research-assistant-product-contract.md#5-请求形态支持矩阵)、[评测隔离与冻结](research-assistant-evaluation.md#1-文件版本与隔离)、[DATA 生命周期/兼容](research-assistant-adr-decisions.md#4-项目访问保留与事件处理-p)及 RA-02 三份使用契约。没有做全仓库审计、读取实际数据库、复核全部执行/网络内部路径。未查看 held-out 样本或 oracle 标签文件；第 6 节是本包独立新写的合成材料，不取自评测集。

既有安全不变量不变：单 trusted operator、FastAPI/PostgreSQL、Default Deny、Target 非授权；一次执行一个 immutable revision、不 union grants，Scope/safety 只收窄；mandatory host allowlist、exact origin、safe path、即时执行前重验、GET-only、no redirects、有界时间/字节/速率/并发及适用 exact-plan approval。认证材料只经 AuthenticationContext；AI 无执行、shell、任意 fetch、凭据、审批、policy-write 或 Finding-confirmation 权限。M14 仍只读瞬态、不创建 plan；confirmed slot 不证明 Resource membership。公网 runtime 继续 blocked，知识发布不改变任何网络或费用 gate。

## 2. 分类与使用资格 P

分类描述内容用途；审核、发布、隐私与复用资格是独立维度，不能用一个 `trusted=true` 合并。

| category | 内容与来源 | 可发布/可消费含义；默认排除 |
| --- | --- | --- |
| `mechanism` | 无项目对象值的机制说明，例如“关系标签和访问权限是不同维度” | 可经独立审核发布为解释材料；不能作为判定规则、业务真值或执行参数 |
| `rule` | 一个有明确适用条件、拒绝条件及正反例的判断/研究建议；candidate 和 reviewed 是此类的不同审核状态 | 只有精确版本获人类发布且实时资格通过才可供后续规则选择；candidate、reviewed-but-unpublished 都不进入普通检索 |
| `project_evidence` | 本项目 observation、subject、assertion、TestRun/M13 等来源引用；各保留本来的 provenance 和生命周期 | 永不直接发布到可复用规则库、不进入模型输入。当前只能讨论/引用合资格 synthetic RA-02 数据；不新增真实私有证据存储 |
| `counterexample` | 对某条规则的前提、结论或支持形态的反例，绑定精确 rule version；正例也须独立标为 example role | 可作为经审配套材料；不能独立触发漏洞结论。私有反例仍私有；“反例”标签不授予复用资格 |
| `external_triage` | 外部 needs-info/duplicate/invalid/accepted 等结果的来源与人工转录记录 | 不等于 confirmed Finding、规则正确、awarded 或 paid；不参与自动发布。当前没有输入/下载该内容的授权或实现，RA-07 负责实际反馈记录 |

`data_class` 提议仅有 `synthetic_authored`、`project_private`、`unknown`；公开可见不自动属于第一类。未来获准通用化材料需新分类决定或明确的独立 synthetic 重写证明；不能将私人内容换标签。当前持久化准入仍只有既有 RA-02 固定 synthetic 元数据/格式；本契约中的正文尚无运行时接收接口。

四种许可分别登记：持有原材料、本项目最小化使用、跨项目通用化/复用、provider 外发。每项指向独立决定的精确版本、责任人、范围、有效期和撤回记录；缺项为 `unknown`，不是默认允许。Target 授权 revision 是测试许可，不能证明前三项；复用批准也不能扩大 Target/Scope 或任何执行预算。

推荐 `scope=project` 或 `scope=reusable_synthetic`。前者固定可信 project/context，只能在原项目资格内使用；后者必须是独立新写、无私有细节/事实的合成机制、规则或例子，并有独立 reuse review。项目证据不因 owner 同为一个操作者而跨项目。必要的源引用留在原项目受限 provenance 中，不把项目 ID、digest、标题或数量暴露给别的项目；共享卡只返回自身合资格的来源证明引用。

**通用化不是移动或复制：** 人工提出新材料→说明独立创作/许可与脱敏依据→检查源项目限制和污染→单独 reuse 决定→规则审核→精确发布。无法证明独立性时保持项目内依赖或直接拒绝；不得通过去掉 source ID 规避 W2 过期。未来确需私人内容通用化时，先取得独立授权、适用 lifecycle/部署证明与分类决定，当前不准入。任何类别中的指令、脚本、模板代码或 URL 都是数据，不能 execute、fetch、提供 shell/tool 指令或修改系统提示词。

## 3. 精确版本和有界记录 P

这是逻辑字段契约，不是 executable schema。推荐以下上限供分类/复用决定一并审查；没有声称代码已强制执行，也不挪用评测 token/cost cap。后续存储/API 物理设计须在批准后落地。

| 字段组 | 必需内容及界限 |
| --- | --- |
| identity | `contract=ra-knowledge/1`；`knowledge_id` 为 1–64 ASCII `[a-z0-9-]`；version 为 strict int 1–10000；identity 是 `(scope, project/context 或共享域, knowledge_id, version)`。版本顺序不代表审核优先级 |
| immutable content | category、data_class、scope、purpose、claim、applicability、source_refs、example_refs、counterexample_refs、supersedes；规则必须有至少 1 正例及 1 反例，最多各 16；sources 1–16，所有引用唯一，数组顺序保留 |
| strings / parsing | 单 claim ≤2048 UTF-8 bytes；每条说明 ≤512 bytes；完整单卡 content ≤16384 bytes；tags ≤16 个、各 ≤32 ASCII bytes；每集合 ≤16 项。strict UTF-8/JSON，root depth 0、最大深度 8、最多 2048 value nodes；拒绝重复/额外 key、BOM、surrogate、NaN、浮点版本、外部引用解析、非法编码，整卡失败，不修复/截断或记录原文 |
| exact source | 来源类型、域、opaque ID、精确 source version 或不可变 ID、批准 payload digest（若该域提供）、创作者/记录者、来源时刻与资格决定引用。source_ref 不能只有 URL、latest 或无版本标题；引用不触发 URL/文件获取。Example 的 `doc_example` 仅属于本文合成命名空间，不能冒充 RA-02 SyntheticReference |
| applicability | purpose、支持形态、显式 actor 类型、必要业务事实/证据、排除形态、拒绝原因；enum 列表 ≤16、每 enum ≤64 ASCII bytes。窄规则不能由关键词扩成通用规则；`unknown` 不匹配 positive 前提 |
| version digest | `sha256` 对完整 immutable content：JSON keys 按字典序、compact separators、ensure_ascii=false、UTF-8、无末尾 LF，不规范化字符串、不重排数组；不包含 digest 自身或 review/publication 事件。相同域/version 不同 digest 拒绝，不能覆盖；它只证明卡内容完整性，不是 M13 fingerprint 或授权签名 |
| review events | 独立不可变 event ID、精确 version/digest、actor reference、role、决定/理由码、aware recorded_at、所审 sources/qualification 与 validation refs、expected prior event。人类角色身份来自可信本地操作记录，文本自称 reviewer 不算批准 |
| publication events | 单独的 publish/withdraw/disable；精确 version/digest、review 与 reuse event refs、actor、aware recorded_at、`valid_from < valid_until` 的有限窗口、前驱事件、理由/条件。未填写有效期不可发布；不预设全局 TTL，不把 source 期限延长到 publication 期限 |

逻辑 canonicalization 版本变更须新 contract；内容/条件/来源/例子变化须新 content version，旧 reviewer 决定不继承。只追加审核或撤回事件不改 content digest。当前建议不自动删除独立合成卡的无敏感历史；**使用资格**受发布窗口、源期限及撤回限制。私有源的 DATA 30/30/90/7 天规则不复制为知识永久存储许可；任何新敏感持久化需要先获准并实现适用处理。

关联用带域的精确软引用，消费者可遇到 `source_unavailable`。不得通过知识 FK 阻止 W2 删除，不给 legacy TestRun/M13 自动分配 project、不回填旧行、不改指纹、配对或 review。只留下合资格的无内容 tombstone/事件引用时，正文应 unavailable，不能用“不可变历史”为由继续暴露污染内容。秘密/PII 事故的清理必须遵守适用且已实现的 lifecycle；未具备的类别在存储前拒绝。不能用知识审核顺带清理旧 TestRun。

## 4. 人工审核、发布与失效 P

流程为 `candidate → reviewed`，之后才允许另一次显式 `publish`；也可追加 `reject` / `request_changes`。自动检查、W3 正反例通过、模型置信度、单个成功案例都不能作 publish 事件。reviewed 并非 published，发布也只允许知识消费，不等于 execute、verified truth、confirmed Finding 或向外提交。

人工审核至少逐项核对分类、创作/许可链、项目范围、适用/排除条件、正反例、没有所有权推断、注入/污染检查、精确引用、当前来源资格和后续 validation 证据。单 trusted operator 可以承担不同审核角色，但必须分开作出可追踪的 review、reuse、publish 决定；不虚构第二审批人或密码学签名。Tech Lead 先批准这里的协议；每张卡的 publication 由获准的本地操作者明确决定。

发布前要求有效的 review、scope/reuse qualification、源资格和 W3 正反例 validation 引用；最后一项的执行器/证据格式由 RA-03/W3 实现，本包只能提供预期样例。W2 在此期间只能用明确标为合成测试的发布记录验证检索边界，不能宣称生产知识已可发布，不能为抢先上线跳过 W3 门槛。

发布与复用窗口按可信 server aware 时间、`valid_from <= now < valid_until` 判定；审核时刻不晚于发布时刻。请求自带时钟不改变当前资格，historical read 只展示当时事件，不能回到过去绕过当前失效。期限在检查与消费之间跨过边界时再拒绝；同 version 的冲突决定不能按 confidence 或最新时间选赢家，须显式引用前驱事件的合法状态转换。并发发布/撤回/来源关闭的最终约束与原子审计、响应编码后提交由后续存储实现证明，不存在 W1 运行时锁保证。

| 变化 | 必须保留 | 后续消费及人工责任 |
| --- | --- | --- |
| 修正文案、适用范围、source 或反例 | 新 version、supersedes 精确旧 ref、理由；旧内容/决定/引用不改写 | 新版本重新 review/reuse/publish；不解析 latest 替代旧引用。旧版继续可用与否须显式 withdraw 决定，不能由更正隐式授予新权限 |
| 发现规则错误或污染 | 追加 disable 指向精确 version/digest 和原因，保留既有发布事件与无敏感历史 | 立即排除版本和依赖它的候选；依赖闭包无法确定则暂停该消费范围。重新评估尚未处理的候选，保留原判断/引用，不重写历史 evidence 或自动关闭 Finding；污染 disabled 版本不原地重新发布，修复须新版本并重走全部审核 |
| source 到期/删除/hold/隔离/撤销，项目关闭或 Target 转移 | 本项目允许保留的最小引用/历史；不保存可恢复源副本 | latest/history 的普通消费均 source_unavailable；hold 人工阅读不是检索许可。release/end 只按 W2 实际资格重验，不能复活 expired/deleted 源 |
| 复用批准过期/撤回 | 精确 reuse 决定历史、受影响版本引用 | 立即停止共享使用；source 项目权限不兜底。污染传播到通用化派生物时同样禁用，不能凭曾通过审核忽略 |
| 事故含 secret/私人内容 | 固定 reason code 与 scoped opaque IDs；禁止日志/错误复述内容 | 停止接纳/消费，按已批准生命周期处理本新域及已知副本；不自动解密、登录、调用凭据更新或外部服务 |

错误/审计失败必须 fail closed，不返回部分发布或检索结果。日志只存最小 scoped IDs、事件码、时间；不记录卡全文、原 source 值、查询原文、凭据或其他项目标题。审计存储容量、轮换/恢复与非空 rollback 策略须在后续迁移前与实际有界存储一起审查；不能沿用 M13 永久证据策略或声称已有知识审计设施。

## 5. 后续消费者接口义务 P

W2 输入是可信 project/context、明确 purpose、当前 context/subject 精确版本、显式形态/actor/fact 缺项以及有界关键词/标签与 top-k；不得把 observation.project_ref、AI 文本或 SyntheticReference 声明当认证/批准。知识读者只用必要非秘密元数据；没有 credentials/encrypted_envelope/secret resolution、Target 查询或 session-health GET。

确定性顺序要求：结构/容量校验 → 核验本项目归属和数据资格 → 精确规则版本、review/publication、scope/reuse、源可用性、有效期和污染过滤 → applicability → 受限关键词/标签排序 → 有界结果序列化。非法/外项目引用和不存在引用统一 unavailable；不得泄漏排除条目的 ID、正文、标题、数量或得分。对自己的材料可以给固定缺项原因，对别的项目不给诊断细节。

排序不能救回被排除条目，top-k 不能先截断全局候选再做安全过滤。同一精确版本去重；不同版本不能混合 claim/反例，不能自动升级调用者所选版本。W2 决定具体 top-k 数字、稳定 tie-break、扫描/查询/输出预算并测试恰好/超限，不在 W1 发明 ranking 实现。高分但前提未知/不匹配依然排除。body、query/nested/multiple-resource、变更方法或任意 headers/cookies/browser 不能因命中规则扩大[初始 bridge 形态](research-assistant-product-contract.md#5-请求形态支持矩阵)；query/nested/multiple 可仅给覆盖说明，与可用于后续窄判断的规则分开。

返回契约至少含：contract version、当前 evaluated_at、请求本项目的 context/subject refs；每条 exact knowledge ref/digest、category、适用说明、对应正反例 refs、review/publication/reuse 决定 refs、当次资格判定引用；以及缺项或 `no_match` / `unsupported` / `needs_input` / `source_unavailable`。这些是 proposed 原因而非现有状态枚举，不改变 RA-02 response。零匹配/未知/未执行不能输出 safe。引用历史不得携带源 payload、M12 access truth 或私有内容到共享/模型输入；后续基于规则解释也须独立重验当前项目 M12 facts。

资格判定按用途区分：卡自身的来源持有/本项目使用许可、review/publication 或适用 reuse 决定缺失，一律不返回卡正文；provider egress 许可缺失一律不允许模型输入。W1 的 `permission_missing` 是测试许可缺项，不能用知识补齐：可以显示本项目缺项和独立合资格的一般机制说明，但不得把命中解释为项目研究动作已准备好。需要当前项目事实的规则在事实缺失/冲突时不给判断，只报告原因。W1 `referenced_current` 也不是执行许可，预算未批始终保持。未来实际 Target 动作仍独立经过原 policy/approval 边界，不能把这里的资格结果缓存成 permission。

没有跨请求缓存 authorization、approval、mutable access truth；未来即使缓存不可变卡，也要每次重新过滤当前资格。取出规则后来源/决定变化则停止依赖工作，保留原引用并重新检查；W2 需证明归属校验与 close/transfer、withdraw/hold/delete 的竞态没有窗口。W3 对同一精确 rule/contract/example version 运行独立正例/反例验证，含无匹配、恶意指令和错误结果；失败产生 candidate correction/disable 提议，成功只能提交给人类审核。反馈不自动提升、修改既有标签或发布新版。

[冻结评测](research-assistant-evaluation.md#5-冻结记录与离线复现)不用于规则编写、检索、prompt、训练或反馈提升。W2/W3 的 canary/leakage 测试应自行构造人工合成隔离标记；不能打开 held-out 找样本。`evaluation.ra01 verify` 仅由 evaluator 内部校验冻结，不返回答案给知识消费者。新知识样例不是新的 oracle、阈值或性能证据。

## 6. 新写的合成规则卡与历史示例 P

命名空间 `kw1-demo-*` 仅为本文；逻辑时间固定为 2032 年，不是实际消息、请求或审批时间。场景是一份虚构“展品说明卡”，显式选定 anonymous/bearer，未来窄形态为 `GET /placards/{placard_id}`、JSON object；没有实际 Target、身份、Resource、assertion、TestRun 或 payload。例子不是可直接提交给 RA-02 的输入，不从 observation/评测集中复制正文。

| doc_example ID / version | 独立合成业务设定 | 预期审核/消费解释（未执行） |
| --- | --- | --- |
| kw1-demo-positive / 1 | 此说明卡在展示前禁止原作者读取：owner+denied；无已确认 allowed 主体 | 保留 denied，不把作者做成功 baseline；缺 allowed baseline，needs_input。正例验证“不得从 owner 推断 allowed” |
| kw1-demo-sharing / 1 | 独立授权允许参观者阅读：non_owner+allowed；另一独立情境为 shared+allowed | 两种合法允许都不是漏洞，反驳“非 owner 能读就违规”；不拼接两个情境的权限 |
| kw1-demo-conflict / 1 | 同一 actor/object 在评价时点同时有 eligible verified allowed 与 denied | 保留 conflict 和全部适用来源，needs_input；较新 denied 不胜出 |
| kw1-demo-uncertain / 1 | 只有导入的“会话有效/读到对象”声明，或 bearer login_page/MFA/expired/unknown；也可能未选择 identity | 不是 verified access 或健康证明；未知不降级为 anonymous，不输出 safe，不虚构健康 TTL |
| kw1-demo-shape / 1 | 相同建议尝试用于 query slot、嵌套多个对象或 body | query/nested/multiple 只作覆盖；body 排除，不产生窄规则匹配或执行结论 |

下面两张卡使用同一完整 content 结构；第一版包含上述规则核心，第二版明确增加会话缺项拒绝条件。每个 `doc_example` source 包含本包作者来源声明，**不是 source 资格已获实际批准**。该 JSON 是提议的完整合成 content 示例，sha256 在其外按 §3 计算。

```json
[
  {
    "contract": "ra-knowledge/1",
    "knowledge_id": "kw1-demo-independent-access",
    "version": 1,
    "category": "rule",
    "data_class": "synthetic_authored",
    "scope": "reusable_synthetic",
    "purpose": "offline_context_explanation",
    "claim": "关系标签不能代替独立访问事实；缺少 allowed 主体时不创造 baseline。",
    "tags": ["bola", "independent-access"],
    "applicability": {
      "shape": ["single_resource_path_get_json_object"],
      "actors": ["anonymous", "bearer"],
      "required": ["explicit_identity", "project_scoped_facts"],
      "refuse": ["facts_missing", "facts_conflict", "unsupported_shape"]
    },
    "source_refs": [{"kind": "doc_example", "id": "kw1-demo-origin", "version": 1,
      "author": "synthetic-author", "recorded_at": "2032-01-01T00:00:00Z",
      "qualification_ref": "kw1-demo-reuse-1"}],
    "example_refs": [{"kind": "doc_example", "id": "kw1-demo-positive", "version": 1}],
    "counterexample_refs": [{"kind": "doc_example", "id": "kw1-demo-sharing", "version": 1},
      {"kind": "doc_example", "id": "kw1-demo-conflict", "version": 1}],
    "supersedes": null
  },
  {
    "contract": "ra-knowledge/1",
    "knowledge_id": "kw1-demo-independent-access",
    "version": 2,
    "category": "rule",
    "data_class": "synthetic_authored",
    "scope": "reusable_synthetic",
    "purpose": "offline_context_explanation",
    "claim": "关系标签不能代替独立访问事实；缺少 allowed 主体时不创造 baseline。",
    "tags": ["bola", "independent-access"],
    "applicability": {
      "shape": ["single_resource_path_get_json_object"],
      "actors": ["anonymous", "bearer"],
      "required": ["explicit_identity", "project_scoped_facts"],
      "refuse": ["facts_missing", "facts_conflict", "unsupported_shape", "session_unqualified"]
    },
    "source_refs": [{"kind": "doc_example", "id": "kw1-demo-origin", "version": 1,
      "author": "synthetic-author", "recorded_at": "2032-01-01T00:00:00Z",
      "qualification_ref": "kw1-demo-reuse-2"}],
    "example_refs": [{"kind": "doc_example", "id": "kw1-demo-positive", "version": 1}],
    "counterexample_refs": [{"kind": "doc_example", "id": "kw1-demo-sharing", "version": 1},
      {"kind": "doc_example", "id": "kw1-demo-conflict", "version": 1},
      {"kind": "doc_example", "id": "kw1-demo-uncertain", "version": 1}],
    "supersedes": {"scope": "reusable_synthetic", "knowledge_id": "kw1-demo-independent-access", "version": 1}
  }
]
```

`kw1-demo-origin/1` 是本节原创的“关系和访问独立”说明，不是文件路径或外部来源；其作者是虚构 actor。卡的逻辑 exact ref 还须带下表 content digest；`supersedes` 在此唯一键解析到 v1 的同一 digest，不允许跨域解析。v1 的会话说明缺项不能突破全局安全拒绝条件，v2 只是更正说明；两版都没有 verifier authority。

| content version | canonical UTF-8 bytes / SHA-256 |
| --- | --- |
| 1 | 980 bytes / `3b0592d3a5a7a1c547905345b06479ecf932439ee2c88b522e8586946e1c1a9a` |
| 2 | 1146 bytes / `a71460763a068bbc8cb0115dd48fc2a6ada94f359c686302db158b413b241abb` |

以下是**假设通过未来 W3 验证后的发布历史演算**，不是数据库输出或实际批准。全部 events 的 actor 为 `synthetic-reviewer`，role 按动作分别为 reuse_reviewer / rule_reviewer / publisher；每个事件绑定本域 knowledge_id、表列 version 及上表 digest。`kw1-demo-validation-1/2` 仅表示未来对该精确版本与本节例子执行检查的占位引用，当前状态 NOT_RUN，不可用于实际发布。

| event ID（前缀 kw1-demo-） | aware recorded_at | 精确版 / 前驱 | 假设决定与边界 |
| --- | --- | --- | --- |
| reuse-1 | 2032-01-01T01:00:00Z | v1 / 无 | 独立原创合成复用批准；范围仅本卡及所列合成例，窗口 [2032-01-01T01:00:00Z, 2032-02-01T00:00:00Z) |
| review-1 | 2032-01-01T02:00:00Z | v1 / 无 | accepted，引用 reuse-1、validation-1；此时仍未发布 |
| publish-1 | 2032-01-01T03:00:00Z | v1 / review-1 | 显式 publish，引用 review-1/reuse-1，窗口 [2032-01-01T03:00:00Z, 2032-02-01T00:00:00Z) |
| withdraw-1 | 2032-01-02T00:00:00Z | v1 / publish-1 | correction_requested；停止 v1 普通消费，保留原引用和 publish-1 |
| reuse-2 | 2032-01-02T01:00:00Z | v2 / 无 | 新内容单独复用审核，窗口 [2032-01-02T01:00:00Z, 2032-02-01T00:00:00Z) |
| review-2 | 2032-01-02T02:00:00Z | v2 / 无 | accepted，引用 reuse-2、validation-2；不继承 review-1 |
| publish-2 | 2032-01-02T03:00:00Z | v2 / review-2 | 显式 publish，引用 review-2/reuse-2；窗口 [2032-01-02T03:00:00Z, 2032-02-01T00:00:00Z) |
| disable-2 | 2032-01-03T00:00:00Z | v2 / publish-2 | 假设发现 source_qualification_uncertain，立即阻断及检查派生候选；原 publication 事件不消失 |

实际拿以上示例要求发布，因真实 reuse/review/validation/publish 证据全无，**必须拒绝**。在明确的假设状态演算中，v1 在 01-01 03:00 发布后可作为解释材料；到 withdraw-1 不可用。v2 到 disable-2 不可用，不能退回已撤回 v1。即使删去 disable 测试事件，02-01 00:00 精确边界也因窗口到期排除。没有新的代码运行验证这些未来状态。

额外拒绝演算：保持其余 content 不变而把 version 1 的 claim 改为“非 owner 能读即漏洞”，digest 改变，同域/version 冲突；只附 review-1 没 publish-1 则未发布；把 scope 改成别的项目、缺 reuse、unknown 分类、源 held/过期或将评分集标为来源，均在排序前拒绝。将 `expected_access` 或 secret 作为额外 content 字段同样拒绝；规则卡没有写入 M12 事实或凭据的字段。

## 7. 决定登记与验收映射

K1–K4 是本契约内部审阅项，**不是新增 ADR 或工作包**。它们将 roadmap 已有“分类/复用先批准”要求具体化；集中在本文足够，无需重写既有 ADR 台账。

| 待决定项 | 推荐与可选代价 | 批准责任、最晚检查点 / 当前证据 |
| --- | --- | --- |
| K1 分类/隔离 | §2 五类分域、候选与审核状态分开；仅独立合成材料可申请复用。替代是先只做 project scope，降低跨项目复用收益但仍需相同隔离 | Tech Lead 与操作者确认；任何依赖知识 migration/持久化前。**PENDING，无批准证据** |
| K2 版本/发布 | §3–4 精确不可变 content + 追加 review/reuse/publication 事件、有限发布窗口。替代是只做不可变文档发布包，仍需完整精确决定，不能 mutable latest | Tech Lead 批准兼容/事务/容量方案，操作者确认角色与显式步骤；依赖存储/发布实现前。**PENDING** |
| K3 复用/源失效 | 默认保留依赖、失效即排除，不靠副本或去 ID 续用；独立创作另审新材料。源断链无法证明独立性就拒绝；不自动将历史证据改为共享知识 | 操作者决定各份材料许可，Tech Lead 批准可实现的资格/生命周期机制；依赖 migration 与任何复用前。**PENDING**；DATA 采纳不代批 |
| K4 验证/消费 gate | 人类 publish 必须引用独立 W3 validation；W2 仅合成测试记录验证过滤，不抢先发布。替代可先仅提供审核预览、保持普通检索关闭 | Tech Lead 与操作者确认门槛，Review Project 独立审查实现；普通发布启用前。**PENDING**，本文无实际 rule publication |

后续批准记录须绑定本文 v0.1.0 的确切 commit、具体 K 项、采纳/修改/拒绝、条件、身份/角色、aware 时间及可核对来源；当前为空，不补签名。若这些决定改变 DATA/架构/安全模型，先停止依赖实现并回到原 ADR 决策边界。仍待原检查点处理的 INTENT（baseline/session/时限/配对）、PROPOSAL、EGRESS（外发/用量）、TASK（预算/observer/恢复）、PUBLIC 均未获本包批准；不借知识版本替它们作决定。

| RA-03 验收义务 | W1 可审阅证据 | 后续包及仍需实际验证 |
| --- | --- | --- |
| 分类、版本、来源、适用性、反例、审核人 | §§2–3、§6 完整卡与 exact digest；§1 对当前接口的差异 | W2 实际持久化/返回 exact refs；migration 前先完成 K1–K3 |
| 人工审核/独立发布、候选不自动提升 | §4 状态和资格、§6 追加历史/拒绝演算 | W2 消费只认合资格发布版本；W3 真实正反例验证与反馈审核，不自动发布 |
| 隔离、无许可/过期/未审/污染/脚本排除 | §§2、4–5 先过滤再排序、固定拒绝原因、生命周期依赖 | W2 两项目 canary、来源/关闭并发、注入与存储脚本、过期/权限负例；W3 held-out 泄漏与反馈负例 |
| 可重复检索、零匹配/重复/top-k/高排名不适用 | §5 输入输出义务与拒绝；本包没有 retrieval trace | W2 冻结确定性排序、边界值与精确结果；W3 规则匹配正负例。不能据 W1 退出整个 RA-03 |
| owner-denied/non-owner-allowed/共享/unknown/conflict | §6 新合成正反例与 §1 M12 实际语义 | W3 可执行验证和错误结果检测；RA-04 verifier 仍受 INTENT gate |
| 纠错、禁用污染版本、引用/历史不覆盖 | §§3–4、§6 新版/撤回/禁用示例 | W2 原子事件/审计/非空回退与不阻碍 W2 observation 清理；W3 依赖候选重评、原证据不变 |
| 无匹配拒绝、无越权/外发/训练 | §5 接口限制；本包仅文档，无数据库、网络或模型调用 | W2/W3 实测零能力 guards；release、执行、费用另批；冻结语料不动 |

## 8. 本次验证与限制

Ubuntu WSL（`6.18.33.2-microsoft-standard-WSL2`），项目 `.venv` Python **3.12.3**。验证不需要数据库，未导入 application、读取 `.env` 选库或连接任何 PostgreSQL；没有 Target/provider/credential 操作。

| 本次实际检查 | 结果与边界 |
| --- | --- |
| backend：`.venv/bin/python -m evaluation.ra01 verify` | **VERIFIED**，退出 0；freeze digest 仍为 `692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a`，approval 仍 PROPOSED_PENDING_REVIEW_AND_OPERATOR_APPROVAL；未再生成或修改语料 |
| 本文 JSON/来源标识/历史静态校验 | 2 张卡的独立字段/bytes/depth/node/引用、两版 canonical hash、8 条假设事件的 aware 时间/前驱/窗口一致；只是文档内部一致性，不是 W3 执行验证 |
| 两份变更文档的本地链接/heading anchors、源码 permalink | 核验 **86 个本地链接/anchors 及源码 permalink、15 个具名函数**；未访问外部页面。历史 roadmap 的 source links 仍核对其原 SHA |
| base-to-HEAD 范围及 `git diff --check` | 仅本文与 roadmap；无应用/schema/migration/tests/config/评测冻结变更，无额外未跟踪交付物；无空白错误 |

校验脚本/输出留在 `/tmp`，没有新增 repository tests 或 executable schema。结构/历史拒绝案例按 §§3–6 人工逐项审阅；不以此声称真实 publication/retrieval/rule verifier 通过。数据库回归与迁移检查未运行：本包无运行时代码或迁移，用户指定的是离线契约验证。

本包实现方自检不等于独立 Review Project PASS、K1–K4 批准、实际发布或 RA-03 COMPLETE。只有本文和 roadmap 进入新本地 commit；不 push、不建立 PR/Issue、不开始 W2。待独立审查完整 base-to-HEAD，并由责任人明确分类/复用决定后，依赖实现才可进入其门槛。

[C1]: https://github.com/runyiy/ai-api-security-platform/blob/4ac9b284029a515766d7532a0381be86bea7ce09/backend/app/services/research_context.py
[C2]: https://github.com/runyiy/ai-api-security-platform/blob/4ac9b284029a515766d7532a0381be86bea7ce09/backend/app/services/research_observation.py
[C3]: https://github.com/runyiy/ai-api-security-platform/blob/4ac9b284029a515766d7532a0381be86bea7ce09/backend/app/services/research_subject.py
[C4]: https://github.com/runyiy/ai-api-security-platform/blob/4ac9b284029a515766d7532a0381be86bea7ce09/backend/app/services/resource_access_resolution.py
[C5]: https://github.com/runyiy/ai-api-security-platform/blob/4ac9b284029a515766d7532a0381be86bea7ce09/backend/app/api/routes/resource_access_assertions.py
[C6]: https://github.com/runyiy/ai-api-security-platform/blob/4ac9b284029a515766d7532a0381be86bea7ce09/backend/app/services/resource_access_assertion_review.py
[C7]: https://github.com/runyiy/ai-api-security-platform/blob/4ac9b284029a515766d7532a0381be86bea7ce09/backend/app/api/routes/findings.py
[C8]: https://github.com/runyiy/ai-api-security-platform/blob/4ac9b284029a515766d7532a0381be86bea7ce09/backend/app/services/security_report.py
[T1]: https://github.com/runyiy/ai-api-security-platform/blob/4ac9b284029a515766d7532a0381be86bea7ce09/backend/tests/services/test_research_subject.py
[T2]: https://github.com/runyiy/ai-api-security-platform/blob/4ac9b284029a515766d7532a0381be86bea7ce09/backend/tests/services/test_research_subject_concurrency.py
[T3]: https://github.com/runyiy/ai-api-security-platform/blob/4ac9b284029a515766d7532a0381be86bea7ce09/backend/tests/api/test_research_subjects.py
