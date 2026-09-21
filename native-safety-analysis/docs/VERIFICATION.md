# VERIFICATION — 验证记录

## 验证金字塔现状

| 层 | 状态 | 证据 |
| --- | --- | --- |
| Schema/解析检查 | ✅ 已执行 | tests/test_seed_cases.py（15 种子例）；防御性解析（PARSE/IO 错误路径） |
| 领域语义检查 | ✅ 已执行 | 12 类结构拒绝路径（含 N01–N05 负例、环、K_OF_N 边界、独立性缺失） |
| **失效率语义闸门** | ✅ 已执行 | 10 类 rate 拒绝路径（repairable/dormant/weibull/单位/负值/双声明/version/语义串） |
| 代数/手算用例 | ✅ 已执行 | M01/M02/M03/M04 精确分数对照（0.02/0.28/0.044/0.1） |
| **超越函数数值对照** | ✅ 已执行 | q 与 libm `expm1` 相对误差 ≤1e-15；与精确有理级数 oracle 差 <1e-38 |
| **重要度手算锚点** | ✅ 已执行 | AND/OR/2-of-3/M03 共享子图/跳层构型 5 组手算值精确命中（tests/test_importance.py） |
| **重要度共因子恒等式** | ✅ 已执行 | 每次运行对每个事件精确验算 `Q = qᵢq+ᵢ+(1−qᵢ)q−ᵢ`，不等即判缺陷并如实失败 |
| 小模型穷举 | ✅ 已执行 | verification/reference_fta.py（独立真值表 oracle）；reference_importance.py（独立共因子 oracle） |
| 随机/变形测试 | ✅ 已执行 | 210+60 随机模型交叉（结构）；120+40（rate 转换）；120（重要度，4072 项精确对照）；8 类变形不变量 |
| **支持集双路判定** | ✅ 已执行 | 生产：图上"该层有节点"；参考：真值表仅差一位的行配对。274/509 事件命中"不在支持集" |
| **存储无浮点审计** | ✅ 已执行 | 声明类型审计 + SQLite `typeof()` 逐值扫描（4501 值，零 REAL）；`store verify` 同法自检 |
| **存储数值重推导** | ✅ 已执行 | verification/run_store_verification.py：raw sqlite3 读回，用 2ⁿ oracle 重算概率/割集/七项重要度，**精确相等无容差**（1362 项） |
| **基线与 stale 独立重算** | ✅ 已执行 | 规范形式→SHA-256 独立重实现（53 份基线）；stale 由 `current_baseline_hash` 重算比对 |
| **存储状态机属性** | ✅ 已执行 | 幂等/冲突/原子性/乐观并发/评审状态机/Agent 无批准权，7 类拒绝码实测触发 |
| **验证自身有效性（突变测试）** | ✅ 已执行 | 9 种篡改（改数值/删行/改基线/翻 stale/加浮点列/存 REAL）**全部被检出** |
| **FMEA 行独立重哈希** | ✅ 已执行 | verification/run_fmea_verification.py：15 当前行 + 2 被取代行由**原始列**独立重建 `fmea-canonical-v1` 规范形式，须等于入库 `canonical_json` 且重哈希等于 `content_hash` |
| **FMEA 来源与确认** | ✅ 已执行 | 5 条推断行逐条验证 `inference_note` 非空；批准人不得是机器标识；每行必须可回溯到一份同内容哈希的 `applied` 候选 |
| **FMEA 修订不可变** | ✅ 已执行 | 2 条被取代行的内容字节不变，仅 `superseded`/`superseded_by_version` 变化；后继版本必存在且更大；同名至多一个当前版本 |
| **FMEA 追溯多对多** | ✅ 已执行 | 34 条关联（26 命中 / 8 悬空）由规范形式与关联表**双向**比对；悬空关联**报告并计数**，不判为完整性失败 |
| **FMEA 状态机** | ✅ 已执行 | 独立复跑候选全生命周期：未批准不得应用、机器无批准权、已批准后基线移动即失效、非法草稿被记录为 `invalid`、修订必指名目标版本 |
| **FMEA 突变测试** | ✅ 已执行 | 20 种篡改（改哈希/改规范形式/改标量列/改控制项/改需求/增删关联行/清空推断说明/非人类批准/无批准人/断后继链/植入第二当前版本/清空基线锚/改候选规范形式/非法状态/非人类决定/应用指向不存在版本/**把未填写的关注项晋升入表**/加 REAL 列/存 REAL）**全部被检出** |
| **关注项不得成为事实** | ✅ 已执行 | 带 `[UNCONFIRMED]` 标记的草稿被人类批准后，`apply` 仍以 `FMEA_PLACEHOLDER` 拒绝且正式表零写入；正式表若出现带标记行（绕过闸门或带外改写）即判失败 |
| **FMEA 存储无浮点审计** | ✅ 已执行 | 115 个新增表值 `typeof()` 扫描，零 REAL（与存储层同法） |
| 第三方差分 | ❌ 未执行 | SCRAM 评估列为后续项（许可/GPL 审查先行） |
| 专家 Gold Case | ❌ 未执行 | 需业务专家与脱敏模型（依赖启动会决策） |
| 封存迁移任务 | ❌ 未执行 | 30 天范围 |
| 无外网/无 Medini 运行 | ✅ 已执行 | 见下"独立运行验证" |

