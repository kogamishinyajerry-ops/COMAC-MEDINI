# ALGORITHM — 生产内核算法说明

## 总体管线

```
JSON → 解析/防御检查 → 语义校验（域层）
     → 失效率转换 λ·t → q（域层，仅 0.2.0；一次性高精度求值）
     → BDD 编译（内核） → 概率表（Shannon/有理数） → 最小割集（路径枚举+位掩码）
                        ↘ 重要度（同一概率表 + 一遍自顶向下 reach）
     → 结果信封
     → （可选）持久化：baseline 锚定 → 幂等 run 写入 → 精确重要度入库 → stale 传播
     → （可选）FMEA 基础表：候选提案 → 人工批准 → 应用为不可变版本 → 追溯关联
```

域层（domain/）与内核（kernel/）不依赖任何外部服务；两者均只用 Python 标准库。存储层（store/）是适配层，同样只依赖标准库 `sqlite3`，不做校验、不做文件 IO，只接收已校验的域对象与已算出的结果。

## 1. ROBDD（`kernel/bdd.py`）

经典 Bryant ROBDD：

- 节点为 `(var_level, low, high)` 三元组；终端 `("leaf",0)` / `("leaf",1)`
- **唯一节点表**（unique table）：`mk(level, low, high)` 先应用归约规则（low==high 则消解），再查重；重复结构共享同一节点对象
- **计算缓存**（computed cache）：apply 的结果按（交换律规范化后的）操作数对缓存
- **变量序**：由顶事件自上而下 DFS 的发现序决定（见 §4），跨运行/跨机器确定
- **节点上限**：`max_nodes`（默认 1,000,000），超限抛 `BddNodeLimit` → CLI 返回 resource_limited，绝不输出截断的"成功"
- **apply 迭代实现**：显式工作栈，深链不触发 Python 递归上限
- **mark-sweep 压实**（`compact`）：批量合并间清除不可达死节点，防止链式构型下唯一表膨胀

## 2. 失效率转换（`domain/rate_model.py`，模型 schema 0.2.0）

加载时一次性把 `failure_rate{λ, t}` 转为固定任务概率 `q`，此后内核只见固定概率。**唯一一次引入近似的步骤**，其精度被显式记录。

**求值策略（按 x = λ·t 分档）：**

| 条件 | 处理 | 理由 |
| --- | --- | --- |
| x = 0 | 返回精确 `0` | 不可能事件，避免无谓计算 |
| x·log₁₀e > prec+15 | 返回精确 `1` | `exp(−x)` 低于工作精度，q 舍入为 1（饱和） |
| x ≤ 0.5 | 交错级数 `q = x − x²/2! + x³/3! − …` | 小 x 下 `1−exp(−x)` 有**灾难性相消**，级数无相消 |
| 其余 | `1 − Decimal.exp(−x)`（`decimal` 模块，工作精度 prec+15） | 中大型 x，exp 稳定 |

- 工作精度 = 请求精度 + 15 位保护位；最后一步用 `ROUND_HALF_EVEN` 舍入到请求精度（默认 40 位有效数字）
- `λ`、`t` 以 `fractions.Fraction` 精确解析（支持普通与科学计数法字符串）
- `q` 以 `Decimal` 求值后转为精确 `Fraction` 存入模型；`λ·t`（精确）与 `q` 及精度位数一并写入 `rate_provenance` 证据

**独立性**：参考侧（`verification/run_rate_cross_check.py`）用**纯有理数泰勒级数**（对 x>5 采用半角递推 `exp(−x)=exp(−x/2)²` 收敛加速，容差 1e-90）计算 q，不使用 `Decimal`、不共享任何转换代码；两路 q 均为同一超越数的舍入，故交叉对照用**声明容差**（绝对 1e-30）而非精确相等——容差远高于转换误差（实测最大偏差 3.8e-41）、远低于任何真实缺陷。

## 3. 精确有理数渲染（`domain/ratnum.py`）

"Fraction → 最短精确十进制"集中在一处实现（`exact_decimal` / `exact_decimal_or_fraction`），域层、适配层、语义哈希、CLI 共用，避免多处实现漂移。**独立验证侧刻意不使用它**（自带实现），以维持代码独立性。

## 4. 编译（`kernel/solve.py::_compile`）

