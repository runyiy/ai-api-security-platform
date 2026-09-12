# RA-05/W1：AI proposal protocol 与 provider/model 建议

**v0.1.0 · W1 P1–P6 / E1–E6 DESIGN ADOPTED for fake-only implementation.** The [exact adoption record](#w1-design-adoption-and-implementation-record) preserves provenance and conditions; the [adapter](research-ai-provider-implementation.md) was reviewed and integrated through [PR #150](https://github.com/runyiy/ai-api-security-platform/pull/150). Real account/key/material/retention/budget/egress permissions and real-provider acceptance remain unresolved. Broader W2 is incomplete; [current roadmap status](research-assistant-roadmap.md#5-固定阶段与依赖).

The comparison, P-labelled recommendations and code observations below are the original 2026-09-11 design snapshot at `d058cb82215c61b4c7811784abb24dc0ae069f80`, retained with their official-source dates. The fixed profile was subsequently adopted; historical pending-design requests and package-only stop instructions are retired. This cleanup does not refresh provider availability/prices, approve a new model, grant live egress or alter the adopted protocol. Original preparation/validation history is retained in [PR #148](https://github.com/runyiy/ai-api-security-platform/pull/148).

## 1. 规范、已采纳决定与当前实现

[架构](architecture-decisions.md)、[安全模型](security-model.md)、[产品契约 §§5–7](research-assistant-product-contract.md#5-请求形态支持矩阵) 和[冻结评测](research-assistant-evaluation.md)保持优先。本文中的字段、限制、配置名、接口及验收用例都是设计建议，不是现有 API/schema/运行能力。历史文档的 C/P 只描述各自基点，以下才是本 base 的静态检查结论。

| 约束/代码 | 本包沿用的边界 |
| --- | --- |
| [DATA D1–D4 采纳](research-assistant-adr-decisions.md#data-后续决定记录ra-02w1)，精确 `dcdb50fd36c098173c2580389577bb59558c0982` | 持有、导入、项目使用、复用、provider 外发分别核验；D3 的 payload 30天、hold单次30天、tombstone 90天、备份7天不自动成为 provider retention 许可。D4 不清洗旧 TestRun/M13，不伪造 source availability。 |
| [K1–K4 采纳](research-knowledge-contract.md#k1k4-后续采纳记录ra-03w2)，精确 `ec0f1eefef0eaf2a5bdf652f58ba2c0c6a083810` | mechanism/rule/project_evidence/counterexample/external_triage 分开；精确不可变版本，review/reuse/validation/publish 分开；来源失效即停止消费。`project_evidence` 即使 synthetic 也不直接进入模型。通用化必须独立新写并审核，不能只去 ID。 |
| [I1–I7 采纳](research-intent-contract.md#i1i7-后续采纳与-w1-实施记录)，精确 `6723cb5bfa62a10453f18f8158c25000a1997711` | 单 resource path、builder-compatible GET、JSON object、显式 anonymous/bearer；health120s / pair30s / intent300s、半开截止及更早依赖收窄，变化重建整对。AI 不补 health、access truth、mapping、revision、approval 或平台证据。 |
| [旧 AI protocol/schema](../backend/app/ai/provider.py)、[schema](../backend/app/ai/schemas.py)、[route](../backend/app/api/routes/ai_analysis.py)、[service](../backend/app/services/ai_analysis.py) | route 装配 `MockAIProvider`；`AIProvider.analyze(evidence=SanitizedFindingEvidence)` 是 Finding 后 advice。结果含 confidence/severity/reason/review/fix 文本，既没有上游 proposal，也没有 provider usage receipt；新 research TestCase/baseline 被 legacy gates 拒绝。 |
| [redaction](../backend/app/ai/redaction.py) | 敏感键替换、非 JSON placeholder、16000字符截取仍可能保留普通字段中的私人值；不能把整个 `SanitizedFindingEvidence` 发到云端。旧 mock/advisory 路由及历史 `FindingAIAnalysis` 保持兼容，不把全局 mock 换成 live provider。 |
| [离线 ModelCall/Ledger](../backend/evaluation/ra01/contracts.py) 与[评测核算](research-assistant-evaluation.md#4-分母硬门槛与核算) | `inclusive-input-output-v1` 是 synthetic evaluator 契约，不是现实 observer。10240 tokens / 2000 microusd 是冻结的合成 fixture，不是运行额度；B 和未来 task 总预算仍未批准。本包不改 evaluator、语料、hash 或阈值。 |

AI 永远不能执行、批准、确认 Finding、读凭据、任意 fetch、invoke shell/tools、发布规则或改 policy。采纳本文后也只有确定性流程和人工拥有原权限。M14 保持只读瞬态；M13/legacy 配对、指纹、review/report 不重写；M14-01–M14-06 不重开。

## 2. Proposal protocol P

### 2.1 版本与引用

独立协议名为 `ra-ai-proposal-input/1` / `ra-ai-proposal-output/1`，不兼容旧 `AIAnalysisResult`、RA observation、INTENT 或 evaluator JSON。双方只支持精确字符串 `1` 所对应的完整契约；不做宽松 semver 协商、降级、字段忽略或模型“修复”。语义、枚举、大小、canonicalization 或引用规则变更必须新协议版本及审查；prompt/model/settings/transport/usage/rate-card 各有独立版本。文档 v0.1.0 是本设计修订号，不是 wire version。

引用由可信消费者为**一次调用**生成；语法为 `(q|c|k|g)_[0-9a-f]{32}`，严格34个ASCII bytes，分别为 request、synthetic candidate、knowledge、gap。模型只能回显该请求 registry 内相同类型的引用；不是数据库主键、URL、path、hash、权限或跨调用稳定标识。即使字符串猜中另一个项目的真实 ID，也返回统一 `REFERENCE_UNAVAILABLE`，不泄漏存在性、标题或数量。

本地 registry 固定可信 project/context、context generation、精确来源 ID/version/digest、分类/资格/复用/发布/外发决定引用、有效期、allowed candidate-rule 组合及 gap 原因。它不发送给 provider；其中的 source 必须是独立 synthetic catalog 或合资格通用知识。不能为项目 evidence、access assertions、TestRun、health、plan、approval、Finding、秘密、held-out/oracle 分配模型引用。一个 handle 不能别名到多个来源；同一 registry 中相同来源版本只能分配一个 handle。不同类型建议可以引用同一 handle，数组内重复及相同建议组合仍拒绝。`q_…` 绑定最终获准 payload、registry 和配置版本的本地摘要；摘要不由模型给出，不能证明业务事实或授权。

### 2.2 精确输入白名单

所有字段必填，只有明示 nullable 者允许 null；所有 object 禁止额外字段。严格类型，无 trim、Unicode normalization、数字/string/bool 强转或截断。协议之外的 provider envelope 由 adapter 固定构建（§4），模型不选择它。

| 字段 | 类型、精确上限与语义 |
| --- | --- |
| root `protocol` | literal `ra-ai-proposal-input/1` |
| root `request_ref` | `q_…`，本次新随机 opaque handle |
| root `task` | literal `prioritize_review`；不请求事实判决、工具调用或执行步骤 |
| root `candidates` | 有序 array，0–8；每项仅 `ref`、`shape`、`actor_mode` |
| candidate `ref` | 唯一 `c_…`，仅已独立批准的 synthetic scenario descriptor |
| candidate `shape` | enum `single_resource_path_get_json_object`、`unsupported_shape`；不能夹带 URL、模板或对象值 |
| candidate `actor_mode` | enum `anonymous`、`bearer`、`unknown`；这是 synthetic descriptor，不泄露实际账号/session/credential |
| root `rules` | 有序 array，0–4；每项仅 `ref`、`claim`、`applicability_codes` |
| rule `ref` | 唯一 `k_…`；本地绑定 exact version 及完整 review/reuse/validation/publication/egress 资格 |
| rule `claim` | 1–512 UTF-8 bytes，独立合成、经审核的机制/规则/反例说明；纯文本，不含项目实例、事实、指令或链接 |
| rule `applicability_codes` | 1–4个互异 enum：`single_resource_path_get_json_object`、`anonymous`、`bearer`、`independent_facts_required`；由经审核卡投影，不能模型补齐 |
| root `gaps` | 有序 array，0–8；每项仅 `ref`、`code` |
| gap `ref` / `code` | 唯一 `g_…`；code enum `FACTS_MISSING`、`UNSUPPORTED_SHAPE`、`NO_APPLICABLE_RULE`，只来自获准 synthetic descriptor 的缺项，不外发真实项目缺项 |

至少一个 candidate/rule/gap 非空；无可用材料时本地拒绝，不为“请拒绝”付费调用。claim 是唯一内容文本字段，不能读 observation/body 自动摘要填入；512字节不足以容纳完整材料时拒绝该调用或由人工提交新的独立版本，不自动删去限制/反例。所有引用和数组保留顺序、拒绝重复。先做资格过滤和规则适用性，后做有界选择；不从全局 top-k 结果再移除秘密。

### 2.3 精确输出白名单

**不接收模型自由文本事实或理由。** 模型只能建议排序/选择已经合资格的对象，或指出已列出的缺项；消费者从固定模板渲染“建议复核”“需要补充输入”等文字。这是为实现确定性拒绝 invented facts 的刻意限制，代价是较弱的开放式解释。若以后需要自由文本，须另定新协议及事实校核门槛，不能静默开放 `reason`。

| 字段 | 类型与上限 |
| --- | --- |
| root `protocol` | literal `ra-ai-proposal-output/1` |
| root `request_ref` | 必须精确等于本次 `q_…` |
| root `status` | enum `suggestions`、`refusal`；不是 execution 或 Finding 状态 |
| root `suggestions` | 有序 array，0–4；每项仅下述7字段 |
| suggestion `suggestion_ref` | 按数组位置精确 `s_1`…`s_4`，无重复；只是本次输出序号 |
| suggestion `type` | enum `REVIEW_CANDIDATE`、`REQUEST_INPUT`、`EXPLAIN_RULE` |
| suggestion `candidate_ref` | 当前 registry 的 `c_…` 或 null |
| suggestion `rule_refs` | 0–2个互异本次 `k_…` |
| suggestion `gap_refs` | 0–2个互异本次 `g_…` |
| suggestion `reason_code` | enum `CANDIDATE_REVIEW_ONLY`、`INPUT_REQUIRED`、`GENERAL_RULE_ONLY` |
| suggestion `uncertainty_codes` | 1–4个互异 enum `NOT_EXECUTED`、`FACTS_MISSING`、`UNSUPPORTED_SHAPE`、`NO_APPLICABLE_RULE` |
| root `refusal_code` | null 或 enum `INSUFFICIENT_CONTEXT`、`NO_APPLICABLE_RULE`、`UNSUPPORTED_SHAPE`、`SAFETY_REFUSAL` |

固定组合，不是模型自行声明适用：

| type/status | 必需关系与消费者含义 |
| --- | --- |
| `REVIEW_CANDIDATE` | candidate非null，shape为单path GET、actor为anonymous/bearer；rule_refs为1–2，且每一 candidate/rule 配对在本地允许集合内；gap_refs为空；reason=`CANDIDATE_REVIEW_ONLY`，uncertainty恰为 `["NOT_EXECUTED"]`。只入待人工复核队列，不创建 intent/plan。 |
| `REQUEST_INPUT` | candidate=null，rule_refs为空，gap_refs为1–2；reason=`INPUT_REQUIRED`；uncertainty恰为 `NOT_EXECUTED` 加所引gap code的去重集合。按枚举表顺序编码；人类从本地模板看到缺项，不由模型提出任意问题或索取账号。 |
| `EXPLAIN_RULE` | candidate=null，rule_refs恰1，gap_refs为空；reason=`GENERAL_RULE_ONLY`，uncertainty恰为 `["NOT_EXECUTED"]`。展示被引用合资格卡的固定说明，无本项目事实结论。 |
| `status=suggestions` | 1–4项，refusal_code=null；同type/candidate/rule/gap组合不得重复。 |
| `status=refusal` | suggestions为空，refusal_code非null；不保留半份建议，拒绝不算safe。前三个context原因分别须有 `FACTS_MISSING`、`NO_APPLICABLE_RULE`、`UNSUPPORTED_SHAPE` 本地gap依据；`SAFETY_REFUSAL`允许保守拒绝，不能用拒绝隐藏已发生的费用。 |

### 2.4 解析上限与确定性消费

| 层次 | 同时生效的P上限；恰好上限仍须满足其他条件 |
| --- | --- |
| model input JSON | 16384实际UTF-8 bytes，含whitespace；root depth=0，最大depth=5；最多1024个JSON value nodes（key不另计） |
| model proposal JSON | 8192实际UTF-8 bytes；相同depth≤5、nodes≤1024；单suggestion按compact sorted-key UTF-8计≤1024 bytes |
| transport | 完整request envelope≤32768 bytes（含固定prompt/schema）；响应envelope body≤65536 bytes；HTTP response headers≤16384 bytes；完整JSON envelope另限depth≤16、value nodes≤8192；不能用proposal限制替代完整响应限制 |
| JSON规则 | 仅单个object；无BOM、无效UTF-8、unpaired surrogate、重复key、NaN/Infinity、尾随数据、代码围栏、压缩数据或外部schema引用。解析器在遍历前执行有界读取与depth/node约束；整次失败，无自动修复/拆包/重试。 |
| 本地错误 | 固定object仅含必填string `status="refused"`、`code`（下列本地错误enum），UTF-8≤256 bytes；不回显provider错误body、无效key/value、prompt、路径或异常repr。 |

消费者顺序：可信项目/调用配置与全部决定 → 当前源资格 → final payload/registry/version绑定与预算 → 发送前最终重验 → bounded provider envelope解析 → provider终态/拒绝/usage独立处理 → strict proposal解析 → 精确版本/request → 引用/组合/类型 → 当前生命周期与资格再次重验 → 固定模板只读展示。任何一个建议不合法整批拒绝；不从混合好坏输出捞取“好项”。

Provider `completed` / HTTP200 不是业务成功。Refusal、incomplete/max-token、content-filter、未知stop reason、多条结果、tool/function call item、意外annotation/URL、schema不符都不进入成功建议。已知reasoning item可由adapter识别并丢弃内容，只核算官方usage；不请求/保存/解析reasoning summary、encrypted reasoning或签名作为依据。既有执行证据、human approval、Finding confirmation、verified/access/session facts、plan digest、cost等字段均不在输出schema；放在额外字段、ref或enum里同样拒绝，不能交给另一个模型“鉴真”。

本地错误码固定为：`PROVIDER_DISABLED`、`CONFIG_UNAPPROVED`、`DATA_INELIGIBLE`、`SOURCE_UNAVAILABLE`、`INPUT_LIMIT`、`OUTPUT_LIMIT`、`MALFORMED_OUTPUT`、`VERSION_UNSUPPORTED`、`REQUEST_MISMATCH`、`REFERENCE_UNAVAILABLE`、`SUGGESTION_UNSUPPORTED`、`CONTEXT_CHANGED`、`PROVIDER_REFUSAL`、`PROVIDER_INCOMPLETE`、`TRANSPORT_DENIED`、`PROVIDER_TIMEOUT`、`PROVIDER_FAILURE`、`CREDENTIAL_UNAVAILABLE`、`USAGE_UNKNOWN`、`USAGE_INVALID`、`BUDGET_UNAVAILABLE`、`CANCELLED`、`AUDIT_UNAVAILABLE`。模型没有权指定这些本地状态；其refusal映射为`PROVIDER_REFUSAL`；经过校验的reason enum仅用于本地固定模板，不增补错误object字段。未知/外项目/错误类型ref统一`REFERENCE_UNAVAILABLE`。

### 2.5 新写的合成 JSON 示例

仅为本文命名空间，未创建数据库行、规则发布或调用。独立 synthetic catalog 描述一种单path GET复核对象，规则说明不能从关系推断权限；没有提供真实access truth或响应。假设本地允许该candidate/rule配对，并已单独批准输入中的每个字段供模型使用。

```json
{
  "protocol": "ra-ai-proposal-input/1",
  "request_ref": "q_11111111111111111111111111111111",
  "task": "prioritize_review",
  "candidates": [{"ref": "c_22222222222222222222222222222222", "shape": "single_resource_path_get_json_object", "actor_mode": "anonymous"}],
  "rules": [{"ref": "k_33333333333333333333333333333333", "claim": "Relationship labels do not establish access permission. Require independent facts before review.", "applicability_codes": ["single_resource_path_get_json_object", "anonymous", "independent_facts_required"]}],
  "gaps": []
}
```

```json
{
  "protocol": "ra-ai-proposal-output/1",
  "request_ref": "q_11111111111111111111111111111111",
  "status": "suggestions",
  "suggestions": [{"suggestion_ref": "s_1", "type": "REVIEW_CANDIDATE", "candidate_ref": "c_22222222222222222222222222222222", "rule_refs": ["k_33333333333333333333333333333333"], "gap_refs": [], "reason_code": "CANDIDATE_REVIEW_ONLY", "uncertainty_codes": ["NOT_EXECUTED"]}],
  "refusal_code": null
}
```

对同一输入，以下是可接受的保守拒绝；不是未收费的证明：

```json
{
  "protocol": "ra-ai-proposal-output/1",
  "request_ref": "q_11111111111111111111111111111111",
  "status": "refusal",
  "suggestions": [],
  "refusal_code": "SAFETY_REFUSAL"
}
```

把建议增加 `approved:true`、`executed:true`、`expected_access:"denied"`、`finding_status:"confirmed"` 或自由文本`reason`，一律`MALFORMED_OUTPUT`。换成其他请求/项目的合法语法handle仍拒绝；不是字符串符合regex就通过。

## 3. Provider/model 比较与推荐 P

全部以下官方资料实际访问于 **2026-09-11**。价格是当日公开标准text API的 **USD / 1M tokens**，不是账户报价、税后结算、特价承诺或实测。大context是厂商容量，不是本项目可用额度；本提案仍≤4096 input / ≤1024 total output。只比较两个直接provider的三个模型，不接中转平台；三个选项均未做账户可用性或性能验证。

| 候选 | 官方容量、协议与推理 | 标准 input / cached-read / output；cache-write | 本任务取舍 |
| --- | --- | --- | --- |
| **OpenAI `gpt-5.6-terra`（推荐）** | 1,050,000 context，922,000 max input，128,000 max output；Responses、Chat Completions及structured outputs；effort none/low/medium/high/xhigh/max，medium默认。[模型页](https://developers.openai.com/api/docs/models/gpt-5.6-terra) | **$2 / $0.20 / $12**；write为普通input的1.25倍，即$2.50。>272K input时整个请求input×2、output×1.5；本提案不会到该区间。[同页定价](https://developers.openai.com/api/docs/models/gpt-5.6-terra) | 平衡档位、显式细分usage、可明确禁止cache写入；推荐low是设计起点，未证明1024总输出足够或质量优于替代。 |
| OpenAI `gpt-5.6-luna` | 相同1,050,000/922,000/128,000上限、Responses及structured outputs、相同effort集合；官方定位cost-sensitive。[模型页](https://developers.openai.com/api/docs/models/gpt-5.6-luna) | **$0.20 / $0.02 / $1.20**；write=$0.25；同样长context倍率。[同页定价](https://developers.openai.com/api/docs/models/gpt-5.6-luna) | 明显更低单token价格；对本任务引用选择/拒绝质量没有证据。可由操作者改选，不能失败后自动切到Luna。 |
| Anthropic `claude-sonnet-5` | Claude Messages API；1M context、128K max output；adaptive thinking默认开启，可disabled；manual budget_tokens及非默认sampling设置会400。`max_tokens`硬限thinking+text。[模型页](https://platform.claude.com/docs/en/models/sonnet-5/overview)、[版本行为](https://platform.claude.com/docs/en/models/sonnet-5/whats-new-sonnet-5) | **$2 / $0.20 / $10**；5分钟write=$2.50，1小时write=$4。[模型定价](https://platform.claude.com/docs/en/models/sonnet-5/overview) | output单token价较低；不同tokenizer不能推导相同文本更便宜。需独立Messages envelope/auth/usage映射；文档retention口径需核实。 |

**结构化输出限制。** OpenAI Responses采用`text.format`的`json_schema`/`strict:true`；仅支持JSON Schema子集，root object、所有字段required、每个object `additionalProperties:false`。Refusal可能不符合schema，max output可能产生incomplete；形状合规不证明引用或事实正确。[OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)。Claude采用`output_config.format`；不支持numeric min/max、string length及一般array上限，SDK可能移除限制并写入描述；refusal可HTTP200且收费、max_tokens会截断。schema grammar可缓存24小时，额外格式提示计input。[Claude Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)。所以provider schema只作第一层约束，本地完整byte/类型/组合校验不可省，也不采用SDK隐式schema弱化。

**用量可见性。** OpenAI Responses返回`input_tokens`、`output_tokens`、`total_tokens`和cache-read/write、reasoning明细；`max_output_tokens`覆盖可见及reasoning生成。[Responses reference](https://developers.openai.com/api/reference/resources/responses/methods/create)。Claude Messages返回普通input、cache-read、cache-creation和output总量，output包含完整thinking，不能数summary字符估算；未证明有可独立使用的完整reasoning-token数时将该明细记null，已知output总数仍可核销。[Messages reference](https://platform.claude.com/docs/en/api/messages/create)、[Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)。映射见§6，不套用相同字段名的直觉。

**缓存。** GPT-5.6的`prompt_cache_options.mode="explicit"`且无breakpoint时不读/写prompt cache；默认implicit会写，read/write是input的互斥子集。启用缓存时最小可缓存prefix为1024可见input tokens，`ttl="30m"`是最近写入/复用后的最短时间，不能当最大删除期限。[OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)。Claude的普通input不含两类缓存input，三者才是总input；5分钟及1小时写入须分费率，不可再加cache_creation明细重复计数。[Claude Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching?s=09)。本次推荐禁用prompt cache及应用分析cache；W3另审隔离，任何未来cache key都不是许可。

**训练与保留。** OpenAI API默认不用于训练，除非明确opt-in；普通abuse日志可含prompt/response，通常至多30天但有法律/防危害例外；`store:false`不等于ZDR，ZDR/MAM须事先获批。Responses默认/`store:true`有应用状态保留；prompt cache可能保留KV至24小时，30m TTL不是这一上限。[OpenAI Data controls](https://developers.openai.com/api/docs/guides/your-data)。拟禁用training/data-sharing opt-in、store/background、文件、对话续接和cache；仍须操作者接受实际账户适用的保留条款。

Claude当前平台页称未明确允许不训练，ZDR需逐organization安排，具体feature可能保留技术artifact；Sonnet 5支持有ZDR协议的组织。[API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)、[Sonnet 5说明](https://platform.claude.com/docs/en/models/sonnet-5/whats-new-sonnet-5)。该平台页的“不默认保留对话内容”与商业privacy页的“通常30天内删除”不能简单合并成零保留保证；后者还列UP/法律等更长保留例外。[Commercial retention](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data)。操作者必须按实际合同/组织核实，未核实保持pending；本包不联系厂商或设置账号。

**推荐理由及未知。** Terra有文档明确的完整output上限、细分usage和显式无cache路径，适合作为一个有界adapter的设计目标；Luna保留为费用优先替代，Sonnet 5为不同provider替代。此判断是从接口/边界复杂度作出的工程推论，不是模型准确率、速度或节省实测。所有选项都可能拒绝安全研究相关请求；接受拒绝，不换模型规避。Terra页面只列`gpt-5.6-terra`快照名：固定该ID及本地配置/官方资料快照，不能编造dated snapshot或保证字节级可重现。模型/账户可用性、变更通知、实际schema接受、hidden input overhead和本地总量上限可行性仍待核验；缺任一必要证明就不启用。没有默认fallback、provider自动路由或重试。

## 4. 外发数据和独立 transport P

### 4.1 默认关闭与数据资格

建议`provider_calls_enabled=false`，缺配置也为disabled；在数据读取/secret resolution/DNS之前拒绝。只有明确的operator运行许可同时绑定provider/model/settings、account、项目/context、逐字段数据清单、用途、有限时间及数值预算，且Tech Lead批准协议/transport后，未来实现才可考虑发送。CI永远fake，本文真实调用/费用上限为0。

可外发内容仅为§2输入、固定无私人内容的prompt/schema以及必要provider配置。合资格generalized材料在本版具体指**独立新写的 `synthetic_authored` reusable机制/规则/反例**，同时满足K1–K4和单独egress许可；公开可读、项目内已审或“已脱敏”均不够。不外发仓库文件、私有项目名称/规则/标题/数量、真实Target/origin/path/query、资源/身份值、access truth、observation/TestRun/M13/health/approval/Finding/report、external triage、held-out/labels或credentials。也不把其摘要、hash、embeddings或更名副本作为出口。

在读取、组包、等待后最终发送以及输出展示前，按可信project/context重验归属/generation、active状态、精确source版本、有效期、hold/delete/quarantine/revoke、review/reuse/publish/egress决定及污染状态；`now < earliest_expiry`，clock回退/次序异常拒绝。close、Target转移或来源撤回不能通过删source引用/复制正文恢复；已有不可变建议只作受限历史，不继续消费。最后可发送处与同域lifecycle更新协调，不能持事务跨网络等待；具体锁/代际实现留给获批代码，W1需fake证明late-change阻断，W2实现可信预算/observer协作。

所有源文本、导入、下载资料及模型输出视为不可信数据。固定prompt把输入标为数据，禁止把claim拼成system指令；只用结构化编码。值级资格审核、secret/PII canary和指令污染检查在发送前执行；检测不是完美注入识别器，最终保护是无工具、严格输出、引用/组合检查和零authority。恶意材料隔离为不可用，不把原文写进隔离日志或送另一个provider检查。

### 4.2 固定边界和局部 POST 例外

下表整体是 **EGRESS待批准的局部provider POST例外**。不能通过修改Target `allowed_methods`、public flag、Scope、AuthenticationContext或既有Gateway bypass来实现。provider公网服务出口与Target public execution是不同边界；`external_public_authorized`继续runtime blocked，Target自动执行继续GET-only。当前规范未获此例外批准，故此文不授予发送权限。

| 组件 | P固定值/行为 |
| --- | --- |
| 唯一destination/method | **`POST https://api.openai.com:443/v1/responses`**，path精确，无query/fragment/userinfo、redirect或替代host。仅native Responses，不用Chat Completions兼容层。Claude若被改选须另批`POST https://api.anthropic.com:443/v1/messages`及独立映射，当前不在allowlist。 |
| request controls | `model=gpt-5.6-terra`、`reasoning.effort=low`、`max_output_tokens=1024`、`stream=false`、`background=false`、`store=false`、`truncation=disabled`、`tools=[]`、`tool_choice=none`、`parallel_tool_calls=false`；标准service tier，不自动升级priority/flex/batch。不提供conversation/previous_response_id、文件/图片/音频、MCP、web/file search或任何server tools。 |
| cache/schema | `prompt_cache_options.mode=explicit`且任何content均无breakpoint；不设legacy retention或cache key。`text.format`固定json_schema/strict/name与§2对应schema；schema/prompt为受审本地常量，不从provider获取。账户策略如果不能满足明确无cache要求则保持关闭。 |
| headers/secret | 固定Content-Type/Accept JSON、Accept-Encoding identity；仅独立provider认证组件可加入Authorization Bearer；可有固定非秘密client版本。额外project/org路由header只来自已批准静态account配置；拒绝caller/model任意header和环境代理。 |
| DNS/IP/TLS/peer | 固定hostname可信解析，最多8个地址；任何loopback/private/link-local/metadata/reserved或混合禁止地址集合均拒绝。连接pin到通过检查的地址，TLS certificate/SNI仍校验固定hostname，首次发送字节前验证实际peer；无再解析竞态/隐式proxy。peer能力无法证明则禁用。 |
| 时间/并发/速率 | 每call绝对monotonic deadline≤30秒，覆盖预算等待、DNS、连接、发送、全部读取；connect≤3秒、read-idle≤5秒并受更短剩余deadline限制。最多一个active call/账户/部署，调用开始间隔≥1秒并遵守更严格provider限制；总case model wall≤60秒，最多2次独立获准call的设计上界，W2才实现。 |
| 完整响应 | §2.4 byte limits独立于Content-Length；流式有界读取但不用API streaming；拒绝unexpected compression、非JSON、redirect、超header/body/depth/node预算。最多一个assistant output message/一个output_text；无partial salvage。 |
| failure/stop | 禁用SDK、HTTP和应用自动retry；无跨host/IPv4-v6发送重试、fallback或重定向。429/5xx/超时/取消/证书/peer/审计失败停止；发送后失败保留unknown delivery/usage。global/provider/account/project kill在最终发送前重验；无可信共享协调或W2预留receipt时live拒绝。 |

本地只能留minimized decision/usage元数据：scoped call ID、版本、决定引用、时间、限额、counts、固定error、受限provider request ID；不留raw prompt/response/error body、secret、reasoning或完整headers。建议新provider日志最多90天、可用建议不超过其源最早截止且最多30天，提前delete/hold立即停止消费；这是独立待批的retention选择，不套用M13。未核销的无内容费用记录不得因建议过期而释放预算；到期移交有界人工对账/停止该账户，不无限追加未知记录。具体ledger保留/容量在W2前批准。

## 5. Provider 账号与凭据 P

不复用Target `TestIdentity`、`CredentialBinding`、bearer stored-secret表或Target encryption key，不把provider账号登记成Target。建议由操作者单独持有受限API服务账号/项目、独立费用账户；模型不能看到account管理凭据或自己注册/充值。

| 决定面 | 推荐及失败边界 |
| --- | --- |
| 长期存储 | 操作者管理的本地secret文件，位于受控加密存储，0600且仅受信服务用户可读，容器/worker只读挂载。application配置仅存固定逻辑`provider_secret_ref`和版本；映射到预配置文件/descriptor，拒绝请求指定任意路径。不是本包要新建的文件，不引入Vault/KMS或DB secret表。 |
| 短期读取 | 仅provider transport的认证组件在全部资格与预算通过后读取exact配置版本；模型、context builder、日志、API返回、proposal、Target resolver不可读取。禁止shell参数、URL、prompt、repo/.env、自动环境发现或fallback key；受限内存，不声称Python可靠零化。 |
| 配置绑定 | 本地不可变config revision绑定provider/model/secret_ref+version、account/project routing、data/retention许可及费率版本。secret_ref为operator配置registry中的1–64 ASCII opaque名；不是secret内容或网络URL。key读取最大8192 bytes，空值、CR/LF/NUL或不合法header bytes拒绝，不回显。 |
| 轮换/撤销 | 操作者创建新key/version→暂停新增call→对旧in-flight/unknown保留原account/version→审核更新config→受控恢复。发送前version/active再验，旧排队调用失效；不自动取latest或换account。撤销不能证明已送请求无收费。 |
| 错误/事件 | 缺文件/权限错误/未知版本/撤销/401/403固定`CREDENTIAL_UNAVAILABLE`或`PROVIDER_FAILURE`，不自动检索其他credentials、续期、登录或发送key测试请求。只记录逻辑ref/version；疑似泄漏停用并由操作者处置，不能在日志复述。 |
| 运维条件 | 账号可访问性、最小权限、storage/backups/swap/core-dump/调试采样控制与rotation可行性须operator批准并在实际部署验证；未满足只允许fake。实际account quota/retention/ZDR不从公开docs推定。 |

这是对provider认证域的独立设计，不改变Target请求认证必须经AuthenticationContext的规范。Tech Lead批准隔离方式，操作者另批账号、存储和真实key使用；本包没有读取任何现存key、配置secret或账户。

## 6. Usage、预算与未来 W2 接口 P

### 6.1 官方用量映射与算术

统一定义`I=全部input`、`O=全部generated output（含reasoning）`、`C=cache read input`、`W=cache write input`、`R=reasoning output子集`。**总token=`I+O`**，embeddings=0；byte数、discount和cache命中不减少该总量。计数必须strict非负整数（bool/float/负数拒绝），未知为null；`C+W≤I`、已知`R≤O`。不要向冻结evaluator直接提交live结果，未来映射是新版本边界。

| Provider mapping（拟议版本） | 官方字段→统一量；不可重复计数 |
| --- | --- |
| `openai-responses-2026-09-11/1` | `I=usage.input_tokens`；`C=input_tokens_details.cached_tokens`；`W=input_tokens_details.cache_write_tokens`；`O=usage.output_tokens`；`R=output_tokens_details.reasoning_tokens`；验证`total_tokens=I+O`。普通input=`I-C-W`。依据[Responses字段](https://developers.openai.com/api/reference/resources/responses/methods/create)及[官方缓存成本公式](https://developers.openai.com/api/docs/guides/prompt-caching)。 |
| `claude-messages-2026-09-11/1`（替代，未选择） | `I=usage.input_tokens+cache_read_input_tokens+cache_creation_input_tokens`；后两项分别为C/W；`O=usage.output_tokens`包含thinking。`cache_creation.ephemeral_5m_input_tokens`及`ephemeral_1h_input_tokens`仅拆分W，不能再加进I；R没有受核验独立数值则null，不能从summary估计。依据[缓存字段](https://platform.claude.com/docs/en/build-with-claude/prompt-caching?s=09)、[Messages](https://platform.claude.com/docs/en/api/messages/create)及[thinking计费](https://platform.claude.com/docs/en/build-with-claude/thinking)。 |

Terra的普通input/read/write/output费率依次为 **2 / 0.2 / 2.5 / 12 USD每百万tokens**。金额用Decimal或整数有理数计算，逐call向上取整到microusd，不用binary float；`1 USD=1000000 microusd`，所以一token的microusd费率数值与USD/百万费率数值相同。`cost=ceil((I-C-W)×2+C×0.2+W×2.5+O×12)`。Reasoning已含O，绝不能再加R×12。

费率快照建议ID `openai-terra-usd-standard-2026-09-11/1`，绑定model、standard tier、text-only、地区/账户适用性、currency=USD、每个cache类别与长context阈值、访问日期、官方来源、人工批准、生效/失效时间；变价/换model/tier/tokenizer必须新version，不改旧账。公开price计算是**token-rated cost**，不能冒充invoice结算；税、折扣、账户credit、外汇及其他费用未核实时分别unknown，不能说总账单已硬限。本提案禁止额外付费tools/服务、batch/priority/长context档，不预期cache折扣。

### 6.2 发送前最坏预留及新写合成例

预留先于任何provider请求字节，含未知是否送达的尝试。设计上界`Imax=4096`、`Omax=1024`，token预留5120。虽然推荐无cache，为不依赖cache命中/配置侥幸，费用示例用最大的允许input类别 **$2.50/MTok**：

`reserve=ceil(4096×2.5+1024×12)=22528 microusd=$0.022528`。

两次调用的**纯合成算术边界**为10240 tokens / 45056 microusd。它不是冻结evaluator的2000 microusd fixture，也不是本包或未来task获批预算。实际运行总额、币种、期限与税费范围必须operator另行决定；缺值不发送。

下面是未来W2 receipt的**非执行性草图**，不是已存在schema/API；金额/计数都是本包独立编写的synthetic values。所有字段必填；receipt的ID/version字段是本地opaque字符串（1–64 ASCII），时间是可信服务RFC3339 UTC，不由模型给出。

```json
{
  "contract": "ra-provider-reservation-example/1",
  "measurement_kind": "synthetic",
  "call_ref": "doc_call_1",
  "attempt": 1,
  "account_ref": "doc_account",
  "config_version": "doc_config_1",
  "usage_mapping": "openai-responses-2026-09-11/1",
  "rate_card": "openai-terra-usd-standard-2026-09-11/1",
  "currency": "USD",
  "input_limit_tokens": 4096,
  "output_limit_tokens": 1024,
  "reserved_tokens": 5120,
  "reserved_cost_microusd": 22528,
  "delivery": "not_started",
  "usage_state": "pending"
}
```

| 独立合成终态 | 已知用量/费用 | 核销与消费者行为 |
| --- | --- | --- |
| completed，无cache | I=3000，O=600（R=200），C=W=0；总3600；cost=6000+7200=**13200** | 扣3600tokens/13200microusd，释放1520/9328。只有proposal及所有资格也通过才展示建议。 |
| completed，**仅用于未来cache mapping测试** | I=3000，C=1000，W=500，普通I=1500；O=600（R=200）；总3600；cost=3000+200+1250+7200=**11650** | 释放1520tokens/10878microusd。此组合不许可实际cache；推荐nocache配置若收到非零C/W，核销可信已知用量并以配置不符停止后续调用。 |
| malformed/refusal/failed，但final usage同首行且delivery已明确 | 总3600，cost=13200 | 与成功同样扣费；不因为建议被拒绝就释放全部预留。 |
| 取消，已明确final usage | I=2500，O=250（R=150），C=W=0；总2750；cost=5000+3000=**8000** | 扣2750/8000，释放2370/14528；不给建议，不声称取消撤回已发送工作。 |
| 已证明pre-send取消/失败 | 可信observer证明从未跨发送边界，实际model tokens/cost=0 | 释放全部5120/22528；不是从缺少response推导zero。 |
| send后timeout/断连/取消，或delivery不明 | 即使有partial counts也不是final；actual totals=null | **保留全部5120/22528**，record known subtotal但不提前释放。人工对账证明最终用量/未送达之前不重试，不把时间过去或lease expiry视为免费。 |
| usage字段缺失、类型错、子集越界、total不符 | unknown或invalid，非zero | 保留全部；停止受影响account/task新增call，保留独立observer证据，不修补/猜测字段。 |

建议nocache profile只有官方terminal usage中的零C/W才算已知零；字段缺失本身不是零。已知O但缺非计费R明细时标R=null、保留O，不重复预留reasoning。若必需的I/O/cache费率明细缺失，cost仍未知，按上表保留完整预留。实际超过声明cap须记录真实已知超额并报失败，不能clamp到cap来造成功。

```json
{
  "contract": "ra-provider-reconciliation-example/1",
  "measurement_kind": "synthetic",
  "call_ref": "doc_call_1",
  "state": "cancelled",
  "delivery": "unknown",
  "usage_state": "unknown",
  "input_tokens": null,
  "output_tokens": null,
  "cached_input_tokens": null,
  "cache_write_tokens": null,
  "reasoning_tokens": null,
  "actual_total_tokens": null,
  "actual_cost_microusd": null,
  "retained_reserved_tokens": 5120,
  "retained_reserved_cost_microusd": 22528
}
```

**硬cap边界。** 同一task/account的`known_spend + unresolved_reservations + new_worst_case`必须同时≤获批token及cost cap；还须有call count和deadline余额。合成cap=45056时两个22528预留恰好通过，第三个失败；cap=45055时第二个失败；token cap=10239同理拒绝第二个5120。并发双方不能先读同一余额再各自发送，W2必须原子预留并记录可信发送事件。未知用量默认暂停该account/task，即使算术剩余够用也不自动继续。人工补批额度不自动解除unknown或重放旧call。

### 6.3 可行性缺口与 W2 分工

公开API文档支持total-output硬限；它**没有证明本地4096 input硬限已可实现**。需对最终prompt+schema+envelope渲染开销及provider hidden/system tokens建立经过核验的保守上界，并固定tokenizer/counting版本；仅字符÷4、历史均值、模型context大小或请求byte cap都不是token证明。未能在发送前证明I≤4096、output cap确含所有生成、费率及额外收费有界，就保持live disabled。不能为检查token自动调用额外token-count endpoint；如以后确需该POST，先扩展已审transport/数据/计量清单，不借本页许可。Provider异常超生成/变价也不能被本地网络断连消除，实际账户侧保护只能作额外控制，不能宣传绝对账单保证。

W1未来可定义并fake测试三个接口义务；**本包不实现，W2不提前开始**：

| 未来接口职责 | 约束与责任 |
| --- | --- |
| `prepare_proposal` | W2确定性升级/有界检索构建§2 payload及本地registry，先规则再检索再模型；无资格/无必要/无预算返回固定缺项。不是模型自调度。 |
| `reserve_call` / `mark_send_started` | W2可信预算/observer原子创建唯一call+attempt、完整payload/config/rate绑定、最坏预留与send标记；adapter没有有效receipt即拒绝。标记后崩溃即保守unknown，不能因未记录response重发。存储/事务/ledger/恢复的设计及实现留在W2/适用TASK门槛。 |
| `propose_once` / `reconcile_call` | W1 adapter一次有界传输返回经过识别的provider终态、受限JSON bytes及usage投影；W2独立observer逐call核对发送、结果、费用集合，幂等核销。重复callback不扣两次，冲突不覆盖；遗漏结果仍保留已观察成本，不能从proposal数组反推调用数。 |

没有background worker、自动重试、跨case共享额度或任何provider/Target调用来自这些名字。W1 fake receipt只能用于fake transport，不可伪造live许可。W3缓存只考虑不可变分析，key至少含project/data class、source/context/rule/prompt/schema/provider/model/settings/usage版本和payload digest；每次读取重验实时资格，不能缓存approval、mutable access truth或执行权限。旧Finding advice不走这个缓存或proposal入口。

## 7. 决策登记 P

P1–P6/E1–E6仅为既有PROPOSAL/EGRESS的本页审阅项，不是新ADR、package或milestone。设计推荐已按后续记录采纳；下表保留原选择、替代与条件，不重开设计审批。实际材料/账号/retention/费用许可仍独立。真正决定须引用本文件版本和exact commit、具体项、采纳/替代/拒绝及条件、真实责任人/角色和aware时间；此处不填签名或批准ID。Tech Lead协议/传输决定不能代替operator模型/数据/账号/预算决定。

| 项 | 推荐 | 替代及代价 | 决定者 / 未决条件 |
| --- | --- | --- | --- |
| P1 协议/兼容 | §2独立input/output v1，旧Finding mock保留 | 合并旧advisory易混淆上游和证据，不采纳；仅规则/Mock仍可保留 | Tech Lead；字段/版本/legacy分派批准前无adapter代码 |
| P2 类型/事实拒绝 | 3种引用+代码建议，无模型自由文本 | 新版开放解释更灵活，但需额外事实校核，当前不纳入 | Tech Lead批准consumer；operator确认解释/拒绝可用性 |
| P3 引用/资格 | 单call opaque registry、精确版本/组合、全程重验 | 稳定全局IDs简单但泄漏/重放风险，不采纳 | Tech Lead；source/close/transfer竞态和最终消费可实现性待验证 |
| P4 schema/容量 | §2所有字段、8192 output / 16384 input与独立transport限额 | 更大上限增加成本和解析面；更小可能过度拒绝 | Tech Lead；fake边界与完整schema兼容证据待实施 |
| P5 下游authority | 待复核建议，不造intent/plan/fact/approval/Finding | 自动执行违反规范，无可采纳替代 | Tech Lead；逐禁止能力零调用fake证据待实施 |
| P6 状态/拒绝 | 固定codes、无partial salvage、unknown不算safe | 自由文本错误易回显秘密；模型自评不可靠 | Tech Lead + operator；固定文案可用性待审 |
| E1 模型 | Terra / Responses / low / fixed ID / 无fallback | Luna费用优先；Sonnet 5原生Messages；均需自己冻结配置/计量 | **Operator选定model/provider**；Tech Lead确认技术兼容；无实测质量/账户资格，不能称已选择生效 |
| E2 数据/retention | 独立synthetic/generalized白名单、store/background/cache关闭 | 只用规则/Mock避免外发；私有数据不是本版替代 | **Operator逐材料批准外发/retention**；Tech Lead批准minimization/lifecycle；OpenAI账户适用条款及Claude文档口径待核实 |
| E3 transport/POST | §4固定OpenAI POST、peer绑定、无redirect/proxy/tools/retry | 不开provider；若换vendor须新固定边界，不复用Target许可 | **Tech Lead批准局部POST例外**；operator批准实际服务外发；未获批则默认关闭 |
| E4 account/key | §5独立账号/secret reference及版本轮换 | 外部secret manager需额外部署；复用Target credentials不采纳 | **Operator批准account/storage/key使用**；Tech Lead批准认证隔离；无真实key或配置验证 |
| E5 accounting/caps | §6总input/output映射、保守预留、unknown全保留 | 少报reasoning/cache或估算当actual不可采纳 | **Tech Lead批准计量/receipt协议**；**operator批准总token/cost/currency/time和费率**；input overhead、invoice边界与可信observer证明待补 |
| E6 cache/包顺序 | W1无cache、W2预算/升级、W3隔离与另批synthetic smoke | 提前开cache可能降成本但新增retention/隔离义务 | Tech Lead + operator；不提前W2/W3，不由本建议批准smoke/benchmark或消费 |

## 8. 未来 fake-only 验收表 P

这些是依赖实现的验收要求，**未运行、未宣称PASS**；fake应提供独立网络/secret/副作用计数和可信clock/observer替身，不能只断言自报`safe=true`。所有provider测试用新写synthetic canary，不读held-out、不发真实请求。W2/W3行仅列未来责任。

| 用例 | 必需断言 / 所属工作 |
| --- | --- |
| 正常suggestion / REQUEST_INPUT / EXPLAIN_RULE / refusal | exact input→fake response→正确固定模板/顺序；无intent/plan/fact/Finding写入，provider/Target真实网络0。W1 |
| version/type/shape错误 | 旧advisory、未知协议、缺/额外key、bool替integer、duplicate JSON key、BOM/surrogate/NaN/尾随数据/围栏拒绝；不修复或取部分。W1 |
| 字节/结构边界 | input16384/16385、proposal8192/8193、suggestion1024/1025、request32768/32769、response65536/65537、headers16384/16385；proposal depth5/6、nodes1024/1025、envelope depth16/17、nodes8192/8193、candidates8/9、rules4/5、gaps8/9、suggestions4/5、claim512/513；UTF-8多byte和谎报长度。结构单项通过不豁免其他cap。W1 |
| 引用/组合隔离 | foreign/unknown/held-out canary、上次q/ref、同ref换version、重复suggestion、非法candidate-rule、wrong-kind引用均拒绝且不泄漏外项目存在性。W1；未来cache同样W3 |
| invented facts/authority | 增加approved/executed/evidence/plan_digest/confirmed/access truth/cost、把这些塞入ref/enum、返回tool item或URL，均拒绝；credential/execute/approve/confirm/policy/publish/shell/fetch计数全0。W1 |
| injection与egress canary | claim/ordinary field包含指令、合成secret/PII、项目evidence摘要、隐藏URL；未经资格数据不进fake captured payload、logs/errors/cache/临时文件。泄漏canary不靠redaction键名；纯schema合法也不能越权。W1 |
| 默认关闭/许可不足 | 无enable/config/egress/account/budget许可时，secret resolve、DNS、connect、send计数都0；Target批准不改变结果。W1 |
| 生命周期/时间 | close/transfer/hold/delete/quarantine/revoke/source/publication版本变化发生于读取后、rate等待后、最终send前及返回编码前；半开expiry等于边界和clock回退拒绝。已经发送则核算且不给stale success。W1接口；W2并发实现 |
| transport | fake wronghost/port/path/method、redirect、mixed DNS、metadata/private/loopback、rebinding/peer/TLS mismatch、proxy/env注入、compression、慢滴流；有界拒绝，无第二次请求。Target GET-only/public-blocked回归另保持。W1 |
| provider终态 | HTTP200 refusal/max_output/incomplete、429/5xx、empty/multiple/tool输出、未知stop reason；不按HTTP200成功，不抄raw error，不fallback/retry。W1 |
| timeout/cancel/crash | pre-send可证明0与post-sendunknown区别；read-idle及30秒绝对deadline，60秒case上限；已发不能取消费用。W1返回receipt；W2真实observer/恢复 |
| usage与预算 | §6所列终态、cache/read/write/reasoning子集、negative/bool/overflow/missing/duplicate callback；5120/22528及两次边界，差1即拒绝，并发不双花；omitted call仍可被observer发现。W2 |
| version/cache/历史 | config/model/rate/secret变更停旧排队call；无隐式最新版本。跨project/privacy/source/model/prompt/schema cache隔离，过期/污染不可复用，旧advisory及M13不重标。W1分派、W3 cache |

## 9. 本包验证与停止点

本次仅三份Markdown：本文、roadmap、ADR交叉引用/当前状态。使用environment-cleared `python3 -I` 标准库检查相对链接/anchors、Markdown围栏、全部合成JSON严格解析及字段/引用/类型/组合/大小、Decimal费用与预留/释放算术；静态核对代码定义与文档角色，检查本页官方来源的实际内容和访问日期。结果：三份文档的 **90** 个本地链接/anchors、**5** 个JSON示例（其中3个protocol示例实际为561/427/179 bytes）、**11** 个样例检查器反例控制、Decimal核算及恰好/差1预算边界均通过；这11个控制只验证文档样例检查器能识别所构造错误，不是adapter测试。14个公开官方引用的主张已人工核对，访问日期均为2026-09-11。检查脚本和结果保存在本次自有临时目录 `/tmp/ra05-w1-docs-i5co6k9j/`；临时artifact不属于仓库交付，也不替代独立review。

`git diff --check`及documentation-only文件范围检查通过。未import application，未运行DB-backed测试、backend suite、migration、evaluation命令或held-out数据读取；没有依赖/应用/测试/CI变更。公开官方文档研究只发送通用API主题查询和文档GET，不发送repository/private材料到provider API；没有账户/credential inspection、付费调用、部署或Target请求。

本包不批准模型/数据/费用，不签署独立Review Project PASS，不宣告W1或RA-05 COMPLETE。创建feature branch本地commit后 **STOP**；不push、PR、merge、adapter实现、RA-05/W2或任何新package。等待独立Review Project审阅exact commit以及§7的Tech Lead/operator决定。

## W1 design adoption and implementation record

- **Exact adopted material:** document v0.1.0 at reviewed commit `29376dda7e0ffa99fe3f1947bb2c3e83df81c228`, §7 P1–P6 and the design constraints of E1–E6, incorporated in starting main `386b08b2cca8f49a8ebeff14cc64e4a30d8b2037`.
- **Decision evidence:** the current user handoff records the user's reply **“采用”** to Review Project's explicit adoption request for W1 fake-only implementation. Review Project carries these recommendations as implementation requirements. The decision was observed on **2026-09-11**; no exact message timestamp, additional approver, signature or approval ID is supplied or invented.
- **Effect:** design adopted and existing W1 implementation authorized, including fixed OpenAI Responses / `gpt-5.6-terra` / low reasoning / no fallback and the protocol, data, transport, credential and usage constraints. DATA D1–D4, K1–K4 and INTENT I1–I7 remain applicable.
- **Operational limits:** no actual account/key use, per-material egress/retention, operational budget, paid call, deployment or Target execution is approved. E5 examples remain synthetic arithmetic, not spending limits. The provider POST design exception does not broaden Target GET-only/public-blocked behavior.
- **Implementation:** [W1 adapter and validation record](research-ai-provider-implementation.md), **REVIEWED / INTEGRATED via PR #150**. Real calls remain disabled; real-provider acceptance is pending. Broader W2 and RA-05 remain incomplete; W3 has not started.

The original recommendations in §7 and documentation-only validation in §9 preserve the proposal's history. This appended record establishes subsequent design adoption without changing those historical claims or converting design adoption into operational authority.