## 实际执行的命令与结果（2026-09-21，Windows 11，Git Bash）

```text
$ python -m pytest tests/ -q                     # venv Python 3.13.12 + pytest 9.1.1
226 passed in ~37s    （含 test_fmea.py 85 项）

$ python verification/run_cross_check.py         # 结构：标准库 Python 3.13.12
cross-check: 210/210 passed
（10 种子正例 + 200 随机模型；概率 Fraction 精确相等，割集族精确相等）

$ python verification/run_rate_cross_check.py    # 转换：独立有理级数 oracle
rate cross-check: 120/120 passed
  tolerance        : 1e-30
  max observed diff: 3.792e-41  (3.792e-11 of tolerance)
  max event q      : 9.999546e-01

$ python verification/run_importance_cross_check.py   # 重要度：精确相等，无容差
importance cross-check: 120/120 models passed
  exact comparisons : 4072 values (no tolerance used)
  part A            : 80 fixed-probability models
  part B            : 40 rate-derived models, smallest event probability 1.000e-07
  coverage (a green run is not vacuous):
    events compared            : 509
    events outside the support : 274
    events in >1 gate input    : 151
    zero Birnbaum results      : 298
    undefined measures hit     : 301
    zero-top-probability models: 13

$ python verification/run_store_verification.py  # 存储：2^n oracle 重新推导库内数值
mutation check (the checks above must be able to fail)
  caught   9 / 9 tamperings
store verification
  population            : 14 fixed + 8 rate-derived runs
  runs re-derived       : 52
  events compared       : 202
  importance values     : 1362 (exact, no tolerance)
  undefined measures    : 52
  exact rational texts  : 166 stored as n/d, 1196 as decimals
  baselines re-hashed   : 53 (canonical form -> SHA-256, recomputed independently)
  storage values scanned: 4501 (SQLite typeof — zero REAL expected)
  superseded runs       : 7 (re-derived like any other run)
  idempotent no-ops     : 1
  refusals triggered    : APPROVAL_AUTHORITYx1, BASELINE_CONFLICTx4, BASELINE_NOT_CURRENTx1,
                          REVIEW_NOT_FOUNDx2, REVIEW_STATEx3, RUN_ID_CONFLICTx1,
                          STORE_SCHEMA_MISMATCHx1
  population problems   : 0
store verification: PASSED

$ python verification/run_fmea_verification.py   # FMEA：原始列独立重建 + 重哈希
mutation check (the checks above must be able to fail)
  caught   20 / 20 tamperings
fmea verification
  models                : 5 with a recorded baseline
  official rows         : 15 current, 2 superseded (all re-hashed from raw columns)
  links checked         : 34 (26 resolved, 8 dangling — reported, not repaired)
  many-to-many proof    : 8 events cited by >1 row, 12 rows citing >1 event
  machine-inferred rows : 5 (each still states what is unconfirmed)
  attention drafts      : 1 marked work items (refused promotion, custom text)
  candidates            : 20 (17 applied, 1 rejected, 1 invalid, 1 pending)
  storage values scanned: 115 (SQLite typeof — zero REAL expected)
  refusals triggered    : APPROVAL_AUTHORITYx2, BASELINE_CONFLICTx1, FMEA_NOT_FOUNDx2,
                          FMEA_PLACEHOLDERx1, FMEA_REVISION_CONFLICTx4, FMEA_STATEx4,
                          MODEL_NOT_FOUNDx1, STORE_BAD_ARGUMENTx1
  population problems   : 0
fmea verification: PASSED

$ python run.py analyze examples/R01_rate_and.json --evidence-dir evidence --run-id r01_doc
status: succeeded; model_schema 0.2.0; top_probability 1.997002664917588e-6; exit 0
  └ 证据包：manifest.json + inputs/ + results/ + reports/report.md + execution/

$ python run.py analyze …M03… --db store.sqlite --run-id r1   → store.status=recorded;   exit 0
$ python run.py analyze …M03… --db store.sqlite --run-id r1   → store.status=idempotent; exit 0
$ python run.py analyze …M02… --db store.sqlite --run-id r1   → RUN_ID_CONFLICT;        exit 2
$ python run.py store  verify store.sqlite                    → problems=[];            exit 0
$ python run.py store  show   store.sqlite nope               → RUN_NOT_FOUND;         exit 2
$ python run.py review propose … --reviewer agent             → 记 proposed；基线不动
$ python run.py review decide  … --approve --reviewer agent   → APPROVAL_AUTHORITY;    exit 2
$ python run.py review apply   … --reviewer JerryKogami       → applied；旧 run 标 stale

$ python run.py fmea propose   … --source inference --inference-note "…" --reviewer agent
                                                              → 记 draft；正式表仍为空
$ python run.py fmea propose   … --source inference            → FMEA_SOURCE;          exit 2
$ python run.py fmea decide    … --approve --reviewer agent    → APPROVAL_AUTHORITY;   exit 2
$ python run.py fmea apply     … --reviewer JerryKogami        → applied；推断说明原样保留
$ python run.py fmea rows      <db> --model MOD              → 仅当前版本；--all 含被取代
$ python run.py fmea trace     <db> --event A                 → 引用 A 的全部行
$ python run.py fmea trace     <db> --component C01           → 该组件的全部行
$ python run.py fmea trace     <db> --dangling                → 悬空关联（基线移动后失联）
$ python run.py fmea apply     … （批准后基线被移动）           → BASELINE_CONFLICT;   exit 2
$ python run.py store  verify  store.sqlite                   → problems=[]（悬空关联不算损坏）; exit 0

$ python run.py fmea propose-from-importance <db> --run r1 --top 2
                                                              → 生成 2 条关注项草稿；正式表仍为空
$ python run.py fmea propose-from-importance <db> --run r1 --top 2
                                                              → 再跑：proposed=0，全部 already_pending
$ python run.py fmea propose-from-importance <db> --run r1 --min-value 0.9
                                                              → 按精确有理数过滤；proposed=1
$ python run.py fmea propose-from-importance <db> --run r1 --by risk_reduction_worth
                                                              → 未定义的 RRW 跳过并注明 measure_is_undefined
$ python run.py fmea propose-from-importance <db> --run r1（该 run 已被取代）
                                                              → BASELINE_NOT_CURRENT; exit 2
$ python run.py fmea decide    <db> ATTN-r1-A --approve --reviewer JerryKogami → approved
$ python run.py fmea apply     <db> ATTN-r1-A --reviewer JerryKogami
                                                              → FMEA_PLACEHOLDER;    exit 2（未填写不得入表）

$ python run.py validate <repairable:true>      → RATE_UNSUPPORTED; exit 3
$ python run.py validate <模型 weibull>          → RATE_UNSUPPORTED; exit 3
$ python run.py validate <v0.1.0 含 failure_rate>→ VERSION;          exit 2
$ python run.py validate <语义串与内容不符>       → ASSUMPTIONS;      exit 2
$ python run.py validate <probability+rate 并存> → RATE_VALUE;       exit 2
$ python run.py validate <lambda_unit=1/s>       → RATE_UNITS;       exit 2
```

