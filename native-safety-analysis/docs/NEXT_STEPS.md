# NEXT_STEPS — 后续工作（按步骤，不按日期）

按 30 天工作包（B_核心规划 §10）映射为可执行步骤。每步完成标准：独立验证证据 + 变形/负例测试同步扩展。

## 已完成

- **持久化与基线（B02）+ 变更管理最小闭环** ✅
  - 单文件 SQLite，五表：`models` / `baselines` / `runs` / `importance` / `reviews`（+ `schema_meta` 迁移注册表）；DDL 见 `store/schema.py`
  - 语义哈希即 baseline 主键；库内保存产生该哈希的规范形式，任何人可重哈希自证（§203 基线不可就地改写）
  - `run_id` 幂等：内容指纹相同 → 空操作（整行不变）；不同 → `RUN_ID_CONFLICT` 拒绝，绝不覆盖
  - 乐观并发：`--expected-baseline-hash` 前置核对（§201）；首次见到模型时的注册与插入 run 在**同一事务**内，被拒写入零副作用
  - stale 为**派生量**：`stale ⟺ baseline_hash ≠ 模型当前基线`；旧 run 载荷完整保留，旧证据仍可读
  - 精确入库（§231）：概率/重要度全为精确十进制或精确 `n/d` 文本；库中无 REAL/FLOAT 列，且用 `typeof()` 逐值验证
  - 变更闭环：`review propose → decide（需具名人类）→ apply`；Agent 只能提案（§181），批准绑定基线与内容（过期批准 / 被篡改提案一律拒绝）
  - CLI：`analyze --db`、`store {init,status,runs,show,baseline,adopt-baseline,important,verify,export}`、`review {propose,patch,patch-preview,impact,revert,list,show,decide,apply}`
  - 独立验证：`verification/run_store_verification.py`（raw sqlite3 读回 + 2ⁿ oracle 重推导 1362 项精确相等；53 份基线独立重哈希；**9/9 突变被检出**；dump→restore 往返一致）
  - 过程中被抓出的真实缺陷：`record_run` 的基线与 run 写入原先分属两个事务（拒绝后留下孤儿基线）；`store.verify` 删掉一行重要度时漏报（自洽 ≠ 完整）

