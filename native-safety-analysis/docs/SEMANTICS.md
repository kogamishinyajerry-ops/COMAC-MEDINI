# SEMANTICS — 支持的模型语义（模型 schema 0.1.0 / 0.2.0）

本文档定义本引擎**实际实现并验证**的语义范围。任何未列于此的输入语义都必须被拒绝，不得静默退化。

结果信封契约（`analysis_result.schema.json`）恒为 **0.1.0**，不随模型 schema 变化。

## 输入契约

采用开工包 `reference/03_contracts/schemas/static_fta.schema.json`（v0.1.0），并按 v0.2.0 增量扩展：

- `schema_version`: `"0.1.0"` 或 `"0.2.0"`（二者为**增量**关系：0.1.0 模型语义不变）
- `basic_events[]`: `id`（正则 `^[A-Za-z][A-Za-z0-9_.:-]{0,127}$`）、`label`、`source`（非空，概率来源声明），以及**恰一个**概率声明：
  - v0.1.0：`probability`（**字符串**十进制，范围 [0,1]）
  - v0.2.0：`probability` **或** `failure_rate`（见下"失效率转换语义"），两者同时出现或同时缺失 → 拒绝
- `gates[]`: `id`、`kind` ∈ {AND, OR, K_OF_N}、`inputs[]`（非空引用列表）；K_OF_N 必须有整数 `k ∈ [1, len(inputs)]` 且输入引用互异；非 K_OF_N 门带 `k` 视为非法
- `top_event`: 必须引用已定义的 ID
- `assumptions`: `basic_events_independent: true`（必须显式声明）、`probability_semantics`、`condition`（非空字符串，说明概率适用条件）
  - `"fixed_conditioned_probability"` — 模型内不存在任何 `failure_rate`
  - `"fixed_mission_probability_from_constant_rate"` — 模型内至少一个 `failure_rate`
  - 语义串与实际内容不符 → ASSUMPTIONS 拒绝（双向检查）
- `rate_precision_digits`（可选，仅 0.2.0）：整数 ∈ [15, 200]，默认 40

## 事件身份与重复引用（核心语义）

**一个 BasicEvent ID = 一个失效事实 = 一个布尔随机变量。**

- 同一事件 ID 在多个门中被引用时，全部指向**同一**变量。`(A AND B) OR (A AND C)` 中两个 A 是同一个 A，正确结果 0.044，绝不允许按独立分支误算 0.0494。
- 模型是可共享节点的有向无环图（DAG），不是每支独立的树。共享子图、重复引用均按 DAG 语义计算。
- 名称不承担身份职能；只有 ID 是身份。
- 概率为 0 的事件仍参与结构布尔语义（割集计算不删除概率 0 事件，见 M06/M09 语义）。

## 门语义

| 门 | 语义 | 验证状态 |
| --- | --- | --- |
| AND | 全部输入为真时为真 | verified（种子 + 交叉验证） |
| OR | 任一输入为真时为真 | verified |
| K_OF_N | 至少 k 个输入为真（输入引用必须互异） | verified（含边界 k=1、k=n） |
| 其他一切（PAND、PDP、SPARE、NOT、XOR…） | **UNSUPPORTED_GATE 拒绝**，退出码 3 | verified（N05 负例） |

## 概率语义

- 固定条件概率 p ∈ [0,1]，无量纲。输入为十进制**字符串**，内部以 `fractions.Fraction` 精确表示，无浮点中间量。
- 不同基本事件相互独立，以输入 `assumptions.basic_events_independent: true` 显式声明为前提；未声明或为 false 一律拒绝（ASSUMPTIONS）。
- 顶事件概率按 BDD 上的精确 Shannon 展开 `(1-p)·P(low) + p·P(high)` 计算，结果为精确有理数，输出渲染为十进制字符串。
- **不支持**：修复、潜伏/检查间隔、共因模型、任意分布、顺序/动态门。这些语义到达时在验证层拒绝，绝不静默当作固定概率。

## 失效率转换语义（模型 schema 0.2.0）

