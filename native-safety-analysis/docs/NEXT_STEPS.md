# NEXT_STEPS — 后续工作（按步骤，不按日期）

按 30 天工作包（B_核心规划 §10）映射为可执行步骤。每步完成标准：独立验证证据 + 变形/负例测试同步扩展。

## 已完成

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

## 立即可做的下一步（按优先级）

1. **持久化与基线（B02）**
   - SQLite 单文件存储：models/baselines/runs/reviews 表
   - 语义哈希做版本锚；模型变更 → 旧 run 标 stale
   - 幂等 run_id、乐观并发（baseline_hash 前置核对）
   - 重要度结果一并入库（按 run + event 存精确有理数字符串，不存浮点）

2. **FMEA 基础表（B05）**
   - FmeaRow Schema（组件/功能/失效模式/影响/控制/证据）
   - 与 BasicEvent 关联（多对多，不强迫一对一）
   - Agent 候选行必须带 source=inference 标记，人工确认前不入正式表

3. **SCRAM 第三方差分评估**
   - 先行工作：GPL-3.0 许可审查（法律/合规裁决，非工程决定）
   - 若可行：固定 commit、独立进程调用、对照协议（同模型双向转换损失报告）
   - 若不可行：记录裁决，另选差分策略（如自建第二 BDD 变体 + 变量序扰动）

4. **重要度落地的工程接口（B03 收尾）**
   - 按度量排序的"关键事件 Top-N"汇总（现已在结果内排序，待做成独立视图）
   - 与 FMEA 表联动：高 FV/高 RAW 事件驱动 FMEA 关注项（需先有 B05）
   - 增量重算：单事件概率变更下只重算受影响度量（当前为全量 O(#节点)，已足够快，先测再优化）

## 变更管理最小闭环（并入步骤 2）

- propose_change(patch, expected_baseline_hash) → validate → review → apply
- 审批身份：本地 CLI 阶段用 `--reviewer` 显式声明，记录到 manifest；Web 阶段接认证层
- Agent 无批准权：提案进入 review 状态即停

## 需要外部裁决的事项（不擅自决定）

| 事项 | 裁决者 |
| --- | --- |
| 数值容差正式冻结（rate 转换侧绝对 1e-30 提案；结构侧与重要度侧均为精确有理数，无误差） | 算法负责人 + 验证者 |
| 重要度口径确认（RAW/RRW 比值形式 vs 增量形式；若要增量须作新增度量而非改定义） | 安全专家 |
| 重要度未定义情形的工程处置（`Q=0` / `q−ᵢ=0` 时是否需要"退化值"，需先给语义规格） | 安全专家 |
| 失效率语义适用范围确认（恒定率 + 不可修复 + 固定任务时间） | 安全专家 |
| 种子语义确认（p=0 事件保留在割集等） | 安全专家 |
| 首批业务 Gold Case 选取（7 开发 + 3 封存） | 安全专家 + 质量/协调角色 |
| SCRAM/GPL 商用合规 | 合规/法务 |
| 事件/概率/单位约定入契约 | 两线技术负责人 |
| 正式用途边界（研究/受监督/批准） | 项目负责人 + 适航团队 |

## 明确不做（本阶段）

- 动态门、修复、潜伏、共因 —— 每项需独立语义规格 + 验证项目
- 非恒定失效率（Weibull/老化）、周期性检查间隔下的平均不可用度 —— 各需独立语义
- 图数据库、微服务、Kubernetes
- 前端工作台（先 CLI → API → 再 UI）
- 任何"计算成功 = 安全结论"的表述
