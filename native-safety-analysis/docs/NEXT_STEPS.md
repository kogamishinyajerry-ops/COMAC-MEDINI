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
  - CLI：`analyze --db`、`store {init,status,runs,show,baseline,adopt-baseline,important,verify,export}`、`review {propose,list,show,decide,apply}`
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
  - 独立验证：`verification/run_fmea_verification.py`（15 当前行 + 2 被取代行由**原始列**独立重建规范形式并重哈希；34 条关联双向比对；7 类拒绝码实测；**19/19 突变被检出**；真空度闸门强制拒绝码 ≥7 且悬空集非空）
  - 过程中被抓出的两个真实缺陷：`_verify_fmea` 的 `versions` 集合在行循环内累积导致后继版本误报缺失（改为循环前全表预扫）；`certainty`/`inference_note` 在哈希之外，若不随候选落盘会在应用后**静默丢失**

## 立即可做的下一步（按优先级）

1. **SCRAM 第三方差分评估**
   - 先行工作：GPL-3.0 许可审查（法律/合规裁决，非工程决定）
   - 若可行：固定 commit、独立进程调用、对照协议（同模型双向转换损失报告）
   - 若不可行：记录裁决，另选差分策略（如自建第二 BDD 变体 + 变量序扰动）

2. **重要度与 FMEA 联动的工程接口（B03 收尾）**
   - ~~按度量排序的"关键事件 Top-N"汇总~~ ✅ 已落地为 `store important --by … --top N`（按精确 Fraction 排序，不是按文本）
   - 高 FV / 高 RAW 事件**驱动 FMEA 关注项**：由重要度排序生成候选 FMEA 行（`source=inference` + 说明），仍须人工批准入表
   - 增量重算：单事件概率变更下只重算受影响度量（当前为全量 O(#节点)，已足够快，先测再优化）

3. **变更管理的下一层**
   - 结构化 patch（当前提案是整份模型）+ 影响范围分析
   - 回退：目前只能"再提案一次旧语义"；是否需要一等公民的 revert 命令待定
   - 认证层接入，用身份系统替代 `--reviewer` 字符串 + 保留标识判断（见 VERIFICATION.md 待裁决项）

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
- 非恒定失效率（Weibull/老化）、周期性检查间隔下的平均不可用度 —— 各需独立语义
- 图数据库、微服务、Kubernetes
- 前端工作台（先 CLI → API → 再 UI）
- 任何"计算成功 = 安全结论"的表述；任何把工程变更批准等同于安全批准的表述
- 结构化 patch 语言与对象级合并（先用整份模型的提案闭环跑通业务）