**闸门条件（三者全部满足才转换，缺一即拒）：**

1. `failure_rate.model == "constant_failure_rate"`（其余模型名 → `RATE_UNSUPPORTED`）
2. 显式声明 `repairable: false`（缺失或 true → `RATE_UNSUPPORTED`）
3. `lambda` 与 `mission_time` 单位显式且合法（`lambda_unit ∈ {1/h, /h, per_hour, h^-1, 1/hour}`；`mission_time_unit ∈ {h, hour, hours}`），且 `source` 非空

**转换公式：**

```
q = 1 − exp(−λ·t) = −expm1(−λ·t)        （任务失效概率，无量纲，∈ [0,1]）
```

- 其中 `λ` 单位 1/h，`t` 单位 h，`λ·t` 为无量纲期望失效数
- 转换在**加载时**执行一次；此后事件即为固定概率事件，内核全程精确有理数
- 内部以十进制高精度（默认 40 位有效数字）求值，再存为精确 `Fraction`；位数记录于每次运行的 `rate_provenance`
- **语义警告（强制输出）**：`q` 是**任务时间段内的失效概率**，**不是**每飞行小时失效率指标。输出 `probability_interpretation = mission_failure_probability_from_constant_rate` 并附显式警告，禁止把它当作 per-flight-hour 率使用。

**明确拒绝的失效率语义**（`RATE_UNSUPPORTED`）：可修复（repairable）、`dormant`/`latent`、`inspection_interval`、`repair_rate`、`restoration`、非恒定率（Weibull 等）。这些需要各自的语义规格与验证项，绝不近似为常率。

## 重要度度量语义

对每个基本事件 i，定义两个**共因子**（cofactor）顶事件概率：

```
q+ᵢ = P(top | xᵢ = 1)      事件 i 确定失效时顶事件的条件概率
q−ᵢ = P(top | xᵢ = 0)      事件 i 确定正常时顶事件的条件概率
Q   = P(top)
```

Shannon 展开把 xᵢ 的取值空间分成**互斥**的两部分，因此在全部精确有理数运算下恒有

```
Q = qᵢ·q+ᵢ + (1 − qᵢ)·q−ᵢ          （多重线性恒等式，无任何额外近似）
```

该恒等式在每次运行中对**每个事件**精确校验，不等则判定为内核缺陷并如实失败（退出码 5），绝不静默输出。

四个标准度量（均以精确有理数计算，不引入浮点）：

| 度量 | 定义 | 字段名 |
| --- | --- | --- |
| Birnbaum 重要度 | `BIᵢ = ∂Q/∂qᵢ = q+ᵢ − q−ᵢ` | `birnbaum_importance` |
| Fussell–Vesely | `FVᵢ = qᵢ·BIᵢ/Q = 1 − q−ᵢ/Q` | `fussell_vesely` |
| Risk Achievement Worth | `RAWᵢ = q+ᵢ/Q` | `risk_achievement_worth` |
| Risk Reduction Worth | `RRWᵢ = Q/q−ᵢ` | `risk_reduction_worth` |

**约定（已在每次输出中随 `importance_conventions` 发布）：**

- RAW / RRW 采用**比值形式**（DOE/NASA PSA 惯例），**不是**增量形式 `1 + ΔQ/Q` 或 `1 − ΔQ/Q`
- 基本事件独立（本引擎已声明的语义）时，FV 等价于该事件的 criticality importance
- 相干性保证不变量：`BI ≥ 0`、`RAW ≥ 1`、`RRW ≥ 1`、`0 ≤ FV ≤ 1`

**未定义策略（绝不填占位数字）：**

| 情形 | 未定义的度量 | 上报的 reason |
| --- | --- | --- |
| `Q = 0`（顶事件不可能发生） | FV、RAW、RRW（0/0 或 x/0） | `top_probability_is_zero` |
| `q−ᵢ = 0`（去掉事件 i 后系统不可能失效） | 仅 RRW（Q/0 无界） | `risk_reduction_worth_unbounded` |

