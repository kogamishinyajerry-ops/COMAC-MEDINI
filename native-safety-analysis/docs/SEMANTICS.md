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
| VERSION, ASSUMPTIONS, ID, DUPLICATE_ID, INPUTS, SOURCE, PROBABILITY, RATE_VALUE, RATE_UNITS, K_OF_N, UNKNOWN_REFERENCE, CYCLE, TOP_EVENT, SIZE_LIMIT, PARSE, IO | 2（非法输入） |
| 节点/路径上限触发 | 4（资源受限，非错误，结果标 resource_limited） |
| 内部异常 | 5 |

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

## 明确排除（当前版本的拒绝边界）

动态门、顺序失效、修复过程、潜伏/检查间隔、未建模相关性/共因、NOT/非相干逻辑、任意概率分布表达式、软件失效随机化、非恒定失效率（Weibull/老化）。以上任何一项出现在输入中都会导致明确拒绝，不会退化为 AND/OR。

> 注：**恒定失效率λ + 任务时间 t → 任务失效概率 q** 的转换已实现并验证（见"失效率转换语义"），是本版本新纳入的语义，不再属于排除项。