## 关键数学锚点（全部通过）

| 用例 | 期望 | 生产内核 | 独立参考 |
| --- | --- | --- | --- |
| M03 重复事件 (A∧B)∨(A∧C) | 0.044（**非 0.0494**） | 11/250 ✅ | 11/250 ✅ |
| M04 吸收 A∨(A∧B) | 0.1，割集仅 {A} | 1/10 ✅ | 1/10 ✅ |
| M05 2/3 表决 | 0.098 | 49/500 ✅ | 49/500 ✅ |
| M08 极小概率 | 1e-24 精确 | 1/10²⁴ ✅ | 1/10²⁴ ✅ |
| M06 p=1 边界 | 1 | 1 ✅ | 1 ✅ |
| 210 随机结构模型 | — | Fraction 全等 ✅ | — |
| **λ=1e-6, t=1000 → q** | 9.995001666250083e-4 | libm 一致（≤1e-15 相对）| 有理级数差 <1e-38 ✅ |
| **λ=1e-6, t=1000；λ=2e-6, t=1000 的 AND** | — | q₁·q₂ = 1.9970026649175885e-6 ✅ | 同值（容差内）✅ |
| **120 随机 rate 模型** | — | 与 oracle 最大差 **3.79e-41**（容差 1e-30）✅ | — |
| 重要度 AND(A,B) q=0.1/0.2 | BI=0.2/0.1，FV=1，RAW=10/5，RRW 无界 | 精确命中 ✅ | 精确命中 ✅ |
| 重要度 OR(A,B) q=0.1/0.2 | BI=0.8/0.7，FV=2/7，RAW=25/7，RRW=7/5 | 精确命中 ✅ | 精确命中 ✅ |
| 重要度 2-of-3 等概率 q=0.1 | BI=9/50，q+=19/100，q−=1/100 | 精确命中 ✅ | 精确命中 ✅ |
| 重要度 M03 共享子图 | A:FV=1（本质事件）；B:FV=7/22；C:FV=6/11 | 精确命中 ✅ | 精确命中 ✅ |
| 重要度 跳层构型 x₂∧(x₀∨x₁) | BI(x₁)=27/100（q+=3/10, q−=3/100） | 精确命中 ✅ | 精确命中 ✅ |
| **120 重要度模型（含 rate 派生 1e-7 量级）** | — | 与 oracle **精确相等**（无容差，4072 项）✅ | — |
| **支持集判定** | — | 与真值表差分判定 509/509 一致 ✅ | — |
| 重要度性能压力（链式 n=1500） | — | 2.12s（关）→ 5.41s（开），恒等式对 1500 条记录全成立 ✅ | — |
| **库内 52 次 run 的概率/割集/重要度** | — | 与 2ⁿ oracle 重推导值**精确相等**（1362 项，无容差）✅ | 同值 ✅ |
| **库内 53 份基线规范形式** | — | 独立重哈希 == 主键（53/53）✅ | — |
| **`Fraction(store(x)) == x`（M03 逐字段）** | — | 包括 `fussell_vesely="7/22"` 等非有限小数 ✅ | — |
| **导出→恢复往返** | — | `dump(restore(dump)) == dump`，且副本再通过全部检查 ✅ | — |