1. 从 top_event 做可达性分析，仅编译可达门（其余门不参与计算，但仍在域层接受环检查）
2. **同门扁平化**：AND(AND(a,b),c) 按结合律内联为 AND(a,b,c)。仅 AND/OR 参与；K_OF_N 永不拼接（其子门即使同为 K_OF_N 也各自建 BDD）。链式模型因此从 O(n²) 节点重建降为一次平衡合并
3. **Kahn 拓扑排序**自底向上构建，每个"必要"门（被异类父门引用或为顶）生成 BDD
4. **平衡两两合并**（`_combine`）：操作数列表按完全二叉树形态逐轮合并，每轮之间压实；语义由结合律/交换律保证不变

## 5. K_OF_N 编译

无否定阈值递推：`T(j,c) = (x_j AND T(j+1,c+1)) OR T(j+1,c)`，边界 `T(j,c≥k)=ONE`、`T(n,c<k)=ZERO`，另含可达性剪枝（k−c > 剩余输入数时为 ZERO）。对纯变量输入为 O(k·n) 节点，支持任意 BDD 输入（嵌套表决门）。

## 6. 概率表（`_probability_table`）

BDD 上精确 Shannon 展开：`P(node) = (1-p_v)·P(low) + p_v·P(high)`。收集全部节点按层深排序（深先），迭代自底向上填 memo，返回 `节点 → 概率` 全表（含两个终端）。**全程 `fractions.Fraction`，无浮点**。1e-24 量级概率精确表示（M08 验证）；rate 派生概率的舍入只发生在 §2，内核对 q 精确。

顶事件概率即 `表[root]`。**同一张表同时供重要度使用**，因此概率与共因子不可能对同一张图产生分歧。

## 7. 重要度度量（`kernel/importance.py`）

四个度量（BI/FV/RAW/RRW）的定义、约定与未定义策略见 `docs/SEMANTICS.md`；这里只讲算法。

