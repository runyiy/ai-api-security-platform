# RA-03/W3：规则正反例验证与反馈审核

> **记录状态（[PR #142](https://github.com/runyiy/ai-api-security-platform/pull/142)）：** 合成规则验证与独立发布 gate 已集成。下文基点、提交时 pending 状态、验证结果和包内停止指令是历史记录；旧停止点不约束后续已授权工作。契约/安全/验收要求仍有效，采纳按精确记录、当前进度按 [roadmap](research-assistant-roadmap.md#5-固定阶段与依赖)；集成不授予操作许可。

**IMPLEMENTED / PENDING_INDEPENDENT_REVIEW**。本包仅实施RA-03/W3，不宣布RA-03完成、独立reviewer PASS或RA-04启动。起点为本地核验的clean main `cd60493cafc1d8bdcbf57cea84638d3138b3e78b`；分支 `codex/ra-03-w3-rule-validation`。W2经PR #141合并及main 2690 passed来自用户交接；本包不重开已关闭的Planning Issue #132，不创建milestone/PR，不push。K1–K4的[采纳记录](research-knowledge-contract.md#k1k4-后续采纳记录ra-03w2)和既有[架构](architecture-decisions.md)/[安全模型](security-model.md)继续约束实现。

## 1. 实际验证能力

[执行器](../backend/app/services/research_rule_engine.py)对精确不可变rule content运行解释逻辑，并调用W2的真实`_applicable`路径；[独立案例与预期](../backend/app/services/research_rule_cases.py)提供输入及另一侧的字面预期。执行器不读取预期标签。每次[验证服务](../backend/app/services/research_rule_validation.py)执行全部14例，逐项比较实际结果与预期；从结果计算passed，不能提交passed、输出、标签、程序、文件路径或任意测试数据。

这是对W2封闭的四种claim及actor/fact/shape拒绝条件的**合成解释检查**，不是任意自然语言规则证明、漏洞检测、真实业务事实验证、baseline/session健康证明或RA-04响应verifier。没有HTTP响应解析、intent、plan、TestRun、模型或凭据操作。关系与访问分别保留；输出只有explanation/no_match/needs_input/unsupported/rejected，execution_authorized恒false，不能输出safe、confirmed或可执行建议。

| 精确版本 | 内容及检查 |
| --- | --- |
| validator `ra03-offline-rule/1` | 固定纯Python实现；不eval/exec数据库或规则文本 |
| example bundle `synthetic_fixture:3001/1` | author-denied、visitor-grant、shared-grant三例，独立覆盖owner+denied、non_owner+allowed、shared+allowed |
| counterexample bundle `synthetic_fixture:3002/1` | 无事实、访问冲突、关系冲突、bearer健康未验证、身份未知、query/nested/body、恶意指令、独立隔离canary、缺baseline共11例 |
| rule binding | exact scope/knowledge_id/version/digest、ra-knowledge/1 contract、上述两个bundle的精确引用及内容digest、包含预期的suite digest、validator version |

上述命名空间是本包新写的独立synthetic案例，不解析W1文档示例、RA-02观察别名或RA-01评测ID。rule的example_refs必须恰为3001/1、counterexample_refs恰为3002/1；其他版本/集合拒绝，不自动latest替换。每个案例记录输入digest、预期digest、受限实际结果和比较结果；不回显恶意指令或canary正文。必须实际通过至少一个positive匹配，不能用全部no-match获得PASS。因W2仍不验证bearer健康，bearer-only规则不能满足本版本positive覆盖；不通过伪造健康事实补齐。机制卡暂不进入W3验证/普通发布路径，现有合成测试机制检索不变。

缺baseline案例保留独立denied并返回needs_input，可以仍匹配解释卡；这不为W2运行时增加baseline字段或验证能力。未选actor不匹配，unknown/conflict/session/unsupported不变成安全结论。案例、预期或执行语义改变需要新validator/bundle版本；反馈没有修改这些文件、标签或阈值的入口。

## 2. 不可变证据与发布资格

新增三张表：[models](../backend/app/db/models/research_rule_validation.py)中的research_rule_validations、research_rule_feedback、research_rule_feedback_reviews。数据库UPDATE trigger拒绝改写历史，FK使用RESTRICT；没有删除或覆盖接口，没有ObservationPayload FK或正文副本。验证记录绑定exact version、完整证据digest、server recorded_at及有限valid_until。验证失败也可留作反馈依据；审计或消费资格失败则整个事务回滚，不能留半条成功证据。

`ValidationRef`只含validation_id和完整digest，必须在同一精确rule下找到。每次用于read/review/publication/feedback时核验记录、digest、时间窗口、当前validator/suite/bundle及完整报告；执行固定有界检查重验报告，不能只信数据库的passed字段。缺失、外项目、错版本、伪造或损坏引用统一拒绝。窗口使用`recorded_at <= now < valid_until`；一次验证窗口最多24小时，并缩短到来源的更早期限。过期须新运行、新证据，不续写旧行。

普通发布的最小变化在[knowledge service](../backend/app/services/research_knowledge.py)：

1. 独立运行W3验证，取得当前passed证明。成功只返回ready_for_review；此时没有review/reuse/publish事件。
2. 操作者分别调用`/decisions`记录review（携带ValidationRef）及需要的独立reuse。原W2无证明review可继续作为历史审核记录，但返回validation_qualified=false，不能用于W3发布资格。
3. 操作者另一次显式publish，指定相同ValidationRef、review_event_id和适用的reuse_event_id及有限窗口。事件必须属于同一精确版本、顺序合法、operator_recorded/local_operator、审核窗口当前有效。shared scope必须有独立reuse；project scope可无reuse。
4. 检索继续先资格/源/污染/actor过滤、再rank/top-k；真实合成发布返回operator_recorded和ValidationRef，deadline取publication/review/reuse/validation/来源/当前上下文转换的最早边界。

W2 synthetic_test_only及NOT_RUN只能走原测试夹具分支，绝不升级为W3证明。真实publish不能引用测试review/reuse，也不能利用已有placeholder补齐。没有普通发布seed或启动批准；本次只有**测试拥有的数据库**内显式合成决策用于验证正向路径，未在操作者部署执行实际规则发布。

QueryRead的ordinary_publication_allowed仍恒false，表示检索结果本身不授予下一次发布权限；固定缺项改为human_publication_required，不再把已实现的有条件gate描述为全局关闭。执行权限恒false，预算/事实/会话等W2缺项不被发布覆盖。独立共享合成根的验证证明不依赖作者后来退出的项目测试permission；含项目观察的卡仍局限原context，不复制为共享。

## 3. 反馈、人工决定和污染

FeedbackInput绑定原exact rule及实际ValidationRef，proposal仅promotion/correction/disable，reason为封闭枚举、review为原SyntheticReference，不收自由外部triage或私有正文。promotion必须引用当前passed证据；correction/disable可以引用仍合资格的failed证据。correction还必须显式引用已记录、同一拥有者、直接supersedes原版本的新精确版本，不能修改原卡。

`/feedback/reviews`是独立本地操作者动作，仅accept/reject，expected_sequence固定0。每条feedback最多一条terminal review，唯一约束和目录锁防止并发覆盖。accept promotion/correction**只接受提议**，不会生成新版本、替其运行验证、生成规则审核/复用或publish。更正版本必须另行验证、审核及发布；旧ValidationRef不能继承。accept disable会在同一事务追加原版本disable事件。

任何知识disable（包括原W2入口）都会在目录锁内计算supersedes污染影响，给受影响的pending feedback追加invalidate/dependency_disabled事件。也检查correction的目标依赖。原版本、验证报告、feedback及已经完成的review不改写；无法解析候选依赖时保守失效本context所有pending提议。读取feedback给最小历史receipt和动态eligible_for_review；当前合资格时附上有界proposal及已记录的review，便于人工检查reason/correction/ValidationRef。过期或源失效时这些详情为null，不能把仍pending误作可审核。

W2对disabled祖先的污染过滤保持：受污染链不会因更正或旧PASS复活。污染后的独立重写需要新的独立根及其完整新资格，不能自动移除source或改变scope。源关闭/hold/delete/到期后不能继续消费证据正文。直接原W2 disable入口仍可用于已不可消费版本的终止处理；不要求先“恢复”过期来源以禁用它。

## 4. 有界接口、事务和迁移

路径前缀仍是`/api/research-projects/{project}/contexts/{context_id}/knowledge`。新增POST端点`/validations`、`/validations/read`、`/feedback`、`/feedback/read`、`/feedback/reviews`，沿用严格application/json、拒绝query/压缩/重复key/非法UTF-8/extra、固定脱敏错误及no-store。完整schema见[W3 requests](../backend/app/schemas/research_rule_validation.py)。例如在测试拥有的合成context中，先用原`/versions`保存具有3001/1及3002/1引用的rule，再提交：

```json
{"reference":{"scope":"project","knowledge_id":"knowledge-1","version":1,"digest":"<该版本的64位digest>"},"valid_until":"<当前server时刻之后、不超过24小时的aware RFC3339>"}
```

示例中的占位值必须换成实际返回的digest和时间；不是可执行发布批准。返回的ValidationRef可原样用于`/validations/read`或反馈。无全局列表、跨项目搜索、导出或外部获取入口。

| 限额 | 失败行为 |
| --- | --- |
| 请求32768实际字节、depth5、2048 nodes；沿用W2字段严格上限 | 整次422/413，不修复或截断 |
| 固定14个案例，硬上限24；每次验证/证明重验最多24次纯计算 | 超限knowledge_validation_limit；不接受动态案例/代码 |
| evidence canonical及DB JSONB文本分别≤32768 bytes；实际结果字段/枚举封闭 | 非法执行器输出记录为failed/actual=null，不保存原文；超限回滚 |
| 每context最多128条validation、128条feedback；每feedback最多一条review | 恰好上限允许；下一条拒绝，不轮换/覆盖证明历史 |
| catalog自身+共享≤256，supersedes遍历≤128，pending扫描LIMIT129，事件≤16/version | 超限整次fail closed；无部分污染处理 |
| 完整编码响应≤65536 bytes；审计复用W2每context4096条及显式轮换 | 任何失败回滚；record/decision/query代码对应新增域同类操作，不记录正文 |

所有新服务先取W2知识目录advisory lock、再取可信context锁，复用来源当前资格；不新增其他锁顺序。验证不是读取真实assertion/执行Target：业务设定仅来自固定独立suite。证据/事件窗口及source deadlines在工作开始后保留，在执行结束、审计、服务编码及API响应编码后重验；锁到响应编码完成后的commit才释放。W2 permission/fact未来生效及到期边界回归保持。动态资格不能由immutable evidence永久缓存。

[增量迁移a1c3e5f7b9d0](../backend/alembic/versions/a1c3e5f7b9d0_add_research_rule_validation.py)只新增三表/index/trigger，parent f0b2d4e6a8c0；不改旧migration、不读/回填legacy或source body。downgrade先锁三表ACCESS EXCLUSIVE，任一非空则research_rule_validation_populated_downgrade_blocked；全空才移除。应用回退须保留有价值新历史并停用新入口，不能为降级清空它。

## 5. 实际验证记录

Ubuntu WSL、原backend/.venv Python 3.12.3，全新本人拥有0700 data directory的PostgreSQL 16.15 UTF-8实例：database/user为合成ra03_w3_test，127.0.0.1:55487，目录/tmp/ra03-w3.XyPpQP/data。导入应用前psql核对专用database/user、独占loopback端口/data_directory/encoding，public表数0、其他clients 0。428个backend tracked/new文件与测试副本逐字节一致；副本不含.env，env -i只设置测试DATABASE_URL/PATH/LANG；不使用ambient/operator encryption key，原opt-in临时加密fixture保持。

数据库测试串行。所有测试流量限于既有套件拥有的本地合成服务；新W3检查与服务guard验证网络/凭据/执行/provider调用为0。冻结内容不改，不读取其案例/标签来构建规则或本包fixture；仅evaluator内部verify和原有评测回归消费它们。

```bash
python -m alembic upgrade head
python -m pytest tests/services/test_research_rule_validation.py tests/services/test_research_rule_validation_concurrency.py tests/api/test_research_rule_validation.py tests/schemas/test_research_rule_validation.py tests/migrations/test_research_rule_validation_migration.py tests/services/test_research_knowledge.py tests/services/test_research_knowledge_boundaries.py tests/services/test_research_knowledge_concurrency.py tests/api/test_research_knowledge.py tests/schemas/test_research_knowledge.py tests/migrations/test_research_knowledge_migration.py --tb=short -q
python -m pytest --tb=short -q
python -m alembic heads
python -m alembic current
python -m alembic check
python -m evaluation.ra01 verify
python -m pip check
git diff --check
```

| 检查 | 本次结果 |
| --- | --- |
| 开发service首轮 | 38 passed，23.73s |
| 开发W3+W2首轮 | 243 passed，1 warning，108.66s；随后增加边界/隔离用例及withdraw资格检查 |
| 最终focused | **263 passed，1 warning，112.22s**；包含100个W3用例及全部163个W2用例 |
| 首次full | **2779 passed、9 failed、55 warnings，303.04s**；九项均为migration目录以外旧测试的Alembic head固定值未更新。仅将其head断言从f0b2d4e6a8c0更新为a1c3e5f7b9d0，未改业务断言或生产代码；随后重新完整验证 |
| head断言修复后full | **2788 passed，55 warnings，320.84s**；随后完善合资格反馈详情读取及其伪造详情回归 |
| 最终full | **2790 passed，55 warnings，416.15s**；没有剩余失败 |
| 迁移 | fresh/populated升级、旧数据/配对兼容、空降级和非空拒绝测试通过；heads/current均a1c3e5f7b9d0，check退出0：No new upgrade operations detected |
| freeze | VERIFIED，digest `692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a`；approval仍为PROPOSED_PENDING_REVIEW_AND_OPERATOR_APPROVAL，冻结内容未改 |
| 依赖 | pip check：No broken requirements found；只有pip cache不可写警告 |
| diff及清理 | git diff --check通过；78个本地文档链接文件目标存在，428个backend文件与最终测试副本逐字节一致。退出前再次核对同一owned实例、其他clients 0；仅停止该实例，日志确认shutdown完成且postmaster.pid移除 |

证明范围是有界合成解释及事务资格，不是任意规则质量、外部有效性或产品release。INTENT/session/baseline、PROPOSAL、EGRESS、TASK及PUBLIC pending ADR没有因此获批。本地commit后停止，等待Review Project；不push、不创建/合并PR、不开始RA-04。