## 失效率转换的独立性证据链

生产路径（`domain/rate_model.py`）与参考路径（`verification/run_rate_cross_check.py`）的分离方式：

| 维度 | 生产路径 | 参考路径 |
| --- | --- | --- |
| 数值类型 | `decimal.Decimal`（工作精度 prec+15） | `fractions.Fraction`（纯有理数） |
| exp 求值 | 小 x 用 q 的泰勒级数；中 x 用 `Decimal.exp` | exp(−x) 的泰勒级数；x>5 用半角递推 `exp(−x)=exp(−x/2)²` |
| 收敛判据 | 工作精度保护位 + `ROUND_HALF_EVEN` | 项绝对值 < 1e-90（远超所需） |
| 求解 | ROBDD + 精确 Shannon | 2ⁿ 真值表穷举 |
| 共享代码 | **无**（仅共享 `False`/`True`） | **无** |

因为 q 是超越数，两条路径各自产生**不同的有理舍入**，故对照使用**声明容差**（绝对 1e-30）而非精确相等。容差的两个方向都被约束：
- **上界不掩盖缺陷**：实测最大偏差 3.79e-41，仅为容差的 3.8e-11；一个真实的实现缺陷（如少乘一项、极性错误、精度不足）会带来 ≫1e-30 的偏差
- **下界不受舍入支配**：两路舍入误差上界约 1e-40，远低于 1e-30

## 重要度度量的独立性证据链

| 维度 | 生产路径（`kernel/importance.py`） | 参考路径（`verification/reference_importance.py`） |
| --- | --- | --- |
| 共因子求法 | 单遍自顶向下 reach + 差分数组累计跨层质量 | 对每个事件把 xᵢ 钉死，穷举其余 n−1 个变量的全部赋值 |
| 数据来源 | 编译后的 ROBDD 与概率表 | 门树直接递归求值，**没有任何图结构** |
| 支持集 | 图上"该层是否存在节点" | 真值表仅差一位的行配对比较 |
| 处理"变量不在支持集" | 层高于根节点时取 q±=Q | 不特殊处理（穷举自然得出同一个值） |
| 共享代码 | **无** | **无** |

两条路径均为精确有理数运算，**不含任何超越函数**，因此对照判据是**逐值精确相等，无任何容差**——比 rate 转换侧更强。

**这个对照抓到了两个真实缺陷**（不是理论演练）：

1. **生产侧**：变量层高于根节点时，差分数组收不到跳跃边，"未提及 xᵢ 的质量"被算成 0 而正确值是 Q，导致共因子恒等式失败（随机模型 RANDOM_2476）。已修复并锁进回归测试。
2. **参考侧**：穷举时把被钉死变量的概率因子也乘进权重，实际算的是 P(top ∧ xᵢ) 而非 P(top | xᵢ=1)。症状是出现**负的 Birnbaum 值**（相干系统中不可能）。已修复。

**覆盖度（说明绿不是空绿）**：120 个模型共对照 509 个事件；其中 274 个事件**不在支持集**内（走 `BI=0, RAW=RRW=1` 分支）、151 个事件出现在多个门输入中（重复事件/共享子图）、298 个事件 Birnbaum 为 0、命中 301 个未定义度量、13 个模型顶事件概率为 0。全部走"恒为真"分支是不可能的。

> 说明：生产侧内建的共因子恒等式校验（`Q = qᵢq+ᵢ+(1−qᵢ)q−ᵢ`）只是**自检**——它约束加权和，不单独约束 `q+ᵢ`、`q−ᵢ` 各自正确。真正排除"两者同时错但加权和凑巧正确"的是上面的独立穷举对照。

## 存储层的独立性证据链

存储的"正确性"不是"自己和自己一致"，而是"库里的每个数都能被另一条算法重新算出来"。验证脚本 `verification/run_store_verification.py` 因此**不导入 store 模块来读数据**，而是用 raw `sqlite3` 打开文件——这本身就证明了落盘格式可被第三方直接检视。

