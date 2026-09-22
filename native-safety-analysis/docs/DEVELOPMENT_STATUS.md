# DEVELOPMENT_STATUS — 开发状态（真实记录）

基线：2026-09-21。环境：Windows 11，Git Bash，Python 3.13.12（托管）+ 3.11.8（系统）实测。

## 已实现

- `src/native_safety/domain/`：模型数据类、错误码体系（20 类，含 rate 三类 + FMEA 四类）、语义校验（引用/循环/重复 ID/概率词法与范围/失效率闸门/K_OF_N 边界/独立性声明/全图环检测）、**恒定失效率→任务概率转换**（`rate_model.py`，高精度 Decimal + 无相消泰勒级数）、规范化语义哈希（canonical-v1，含 rate 来源）、**共享十进制定点渲染**（`ratnum.py`，5 处重复实现已收拢为一处）、**FMEA 领域层**（`fmea.py`：`FmeaRow`/`EvidenceRef`、`fmea-canonical-v1` 规范形式与哈希、来源与推断说明校验、稳定标识符身份、**重要度驱动的关注项规划** `plan_attention_drafts()` 与 `[UNCONFIRMED]` 占位标记语义）
- `src/native_safety/kernel/`：ROBDD（唯一节点表、计算缓存、迭代 apply、mark-sweep 压实、节点上限）、平衡合并编译、同门扁平化（链式 O(n²)→近线性）、K_OF_N 阈值递推、**共享概率表**（`_probability_table`，同时供概率与重要度）、位掩码最小割集（路径上限+诚实截断标记）、**重要度内核**（`importance.py`：单遍自顶向下 reach + 差分数组累计跨层质量，O(#节点) 得到全部变量的共因子；每事件内建恒等式自检；未定义项如实上报）
- `src/native_safety/adapters/`：防御性 JSON 读取、契约词法概率输出
- `src/native_safety/evidence/`：run_<id>/ 证据包（manifest/inputs/results/reports/execution，SHA-256 工件哈希）
- `src/native_safety/store/`：**持久化适配层**（`schema.py` 迁移注册表 + 无浮点列审计，**schema v2 八表**；`repository.py` 仓储 + 幂等 run + 乐观并发 + stale 派生 + 评审状态机 + **FMEA 行/关联/候选闭环与追溯** + **关注项占位守卫与覆盖查询** + dump/restore + 自校验 `verify()`；`errors.py` 十五类存储错误码）。单文件 SQLite，只依赖标准库 `sqlite3`，不做校验、不做文件 IO
- `src/native_safety/cli/`：validate / analyze / capabilities / **store**（init/status/runs/show/baseline/adopt-baseline/important/verify/export）/ **review**（propose/**patch**/**patch-preview**/**impact**/**revert**/list/show/decide/apply）/ **fmea**（rows/candidates/show/propose/**propose-from-importance**/decide/apply/trace），退出码 0/2/3/4/5；rate 模型额外输出 `rate_provenance[]`（每事件含 `interpretation`）、`probability_interpretation` 与强制非 per-flight-hour 警告；**`--importance {auto,on,off}`** 与 `importance_measures[]`（按 FV 降序）/`importance_status`/`importance_conventions`/`importance_exact`，报告含重要度表；`analyze --db` 增加 `store{status,...}` 块（被拒时如实标 refused 且退出码非零，但保留分析结果）
- `verification/`：独立参考实现（真值表穷举 + Fraction，与生产内核零共享）、210 模型结构交叉对照脚本、**120 模型 rate 转换交叉对照脚本**（独立有理级数 oracle）、**120 模型重要度交叉对照脚本**（独立共因子 oracle + 真值表支持集判定，4072 项精确相等，附覆盖度统计）、**存储验证脚本**（raw sqlite3 读回 + 2ⁿ oracle 重推导 + 独立重哈希 + 9 项突变测试 + 往返测试）、**FMEA 验证脚本**（raw sqlite3 由列级原值独立重建规范形式并重哈希 + 状态机独立复跑 + **20 项突变测试** + 真空度闸门）
- `tests/`：**314 项测试**（15 种子 + 12 结构拒绝 + 10 rate 拒绝 + 8 变形不变量 + rate 数值/边界不变量 + 22 项重要度 + 53 项存储 + **85 项 FMEA（校验拒绝/顺序不敏感/哈希边界/候选闭环/乐观并发/修订不可变/悬空关联/**重要度关注项**/往返/CLI 全流程）** + **24 项变更管理（纯函数 diff/词法噪音/order_only/impact 从库内读/revert 生命周期/stale 双向翻转/守卫/CLI）** + **55 项 patch（纯函数/按序应用/共享渲染/rate 守卫/收敛性/set_event_rate+add_event(rate) 转换闸门+oracle/语义显式声明/schema 只升不降/22 种非法操作/环检测/CLI）** + 资源上限 + CLI/证据包集成 + 哈希稳定性 + 交叉验证）

## 实际执行并通过（2026-09-21）

```text
python -m pytest tests/ -q                       → 314 passed in ~60s    (venv 3.13.12, pytest 9.1.1)
python verification/run_cross_check.py           → 210/210 passed       (结构，Fraction 精确相等)
python verification/run_rate_cross_check.py      → 120/120 passed       (rate，tolerance 1e-30，max diff 3.79e-41)
python verification/run_importance_cross_check.py→ 120/120 passed       (重要度，4072 项精确相等，无容差)
python verification/run_store_verification.py    → PASSED               (存储，1374 项重推导精确相等；含 impact/revert/patch+rate-edit 契约；10/10 突变被检出)
python verification/run_fmea_verification.py     → PASSED               (FMEA，17 行独立重哈希；34 条关联；20/20 突变被检出)
python run.py analyze …R01_rate_and… --evidence-dir … → succeeded, schema 0.2.0, p=1.997002664917588e-6, exit 0
python run.py analyze …M03…                      → status succeeded, p=0.044, exit 0, importance ranked A/C/B
python run.py analyze …M03… --importance off     → importance_measures null + 明确警告, exit 0
python run.py analyze …M03… --db store.sqlite    → store.status=recorded (再跑 → idempotent), exit 0
python run.py analyze …M02… --db store.sqlite --run-id=<已用> → RUN_ID_CONFLICT, exit 2（分析结果仍输出）
python run.py store verify store.sqlite          → problems=[], exit 0
python run.py review decide … --reviewer agent   → APPROVAL_AUTHORITY, exit 2
python run.py review apply  … --reviewer JerryKogami → applied, 旧 run 标 stale, exit 0
python run.py review impact <db> REV-1         → 结构化 diff（事件/门/假设/顶事件增删改）, exit 0
python run.py review patch-preview <db> --model-id M --patch p.json → 只读 diff 预览, exit 0
python run.py review patch <db> --model-id M --patch p.json --review-id P1 --expected-baseline-hash H
                                                              → 普通提案（作用于库内基线）, exit 0
python run.py review patch <db> … --patch <非法>              → 记 invalid + PATCH_OP（留审计）
python run.py review patch <db> … --expected-baseline-hash <过期> → BASELINE_CONFLICT, exit 2
python run.py review revert <db> --model-id M --target-baseline-hash <本库持有过的> --review-id RV-1
                                                              → 提案自建自库, exit 0
python run.py review revert <db> … --target-baseline-hash <当前基线> → REVERT_TARGET, exit 2
python run.py review revert <db> … （agent 决定）             → APPROVAL_AUTHORITY, exit 2
python run.py review apply  <db> RV-1 --reviewer JerryKogami → 旧基线恢复, stale 双向翻转, verify 干净
python run.py fmea propose … --source inference --inference-note "…" → draft，正式表仍为空, exit 0
python run.py fmea propose … --source inference（缺说明）          → FMEA_SOURCE, exit 2
python run.py fmea decide  … --reviewer agent    → APPROVAL_AUTHORITY, exit 2
python run.py fmea apply   … --reviewer JerryKogami → applied，推断说明原样保留, exit 0
python run.py fmea trace   store.sqlite --dangling → 悬空关联如实列出（不改 verify 结论）
python run.py fmea propose-from-importance … --run r1 --top 2 → 2 条关注项草稿，正式表仍为空, exit 0
python run.py fmea propose-from-importance … （再跑）         → proposed=0，全部 already_pending, exit 0
python run.py fmea propose-from-importance … --by risk_reduction_worth → 未定义度量跳过并注明, exit 0
python run.py fmea propose-from-importance … （run 已被取代） → BASELINE_NOT_CURRENT, exit 2
python run.py fmea apply <ATTN 草稿> --reviewer JerryKogami  → FMEA_PLACEHOLDER, exit 2（未填写不得入表）
python run.py validate …N05…                     → unsupported, exit 3
python run.py validate <repairable:true / weibull> → RATE_UNSUPPORTED, exit 3
python run.py validate <v0.1.0 含 rate>           → VERSION, exit 2
python run.py validate <lambda_unit=1/s>          → RATE_UNITS, exit 2
```

性能实测（链式 OR 压力模型，i7，Python 3.13.12）：
n=400 → 0.13s；n=800 → 0.52s；n=1500 → 1.63s；n=3000 → 7.88s（BDD 节点=n，割集=n）
加算重要度：n=200 → 0.058s；n=800 → 1.23s；n=1500 → 5.41s（约为概率计算的 2.6 倍，绝对值与 1500 条记录恒等式校验全通过）

编译复用（B03 收尾：先测再优化的实测落地，`verification/run_incremental_bench.py`）：
相位分解（链式 OR，i7，venv 3.13）：compile 占总时长 **31–45%**（n=3000：compile 3.38s / table 0.42s / importance 7.05s），
且**数学上与概率无关**（变量序/图/割集只读结构）。`solve_model` 按结构指纹缓存结构产物
（BDD + 变量序 + 割集），概率变更命中缓存只付 table+importance：n=1500 实测
**冷 1.83s → 暖 1.13s（1.62×）**、概率变更暖路 1.46s vs 全新 2.24s（**1.53×**）；
正确性不依赖缓存——table 与 importance **每次全量重算**，复用由 9 项恒等测试锁定
（缓存命中结果 == 清缓存后全新求解，逐字段含 undefined reasons）。

存储侧无性能瓶颈：写入为单事务常数条语句；`store verify` 对 22 run / 4501 值在秒级完成。

## 未执行 / 阻塞

- 第三方差分（SCRAM）：BLOCKED: GPL-3.0 商用合规裁决
- 业务 Gold Case：BLOCKED: 等待启动会确定专家与脱敏模型
- 真实 Medini 对照：不在 B 线范围（A 线职责；对照属联合验收）
- **FMEA 基础表与追溯：已实现并验证（B05）**；FMECA 风险排序（严重度/发生度/探测度/RPN）**明确不在范围**
- 重要度→FMEA 关注项的自动生成：**已实现并验证**（`fmea propose-from-importance`）；生成的是**带标记的工作项**，不是内容
- **编译复用：已实现并验证**（结构指纹缓存，概率变更免重编译 1.5–1.6×；table/importance 仍每次全量重算——数学上不可避免；9 项恒等测试 + 基准脚本入库）
- API / Web 工作台：未开始（30 天工作包序列；先 CLI → API → 再 UI）
- PostgreSQL 迁移脚本：未开始（一期已备 `store export` 往返；团队版阶段）
- **影响范围分析与 revert：已实现并验证**（`review impact` / `review revert`；revert 从库内自建提案，仍走两步人类批准）
- **对象级 patch 语言：已实现并验证**（`review patch` / `review patch-preview`，十种操作、与全模型提案收敛）；**`set_event_rate` 编辑既有 rate 记录**与**`add_event(rate)` 新增 rate 事件**均已实现（转换闸门重跑 + 独立 oracle 对照 + 收敛性）；首个 rate 事件自动升 schema 0.2.0、语义声明须显式 `set_probability_semantics`、schema 只升不降
- 适航/工具鉴定材料：未开始，本版本不作此声明

## 已知限制

1. MCS 枚举为路径枚举+包含极小化；>200k 满足路径的模型触发截断（如实标记 incomplete）。超大割集族需要 Prime-Implicants/零抑制 BDD 等专用结构（后续）
2. 变量序为 DFS 发现序启发式，无动态重排序；病态交织结构可能触发节点上限（如实返回 resource_limited）
3. **失效率转换是全局唯一引入近似的步骤**：默认 40 位有效数字（`rate_precision_digits` 可在 [15,200] 调整），实测偏差 ~1e-41；其后全部精确有理数。仅覆盖恒定率 + 不可修复 + 显式任务时间
4. NOT/非相干、动态语义：显式 unsupported（能力矩阵见 CAPABILITY_MATRIX.md）
5. **重要度仅在相干模型内有定义**（与本引擎模型边界一致）；`Q=0` 或 `q−ᵢ=0` 时 FV/RAW/RRW 数学上无定义，如实输出 `null` + reason，绝不填占位数字；RAW/RRW 为比值形式（口径已在 `importance_conventions` 中随结果发布，待外部裁决确认）
6. 重要度在基本事件数 > 1000 时 auto 模式跳过（避免结果信封过大），跳过原因写入输出与警告；`--importance on` 可强制
7. 证据包身份字段为占位（local-cli, no approval authority）；正式审批身份待认证层
8. **存储为单写者模型**：靠写入时校验预期基线哈希实现乐观并发，无行级锁、无重试、无多进程协调。多写者阶段按 VERIFICATION.md 的待裁决项扩展
9. 存储层不授予任何批准权；`reviews` 记录的是工程变更决定，`approval_state` 恒为 `not_granted_by_this_result`
10. "可批准身份"当前按保留标识名单判断（agent/assistant/ai/bot/local-cli/unknown/空），属权宜实现；正式阶段须接认证层
11. 测试随机结构模型规模 ≤8 事件（参考实现 2^n 穷举上限 20 事件的契约约束）；更大规模由链式/结构化压力测试覆盖（重要度恒等式已在 n=1500 压力模型上逐记录校验）。rate 交叉验证模型 ≤5 事件，存储重推导模型 ≤7 事件（同为 2^n 穷举约束）
12. **FMEA 表不含风险排序**：无严重度/发生度/探测度/RPN，不产生 FMECA/FMEDA 结论；表的定位是"失效模式 + 追溯"，不是风险台账
13. **推断行必须人工确认**：`source=inference` 的行强制写 `inference_note` 且须具名人类批准方可入表；但"确认到什么程度算充分"尚无规程（待安全专家）
14. **悬空关联不自动修复**：基线移动后失联的关联只报告与计数，`store verify` 保持干净（合法变更不得读作损坏）；是否提供重新挂接/断开操作待定
15. `certainty`/`inference_note` 置于规范形式哈希之外（描述性字段），因此不参与去重与完整性判定；其保真依赖候选落盘的 provenance 副本
16. **关注项只是工作项**：`fmea propose-from-importance` 的四个描述字段永远是 `[UNCONFIRMED]` 占位，组件/功能为 `*-UNASSIGNED`；带标记的行**无法**进入正式表（`FMEA_PLACEHOLDER`）。引擎不撰写 FMEA 内容，也不存在让它自动补全的路径
17. 关注项生成只对**单一模型当前基线上的 run** 有效：run 已被取代即拒绝；跨模型/跨基线的重要性比较不在本版本定义范围
18. **revert 只能回到本库持有过的基线**：没有库外的"旧版本回退"；diff 的 `order_only` 标注依赖 canonical-v1 保序这一事实，若未来哈希改为输入序不敏感需同步重审该判定
19. **patch v1 边界**：无移动/重命名；既有普通事件不可"转成" rate 事件（删了重加是不同身份）；schema 只升不降（删光 rate 事件后终检拒绝，降级须显式决定）；patch 校验在规范形式层做（canonical 的 `n/d` 概率宽于模型输入词法，这是设计决定）
20. impact/revert/patch 的批准者身份仍是 `--reviewer` 字符串 + 保留标识判断（权宜实现，同第 10 条）；认证层未接入

## 与开工提示词「最小交付链」的对照

| 要求 | 状态 |
| --- | --- |
| 1. 自主 JSON 读取 + 结构/语义校验 | ✅ |
| 2. CLI 生产内核：顶事件概率/假设/版本/语义哈希/运行状态 | ✅（engine_stats 附变量序、节点数、限额） |
| 3. 小规模模型经验证的最小割集（未实现则明确排除） | ✅（已实现并验证；截断诚实标记） |
| 4. 可读报告 + 结构化证据清单 | ✅（report.md + manifest.json 五件套） |
| 5. 独立参考、种子、负例、变形验证；无 Medini/A 线/LLM/网络重跑 | ✅（全部本地标准库执行） |
| 6. FMEA 基础表 + 与基本事件的追溯（B05） | ✅（多对多关联；推断行强制说明且须人工批准；修订即新版本；独立验证 20/20 突变检出） |
| 7. 重要度驱动的 FMEA 关注项 | ✅（生成带 `[UNCONFIRMED]` 标记的**工作项**；幂等、覆盖跳过、精确过滤；带标记的行无法入表） |
| 8. 影响范围分析 + 一等公民 revert | ✅（`review impact` diff 两侧取自库内行；`review revert` 从基线表自建提案仍走两步人类批准；stale 双向重派生） |
| 9. 对象级 patch 语言 | ✅（`review patch` 十种操作作用于库内规范形式；与等价全模型提案**收敛于同一哈希**；rate p 不可直设；`set_event_rate`/`add_event(rate)` 重跑转换闸门并对照独立 oracle；schema 只升不降；非法 patch 记 invalid） |

首 72 小时完成定义（无 UI）：读取 JSON、校验 AND/OR 图、独立计算概率、结构化输出、最小范围报告、测试通过、重复事件例题通过、未知门拒绝 —— **全部满足**。

## 诚实声明

本版本是研究样机 + 方法可用候选。计算成功 ≠ 验证通过 ≠ 工程审查通过 ≠ 批准用途。所有输出 `approval_state = not_granted_by_this_result`。