**目标**：在 O(#节点) 内得到**全部**变量的共因子概率，而不是每个变量重建一次图（后者是 O(事件数 × 节点数)）。

### 7.1 单遍自顶向下 reach

- `reach(v)` = 求值路径到达节点 v 的**概率质量**（含多个父节点路径的累加；路径上的子立方体互斥，故直接相加即为概率）
- 自根向下按层升序推进：`reach(low) += reach(v)·(1−p_v)`，`reach(high) += reach(v)·p_v`
- 于是第 i 层（该层所有节点考察同一个变量 xᵢ）满足

```
q+ᵢ = Σ_{v: level(v)=i} reach(v)·P(v.high)  +  T(i)
q−ᵢ = Σ_{v: level(v)=i} reach(v)·P(v.low)   +  T(i)
```

其中 `T(i)` 是**不经过第 i 层的路径**贡献的质量。这类路径要么从层 < i 的节点直接跳到层 > i 的节点（BDD 归约允许跳层），要么在该处终止于常量终端；两种情况下路径都**没有提及 xᵢ**，因此对 `q+ᵢ` 与 `q−ᵢ` 的贡献**完全相同**，在 `BIᵢ` 中相消。

### 7.2 差分数组累计 T(i)

每条"跳跃边" u→c 的贡献区间是 `[level(u)+1, eff_level(c)−1]`（`eff_level(终端) = nlev`），用差分数组 `diff[level(u)+1] += x; diff[eff_level(c)] -= x` 记录，最后前缀和即得所有层的 `T(i)`。全部边处理一遍 = O(#节点)。

### 7.3 两个必须显式处理的缺口

1. **层高于根节点**：若 `i < level(root)`，图上不存在层 < i 的节点，差分数组收不到任何跳跃边，但此时全部路径都不提及 xᵢ。该情形须直接取 `q+ᵢ = q−ᵢ = Q`。
   > 这个缺口是**实测缺陷**：交叉验证在随机模型 RANDOM_2476 上抓到共因子恒等式失败，最小复现见 `tests/test_importance.py::test_variable_above_root_level_reports_cofactor_equal_to_top`。
2. **支持集判定**：`in_support` 直接取"该层是否存在节点"。ROBDD 的归约性保证变量出现在支持集内 ⟺ 有节点考察它，因此这是结构性判定，不依赖概率。

### 7.4 内建恒等式校验

每个事件都精确验算 `qᵢ·q+ᵢ + (1−qᵢ)·q−ᵢ = Q`，不等即判内核缺陷并如实失败（退出码 5）。这是**自检**，不能替代交叉验证（恒等式只约束加权和，不单独约束 `q+ᵢ`、`q−ᵢ` 各自正确）。

### 7.5 成本

一次概率表（#节点次乘加）+ 一次 reach（约 2×#节点次乘加）→ 约为概率计算的 2～2.6 倍。实测链式模型：n=200 时 0.038s→0.058s，n=1500 时 2.12s→5.41s，与"两遍 O(#节点)"一致，不随事件数二次增长。

## 8. 最小割集（`_minimal_cut_sets`）

- 结构语义（与概率无关）：根到 ONE 终端的每条路径的正变量集是一个满足赋值；其包含极小族即相干函数的最小割集
- **位掩码表示**：路径正变量集用整数位掩码（第 i 位 = 第 i 层变量），低分支共享同一 int，高分支 O(1) OR；深链/宽树均高效
- 迭代遍历（显式栈），路径数上限 `max_mcs_paths`（默认 200,000）；超限 → 返回已收集子族的极小化结果并置 `cut_sets_complete=false`，CLI status=resource_limited
- **极小化**：按 popcount 升序 + 位运算包含测试 `(x & m)==x`，无超集残留（变形测试保证）

## 9. 语义哈希（`domain/semantic_hash.py`）

规范化（canonical-v1）：事件按 ID 排序、概率渲染为精确十进制（词法差异归一，"0.10"≡"0.1"）、门按 ID 排序（inputs 保序）、仅语义字段进入 JSON（sort_keys、紧凑分隔符、UTF-8）→ SHA-256。**含失效率时，λ/t/单位/`derivation` 一并进入哈希**（"0.001"式声明的 q 与由 rate 转换得到的同值 q 哈希不同——来源不同即语义不同）。布局/标签/时间戳不参与。哈希稳定性由测试锁定。

## 10. 与独立参考实现的分离

| 对象 | 生产实现 | 独立参考 | 对照判据 |
| --- | --- | --- | --- |
| 顶事件概率 / 割集 | ROBDD + Shannon | `verification/reference_fta.py`：2^n 真值表穷举 + 逐赋值有理数加权 | **精确相等** |
| λ·t → q | Decimal 分档求值 | `verification/run_rate_cross_check.py`：纯有理数泰勒级数（半角递推） | 声明容差（1e-30） |
| 重要度四度量 | reach/差分数组（§7） | `verification/reference_importance.py`：对每个事件强制 xᵢ 后穷举其余变量 | **精确相等** |
| 支持集 | 图上"该层有节点" | `reference_importance.py`：真值表仅差一位的行配对比较 | **精确相等** |
| 库内数值与 stale 标记 | 结构化列 + `payload_json` 两路序列化 | `verification/run_store_verification.py`：raw sqlite3 读回，用 2^n oracle **重新推导**，并独立重算基线哈希与 stale | **精确相等** |
| FMEA 行规范形式与哈希 | `domain/fmea.py` 的 `canonical_fmea_form` + SHA-256 | `verification/run_fmea_verification.py`：由**列级原值**重新实现规范化与哈希，三方比对 | **精确相等**（20 种突变全部检出） |
| applied review 锚定 | `reviews.proposed_hash` ↔ `applied_baseline_hash` | `run_store_verification.py`：raw sqlite3 用**独立重实现**的规范形式哈希复验（10 种突变含"锚定基线被改指"） | **精确相等** |

七对实现均无共享代码路径、无共享算法。种子参考脚本 `reference/03_contracts/verify_seed_cases.py` 为开工包自带 oracle，与以上全部独立。

## 11. 持久化、基线与并发（`store/`）

### 11.1 事务边界

`record_run` 的"首次见到该模型时注册基线"与"插入 run"在**同一个事务**内完成。若 run 被拒（例如 `run_id` 冲突），模型行、基线行都不会留下——拒绝必须是无副作用的。`apply_review` 同理：基线搬移与评审状态变更同事务提交，不出现"基线动了但评审没标 applied"。

仓储内部把这两步拆成 `*_locked` 私有方法（假定事务已打开）与公开包装（自己开事务），以避免 `with conn:` 嵌套造成的内层提前提交。

### 11.2 乐观并发

读取 `models.current_baseline_hash` 作为前置条件；调用方给出的 `expected_baseline_hash` 与之不符即拒绝。这是单写者模型下的冲突检测（§201）：不做锁，靠"写入时校验预期值"防止把基于旧基线的判断套用到新基线上。

### 11.3 内容指纹与幂等

`content_fingerprint` = SHA-256 over（baseline 哈希、引擎/契约版本、计算结果状态、**精确**顶事件概率文本、割集族、`cut_sets_complete`、`importance_status`、每事件的九个精确值）。

- 指纹取自**权威计算结果**，不取显示串：同一计算换了显示精度仍是同一内容，幂等成立。
- 指纹相同 → 空操作；**不同 → `RUN_ID_CONFLICT`**，绝不覆盖。`run_id` 因此是不可变的主键，而不是可覆盖的标签。

### 11.4 stale 的派生方式

`stale` 是**派生量**而非独立状态：`stale ⟺ run.baseline_hash ≠ models.current_baseline_hash`。换基线时用 `_refresh_staleness_locked` **双向**重派生：落在旧基线上的 run 置 `stale=1` 并写原因；**回到**当前基线的 run 清 `stale=0` 连同原因清空（revert 场景）。旧载荷保持只读可用。

因为它是派生量，完整性检查可以直接**重算**它并与存储值比对——不依赖写入路径是否写对，也就不会出现"写错了却自洽"的情形。早期实现只置不清（`WHERE ... AND stale=0`），revert 后旧 run 会永远卡在 stale——这正是"派生量必须双向维护"的实例。

### 11.5 影响范围分析与 revert（`domain/model_diff.py` + `store/`）

- **diff 是纯函数**：`diff_canonical_forms(old, new)` 输入输出皆 JSON，确定性、可入库、可对照；比较按**规范内容**进行，词法噪音（"0.10" vs "0.1"）在 diff 之前就消失了。
- **门输入仅重排**确实改变哈希（canonical-v1 保序），因此 diff 如实报告 changed，但额外标 `order_only=true`——布尔等价编辑与逻辑变更必须分得开。
- `review impact` 的**两侧都从库内行读出**（提案的 `proposed_canonical_json` + 当前基线的 `canonical_json`）：分析不依赖模型文件，不可能与盘上实情漂移。
- `review revert` 从**基线表自建**提案：读目标基线的 `canonical_json` → 重新哈希必须等于目标哈希（防旁路篡改）→ 以普通 `proposed` 提案入库。目标必须是该模型在本库持有过的基线，且不得是当前基线（`REVERT_TARGET`）。此后与任何提案完全同构：具名人类 decide → apply（复用 `_anchor_baseline_locked`，apply 侧的重哈希锚定检查同样生效）。
- **没有"revert 专用快车道"**：不做 assume-yes、不跳审批、乐观并发不豁免——revert 的 revert 需要全新提案与全新期望哈希。

### 11.6 编译复用（`kernel/solve.py` 结构指纹缓存）

- **先测再优化**（`verification/run_incremental_bench.py`）：compile 占总时长 31–45%，且变量序/图/割集**只读结构不读概率**——这是复用的数学依据。
- `solve_model` 以 `(structure_fingerprint, max_nodes)` 为键缓存 BDD 管理器、根节点、变量序与割集；概率变更命中缓存只付 table+importance（实测 1.5–1.6×）。
- **正确性不依赖缓存**：table 与 importance 每次从零重算（节点值对 q 多线性、Q 变则全部比值变——数学上不可避免）；复用本身由 9 项恒等测试锁定（缓存命中 == 清缓存全新求解，逐字段含 undefined reasons）。

### 11.7 对象级 patch 语言（`domain/patch.py`）

- **patch 是纯函数**：`apply_patch(canonical, ops)` 深拷贝后按序应用十种编辑操作，输入永不改写；终态整体校验（引用/全图无环/K_OF_N/双向语义守卫）后才返回。
- **校验在规范形式层**（这是设计决定而非偷懒）：canonical 的 `p` 可以是精确 `n/d` 文本（`1/3`），超出模型输入词法——重建模型文件无法合法表达它。因此 patch 结果在其所在层做**实质等价**的检查，不经过文件往返。
- **概率走共享渲染器**：`exact_decimal_or_fraction` 归一化（`"0.250"` ≡ `"0.25"`，同一哈希）；有理输入精确保留。
- **收敛性**：patch 与等价的全模型提案产出**相同 `proposed_hash`**（独立验证锁定）——两条入口不可能各自漂移出不同的规范形式。
- **守卫**：rate 事件的 p 不可直设（由 λ·t 派生）；`set_event_rate` 编辑既有 rate 事件时**完整重跑转换闸门**（`parse_rate_spec` → `mission_probability` 按已记录精度 → 重写 provenance 并重派生 p；错误码保留底层分类），q 对照独立有理级数 oracle（容差 1e-30）且与模型级 λ/t 变更**收敛同哈希**；被引用对象不可删；跨操作构造的环被终检拒绝；非法 patch 记为 `invalid`。

### 11.8 精确性在存储上的落地

写入时把结果里的 `Fraction` 用同一条渲染规则（§3）转成精确十进制或精确 `n/d` 文本；读回时用 `Fraction(text)` 还原。**往返无损**：`Fraction(store(x)) == x`，由测试逐字段锁定。重要度值按 run + event 存九列精确文本 + 未定义原因 JSON。

### 11.9 迁移与往返

`MIGRATIONS: {版本号 → DDL 语句元组}`，启动时按版本号升序补齐并写 `schema_meta`；版本高于引擎（库来自更新的世代）则拒绝打开，不尝试猜测兼容。`dump()` / `restore()` 提供确定性 JSON 往返（表按主键排序），既服务于 PostgreSQL 迁移，也是"备份可恢复"的实测手段。

## 12. FMEA 基础表与追溯（`domain/fmea.py` + `store/`）

FMEA 基础表承载**失效模式、原因、局部/系统影响、现有控制、证据、需求**，并与基本事件建立**多对多**追溯。它不参与概率计算，因此不引入任何数值算法；设计要点在于**身份、来源与修订**。

### 12.1 身份是稳定标识符，不是显示名（§100）

`fmea_id` 是身份；`failure_mode` 等文案是描述。改名不改身份，因此"同一失效模式换了个说法"不会被误判为新行、也不会造成追溯断裂。哈希覆盖语义内容但**以稳定 id 为键**。

### 12.2 多对多关联，永不强制一对一（§102）

关联存于独立的 `fmea_links(model_id, fmea_id, version, event_id)`，主键为四元组，外键级联到行版本。一行可关联多个基本事件（一种失效模式可能由多个底事件共同刻画），一个基本事件也可被多行引用（多个失效模式共享同一根因）。**不设唯一约束、不做自动配对**——工程现实本就是多对多。

### 12.3 来源与"推断不得成为定论"（§110）

`source ∈ {human, inference, import}`。`inference`（机器推断）行**必须**提供非空 `inference_note`，明确写出尚待确认的内容；缺失即在校验层拒绝（`FMEA_SOURCE`）。这使"agent 生成的候选"在**数据结构层面**无法伪装成已确认事实。

### 12.4 规范形式 `fmea-canonical-v1` 与哈希边界

规范形式对**集合型字段排序**（关联事件、控制项、证据、需求）后序列化——顺序不敏感；标量字段原样。`certainty` 与 `inference_note` 是**描述性**字段，**刻意置于哈希之外**：它们不改变行的工程语义，只说明把握程度。

> 推论：既然哈希不含它们，就不能从规范形式反推。因此候选提案时必须把 provenance（`fmea_provenance(row)`）**单独落盘**到 `fmea_candidates.proposed_provenance_json`，应用时读回；否则批准后推断说明会**静默丢失**（这是实测抓出的缺陷）。

### 12.5 修订即新版本，批准内容不可就地改写（§198）

`fmea_rows` 主键为 `(model_id, fmea_id, version)`。应用一次修订 = 插入 `version+1` 的行 + 把旧版标 `superseded=1` 且 `superseded_by_version=新版本`，**旧版内容字节不变**。完整性检查要求：后继存在且版本更大、同名至多一个当前版本、被取代行的批准信息保留。

### 12.6 批准绑定基线与内容（复用 `reviews` 语义）

候选流转 `proposed → approved/rejected`（或在提案时即被判 `invalid`），仅 `approved` 可应用，且**仅具名人类**可决定/应用（机器标识一律 `APPROVAL_AUTHORITY`）。候选同时记录 `expected_baseline_hash` 与 `proposed_content_hash`：应用时基线已移动 → `BASELINE_CONFLICT`；内容被旁路篡改 → 重新哈希不匹配即拒。

### 12.7 悬空关联：是缺口，不是损坏

基线移动（模型语义变化）后，某些关联的 `event_id` 可能不再出现在当前基线的事件集中。这是**合法工程演化的结果**，不是数据损坏。因此：

- `fmea trace --dangling` 与 `status().dangling_fmea_links` **报告并计数**它
- `store verify` **不**把它判为失败（否则一次合法的模型变更会读作库损坏，掩盖真正的完整性问题）

### 12.8 重要度驱动的关注项（`domain/fmea.py::plan_attention_drafts`）

重要度排序可以**驱动** FMEA 工作，但不能**替代**它。`plan_attention_drafts()` 是纯函数（不碰存储），把一份已排序的重要度表变成关注项草稿：

1. **排序与过滤全程走精确有理数**：输入表由仓储按 `Fraction` 排好序（不是文本序）；`--min-value` 也以 `Fraction` 比较，故 `1/3` 不会被 `0.333` 混过。
2. **未定义度量直接跳过**（`measure_is_undefined`）——`null` 不是关注信号。
3. **去噪靠两张集合**：`covered_event_ids`（当前正式行已引用的事件）与 `drafted_fmea_states`（该 `fmea_id` 已有草稿及其状态）。命中即跳过并记录理由，因此重复生成是空操作，不会对着已决事项反复唠叨。
4. **`top` 只计新增草稿**：已覆盖/已起草的不占名额；提前跳出时把"未扫描的剩余条数"作为计数如实返回，而不是补一堆无意义的跳过项。
5. **定量理由写入 `inference_note`**：度量名 + 精确值 + 可比事件中的排名 + `run_id` + 基线哈希前 12 位。排名只在**可比**（度量有定义）事件中计算。
6. **不可知的字段一律打标**：四个描述字段以 `[UNCONFIRMED]` 开头，组件/功能置 `*-UNASSIGNED`，控制/证据/要求为空。生成器**不编造任何工程内容**。

对应的存储侧闸门：`apply_fmea_candidate` 检测规范形式是否仍带标记，带则 `FMEA_PLACEHOLDER` 拒绝；`_verify_fmea` 另查"正式表里是否出现带标记的行"（与生成器无关的兜底，防止绕过闸门或带外改写）。

### 12.9 独立验证

`verification/run_fmea_verification.py` 不 import store，用 raw `sqlite3` 从**列级原值**重建对象、**重新实现**规范化与 SHA-256，三方比对（列 ↔ `canonical_json` ↔ `content_hash`）；在临时库上独立复跑状态机（含"带标记的关注项被批准后仍无法入表"）；并以 **20 种突变**证明这些检查真的会失败。判据**逐值精确相等、无容差**。标记字符串在本文件中**另行列出**（不从生产代码 import），否则标记被悄悄改弱时这条检查无法察觉。

## 13. 已知限制

- MCS 枚举是路径级枚举后极小化，不是 Prime-Implicant 专用算法；超大割集族（>20 万路径）触发截断并如实标记
- 变量序启发式（DFS 发现序）对多数工程构型良好，但对病态交织结构未做动态重排序（sifting 等），属后续工作
- rate 转换仅覆盖恒定失效率 + 不可修复 + 显式任务时间；修复/潜伏/非恒定率显式拒绝
- 重要度仅在相干模型内有定义（与本引擎的模型边界一致）；`Q=0` 或 `q−ᵢ=0` 的未定义项如实上报，不填占位数字
- 存储为**单写者**模型：靠写入时校验预期基线实现乐观并发，不做行级锁；多写者阶段在此扩展
- 变更管理：影响范围分析（`review impact`）、一等公民 revert（`review revert`）、**对象级 patch 语言**（`review patch` / `review patch-preview`，`domain/patch.py`）均已实现——三者都产出/操作普通提案，复用同一闭环；认证层未接入
- FMEA 基础表**不含**严重度/发生度/探测度/RPN，不做风险排序（FMECA/FMEDA 不在范围）；悬空关联只报告不自动修复
- 引擎**不撰写** FMEA 内容：重要度只能产出带标记的**工作项**，四个描述字段必须由人填写；带标记的行永远无法入表，也不存在"自动补全失效模式"的路径
- 单线程；未做原生加速（规划中按实测瓶颈决定）