| 检查维度 | 被测对象（`store/`） | 独立验证 |
| --- | --- | --- |
| 顶事件概率 | 结构化列（精确文本） | 由规范形式重建 0.1.0 模型 → `reference_fta.ref_solve` 2ⁿ 真值表穷举 |
| 最小割集 | `cut_sets_json` | 同上 oracle 的独立割集枚举 |
| 重要度七项 | `importance` 表九列 | `reference_importance.ref_importance` 逐事件钉死穷举 |
| 支持集 | `in_support` 列 | 真值表仅差一位的行配对 |
| 基线锚 | `baselines.baseline_hash` | **本文件内**重新实现规范化→SHA-256，与主键比对 |
| stale | `runs.stale` | 由 `models.current_baseline_hash` 重算该派生量 |
| 无浮点 | 结构化列 | 声明类型审计 + SQLite `typeof()` 逐值扫描 |
| 载荷一致性 | `payload_json` 与结构化列 | 两份**独立序列化**逐条比对 |

判据是**逐值精确相等、无任何容差**：库里没有超越函数，任何差异都是缺陷。

### 突变测试：证明上面这些检查真的会失败

绿灯只有在这个脚本"能红"的时候才有意义。因此每次运行都对一份副本施加 9 种篡改，要求全部被检出：

| 篡改 | 被哪条检查抓住 |
| --- | --- |
| 改写一个已存重要度值（FV） | 与 oracle 精确比对 |
| 改写一个已存 Birnbaum 值 | 与 oracle 精确比对 |
| 改写已存顶事件概率 | 与 oracle 精确比对 |
| 改写割集族 JSON | 与 oracle 比对割集 |
| 翻转 stale 标记 | stale 派生量重算 |
| 删除一行重要度 | 行数 vs `variable_count` + oracle |
| 就地改写基线规范形式 | 规范形式→哈希重算不匹配 |
| 在 schema 中加一个 REAL 列 | 声明类型审计 |
| 存入一个 REAL 值（BLOB 亲和列，无强转） | `typeof()` 逐值扫描 |

实测 **9/9 全部检出**。任何一条"未被检出"都会被脚本判为失败——因为那意味着对应的检查是装饰性的。

### 覆盖度（说明存储验证的绿不是空绿）

52 次 run 重推导、202 个事件、1362 个重要度值精确比对、52 个未定义度量、53 份基线重哈希、4501 个值做存储类扫描；其中 **166 个值真的以 `n/d` 有理数形式落盘**（FV/RAW/RRW 通常是概率之比，必然非有限小数）——若存储层偷偷用了浮点，这 166 个值不会以有理数文本存在。7 个 run 位于已被取代的基线上（stale 路径被真实覆盖），7 类拒绝码被实际触发。

### 一个由测试抓出的真实缺陷

`store.verify()` 最初只在"某 run 一条重要度行都没有"时报错。测试用例 `test_verify_detects_corruption[DELETE FROM importance …]` 删掉**一行**（还剩两行）时，剩余行彼此完全自洽，`verify()` 却报干净。修复：用 run 行里已存的 `variable_count` 与行数比对，并要求 `rank_in_run` 是连续的 0..n−1。这正是"自洽 ≠ 完整"的实例——独立 oracle 一开始就抓住了它，因为 oracle 知道应该有几个事件。

## FMEA 基础表与追溯的独立性证据链

FMEA 的"正确性"同样不能靠"自己和自己一致"。`verification/run_fmea_verification.py` 同样**不导入 store 模块**，只用 raw `sqlite3` 打开库文件，从**列级原值**（标量列、JSON 侧列、`fmea_links` 关联行）重新拼装对象、独立重实现 `fmea-canonical-v1` 规范化与 SHA-256，再与库中 `canonical_json`/`content_hash` 三方比对。

| 检查维度 | 被测对象（`store/`） | 独立验证 |
| --- | --- | --- |
| 行规范形式 | `fmea_rows` 标量列 + JSON 侧列 + `fmea_links` | 脚本内**重新实现**规范化（集合并排序、字段裁剪）→ 重建 JSON，逐字节比对 |
| 内容哈希 | `fmea_rows.content_hash` | 对重建的规范形式**独立重哈希** SHA-256 |
| 列↔规范形式一致性 | 全部标量列 | 重建对象与存储对象逐字段比对（差异字段名如实报出） |
| 来源与确认 | `source` / `canonical_json.inference_note` | 推断行说明非空；来源词表校验 |
| 批准权威 | `approved_by` | 脚本内**自带一份** `NON_APPROVING` 名单（不 import 生产常量）逐行判定 |
| 可回溯性 | `fmea_candidates` | 每行须存在一份 `state='applied'` 且 `proposed_content_hash` 相等的候选 |
| 修订链 | `superseded` / `superseded_by_version` | 后继存在且版本更大；同名至多一个当前版本 |
| 追溯完整性 | `fmea_links` ↔ `canonical_json.linked_event_ids` | 双向集合比对；对当前基线事件集解析，悬空项**返回**而非失败 |
| 无浮点 | 三张 FMEA 表 | SQLite `typeof()` 逐值扫描 |
| **关注项标记** | `canonical_json` 的四个描述字段 | 标记字符串**在本文件内另行列出**（不 import 生产常量）；任一正式行带标记即失败 |
| 状态机 | 候选生命周期 | 在**临时库**上独立复跑，8 类拒绝码逐一实测触发（含"带标记的关注项被批准后仍被拒绝晋升"） |
| 生成器降噪依据 | `covered_event_ids` / `drafted_fmea_states` | 在临时库上一致断言：草稿一旦存在，生成器必须能看见它（否则会重复唠叨） |

