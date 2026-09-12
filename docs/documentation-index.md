# 文档目录、权威状态与维护记录

本目录清点维护起点 `eb69379910b99cf312cbc9b08f9a3c4b72088c9d` 的全部 **27 份 tracked Markdown：docs 下 25 份、根 README 和 lab README**。所有 25 份 docs 都有其他 tracked 文档的文本引用；逐项用途仍有效，未证明任何整份文档已废弃或完全被替代，因此本包不删除文件。本目录为新增导航，不提升任何原文件的规范地位。

## 阅读顺序与权威

1. [Architecture decisions](architecture-decisions.md) 与 [security model](security-model.md)是规范优先项；冲突需明确架构决定，不能靠改 roadmap 措辞解决。
2. DATA、knowledge、INTENT、provider、B1–B8/N1 等按精确采纳记录和条件生效。设计采纳不证明代码实现、运行效果或 operational permission。
3. [Research Assistant roadmap](research-assistant-roadmap.md#5-固定阶段与依赖)是当前阶段进度入口；[N1 runtime validation](research-ai-w2-runtime-validation.md#hosted-gate-and-remaining-work)是 PR #153 增量的当前证据入口。完整 W2 preparation/accounting/permit/lifecycle/recovery 未完成，W3/RA-06 未开始。
4. 各实现/验收文档保留相应接口、限制和 exact-base 证据。旧 pending 状态、包内授权/停止句与临时验证明细只描述当时快照，不重新打开已采纳事项或阻止后来明确授权的工作；安全、权限、验收条件不因此退休。

“本地检查通过”“独立审阅接受”“PR CI 成功”“exact-main CI 成功”“验收组完成”“阶段裁决”和“操作许可”分别记录。代码/schema 中的 PASS 或 passed 名称保持原契约语义；文档不把它们提升为其他结论。实施方本地 commit → DO NOT PUSH → STOP，Reviewer 负责独立审阅与远端集成；本目录不发布或替代本地 canonical Review 规则。Issue 是可选跟踪载体，已授权范围内不要求重复逐包授权；范围扩大、未决决定和实际运行/数据/费用许可仍独立。

## Tracked 文档清单与入站引用

下表入站为**维护前 exact-main 的文本引用来源文件**，不含自引用或本新增目录；标识对应本表行，R/L 对应最后两行。同一来源多次引用只列一次。入站存在本身不证明文档有用，保留理由是“用途/权威状态”列；源码永久链接与旧标题 anchors 继续有效。

| ID | 文档 | 用途 | 权威/实施状态与保留理由 | 入站来源 |
| --- | --- | --- | --- | --- |
| D01 | [architecture-decisions.md](architecture-decisions.md) | 已批准产品/架构约束 | 规范；与 security model 优先，保留全文 | R, D04, D05, D07, D11, D12, D13, D14, D16, D17, D18, D19, D20, D21, D23, D25 |
| D02 | [backend-ci-shards.md](backend-ci-shards.md) | 分片、独立数据库、完整/不交集与 aggregate 规则 | 运行/验证说明；PR #146 已集成，时长是历史测量 | D20 |
| D03 | [bola-matrix-preview-api.md](bola-matrix-preview-api.md) | M14 只读 preview API、示例与错误 | 现有 API 使用契约；不授予执行许可 | R, D01, D05, D13, D14 |
| D04 | [level3-roadmap.md](level3-roadmap.md) | Level 3 里程碑与公网门槛 | 规划/历史索引；受两份规范约束 | R, D05, D13, D14 |
| D05 | [m14-offline-matrix-acceptance.md](m14-offline-matrix-acceptance.md) | M14 验收证据、兼容限制与独立 TEST runbook | 历史验收 + 有效隔离操作说明 | R, D01, D03, D11, D12, D13, D14, D15, D19, D21, D24 |
| D06 | [research-ai-budget-contract.md](research-ai-budget-contract.md) | B1–B8 预算/观察设计、算术与 T1–T12 | 已采纳设计；完整 W2 尚未实现 | D09, D10, D11, D14 |
| D07 | [research-ai-provider-contract.md](research-ai-provider-contract.md) | P1–P6/E1–E6 协议、provider 快照、权限与 usage | fake-only 已采纳设计；价格/模型资料为有日期的历史快照 | D06, D08, D09, D11, D14 |
| D08 | [research-ai-provider-implementation.md](research-ai-provider-implementation.md) | W1 adapter 与 timing 修正证据 | PR #150 已集成；live acceptance/operational permissions 未完成 | D06, D07, D09, D11, D14 |
| D09 | [research-ai-w2-implementation-contract.md](research-ai-w2-implementation-contract.md) | Q 表、严格 records/interfaces、G/A、X/Y 与 acceptance 映射 | v0.1.1 已审设计，N1 已采纳；协议实现与验收未完成 | D06, D10, D11, D14 |
| D10 | [research-ai-w2-runtime-validation.md](research-ai-w2-runtime-validation.md) | N1/Linux/AppArmor 条件、实现、PR/main 与失败证据 | PR #153 运行时前提已验收；不证明完整 W2 | D06, D09, D11, D14 |
| D11 | [research-assistant-adr-decisions.md](research-assistant-adr-decisions.md) | DATA 原始契约、六项决策理由与采纳登记 | 已采纳/未决按明确条目区分；C 为历史源码，P 不自动表示待批准 | D06, D07, D09, D14, D15, D16, D17, D18, D21, D24 |
| D12 | [research-assistant-evaluation.md](research-assistant-evaluation.md) | 冻结评测、oracle、分母、预算和输出约束 | 评测契约；批准与产品效果仍须独立证据 | D07, D11, D14, D18, D19 |
| D13 | [research-assistant-product-contract.md](research-assistant-product-contract.md) | 产品输入/输出、人责、调用图和支持矩阵 | 产品规格 + exact-base 能力快照；不放宽安全规范 | D07, D11, D12, D14, D16, D17, D18, D19, D21 |
| D14 | [research-assistant-roadmap.md](research-assistant-roadmap.md) | 阶段进度、有序范围、验收卡和剩余决定 | 当前进度入口与规划；不代替规范、采纳或 operational permission | R, D06, D07, D11, D12, D13, D15, D18, D19, D21, D24 |
| D15 | [research-intake-context.md](research-intake-context.md) | 输入、预算声明、准备度和 context 隔离 | PR #137 实现记录；synthetic-only | D06, D11, D14, D17, D18, D21, D24 |
| D16 | [research-intent-bridge.md](research-intent-bridge.md) | candidate-to-intent/link、时间、兼容、rollback | PR #144 实现记录；W2 依赖已后续集成 | D11, D14, D17, D20, D22 |
| D17 | [research-intent-contract.md](research-intent-contract.md) | I1–I7、health/pair/intent 时间与旧数据兼容 | 已采纳契约；保留精确采纳 provenance | D06, D07, D09, D11, D14, D16, D20, D22 |
| D18 | [research-knowledge-contract.md](research-knowledge-contract.md) | K1–K4 分类、版本、资格、review/validation/publish | 已采纳契约；保留 DATA 与项目隔离边界 | D06, D07, D09, D14, D19, D23 |
| D19 | [research-knowledge-retrieval.md](research-knowledge-retrieval.md) | 先资格过滤、排序/限额、生命周期与修正 | PR #141 实现记录；后续 publication 以 W3 gate 为准 | D06, D14, D17, D18 |
| D20 | [research-local-demonstration.md](research-local-demonstration.md) | 合成本地 walkthrough、场景与明确不支持项 | PR #147 有界验收记录；不是部署或公网许可 | D14 |
| D21 | [research-observation-intake.md](research-observation-intake.md) | 格式/限额、provenance、lifecycle/tombstone 修正 | PR #138 实现记录；不冒充 TestRun/verified truth | D06, D14, D15, D17, D18, D19, D24 |
| D22 | [research-response-verification.md](research-response-verification.md) | 真实 provenance、独立预期、精确 pair/dispatch | PR #145 实现记录；保持 legacy 拒绝与运行资格 | D02, D11, D14, D16, D17, D20 |
| D23 | [research-rule-validation.md](research-rule-validation.md) | 规则正反例、不可变证据和独立 publish 决定 | PR #142 实现记录；验证通过不自动发布 | D06, D14, D17, D19 |
| D24 | [research-subject-context.md](research-subject-context.md) | 身份、Resource/slot 提议、事实缺项与隔离 | PR #139 实现记录；不推导 session/执行 authority | D06, D14, D17, D18, D19 |
| D25 | [security-model.md](security-model.md) | Default Deny、执行/网络/凭据/数据等安全不变量 | 规范；与 architecture decisions 优先，保留全文 | R, D01, D02, D04, D05, D07, D11, D12, D13, D14, D16, D17, D18, D19, D20, D21, D22, D23 |
| R | [根 README](../README.md) | 产品入口、setup、测试安全与主要文档导航 | 操作说明；能力概览包含历史范围，详细 RA 状态看 roadmap | D02, D14, D20 |
| L | [lab README](../backend/tests/labs/README.md) | 确定性本地 BOLA lab | 合成 lab 运行说明，不能代替独立 TEST 环境或真实 Target 许可 | 无精确文件引用；保留 lab 目录运行说明 |

## 本次移除与合并

- Roadmap/ADR 的逐包 handoff overlays 合并为当前状态、采纳登记和 PR 集成索引；移除已完成 Issue #132 的两文件限制、旧分支继续指令、实施方 push 流程和等待重新授权 RA-01 的临时停止点。
- Runtime 的重复本地数据库端口、system identifiers、临时目录、逐次命令/时长和 pending-hosted narratives 合并为当前 PR/main 证据、有效实现边界及失败时间线。完整历史仍固定在 [reviewed feature 文档](https://github.com/runyiy/ai-api-security-platform/blob/f5ac48e2a9b4f6ca3c85cd7923f930e1ee5b04b3/docs/research-ai-w2-runtime-validation.md)，原标题保留 landing anchors。
- B1–B8/N1、DATA/INTENT 和 W1 的过期 pending-adoption 请求改为已采纳设计及未完成实现/操作条件；保留精确版本、reviewed SHA、用户原话和已知观察时间，不补造签名或消息时间。较早实现文档加明确历史记录标识，未改其接口与验收正文。
- 保留九阶段验收卡、DATA/knowledge/INTENT 契约、B1–B8 算术/限额、Q/record/interface/G/A 定义、T1–T12、X1–X8、Y1–Y5（含 Y3a/Y3b）、Linux 四条件和限定 CI AppArmor 采用条件。架构/安全规范、应用、测试、CI、数据库和 GitHub 规划工件不变。

## 未决事项与证据边界

未提供的整体阶段签署、产品评测标签/阈值批准、B cap/总任务与账号预算不得由集成推定；本文不重新开启已采纳的设计。Live input/hidden-token 上界、account billing/retention 与真实材料/凭据/外发/支出、broader TASK/RA-06、PUBLIC 的三道独立门槛继续按各契约处理。早期 provider 官方资料和性能数字保留为有日期的快照，本包不刷新价格/选型或许诺效率收益。

较早 implementation 文档仍保留有用的版本修正与基点限制，可能不能单独充当当前产品全貌；请从本目录/roadmap 和其明确后续记录阅读，不能把历史 C/P 当成新架构决定。本次没有发现可无条件删除的整份文档，也不静默决定更广产品能力。

本包静态验证通过：28 份 Markdown、450 个 local links（含 123 个 anchor links）无缺失目标；五份优先文档的原 heading anchors 保留。规范段落/JSON/验收集合保全检查通过，已审阅完整 diff、当前/历史术语和受影响引用，`git diff --check` 通过。纯文档未运行应用、数据库或 backend 回归；Reviewer 的 PR/exact-main gates 保持独立。本地文档 commit 后停止，不 push、不开始剩余 W2。