未定义时字段为 `null`，并在该事件的 `undefined[]` 中给出 measure 与 reason。BI 在上述两种情形下**仍然有定义**，照常输出。

**支持集（support）报告：** 每个事件带 `in_support` 字段，表示该变量是否真的出现在编译后的函数中。模型里存在但不在支持集内的事件（未被顶事件引用，或其所在层高于根节点层）**不被省略**，而是以 `BI=0, FV=0, RAW=1, RRW=1, in_support=false` 明确报告，使"该事件存在但不影响顶事件"这一事实可见。

> 注意 `in_support` 与 `BI ≠ 0` 不等价：2-of-3 中若另两个事件概率均为 0，则被考察事件在支持集内但 `BI = 0`。

**计算开关（`--importance {auto,on,off}`，默认 auto）：**

- `auto`：基本事件数 ≤ 1000 时计算；超过则**跳过并在输出中声明**（`importance_status = skipped_model_too_wide` + 原因说明），不静默省略
- `on`：总是计算
- `off`：不计算，`importance_measures = null`，`importance_status = not_requested`，并附警告

重要度计算成本约为顶事件概率计算的 2 倍（两遍 O(#节点) 扫描），不随事件数二次增长。

**渲染精度：** 固定概率模型的度量值为**精确有理数**（有限小数渲染为十进制，非有限小数渲染为 n/d，`importance_exact = true`）。由失效率转换而来的模型按模型声明的 rate 精度（默认 40 位有效数字）显示，与 `probability_interpretation` 一致，此时 `importance_exact = false` 并附说明性警告。

## 存储与基线语义（本地 SQLite 库）

单文件 SQLite（`--db <path>`）。关系表足以表达初期本体，不引入图数据库；团队版迁移 PostgreSQL 时用 `store export` 做往返（B_核心规划 §88）。

### 八张表的含义

| 表 | 一行代表 | 关键约束 |
| --- | --- | --- |
| `models` | 一个逻辑模型（按 `model_id` 去重） | `current_baseline_hash` 指向当前基线 |
| `baselines` | 一份**不可变**的语义快照，主键是语义哈希 | 一旦插入，任何字段都不再改写 |
| `runs` | 一次分析结果（含完整结果 JSON） | `run_id` 不可变；`content_fingerprint` 判等 |
| `importance` | 一次 run 中一个事件的度量 | 主键 `(run_id, event_id)`，全为精确文本 |
| `reviews` | 一次模型基线变更提案及其决定 | 状态机见下 |
| `fmea_rows` | 一条**已批准**的 FMEA 行（正式表） | 主键 `(model_id, fmea_id, version)`；内容字段插入后永不改写 |
| `fmea_links` | 正式行 ↔ 基本事件的关联 | 主键 `(model_id, fmea_id, version, event_id)`，多对多 |
| `fmea_candidates` | 一条待确认的 FMEA 草稿 | 状态机与 `reviews` 同构；只有具名人类能批准 |

### 精确存储（§231：显示舍入不得改变存储结果）

- 一切概率类数值以 `TEXT` 存储，内容为**精确十进制**（有限小数）或**精确 `n/d` 有理数**（非有限小数），一律不是浮点。
- 存储的是**计算结果本身的精确有理数**，不是输出信封里经过显示舍入的字符串；两者的差异在结果 JSON 的 `engine_stats.top_probability_exact` 中可见。
- 重要度值按 run + event 逐个精确入库。FV/RAW/RRW 是概率之比，通常非有限小数，因此以 `n/d` 形式入库（例如 M03 的 `fussell_vesely = "7/22"`）。
- 库中**不存在 REAL/FLOAT/NUMERIC/DECIMAL 列**；验证脚本还会用 SQLite 的 `typeof()` 逐个值检查实际存储类（`store verify` 同法自检）。
- 同一 run 的 JSON 载荷（`payload_json`）与结构化列是**两份独立序列化**，完整性检查逐条比对二者。

### 语义哈希作为版本锚

- baseline 的主键即 `canonical-v1` 语义哈希。库内保存产生该哈希的**规范形式**（`canonical_json`），因此任何人都能把规范形式重哈希、比对主键，独立证明这行没有被动过。
- 规范形式之外另存 `provenance_json`（label、source、derivation、rate 记录）：这些**不参与哈希**（改个标签不是语义变更），但一并留档以便溯源。

### 三条不可越过的写入规则

1. **基线不可就地改写**（§203）。改变模型语义产生一份**新的** baseline，旧的仍然完整保留。
2. **`run_id` 幂等**。同一 `run_id` 再次写入：
   - 内容指纹相同 → 幂等空操作，不新增行、不改写已有行（连 `created_by` 都不覆盖）
   - 内容指纹不同 → `RUN_ID_CONFLICT` 拒绝，绝不覆盖
3. **不允许通过重跑悄悄换基线**。`model_id` 已存在且语义哈希与当前基线不符时，记录被拒（`BASELINE_NOT_CURRENT`），必须走评审闭环或显式的 `store adopt-baseline`。
   - 拒绝是**原子**的：首次见到的模型/基线也不会被留下。

### 乐观并发（§201）

- `store adopt-baseline` 与 `review propose` 都要求 `--expected-baseline-hash`；与库中当前值不符即 `BASELINE_CONFLICT`。
- 对尚无基线的模型声明"期望某个基线"同样拒绝（不是静默创建）。
- 一期为单写者模型；多写者阶段在此处扩展为模型级/对象级冲突提示。

### stale 规则（§203）

- 每个模型有一个 `current_baseline_hash`。**run 的 `baseline_hash` 不等于其模型当前基线 ⟺ 该 run 被标 stale**，且必须带 `stale_reason`。
- 换基线（评审 apply 或显式 adopt）时，同模型下落在旧基线上的 run 一律置 stale，并**保留其完整载荷**——旧版证据仍可查看，只是不再代表当前语义。
- **stale 是派生量，双向维护**：基线移动（含 revert）后，回到当前基线的旧 run **清除** stale 与 stale_reason，落下的 run 置 stale。"只升不降"会让 revert 后的旧 run 永远卡在 stale（曾有此缺陷，已修复并锁进测试）。
- 上述等价关系由验证独立重算（不由被测对象自述）。

### 变更管理最小闭环（§195-196）

```
review propose   --expected-baseline-hash H   （校验提案；不改变任何基线）
review impact    <review_id>                  （只读：提案 ↔ 当前基线的结构化 diff，两侧均取自库内行）
review revert    --model-id M --target-baseline-hash H --review-id R
                                              （从基线表自建一份"回到 H"的提案；不改变任何基线）
review decide    --approve | --reject         （需要具名人类）
review apply                                  （需要具名人类；把已批准语义设为当前基线并传播 stale）
```

状态机：`proposed` →（`approved` | `rejected`）；仅 `approved` 可 → `applied`。提案无法通过校验时记为 `invalid`（保留审计痕迹，但无 `proposed_hash`，不可批准）。

三条硬约束：

1. **Agent 无批准权**（§181）。`decide` / `apply` 要求 `--reviewer` 为具名人类身份；`agent`、`assistant`、`ai`、`bot`、`local-cli`、`unknown`、空值一律 `APPROVAL_AUTHORITY` 拒绝。Agent 只能 `propose`。
2. **批准绑定基线**。批准后若基线被他人移动，`apply` 会以 `BASELINE_CONFLICT` 拒绝该过期批准，要求重新决定，而不是盲目套用。
3. **批准绑定内容**。`apply` 时把已批准的规范形式**重新哈希**并与提案记录的哈希比对；不一致（例如库被旁路修改）则 `INTEGRITY` 拒绝，绝不把被改过的内容设为基线。

**存储层不授予任何批准权**：无论评审走到哪一步，结果信封的 `approval_state` 恒为 `not_granted_by_this_result`。`reviews` 表记录的是工程变更的决定，不是安全结论的批准。

### 影响范围分析（impact）

`review impact <review_id>` 输出提案规范形式与**当前基线**规范形式的结构化 diff（`model-diff-v1`）：

- **两侧都从库内行读出**——不依赖任何模型文件，分析不可能与盘上实情漂移；同时报告 `baseline_still_current`（提案期望的基线是否仍在位）。
- 事件/门各分 `added` / `removed` / `changed` / `unchanged`；概率变更以**精确文本**给出（`"0.1"` → `"0.3"`）。
- **词法噪音在 diff 之前就消失**："0.10" 与 "0.1" 的规范形式相同，diff 为空。
- 门输入**仅重排**被如实报告为 changed 但标 `order_only=true`：canonical-v1 保序、哈希确实会变，但审阅者需要知道这是布尔等价编辑而非逻辑变更。
- 假设块 / `schema_version` / `model_id` / `top_event` 的变化各自单列。
- diff **只陈述事实，不裁决可接受性**——批准权仍在具名人类（§181）。

### 一等公民 revert

`review revert` 把"回到某个旧基线"变成一份**普通提案**，但有一个额外的硬约束：

- **提案从基线表自建**：目标必须是**该模型在本库确实持有过**的基线（`store baseline <model> --all` 列出）；未知哈希 / 他模型的基线 / 当前基线 → `REVERT_TARGET` 拒绝。调用方**不能**提供文件——"revert"若能夹带一个不同的模型就失去了意义。
- 目标基线的存储规范形式**重新哈希必须等于目标哈希**（被旁路篡改 → `INTEGRITY` 拒绝）。
- 之后走与任何提案相同的两步：具名人类 `decide` → `apply`。**没有 `--assume-yes`**，也没有跳过审批的路径。
- **基线行永不改写**（§203）：revert 只把 `models.current_baseline_hash` 指回旧行；两条基线行逐字节不变。
- **stale 双向重派生**：回到当前基线的旧 run 清除 stale 与 stale_reason；落在后面的新 run 置 stale。旧载荷始终完整保留。
- 再次往返（revert 的 revert）需要**全新提案与全新期望哈希**——乐观并发对回退不豁免。

## FMEA 基础表与追溯语义

### 范围边界

本版本实现的是 **FMEA 基础表**，不是 FMECA。一条行包含：组件 / 功能 / 失效模式 / 失效原因 / 局部影响 / 上层影响 / 控制措施 / 证据 / 要求引用，以及与基本事件的关联。

**刻意不含** `severity` / `occurrence` / `detection` / `RPN`：这些是 FMECA 扩展，各自需要独立语义规格与验证项，本版本既不计算也不接受。

### 身份（§100：不得用名称当 ID）

`fmea_id`、`component_id`、`function_id`、`requirement_ids`、`evidence_id` 全部是**稳定标识符**（词法 `^[A-Za-z][A-Za-z0-9_.:-]{0,127}$`），不是显示名。组件/功能/要求在本版本仅以稳定 ID 承载于行上，尚无独立对象表。

### 关联语义（§102：多对多，不强迫一对一）

- 一条行可关联 **0..n** 个基本事件；一个基本事件可被 **0..n** 条行关联。
- 关联存在 `fmea_links` 关联表中，可用 `fmea trace --event` / `--component` 双向查询。
- 关联**必须**能对该模型的**当前基线**解析：`fmea propose` 时逐一核对，未知事件 → 记为 `invalid`（`FMEA_LINK`）。
- 基线之后发生变更、导致某个关联不再可解析时：**不自动修复、不自动删除**。该行完整保留（FMEA 文本是人工知识），缺口通过 `fmea trace --dangling` 与 `store status.dangling_fmea_links` 如实上报。这是**可追溯性缺口**，不是库损坏，因此**不进入** `store verify` 的失败项。

### 来源与确认（§110、§181、§198）

```json
{ "source": "human" | "inference" | "import", "certainty": "…", "inference_note": "…" }
```

- `source = "inference"` 的行**必须**填写 `inference_note`，说明**尚待确认什么**；缺失即 `FMEA_SOURCE` 拒收。Agent 可以提出新失效模式，但推断必须被标记，且不得把自己的置信度当作验收结论。
- 草稿**不会**自行进入正式表。唯一路径：

```
fmea propose   --expected-baseline-hash H [--expected-version V]   （记录草稿；校验关联与结构）
fmea decide    --approve | --reject --reviewer NAME               （需要具名人类）
fmea apply     --reviewer NAME                                    （需要具名人类；晋升为正式行）
```

- 状态机与 `reviews` **同构**（同一套状态词表与同一道人类审批闸门）：`proposed` →（`approved` | `rejected`）；仅 `approved` 可 → `applied`；校验不通过的草稿记为 `invalid`（保留审计痕迹，不可批准）。
- `decide` / `apply` 复用与模型变更完全相同的身份闸门：`agent`、`assistant`、`ai`、`bot`、`claude`、`copilot`、`local-cli`、`unknown`、空值 → `APPROVAL_AUTHORITY` 拒绝。
- 晋升后**原始 `source` 与 `inference_note` 一律保留**，另记 `approved_by` / `approved_utc`。"这条是机器提出的"这一事实永不被洗掉。

### 修订语义（§198：不覆盖已批准事实）

- 正式行的**内容字段插入后永不改写**。修订是**新版本**：`version + 1`，旧版本置 `superseded = 1` 并记 `superseded_by_version`，**完整保留可读**（与 run 的 `stale` 同构）。
- 修订必须**显式声明目标版本** `--expected-version`：与当前版本不符 → `FMEA_REVISION_CONFLICT` 拒绝（避免悄悄覆盖他人刚改过的内容）；首版行不得声明修订目标。
- 同一 `fmea_id` 在任一时刻**恰有一个**当前版本（`superseded = 0`），该不变量由验证独立重算。

### 内容哈希（FMEA 规范形式 `fmea-canonical-v1`）

- 覆盖：`model_id`、`fmea_id`、组件/功能、失效模式/原因/两种影响、控制集合、证据集合、要求集合、关联事件集合、`source`。
- 集合一律**排序后**入哈希：同一组控制/关联以不同顺序录入不产生不同哈希。
- **不**覆盖 `certainty` 与 `inference_note`——它们是说明字段，改写置信度措辞不是语义变更（与模型 label 同理）。两者作为 `provenance_json` 侧车一并留档。
- 每次 `apply` 都把草稿的规范形式**重新哈希**并与记录值比对，不一致即 `INTEGRITY` 拒绝。

### 批准绑定

- 批准同时绑定**内容**（规范形式重哈希）与**基线**（`expected_baseline_hash`）：任一被他人移动，`apply` 以 `BASELINE_CONFLICT` 拒绝该过期批准，要求重新决定。
- 与模型评审一致：**库不授予任何批准权**，`approval_state` 恒为 `not_granted_by_this_result`。FMEA 行的批准是工程内容的确认，不是安全结论的批准。

### 重要度驱动的关注项（关注项 ≠ 事实）

引擎能按重要度给基本事件排序，但**不知道**失效模式、原因或控制措施。因此它绝不编造 FMEA 内容——它能产出的唯一诚实产物是**工单**：

```bash
fmea propose-from-importance <db> --run <run_id> [--by <度量>] [--top N] [--min-value X]
```

- **定量理由必须精确可复现**：`inference_note` 写明度量名、**精确值**、在可比事件中的排名、`run_id` 与基线哈希前 12 位。
- **不可知的一律置空并打标**：失效模式/原因/局部影响/系统影响全部以 `[UNCONFIRMED]` 前缀标记；`component_id` / `function_id` 置为 `COMPONENT-UNASSIGNED` / `FUNCTION-UNASSIGNED`；控制/证据/要求为空。**没有一个字段被编造。**
- **关注项永不自动成为事实**：标记行即使被人类批准，`apply` 仍以 `FMEA_PLACEHOLDER` 拒绝入表。人类回答工单的方式是**为同一 `fmea_id` 另行提出真实内容**，再走常规批准；原工单草稿保留在候选队列中作为轨迹。
- **正式表永不出现未填写的关注项**：这是独立验证的一项检查（若某行带着标记进了正式表即判失败）。
- **不重复唠叨**：生成器先查两件事——该事件是否已被当前正式行引用（`already_in_official_table`）、该 `fmea_id` 是否已有草稿（`already_pending` / `already_decided`）。命中即跳过并给出理由，因此重复运行是空操作。
- **`--top` 只计新增草稿**：已覆盖/已起草的事件不占名额；`--min-value` 按**精确有理数**比较（`1/3` 与 `0.333…` 不相等），排序与过滤都不经过文本。
- **未定义度量不是关注信号**：`null`（如本质事件没有 RRW）一律跳过并注明 `measure_is_undefined`。
- **过期基线不生成**：run 已落在被取代的基线上时拒绝（`BASELINE_NOT_CURRENT`），因为那份排名描述的是已经不存在的模型。
- 被跳过的项与"未扫描的剩余条数"**如实输出**，不静默少给。

## 结构校验规则（实现顺序即拒绝优先级）

1. schema_version 不在 {0.1.0, 0.2.0} → VERSION
2. `rate_precision_digits` 非法 / 用于 0.1.0 → RATE_VALUE / VERSION
3. 假设块缺失、`basic_events_independent != true`、语义串取值非法、`condition` 为空 → ASSUMPTIONS
4. `basic_events` 非数组或为空 → INPUTS；事件数 > 20,000 → SIZE_LIMIT
5. 逐事件（下列顺序）：
   1. `id` 词法非法 / 重复（含与门 ID 冲突）→ ID / DUPLICATE_ID
   2. `label` 缺失 → INPUTS
   3. `source` 缺失 → SOURCE
   4. 概率声明检查：0.1.0 下出现 `failure_rate` 一律 → VERSION；0.2.0 下 `probability` 与 `failure_rate` 同时存在或同时缺失 → RATE_VALUE
   5. **失效率分支**：模型名非 constant_failure_rate / repairable 缺失或为 true / dormant·latent·inspection_interval·repair_rate·restoration 出现 → RATE_UNSUPPORTED；λ 或 t 词法非法、为负 → RATE_VALUE；单位非法 → RATE_UNITS；`failure_rate.source` 缺失 → SOURCE
   6. **概率分支**：词法非法、超出 [0,1] → PROBABILITY
6. 全模型语义串与实际是否含失效率不符（双向）→ ASSUMPTIONS
7. `gates` 非数组 → INPUTS；门数 > 100,000 → SIZE_LIMIT
8. 逐门：`id` 非法/重复 → ID / DUPLICATE_ID；`kind` 不支持 → UNSUPPORTED_GATE；`inputs` 空/非字符串 → INPUTS；K_OF_N 的 `k` 非法或输入引用重复、非 K_OF_N 却带 `k` → K_OF_N
9. `top_event` 缺失 / 未定义 → TOP_EVENT / UNKNOWN_REFERENCE
10. 门引用未知 ID → UNKNOWN_REFERENCE
11. 任何环（含不可达子图中的环）→ CYCLE

所有拒绝均带结构化错误码输出 JSON。退出码映射：

| 错误码 | 退出码 |
| --- | --- |
| UNSUPPORTED_GATE, RATE_UNSUPPORTED | 3（不支持） |
| STORE_SCHEMA_MISMATCH（库由不兼容的 schema 世代写入） | 3（不支持） |
| VERSION, ASSUMPTIONS, ID, DUPLICATE_ID, INPUTS, SOURCE, PROBABILITY, RATE_VALUE, RATE_UNITS, K_OF_N, UNKNOWN_REFERENCE, CYCLE, TOP_EVENT, SIZE_LIMIT, PARSE, IO, FMEA_INPUTS, FMEA_ID, FMEA_SOURCE, FMEA_LINK | 2（非法输入） |
| RUN_ID_CONFLICT, BASELINE_NOT_CURRENT, BASELINE_CONFLICT, REVIEW_NOT_FOUND, RUN_NOT_FOUND, MODEL_NOT_FOUND, REVIEW_STATE, APPROVAL_AUTHORITY, STORE_BAD_ARGUMENT, STORE_IO, FMEA_NOT_FOUND, FMEA_STATE, FMEA_REVISION_CONFLICT, FMEA_PLACEHOLDER, REVERT_TARGET | 2（请求被拒） |
| INTEGRITY（`store verify` 发现库内不一致） | 5 |
| 节点/路径上限触发 | 4（资源受限，非错误，结果标 resource_limited） |
| 内部异常 | 5 |

`analyze --db` 的组合语义：**计算成功但记录被拒**时，退出码取该拒绝的码（2/3），结果信封的 `status` 仍如实反映分析本身（通常 `succeeded`），并新增 `store.status = "refused"` 与 `store.error`。即：不丢弃计算结果，也不把持久化失败当成成功。

## 输出语义

`analyze` 输出遵循 `analysis_result.schema.json` 信封（0.1.0）并附加 `engine_stats`（节点数、变量序、耗时、限额）与 rate 相关字段：

- `top_probability`：精确有理数的十进制渲染（有限小数必为精确形式；否则 n/d）
- `minimal_cut_sets`：结构最小割集族（与概率无关的布尔最小族）
- `cut_sets_complete`：割集枚举是否完整；触发路径上限时为 false 且 status=resource_limited，**绝不把截断结果冒充完整**
- `approval_state`: 恒为 `"not_granted_by_this_result"`——计算成功不构成任何批准
- `model_schema_version`：实际使用的模型 schema（0.1.0 / 0.2.0）
- `probability_interpretation`：仅当模型含失效率时出现，恒为 `mission_failure_probability_from_constant_rate`
- `rate_provenance[]`：每个 rate 派生事件的 (λ, 单位, t, 单位, λ·t, q, 精度位数, 公式, `interpretation`)，供证据溯源
- `importance_measures[]`：按 Fussell–Vesely **降序**排列的每事件度量（未定义者置后，同值按 event_id 排序，保证确定性）。每项含 `in_support`、`event_probability`、`top_probability_given_failed`、`top_probability_given_working`、四个度量字段，以及（若有未定义项）`undefined[]`
- `importance_status`：`computed` / `not_requested` / `skipped_model_too_wide`；非 computed 时必有对应警告
- `importance_conventions`：随结果发布的度量定义与约定，使消费方无需猜测口径
- `importance_exact`：布尔，指示渲染是否无损（见"渲染精度"）
- `warnings[]`：含强制提示"mission-time probability, NOT a per-flight-hour metric"
- `store`（仅 `--db` 时出现）：`status ∈ {recorded, idempotent, refused}`，以及 `db`、`run_id`、`baseline_hash`、`content_fingerprint`；被拒时附 `error{code,message}`

## 明确排除（当前版本的拒绝边界）

动态门、顺序失效、修复过程、潜伏/检查间隔、未建模相关性/共因、NOT/非相干逻辑、任意概率分布表达式、软件失效随机化、非恒定失效率（Weibull/老化）。以上任何一项出现在输入中都会导致明确拒绝，不会退化为 AND/OR。

FMEA 侧同样明确排除：FMECA 的 severity/occurrence/detection/RPN、FMEDA、诊断覆盖率与失效率分配、组件/功能/要求的独立对象表、**前向修改的对象级 patch 语言**（revert 与影响分析已实现；前向提案仍为整份模型）。以上均为后续阶段项，本版本不接受、不近似。

同样排除 **"引擎替人撰写 FMEA 内容"**：重要度只能指出**哪个事件值得关注**（并给出精确理由），不能产出失效模式、原因或影响。任何"自动填出失败模式"的行为都属于编造工程结论，本版本在数据结构层面就不提供该能力——关注项的四个描述字段只能以标记占位存在，且带标记的行**永远无法**进入正式表。

> 注：**恒定失效率λ + 任务时间 t → 任务失效概率 q** 的转换已实现并验证（见"失效率转换语义"），是本版本新纳入的语义，不再属于排除项。