判据同样**逐值精确相等、无容差**：FMEA 侧不含超越函数，任何差异都是缺陷。

### 突变测试：证明这些 FMEA 检查真的会失败

每次运行对一份副本施加 **20 种**篡改，要求全部被检出：

| 篡改 | 被哪条检查抓住 |
| --- | --- |
| 改写一行的 `content_hash` | 规范形式独立重哈希不匹配 |
| 就地改写一行的 `canonical_json` | 原始列重建出不同的规范形式 |
| 改写一个标量列（与规范形式不符） | 同上（报出差异字段名） |
| 改写控制项侧列 | 同上（`controls`） |
| 改写需求列表 | 同上（`requirement_ids`） |
| 删除一条关联行 | 同上（`linked_event_ids`） |
| 增加一条关联行 | 同上（`linked_event_ids`） |
| 清空推断行的 `inference_note` | "机器推断却未说明待确认事项" |
| 用非批准身份批准一行 | "由非可批准身份批准" |
| 抹掉批准人 | "无批准人" |
| 断开被取代行的后继 | "被取代但无后继" |
| 为同一 `fmea_id` 植入第二个当前版本 | "同名多个当前版本" |
| 清空行的 `validated_baseline_hash` | "无基线锚" |
| 改写候选的 `proposed_canonical_json` | 候选规范形式与记录的哈希不符 |
| 把候选状态改为未知值 | 该行无法回溯到 `applied` 候选 |
| 用非批准身份决定候选 | 同上 |
| 让 `applied` 候选指向不存在的版本 | "应用版本不存在" |
| **把未填写的关注项晋升入表**（列、规范形式、哈希**一致改写**，只留标记这一处错误） | "正式行仍带机器关注项占位符" |
| 在 schema 中加一个 REAL 列 | 声明类型审计 |
| 存入一个 REAL 值 | `typeof()` 逐值扫描 |

实测 **20/20 全部检出**。任何一条"未被检出"都会让脚本判为失败——因为那意味着对应检查是装饰性的。

### 覆盖度与真空度闸门（说明 FMEA 的绿不是空绿）

脚本在**结束时强制自检**：拒绝码种类必须 ≥7、悬空关联集合必须非空、关注项计数必须非零、且 `FMEA_PLACEHOLDER` 必须被实际触发过，否则直接判失败（防止"温室种群"把绿变成空绿）。本次实测：5 个含基线模型、15 当前行 + 2 被取代行、34 条关联（26 命中 / **8 悬空**）、8 个事件被多于一行引用、12 行引用多于一个事件、5 条机器推断行、1 个关注项工单（被拒绝晋升）、20 个候选（17 applied / 1 rejected / 1 invalid / 1 pending）、115 个存储值扫描、**8 类**拒绝码实际触发（含 `FMEA_PLACEHOLDER`）。

### 一个由测试抓出的真实缺陷（FMEA）

`_verify_fmea()` 最初把"后继版本不存在"误报为完整性失败——因为 `versions` 集合是在**行循环内部**累积的，当遍历到 v1 行时，尚未加入 v2 行，于是一个正常的历史版本被判为"后继缺失"。修复：在循环前**先做一遍全表扫描**收集全部 `(model_id, fmea_id, version)`。

同样在设计 `apply_fmea_candidate` 时发现：`certainty` 与 `inference_note` 被**刻意排除在哈希之外**（描述性），因此无法从规范形式反推——若不在提案时把 provenance 一并落盘，批准应用后会**静默丢失**推断说明。修复：候选表新增 `proposed_provenance_json` 列，提案时存入 `fmea_provenance(row)`，应用时读回。

## FMEA 基础表不变量（tests/test_fmea.py，85 项）

