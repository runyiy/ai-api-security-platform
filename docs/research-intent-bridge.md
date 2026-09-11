# RA-04/W1：有界 candidate-to-intent/plan 转换

**IMPLEMENTED / PENDING_INDEPENDENT_REVIEW · 新purpose审批/执行关闭**

基点 `59390a480db133db9411908af05df37dd625fc91`；本地clean main、origin/main和HEAD一致后创建 `codex/ra-04-w1-intent-bridge`。PR #143/main push CI通过是用户交接证据，本次未查询远端CI。操作者明确采纳 [INTENT v0.1.0](research-intent-contract.md) reviewed `6723cb5bfa62a10453f18f8158c25000a1997711` 的I1–I7，Review Project Tech Lead采用为实现约束；采纳记录在协议和 [ADR](research-assistant-adr-decisions.md#intent-后续采纳记录ra-04w1)。历史pending段落保留，不补造签名/批准时间。

本包只实施W1；[架构](architecture-decisions.md)、[安全模型](security-model.md)、[产品窄请求形态](research-assistant-product-contract.md#5-请求形态支持矩阵)继续优先。不提供W2响应/健康解释器、网络发送时间证明、实际30秒pair判定或W3演示；不宣告独立review PASS、W1验收完成或RA-04 COMPLETE。

## 1. 实现与明确关闭的依赖

[服务](../backend/app/services/research_intent.py)在既有catalog→context锁之后，使用SHARE集合锁保护assertions/revisions/Scope/Endpoint/binding的插入/修改窗口；保持identity→credential binding顺序。只读取非秘密metadata与最新secret version ID，复用M12全部verified facts和M14瞬态composer，未修改preview。`TestIdentity`的raiseload对象在M14/M12调用期间保持强引用，防止weak identity map回收后由resolver重载旧credentials列；底层plan persistence也使用非秘密列投影。来源逐次经既有W2 lifecycle核验/审计；不从operator session claim、ownership或HTTP200产生健康/访问事实。

| 能力 | 当前行为 |
| --- | --- |
| Mapping confirmations | 显式本地人工confirm/withdraw命令，独立synthetic evidence引用；按number/version追加，digest绑定context/Target、Endpoint/template/selector/当前review、Resource/type/value、URL和permission snapshot。不是把subject.operator_proposed直接改成verified |
| Finite manifest | baseline/probe两个不同显式actor、同Resource/Endpoint/slot/当前revision；可另列health_baseline/health_probe，总计2–4个GET。独立allowed baseline，owner+denied、shared/non_owner+allowed原样；缺项/冲突/不支持或source不可用整次失败 |
| Budget decision | 另一个显式本地动作，approved/revoked追加，绑定exact manifest digest/sequence与server时间。核验请求数、duration、rate和concurrency不超过intake草案及当前permission；不把RA-02 unverified approval_reference升级。每个manifest purpose只能转换一次，不跨number/version重复使用额度；无scheduler、aggregate execution approval或RA-06 ledger |
| Conversion | 重新核验当前mapping/subject/M12/M14/permission/source/可选knowledge；冻结core后调用既有plan persistence原语，分别创建单GET plan，再冻结单向link/role成员。每个TestCase真实关联Endpoint/actor/Resource，`test_type=ra_intent_get_v1`、`ownership_relation=unspecified`、`expected_statuses=[]`；保持旧四列唯一键，版本与角色在新域区分 |
| W2 dependency | 生产 `_interpretation()` **无条件抛出 `intent_w2_evidence_unavailable`**。没有环境开关、fixture注册、caller passed/healthy字段或可提交receipt的API。因此当前business及health-bootstrap转换在此明确拒绝，不留下plan/case/audit前缀；不能声称已有合资格健康证据 |
| Independent test envelope | 仅测试monkeypatch未来W2 producer，使用显式受控interpreter/health引用，证明后续确定性转换结构和拒绝行为。不是部署seed、真实health proof或运行时绕过。W1仍检查其exact actor/binding/version/context/Target与health时间包络 |
| Health bootstrap structure | `ra-health-intent/1`及`ra-health-link/1`单actor/单GET，来自manifest中独立allowed健康Resource/mapping，先于business core，无自身前置健康证明；business core引用未来W2真实receipt。结构存在但缺W2解释器时同样拒绝，不能隐式访问login/MFA或通用health URL |
| Knowledge when selected | exact version/digest、rule category/applicability、源/lineage、真实W3 validation和独立review/reuse/publication窗口重验；synthetic_test_only/NOT_RUN、candidate/mechanism、缺失/错误/过期/disabled引用均不能qualify。不执行rule文本，不改变W3 gate |

当前转换只接收现有合资格synthetic记录。W1的metadata读取进一步限定Resource type为`folder/project/task/record`、external ID为1–16位数字，再要求与既有builder一致；其他值明确unsupported，不提供更广renderer或私有值准入。这是本包显式保守实现限制，不宣称覆盖全部可能的legacy Resource值。选用RA-02 observation时还必须符合其独立closed synthetic registry及当前Endpoint形态；不修改旧parser来迎合fixture。

## 2. 本地API与有界事务

[schemas/codec](../backend/app/schemas/research_intent.py)、[routes](../backend/app/api/routes/research_intents.py)在以下共同前缀下注册：`/api/research-projects/{project}/contexts/{context_id}/intents`。由现有trusted-local单操作者边界提供人工命令，不新增SaaS认证域。

| POST后缀 | 严格输入；职责 |
| --- | --- |
| `/mappings/{number}` | `MappingInput`：exact context/Target/Endpoint/binding/Resource、expected_version（首次0）、confirm/withdraw、synthetic evidence；更正新版本，不覆盖 |
| `/manifests/{number}` | `ManifestInput`：有序baseline/probe及可选health角色、精确subject/mapping refs、显式duration/rate/concurrency/evidence、expected_version |
| `/budget-decisions` | `BudgetDecision`：manifest number/version/digest、expected_sequence、approved/revoked、evidence；与创建/执行审批分开 |
| `/versions/{number}` | `ConvertInput`：exact manifest、expected_version、business/health_baseline/health_probe、可选exact knowledge、evidence；不接受health receipt、执行参数、secret或“通过”开关 |
| `/read/{kind}` | exact number/version/digest；kind=mapping/manifest/intent。重新核验当前依赖，不缓存过去资格、不选择latest替换引用；读会追加受限审计 |

全部拒绝query override；只接受application/json和identity编码；按实际字节读取≤32768，不信Content-Length。`ra-json/1`使用排序keys/compact UTF-8/无尾换行和domain-separated SHA256，拒绝重复keys、float/nonfinite、BOM/surrogate、额外字段和类型强转。时间接受aware RFC3339，canonical UTC六位微秒；API没有caller消费时钟。

请求/core/完整typed response均深度≤8（root=0）、nodes≤8192，包含receipt envelope；完整输出≤65536 bytes。Scope在core里采用扁平metadata，生成plan时恢复既有policy_context结构，不扩大响应深度上限。core≤32768、link数据库≤4096 bytes；URL≤2048 characters；全manifest及选定rule合计最多8 distinct observation entry refs；每actor当前/未来verified assertion扫描≤256，第257条拒绝；Scope≤256。每context mapping/manifest/intent各≤1024版本，corrections计数；manifest决定≤16、intent audit≤4096。满额固定码拒绝，不裁剪历史/静默旋转。每intent唯一role、每plan唯一intent membership。

Services要求clean Session、savepoint覆盖所有source审计/新记录/Case/Plan/PlanAction/safety audit，保留caller commit ownership。API完整typed编码和最终资格检查后才commit，错误内容固定且no-store；失败后caller即使catch再commit也无部分新版本。新purpose没有真实网络，因此本包无“网络副作用可回滚”声明。

## 3. 时间、失效与旧入口

Core明确记录health120s/pair30s/intent300s。W1检查health从send开始，send≤complete≤verified≤now，`now < min(send+120s,receipt截止)`；intent≤300s，并被manifest duration、permission/source/当前fact expiry和未来verified fact的`max(asserted_at,valid_from)`收窄。没有W2真实send/complete时间可用时直接缺证据；没有创建假的TestRun或30秒pair结果。

比较exact metadata snapshot、全部eligible支持/未来窗口、当前context/subject/mapping版本、credential ID/version、manifest budget决定、rule资格。改变/撤回后旧依赖不能使用；不自动换最新credential、去掉expired依赖、替换baseline或改旧core。冻结deadline在audit、service dump以及API完整编码后再次检查，等于截止已失败，无grace。服务和完整API编码共享request-local UTC采样状态，任何已观察到的时钟回退均拒绝；新版本/预算决定也不得早于前一条记录。跨进程真实网络时序的可信证明仍是W2依赖。新intent/两计划需新manifest和独立预算决定；旧审批历史不提供新authority。

[统一拒绝guard](../backend/app/services/research_intent_gate.py)识别`ra_`新/未知TestCase type、plan policy marker，以及不可变membership（包括移除type标记的tamper）。普通TestCase planner、public plan creator、exact approval/is-approved、direct和exact execution、legacy probe/baseline analysis、observed-access derivation、AI advisory及formal report的两侧来源均拒绝新purpose，明确`intent_w2_execution_closed`；不能先解密凭据、返回旧canonical当作新执行资格或进入provider。通用plan integrity仍可验证immutable plan bytes，但不赋权。每个新plan的适用execution approval是另一个动作，**当前也关闭**；预算approved不代替它。

Legacy无新类型/marker/member的行为、单process direct限制、M8 canonical/fencing/cancel、owner generator/analyzer、M13指纹/原始source/evidence/review/report均保留。新计划只经转换内部的既有persistence原语创建；公开creator不接受新marker。W1精确read检查core/link/member/plan/action/actor/binding/revision/Resource及中性Case一致；没有新类型到旧语义的fallback。

历史行不删除/改写；当前无资格读取返回固定不可用，而不是把过期/held内容作为可复用receipt重新输出。本包不新增任意历史内容导出；旧source lifecycle和tombstone行为不变。

## 4. 增量持久化与回退

[新增migration](../backend/alembic/versions/4e72a9c1d603_add_research_intent_bridge.py)：`a1c3e5f7b9d0 → 4e72a9c1d603`，六张表由 [models](../backend/app/db/models/research_intent.py)定义：

- `research_intent_mappings`、`research_intent_manifests`、`research_intent_versions`：context/Target及intake版本组合RESTRICT FK、number/version唯一、body/digest/window限制；intent另存独立link/digest。
- `research_intent_budget_decisions`：exact manifest FK、16条有序追加决定。
- `research_intent_plan_members`：intent/role唯一、plan唯一，exact plan/action/TestCase RESTRICT FK。
- `research_intent_audit`：固定事件码与context FK。

复用现有immutable trigger拒绝UPDATE；无运行时删除/覆盖API。Observation refs保持软引用，不能阻碍W2 payload/tombstone清理。无seed approval、无legacy backfill、无旧migration更改。旧测试的机械head期望/后续表排除随additive head推进，未修改M8或其他行为来修饰历史结果。

空新域可downgrade；包含任一新行、任何`ra_` TestCase或带`research_intent` marker的plan（包括无link的残缺记录）时，锁六表、TestCase及ExecutionPlan后拒绝`research_intent_populated_downgrade_blocked`，整个事务保留。回退必须停用新入口并盘点M8；只可用认识新类型且拒绝它的兼容版本恢复legacy execution。**回退到本基点未识别新记录的旧二进制时必须保持执行入口不可用**；旧二进制不认识新flag，不能承诺仅保留新表就安全。不得删除links、改type或清洗旧TestRun/M13来实现回退；实际数据处置另行授权。

## 5. 实际验证

Ubuntu24.04.2 WSL2、仓库backend virtualenv（Python3.12.3）；本包新建自有PostgreSQL16.15于 `/tmp/ra04-w1.tAxwov/data`，数据库/role均`ra04_w1`、loopback `127.0.0.1:55473`。首次application import前通过PostgreSQL自身核验database/user、addr/port、data_directory、UTF8及空public schema/无其他client；未使用operator/shared DB。测试使用去掉`.env*`/`.venv`的临时backend副本，解释器仍来自仓库venv；最终423个application/test/migration Python文件逐字节一致（`copy-check-final.log`，集合SHA256=`56f9fcc0cabdc4de02ef5460942d5b8498be69fbf7bd918138cc09bc4b467d98`）。runner `/tmp/ra04-w1.tAxwov/run`的实际进程环境为：

```sh
cd /tmp/ra04-w1.tAxwov/backend
env -i PATH=/home/runyiy/projects/ai-api-security-platform/backend/.venv/bin:/usr/bin:/bin LANG=C.UTF-8 DATABASE_URL=postgresql+psycopg://ra04_w1@127.0.0.1:55473/ra04_w1 "$@"
```

确认`settings.credential_encryption_key is None`后才测试；既有credential测试只在其自有fixture里临时安装测试key。本包conversion fixture仅存非秘密版本metadata/无效envelope，不调用加解密或resolver。没有实际规则发布/部署approval seed；W3 gate正例的publication只发生于隔离测试事务和fixture。

以下命令都由该runner执行（`git`/文档静态检查在仓库根）；临时原始logs位于 `/tmp/ra04-w1.tAxwov/`，不加入仓库：

```sh
python -m pytest tests/schemas/test_research_intent.py tests/services/test_research_intent.py tests/services/test_research_intent_gates.py tests/services/test_research_intent_concurrency.py tests/services/test_research_intent_knowledge.py tests/services/test_research_intent_sources.py tests/api/test_research_intents.py tests/migrations/test_research_intent_migration.py --tb=short -q
python -m pytest tests/ai/test_analysis_redaction_boundary.py tests/ai/test_analysis_transaction.py tests/reports/test_security_report.py tests/migrations/test_execution_plan_migration.py tests/migrations/test_safety_decision_audit_migration.py --tb=short -q
python -m pytest --tb=short -q
```

| 实际验证 | 结果 |
| --- | --- |
| 最终W1 focused（`focused-final7.log`） | **149 passed**，6 warnings，50.06s |
| 修复后的legacy mock/旧migration回归（`full-fixes4.log`） | **11 passed**，2.15s；补充explicit无membership的mock结果和新增后续表排除，不削弱生产guard |
| 先行相关回归（`regressions1.log`） | **477 passed**，14 warnings，83.34s；M12/M14、credentials、knowledge/W3、legacy planning/approval/execution和research migrations，最终完整suite再次覆盖 |
| clock/depth收紧前完整backend（`full2.log`） | **2935 passed**，60 warnings，298.05s |
| clock/depth收紧后完整backend（`full3.log`） | **2938 passed**，60 warnings，295.80s |
| 增加orphan marker拒绝后完整backend（`full4.log`） | **2939 passed**，60 warnings，301.00s |
| 最终完整backend（`full5.log`） | **2939 passed**，60 warnings，306.31s；无未解决失败，warnings为pytest collection与Starlette/httpx弃用提示 |
| `python -m alembic heads` / `current` / `check` | heads/current均唯一`4e72a9c1d603 (head)`；check：**No new upgrade operations detected** |
| `python -m evaluation.ra01 verify` | **VERIFIED**；freeze=`692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a`，保持原`PROPOSED_PENDING_REVIEW_AND_OPERATOR_APPROVAL`状态；未查看held-out来制作fixture |
| `python -m pip check` | **No broken requirements found**；只报告用户pip cache不可写并自动禁用cache，无dependency变更 |
| `backend/.venv/bin/python /tmp/ra04-w1.tAxwov/check_docs.py` | **PASS**：108本地链接、18 anchors，验证生产proof producer无条件拒绝及private persistence的实际caller集合；9外部链接未fetch |
| `git diff --check`、冻结材料/旧migration差异核对 | **PASS**；frozen evaluation、旧migration、architecture/security规范及dependencies均未修改 |

477-test命令由runner的`bash -c`展开：

```sh
python -m pytest tests/integration/test_m14_matrix_acceptance.py tests/api/test_resource_access*.py tests/api/test_observed_access_assertions.py tests/credentials tests/services/test_test_case_planning.py tests/services/test_execution_plan.py tests/services/test_execution_plan_approval.py tests/services/test_plan_execution*.py tests/services/test_research_knowledge*.py tests/services/test_research_rule*.py tests/api/test_research_knowledge.py tests/api/test_research_rule_validation.py tests/migrations/test_research*.py --tb=short -q
```

初次完整suite（`full1.log`）实际为 **9 failed / 2920 passed / 60 warnings，299.52s**：6个AI mock将未设置scalar返回当作membership、1个report mock缺新lookup序列、2个M5 migration测试未排除六张后续表；已按新依赖修正测试假设。开发期focused试跑还发现并修复SQLAlchemy weak identity map导致M14重载credentials、最终budget payload未再次编码，以及fixture budget/必填request_data和RA-02 registry引用错误；测试未通过的中间结果不作PASS。草稿migration ID曾与既有revision冲突，改为唯一新增`4e72a9c1d603`，未改旧revision。一轮补充fixture试跑曾3 failed/142 passed；修正fixture后145 passed。随后补齐request-local clock rollback和完整response深度/node限制，以扁平Scope metadata保留原plan格式，新增3个回归后148 passed。再补充残缺plan marker的downgrade拒绝回归，并让populated fixture实际包含legacy plan和exact approval（而非只检查空plan表），最终149 passed。未修改M8行为或删除其历史失败记录。

最终独立复核仍为`ra04_w1 / ra04_w1 / 127.0.0.1:55473 / /tmp/ra04-w1.tAxwov/data / UTF8`（`identity-final.log`），operator encryption key仍缺省（`key-absent.log`）。随后执行`/usr/lib/postgresql/16/bin/pg_ctl -D /tmp/ra04-w1.tAxwov/data -m fast -w stop`，成功停止自有instance，核验`postmaster.pid`不存在；没有停止其他数据库。临时日志保留供本地review，未加入commit。

W1验收映射（这些是本地实现测试，不能替代Review Project或W2真实证据）：

| 已实施合同义务 | 固定回归证据 |
| --- | --- |
| §§2–3 core→plans→link、两独立GET、same revision、中性Case | `test_research_intent.py` positive conversion/health bootstrap，exact core/link/hash/role/action及重复manifest拒绝 |
| §§3、5 mapping/independent allowed、owner+denied、shared+allowed | mandatory rejections/changed dependency tests；重跑真实M12/M14，不采用caller candidate |
| §4 health120、intent300、依赖半开窗口及未来facts | send起算±1µs、health bootstrap 300秒、asserted_at/valid_from未来冲突、service audit/dump及API完整编码±1µs、跨service/编码阶段的clock rollback拒绝 |
| §5 来源/资格变更 | `test_research_intent_sources.py` hold/delete/quarantine/revoke/close与source expiry；knowledge测试使用真实W3独立validation+review+publish，仅在隔离fixture内创建 |
| §5 并发/失败原子性 | Scope writer实际等待数据库锁，随后旧intent不可用；同expected-version竞争；第二plan/audit/最终budget payload/response失败后catch+commit也无前缀 |
| §6 所有新类型consumer拒绝 | `test_research_intent_gates.py` direct/planner/creator/approval/exact执行；type tamper仍由durable member拦截；observed derivation、analysis及report/AI两侧来源均拒绝 |
| §2 有限工作/存储/输出 | strict integer/JSON/time、32768输入、65536输出、深度8/nodes8192、256/257 facts、真实1024版本/4096audit、16决定边界 |
| §6 增量兼容/回退 | `test_research_intent_migration.py` fresh/empty roundtrip、populated legacy M13/review/report逐字段保留（包含实际persisted legacy plan与exact approved记录，并在upgrade后重算digest/核验approval）、含新记录、孤立new-type Case或残缺plan marker时拒绝downgrade |
| I6 W2缺失拒绝 | service/API无runtime proof字段；controlled future producer只存在test monkeypatch，SQL capture禁止读取legacy credentials/encrypted envelope，并禁止credential resolver |

未运行的新purpose health/Target请求、真实response解释、30秒实际pair final consumption、M8新purpose预算消耗及逐计划执行批准属于W2依赖，不能从本表推导已实现。


## 6. 剩余边界与停止

I1–I7设计已采纳，不代表独立Review Project对本实现PASS。W2须提供当前合资格、不可伪造的真实interpreter/health/source证明及实际pair消费时间；并在启用新purpose审批/发送前实现完整最终执行资格重验、预算消耗与M8边界集成。本包没有把这些缺口包装成已存在的能力。W3本地端到端演示及通用报告仍按roadmap次序；实际Target/health请求、operator credentials、私有资料和费用仍需各自授权。本地commit后STOP，不push/PR/merge，不开始W2。

## 7. W1 source lifecycle fix（待独立复核）

本次fix继续 `codex/ra-04-w1-intent-bridge`，实际clean HEAD核验为 `81533fe4eddf4ac136cb937d09434bb0f30b9be2`，不amend该commit。[协议§5 / I4、I7](research-intent-contract.md#5-等待变更与失效-p)要求合法恢复source后也不能恢复旧intent。本节追加修复记录，保留以上原始实现/验证历史；不宣告独立Review Project PASS或W2 readiness。

[增量migration](../backend/alembic/versions/5f83bac2e714_pin_observation_hold_generation.py)增加observation记录的 `hold_generation INTEGER NOT NULL DEFAULT 0`，范围0–2147483647。每个合法hold在既有context锁、savepoint和审计事务内加一（包括重复hold/相同timestamp）；达到上限时拒绝，不能回绕。release、自然hold expiry、普通/human read和audit retirement不改该计数。旧记录只获得新增列默认值；不改payload、hold时间、review、source refs或immutable W1历史。此计数无需扫描事件历史，不依赖会被裁剪的audit，也无需消费者在hold期间读取。

W1在既有subject和knowledge source资格核验后，把exact observation对应计数纳入manifest actor source snapshot及可选rule dependency snapshot，随core/digest冻结。消费时在同一context锁内读取当前计数并比较；hold结束后当前availability恢复也不能匹配旧pin。source仍采用context隔离的软引用；记录删除/不可用仍拒绝，不增加保留payload的FK。最多8个distinct entry的限制不变，rule-only额外metadata查询最多8次，现有core/output限制继续适用。

恢复后须重新提交合资格manifest、独立budget decision和新intent/plans；旧预算不转移，旧core/link/计划不改写。旧格式中含observation依赖但未带pin的manifest/intent也拒绝重用，不能回填假历史资格；无observation依赖的旧记录不因本fix额外失效。普通读取及不相关source的hold不会使未变化依赖失效。生产 `_interpretation()` 仍无条件拒绝，所有新purpose审批/执行gate原样关闭；正向新plan测试仅使用原有显式monkeypatch未来W2 producer。

新增migration的downgrade在锁表后拒绝任何非零hold计数或已有manifest/intent，避免删除实际失效依据；空域/仅未hold旧观察记录可安全回退。旧W1 migration继续对其他新域、孤立Case/plan marker拒绝破坏性回退。必须停用不认识hold计数的旧二进制的observation生命周期写入口和W1入口，不支持这些writer混用新旧版本；保留该列不能让旧二进制正确推进计数，也不能恢复新purpose执行。没有修改旧migration或M8行为。历史migration测试仅更新head，并按其当时schema比较旧字段；新增测试独立核验计数默认值、边界、升级保留和非空回退拒绝。

本次验证使用Ubuntu WSL2与仓库backend `.venv`（Python3.12.3），新建自有PostgreSQL16 instance：`/tmp/ra04-w1-lifecycle.tsXYaX/data`、`127.0.0.1:55479`、database/role均 `ra04_lifecycle`。首次application import前以独立 `psql -X` 核验database/user、地址/端口、data_directory、UTF8、public表数0、其他client数0及目录owner/runyiy、mode0700（`identity.txt`）。未读取operator数据库或ambient key；使用排除 `.env*` 的临时backend副本和如下runner环境，并断言 `settings.credential_encryption_key is None`：

```sh
cd /tmp/ra04-w1-lifecycle.tsXYaX/backend
env -i PATH=/home/runyiy/projects/ai-api-security-platform/backend/.venv/bin:/usr/bin:/bin LANG=C.UTF-8 DATABASE_URL=postgresql+psycopg://ra04_lifecycle@127.0.0.1:55479/ra04_lifecycle "$@"
```

临时logs/runner均在 `/tmp/ra04-w1-lifecycle.tsXYaX/`，不加入commit。最终全套测试启动前425个app/test/migration Python文件与仓库逐字节一致（`copy-final.log`，集合SHA256=`fa351fd0f5efc31448b2315ed2c82c5a9bb1dfd439db387e7b5ef288ee36c648`）。本次实际命令（通过该runner执行，git检查在仓库根）：

```sh
alembic upgrade head
python -m pytest /tmp/review-ra04-w1-probe-i7udwumg/test_independent_lifecycle.py -q --tb=short
python -m pytest tests/services/test_research_intent_sources.py tests/services/test_research_intent_knowledge.py tests/services/test_research_intent_concurrency.py tests/api/test_research_intents.py tests/migrations/test_research_intent_lifecycle_migration.py /tmp/review-ra04-w1-probe-i7udwumg/test_independent_lifecycle.py -q --tb=short
python -m pytest tests/schemas/test_research_intent.py tests/services/test_research_intent.py tests/services/test_research_intent_gates.py tests/services/test_research_intent_concurrency.py tests/services/test_research_intent_knowledge.py tests/services/test_research_intent_sources.py tests/api/test_research_intents.py tests/migrations/test_research_intent_migration.py tests/migrations/test_research_intent_lifecycle_migration.py -q --tb=short
python -m pytest --tb=short -q
python -m alembic heads
python -m alembic current
python -m alembic check
python -m evaluation.ra01 verify
python -m pip check
git diff --check
```

| 本次已执行验证 | 实际结果 |
| --- | --- |
| reviewed HEAD上的reviewer复现（`reviewer-before.log`） | **2 failed**，2.32s：两个旧依赖恢复后都没有拒绝 |
| lifecycle补充组合（`lifecycle-focused.log`，含reviewer两例） | **67 passed**，1 warning，44.37s |
| W1 focused，含fresh/populated/empty/refused migration（`w1-focused.log`） | **179 passed**，6 warnings，83.40s |
| 首次full backend（`full-backend.log`） | **35 failed / 2934 passed**，60 warnings，337.45s；逐条核验均为旧head `4e72a9c1d603` 与新head不等，未发现运行行为失败。随后仅机械更新这些head期望；没有修改M8或其他行为 |
| 最终full backend（`full-backend-final.log`） | **2969 passed**，60 warnings，375.34s；包含全部W1、observation lifecycle、knowledge、legacy和migration回归，无未解决失败 |
| 最终独立复现脚本本地重跑（`reviewer-final.log`） | **2 passed**，2.22s；这是本地执行，不是独立Review Project PASS |
| Alembic heads/current/check | 唯一head/current=`5f83bac2e714`；**No new upgrade operations detected** |
| `python -m evaluation.ra01 verify` | **VERIFIED**，freeze=`692c33a321cc59e9799377ed512402ac33826dec06707d494b3d5853006a231a`，既有approval状态不变；未查看held-out来构造fixture |
| `python -m pip check` | **No broken requirements found**；pip仅提示不可写用户cache并自动禁用cache |
| 临时 `check_docs.py` / `check_scope.py` / `check_copy.py`；`git diff --check` | **PASS**：110本地链接、19 anchors，9外链未fetch；生产proof仍无条件拒绝；36个其他测试文件仅改head，冻结材料、旧migration、dependencies及execution gates未改；425个Python文件一致 |

开发中首次source试跑为4 failed/20 passed：测试对hold期间拒绝的异常类型误写为IntentError；既有subject gate实际抛SubjectError。改为明确期待SubjectError，保留恢复后必须抛 `intent_dependency_changed` 的断言。永久回归另覆盖hold expiry的±1µs、是否有中间读取、production旧预算重用、新manifest独立决定、controlled future-W2旧intent/新计划、rule-only source、同timestamp重复hold、audit retirement、无关source隔离、普通/human read、计数上限、audit/receipt rollback、真实锁等待及API拒绝原子性。没有新runtime test seam或真实W2证据；未发送新purpose/Target/health请求。

最终复核为同一 `ra04_lifecycle / ra04_lifecycle / 127.0.0.1:55479 / /tmp/ra04-w1-lifecycle.tsXYaX/data / UTF8 / PostgreSQL16.15`，无其他client，operator encryption key仍缺省。已执行 `/usr/lib/postgresql/16/bin/pg_ctl -D /tmp/ra04-w1-lifecycle.tsXYaX/data -m fast -w stop` 并核验 `postmaster.pid` 不存在；只停止本次自有instance。30个新增永久测试不构成W2证据或执行授权。本地fix commit后STOP，等待Review Project；不push、PR、merge或开始W2。

## 8. W2 continuation

The current handoff records W1 independent review and PR #144 integration at main `c4d6750eb42af5556036419980a0eb312f892d78`. [W2 verification](research-response-verification.md) now supplies production interpretation and a dedicated exact dispatcher. The original W1 refusal, validation results and §7 lifecycle fix above remain historical. Hold-generation invalidation and all legacy consumer refusals remain enforced. W1 receipts now report `requires_exact_dispatch`, with `execution_authorized=false`; the former controlled test seam cannot qualify the production dispatcher. W2 independent review and W3 demonstration remain outstanding.