- **重要度度量（B03 后半）** ✅
  - 四度量：Birnbaum `BI=∂Q/∂qᵢ`、Fussell–Vesely `FV=qᵢ·BI/Q`、RAW `q+ᵢ/Q`、RRW `Q/q−ᵢ`（后两者为**比值**形式，DOE/NASA PSA 口径）
  - 实现：`kernel/importance.py`，复用既有 BDD 概率表 + 一遍自顶向下 reach（差分数组累计跨层质量）→ **O(#节点)** 得到全部变量共因子，不引入新布尔引擎
  - 恒等式：每事件精确验算 `Q = qᵢ·q+ᵢ + (1−qᵢ)·q−ᵢ`，不等即判内核缺陷并如实失败
  - 未定义策略：`Q=0` → FV/RAW/RRW 均 `null`（reason `top_probability_is_zero`）；`q−ᵢ=0` → RRW `null`（reason `risk_reduction_worth_unbounded`）；**绝不填占位数字**
  - 支持集：事件不在编译函数内时不被省略，而以 `in_support=false, BI=0, FV=0, RAW=RRW=1` 明确报告
  - 独立验证：5 组手算锚点精确命中；120 随机模型（含 rate 派生 1e-7 量级）**4072 项精确相等、无容差**；支持集双路判定 509/509；覆盖度统计证明非空绿
  - 过程中被抓出的两个真实缺陷：变量层高于根节点时跨层质量算成 0（生产侧）；穷举时误乘被钉死变量的因子导致**负 Birnbaum**（参考侧）
  - 性能：约为概率计算的 2.6 倍（n=1500 链 2.12s→5.41s）；auto 模式 >1000 事件跳过并声明
  - 样例：`docs/SEMANTICS.md`「重要度度量语义」、报告自动附重要度表

- **λt → q 转换闸门（B06 提前）** ✅
  - 契约：模型 schema 0.2.0；`failure_rate{model=constant_failure_rate, lambda, lambda_unit, mission_time, mission_time_unit, repairable=false, source}`；十进制字符串、显式单位、仅不可修复
  - 实现：`domain/rate_model.py`，`q = −expm1(−λt)`，Decimal 高精度（默认 40 位有效数字）一次性求值 → 精确 Fraction；小 x 走无相消泰勒级数
  - 拒绝路径（10 类，全部实测）：repairable/dormant/weibull → RATE_UNSUPPORTED(3)；单位非法 → RATE_UNITS(2)；负 λ/t、双声明/无声明 → RATE_VALUE(2)；0.1.0 含 rate → VERSION(2)；语义串与内容不符 → ASSUMPTIONS(2)
  - 边界：λ=0 → q=0；λt 饱和 → q=1；单调性（λ、t）已验证
  - 独立验证：libm `expm1` 相对差 ≤1e-15；纯有理级数 oracle 差 <1e-38；**120 随机 rate 模型交叉最大差 3.79e-41（容差 1e-30）**
  - 证据输出：`rate_provenance[]`（含每事件 `interpretation`）+ 顶层 `probability_interpretation` + 强制"非 per-flight-hour"警告
  - 样例：`examples/R01_rate_and.json`、`examples/R02_rate_mixed.json`

- **FMEA 基础表与追溯（B05）** ✅
  - 领域层 `domain/fmea.py`：`FmeaRow`（组件/功能/失效模式/原因/局部影响/系统影响/控制/证据/需求/关联事件/来源/确定性说明）；**稳定标识符即身份**，显示名可改而 `fmea_id` 不变（§100）
  - 与基本事件**多对多**关联（`fmea_links`）：一行可关联多事件、一事件可被多行引用，**永不强制一对一**（§102）
  - 来源三态 `source ∈ {human, inference, import}`；`inference` 行**必须**写 `inference_note` 说明待确认事项，否则拒绝（§110）——推断**永不被记为定论**
  - 规范形式 `fmea-canonical-v1`：关联/控制/证据/需求集合**排序后**参与哈希（顺序不敏感）；`certainty` 与 `inference_note` 刻意**置于哈希之外**（描述性），因此提案时随候选一并落盘（`proposed_provenance_json`），不靠规范形式反推
  - 修订不可变（§198）：已批准内容**不可就地改写**，修订必产生**新版本**且旧版 `superseded` + 指向后继；同名至多一个当前版本
  - 复用既有评审闭环：`fmea propose → decide（需具名人类）→ apply`，与 `reviews` 同构——**未批准不得入正式表**，机器身份不得决定或应用（§181）
  - 批准**同时绑定基线与内容**：批准后基线移动 → `apply` 以 `BASELINE_CONFLICT` 拒绝；被旁路篡改的提案被重新哈希拒之
  - 追溯：`fmea trace --event/--component/--dangling`；悬空关联（基线移动后失联）**只报告与计数**，`store verify` 保持干净——合法的模型变更不得读作损坏
  - Schema **v2**（新增 3 表 + 5 索引，对 v1 **纯增量**迁移，实测可幂等重跑）：`fmea_rows` / `fmea_links` / `fmea_candidates`
  - CLI：`fmea {rows,candidates,show,propose,decide,apply,trace}`
  - 独立验证：`verification/run_fmea_verification.py`（15 当前行 + 2 被取代行由**原始列**独立重建规范形式并重哈希；34 条关联双向比对；8 类拒绝码实测；**20/20 突变被检出**；真空度闸门强制拒绝码 ≥7、悬空集非空、且 `FMEA_PLACEHOLDER` 必须实际触发过）
  - 过程中被抓出的两个真实缺陷：`_verify_fmea` 的 `versions` 集合在行循环内累积导致后继版本误报缺失（改为循环前全表预扫）；`certainty`/`inference_note` 在哈希之外，若不随候选落盘会在应用后**静默丢失**

- **重要度驱动的 FMEA 关注项（B03 收尾之一）** ✅
  - 纯函数 `plan_attention_drafts()`：把已排序的重要度表变成**工作项草稿**，不碰存储
  - 排序/过滤/`--min-value` 全程**精确有理数**（`1/3` 不会被 `0.3` 的文本序弄反）；未定义度量（`null`）一律跳过并注明 `measure_is_undefined`
  - **不重复唠叨**：查 `covered_event_ids`（当前正式行已引用的事件）与 `drafted_fmea_states`（该 id 已有草稿及其状态）→ 跳过并给理由，故重复生成是空操作
  - `--top` **只计新增草稿**；跳出时把未扫描的剩余条数如实输出
  - **定量理由精确可复现**：`inference_note` 含度量名 + 精确值 + 可比事件中排名 + `run_id` + 基线哈希前缀
  - **绝不编造内容**：四个描述字段带 `[UNCONFIRMED]`，组件/功能为 `*-UNASSIGNED`，控制/证据/要求为空
  - **工作项永不自动成为事实**：带标记的行即使被具名人类批准，`apply` 仍以 `FMEA_PLACEHOLDER` 拒绝；人类回答工单的方式是**为同一 `fmea_id` 另行提出真实内容**；`verify()` 另查"正式表是否出现带标记行"作为兜底
  - **过期基线不生成**：run 已被取代时 `BASELINE_NOT_CURRENT` 拒绝（那份排名描述的是已不存在的模型）
  - CLI：`fmea propose-from-importance <db> --run R [--by M] [--top N] [--min-value X]`
  - 独立验证：状态机上独立复跑（**标记字符串在验证脚本内另行列出**，不 import 生产常量），并新增突变"把未填写的关注项晋升入表（列/规范形式/哈希一致改写）"→ 被检出

- **变更管理下一层：影响范围分析 + 一等公民 revert（B07 前半）** ✅
  - 领域层 `domain/model_diff.py`：`diff_canonical_forms()` —— 两份规范形式的结构化 diff（纯函数、全 JSON、确定性）；`change_summary()` 给人读的计数
  - **词法噪音在 diff 之前就没了**："0.10" 与 "0.1" 的规范形式相同 → diff 为空；门输入**仅重排**被如实报告为 changed 但标 `order_only`（canonical-v1 保序，哈希确实会变，但审阅者需要知道这是布尔等价编辑）
  - `review impact <review_id>`：**从库内行**读提案规范形式与当前基线规范形式做 diff——分析不依赖任何模型文件，不可能与盘上实情漂移；同时报告 `baseline_still_current`（提案的期望基线是否还在位）
  - `review revert --model-id M --target-baseline-hash H --review-id R`：**从基线表自建的提案**，绝不经手调用方提供的文件——"revert"若能夹带一个不同的模型就失去了意义
    - 目标基线必须是**本库确实持有过的**（`REVERT_TARGET` 拒绝未知/他模型/当前基线）；存储规范形式重新哈希必须等于目标哈希（被旁路篡改 → `INTEGRITY`）
    - revert 是普通提案：**仍需具名人类 decide + apply 两步**；重复往返 = 新提案 + 新期望（乐观并发不豁免）
    - **基线行永不改写**：revert 只是把 `current_baseline_hash` 指回旧行（逐字节比对锁定）
  - **修复 revert 暴露的真实缺陷**：stale 原来只升不降（`... AND stale=0`），revert 后回到当前基线的旧 run 会**永远卡在 stale**。修复：`_refresh_staleness_locked` 双向重派生（落下者置 1、回位者清 0 连同 reason）
  - `store verify` 新增：applied review 的锚定基线必须**仍是其提案的重哈希**且存在于基线表（历史事实两侧都不许改）
  - 独立验证：population 加一次完整 review 循环 + 一次 applied revert；状态机加 impact/revert 契约复跑（`REVERT_TARGET` 计入真空度闸门）；**突变增至 10 种**（新增"applied review 的锚定基线被改指"→ 被检出）
  - 认证层（身份系统替代 `--reviewer` 字符串）：**仍未做**，待外部裁决（见"需要外部裁决"表）

- **对象级 patch 语言（B07 后半：`model-patch-v1`）** ✅
  - 领域层 `domain/patch.py`：`apply_patch()` / `patch_preview()` 纯函数；八种操作按序应用（`set_event_probability`/`add_event`/`remove_event`/`set_gate`/`add_gate`/`remove_gate`/`set_top_event`/`set_condition`）
  - **校验在规范形式层**（必须如此）：canonical 的 `p` 可为精确 `n/d`（`1/3`），宽于模型输入词法——重建文件无法合法表达；实质检查与 `validate_model` 等价（引用/全图无环含不可达部分/K_OF_N/双向语义守卫）
  - **概率走共享渲染器**：`"0.250"` ≡ `"0.25"`（同哈希）；`"1/3"` 精确保留
  - **patch 与等价全模型提案产出相同 `proposed_hash`**（收敛性，独立验证锁定）——两条入口不可能各自漂移
  - **守卫**：rate 事件的 p 不可直设（λ·t 派生）；被引用对象不可删；跨操作构造的环与不可达环被终检拒绝；非法 patch 记 `invalid`（`PATCH_OP`）
  - `review patch` / `review patch-preview`；之后 decide/apply/revert/verify 与任何提案**完全同构**
  - 样例 `examples/P01_patch_derate_and_add.json`；v1 边界：`add_event` 仅普通概率、无移动/重命名、编辑 rate 记录未实现

## 立即可做的下一步（按优先级）

1. **SCRAM 第三方差分评估**（前置阻塞：GPL-3.0 合规裁决）
   - 先行工作：GPL-3.0 许可审查（法律/合规裁决，非工程决定）
   - 若可行：固定 commit、独立进程调用、对照协议（同模型双向转换损失报告）
   - 若不可行：记录裁决，另选差分策略（如自建第二 BDD 变体 + 变量序扰动）

2. **rate 编辑的 patch 语义**（`model-patch-v2` 候选）
   - `set_event_rate` 操作：改 λ/t/单位后**重跑转换闸门**（恒定率/单位/精度）并重派生 p
   - 需要自己的语义规格与转换验证（复用 `run_rate_cross_check` 的 oracle 思路）
   - 认证层接入，用身份系统替代 `--reviewer` 字符串 + 保留标识判断（见 VERIFICATION.md 待裁决项）

3. **重要度增量重算（B03 收尾之二，先测再优化）**
   - 单事件概率变更下只重算受影响度量（当前为全量 O(#节点)，已足够快）
   - ~~按度量排序的"关键事件 Top-N"汇总~~ ✅ `store important --by … --top N`
   - ~~高 FV / 高 RAW 事件驱动 FMEA 关注项~~ ✅ `fmea propose-from-importance`
   - ~~影响范围分析 + 一等公民 revert~~ ✅ `review impact` / `review revert`
   - ~~对象级 patch 语言~~ ✅ `review patch` / `review patch-preview`

## 需要外部裁决的事项（不擅自决定）

| 事项 | 裁决者 |
| --- | --- |
| 数值容差正式冻结（rate 转换侧绝对 1e-30 提案；结构侧与重要度侧均为精确有理数，无误差） | 算法负责人 + 验证者 |
| 重要度口径确认（RAW/RRW 比值形式 vs 增量形式；若要增量须作新增度量而非改定义） | 安全专家 |
| 重要度未定义情形的工程处置（`Q=0` / `q−ᵢ=0` 时是否需要"退化值"，需先给语义规格） | 安全专家 |
| 失效率语义适用范围确认（恒定率 + 不可修复 + 固定任务时间） | 安全专家 |
| **可批准身份名单**（当前按保留标识拒绝 agent/assistant/ai/bot/local-cli/unknown；正式阶段应改由认证层判定） | 项目负责人 + 质量 |
| **并发模型**（一期单写者 + 乐观并发；团队版 PostgreSQL 的冲突粒度与提示方式） | 技术负责人 |
| 种子语义确认（p=0 事件保留在割集等） | 安全专家 |
| 首批业务 Gold Case 选取（7 开发 + 3 封存） | 安全专家 + 质量/协调角色 |
| SCRAM/GPL 商用合规 | 合规/法务 |
| FMEA 表口径（是否/何时纳入严重度-发生度-探测度-RPN，属新增语义而非就地扩展） | 安全专家 |
| 推断行的确认规程（谁确认、确认到什么程度算充分） | 安全专家 + 质量 |
| 悬空关联的处置（是否需要重新挂接/显式断开操作） | 安全专家 + 技术负责人 |
| 事件/概率/单位约定入契约 | 两线技术负责人 |
| 正式用途边界（研究/受监督/批准） | 项目负责人 + 适航团队 |

## 明确不做（本阶段）

- 动态门、修复、潜伏、共因 —— 每项需独立语义规格 + 验证项目
- FMECA 风险排序：FMEA 基础表**不含**严重度/发生度/探测度/RPN，也不产生 FMECA/FMEDA 结论
- 引擎替人**撰写** FMEA 内容（失效模式/原因/影响）：关注项只能是被标记的空工作项，带标记的行无法入表
- 非恒定失效率（Weibull/老化）、周期性检查间隔下的平均不可用度 —— 各需独立语义
- 图数据库、微服务、Kubernetes
- 前端工作台（先 CLI → API → 再 UI）
- 任何"计算成功 = 安全结论"的表述；任何把工程变更批准等同于安全批准的表述
- 对象级 patch 语言与合并（**前向**修改仍走整份模型提案；revert 已是一等公民但只回退到库内持有过的基线）