34. 身份是**稳定标识符**，不是显示名：改 `failure_mode` 文案不改 `fmea_id`，行身份不变
35. 行 ↔ 基本事件是**多对多**：一行可关联多事件，一事件可被多行引用，**永不强制一对一**
36. `certainty` 与 `inference_note` 在**哈希之外**：仅改这两项（`dataclasses.replace`）不改变 `content_hash`
37. 关联集合**顺序不敏感**：`linked_event_ids` 重排不改变哈希；控制项/证据/需求同理
38. `source='inference'` 而无 `inference_note` → 校验拒绝（`FMEA_SOURCE`）：推断**永不被记为定论**
39. 未知事件 id 关联 → 拒绝（`FMEA_LINK`）；空关联行**允许**（未关联 ≠ 非法）
40. 草稿单独存在**不触碰正式表**：`propose` 后正式行数不变，`list_fmea_rows` 仍为空
41. 机器身份不能决定或应用（`APPROVAL_AUTHORITY`）；推断行**仍须人类批准**
42. 修订是**新版本**：应用后旧版内容字节不变，仅 `superseded`/`superseded_by_version` 改变；同名至多一个当前版本
43. 修订必须**指名目标版本**（`expected_version`），指向被取代版本 → 拒绝
44. 批准**同时绑定基线与内容**：批准后基线被移动 → `apply` 以 `BASELINE_CONFLICT` 拒绝该过期批准
45. 悬空关联（基线移动后不再解析）**只报告与计数**，`store verify` 保持干净（合法的模型变更不得读作损坏）
46. 全程 `store verify` 干净：从草稿到应用、含修订与悬空，`verify()` 始终 `problems=[]`
47. 往返：`dump()` == `restore()`，悬空集合保持一致
48. 未定义项从不被占位替代；无浮点列、无 REAL 值

**关注项（重要度驱动）不变量：**

49. 关注项 `fmea_id` 由**事件 id 派生**（`FMEA-ATTN-<event>`），同一事件重复生成映射到同一身份，不产生第二份工单
50. 关注项的**定量理由精确可复现**：`inference_note` 含度量名、精确值、可比事件中的排名、`run_id`、基线哈希前缀
51. 关注项**不编造任何内容**：四个描述字段带 `[UNCONFIRMED]` 标记，组件/功能为 `*-UNASSIGNED`，控制/证据/要求为空
52. **带标记的草稿即使被具名人类批准，`apply` 仍以 `FMEA_PLACEHOLDER` 拒绝**，且正式表零写入
53. 人类回答工单的方式是**为同一 `fmea_id` 另行提出真实内容**；原工单草稿保持 `proposed`，不被顺带晋升
54. **重复生成是空操作**：已存在草稿（`already_pending` / `already_decided`）或事件已被正式行引用（`already_in_official_table`）一律跳过并给出理由
55. `--top` **只计新增草稿**；已覆盖/已起草的不占名额；跳出时把未扫描的剩余条数**作为计数如实返回**
56. `--min-value` 按**精确有理数**比较（`1/3` 与 `0.3` 的排序不会被文本序弄反）；未定义度量一律跳过（`measure_is_undefined`）
57. **过期基线不生成**：run 已落在被取代基线上时 `BASELINE_NOT_CURRENT` 拒绝，候选表零写入
58. 生成器**不触碰正式表**：`propose-from-importance` 之后 `list_fmea_rows` 仍为空
59. 正式表若出现带标记行（绕过闸门或带外改写）→ `verify()` 报出，且**只报这一条**（即便其余派生工件被一致改写）

## 变形测试（不变量，全部通过）

1. 输入重排（事件/门数组倒序）→ 概率与割集不变
2. 子节点交换（AND(a,b) → AND(b,a)）→ 概率不变
3. 吸收律 A∨(A∧B) ≡ A → 概率与割集不变
4. 分配律 (A∧B)∨(A∧C) ≡ A∧(B∨C) → 概率不变（两侧 0.044）
5. 重复引用 AND(A,A,B) ≡ AND(A,B) → 概率不变、无新事件
6. 单调性：相干模型中提高任一事件概率不降低顶事件概率（25 轮随机）
7. 结果 ∈ [0,1]（15 轮随机）
8. 割集族无包含超集（30 轮随机模型）

rate 转换另有独立不变量（tests/test_rate_model.py）：λ 单调、t 单调、q∈[0,1]、λ=0→q=0、λt 饱和→q=1、小 x 下 q<x 且 q/x→1、精度参数被遵守。

重要度另有不变量（tests/test_importance.py，60+ 随机模型）：

9. 精确恒等式 `Q = qᵢ·q+ᵢ + (1−qᵢ)·q−ᵢ`（逐事件）
10. 相干性序关系：`BI ≥ 0`、`RAW ≥ 1`、`RRW ≥ 1`、`0 ≤ FV ≤ 1`
11. 度量间换算恒等式：`FV = 1 − 1/RRW`（RRW 有定义时）、`RAW = 1 + (1−qᵢ)·BI/Q`
12. `BI = q+ᵢ − q−ᵢ`（定义性）
13. 不在支持集 ⟹ `BI=0, FV=0, RAW=1, RRW=1`
14. `in_support` 与 `BI≠0` **不等价**（构造反例：2-of-3 中另两个事件概率为 0）
15. 吸收律：`OR(A,A)` 与单事件模型的所有度量精确一致
16. 门输入顺序置换不改变任何度量（Boolean 等价 + 变量序变化下的语义不变性）
17. RRW 为 null ⟺ `q−ᵢ=0`，且 reason 恒为 `risk_reduction_worth_unbounded`
18. `Q=0` ⟹ FV/RAW/RRW 三者均为 null 且 reason 恒为 `top_probability_is_zero`，BI 仍有定义
19. 未定义项**从不**被占位数字替代（字段为 `null` 且必带 reason）

存储另有不变量（tests/test_store.py，53 项）：

20. `Fraction(store(x)) == x`：每个概率/重要度列读回后与源 `Fraction` 精确相等（含 `n/d`）
21. 库中任何表、任何列的 `typeof()` 都不出现 `real`
22. 同 `run_id` 同内容 → 幂等空操作，且**整行字节级不变**（连作者都不覆盖）
23. 同 `run_id` 不同内容 → `RUN_ID_CONFLICT`，且已有行不变
24. 被拒的写入是**原子**的：模型表/基线表计数不变（首次注册也回滚）
25. 基线行插入后不再被改写（换基线后逐字节比对旧行）
26. `stale ⟺ baseline_hash ≠ current_baseline_hash`（换基线后新旧 run 各就各位）
27. 换基线后旧 run 的数值字段完全不变（旧证据仍可读）
28. 评审状态机：仅 `proposed` 可被决定，仅 `approved` 可被应用，重复决定/重复应用/未批准即应用均拒
29. `agent`/`assistant`/`ai`/`bot`/`local-cli`/`unknown`/空值均不能决定或应用（`APPROVAL_AUTHORITY`）
30. 批准后基线被移动 → `apply` 以 `BASELINE_CONFLICT` 拒绝该过期批准
31. 提案的规范形式被旁路修改 → `apply` 以 `INTEGRITY` 拒绝（重新哈希比对）
32. 恢复出的副本与原库 `dump()` 逐行相同，且仍会拒绝冲突的 `run_id`
33. `store verify` 能检出：改数值 / 删行 / 改割集 / 翻 stale / 改基线规范形式 / 增加 REAL 列 / 破坏多重线性恒等式

## 独立运行验证（无 Medini、无 A 线、无 LLM、无网络）

- 全部代码仅用 Python 标准库（json/re/fractions/hashlib/dataclasses/argparse/pathlib/decimal/**sqlite3**）
- CLI 与内核在无网络沙箱实测通过（上列命令即实测）
- 系统 Python 3.11 与托管 Python 3.13 均验证通过
- 唯一外部依赖 pytest 仅用于测试执行，生产链路零依赖
- 存储库为单个 `.sqlite` 文件（journal_mode 保持默认 DELETE，不产生 `-wal`/`-shm` 旁文件）

## 待专家复核事项

- 种子案例的语义解释（"概率 0 事件保留在割集中"等）需安全专家确认符合工程预期
- **失效率语义适用范围**：`q = 1 − exp(−λt)` 仅在"恒定率 + 不可修复 + 任务时间固定"下成立。业务模型若含修复/周期性检查/潜伏失效，须走各自语义，本版本一律拒绝。需安全专家确认工程惯例
- **数值容差冻结**：结构侧当前为精确有理数（无误差）；rate 侧引入一次受控舍入（默认 40 位有效数字，实测偏差 ~1e-41）。正式冻结提案：绝对 1e-30 —— 待算法负责人 + 验证者裁决
- K_OF_N 在真实业务模型中的使用约定（k 与输入语义）待 Gold Case 阶段确认
- **重要度口径确认**：本版本 RAW/RRW 采用**比值形式**（`q+/Q`、`Q/q−`，DOE/NASA PSA 惯例），非增量形式。若业务方或适航审查要求增量口径（`1+ΔQ/Q`、`1−ΔQ/Q`），需作为**新增度量**而非改定义，以免历史结果不可比。待安全专家确认
- **重要度未定义情形的处置**：`Q=0` 或 `q−ᵢ=0` 时 FV/RAW/RRW 数学上无定义，本版本如实输出 `null` + reason，不做任何近似替代。若工程上需要"退化值"，需先由安全专家给出语义规格
- **基线与并发策略**：一期单写者 + 乐观并发（写入时校验预期基线哈希），不做锁。团队版（PostgreSQL）需重新裁决并发模型与冲突提示粒度
- **"可批准身份"名单**：当前以显式 `--reviewer` 具名 + 一组保留标识（agent/assistant/ai/bot/local-cli/unknown）做拒绝判断。正式阶段应接认证层，由身份系统而非字符串判定授权
- **FMEA 表口径确认**：本版本刻意**不含**严重度/发生度/探测度/RPN，也不产生 FMECA/FMEDA 结论。若工程上需要风险排序，应作为**新增字段与新增语义**并单独验证，而非就地扩展本表
- **推断行的确认流程**：`source='inference'` 的行必须写 `inference_note` 且须人类批准方可入表，但"谁来确认、确认到什么程度算充分"需安全专家给出规程
- **悬空关联的处置**：基线移动后失联的关联只报告与计数、不判为损坏。是否需要"重新挂接"或"显式断开"的操作，待确认
- **变更提案的粒度**：当前提案是一份完整模型（无结构化 patch 语言）。**B05（FMEA/追溯）落地后**该问题仍待定：是否需要对象级 patch 与影响范围分析，转由后续步骤评估
