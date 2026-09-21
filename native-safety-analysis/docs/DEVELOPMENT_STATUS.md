# DEVELOPMENT_STATUS — 开发状态（真实记录）

基线：2026-09-21。环境：Windows 11，Git Bash，Python 3.13.12（托管）+ 3.11.8（系统）实测。

## 已实现

- `src/native_safety/domain/`：模型数据类、错误码体系（15 类，含 rate 三类）、语义校验（引用/循环/重复 ID/概率词法与范围/失效率闸门/K_OF_N 边界/独立性声明/全图环检测）、**恒定失效率→任务概率转换**（`rate_model.py`，高精度 Decimal + 无相消泰勒级数）、规范化语义哈希（canonical-v1，含 rate 来源）、**共享十进制定点渲染**（`ratnum.py`，5 处重复实现已收拢为一处）
- `src/native_safety/kernel/`：ROBDD（唯一节点表、计算缓存、迭代 apply、mark-sweep 压实、节点上限）、平衡合并编译、同门扁平化（链式 O(n²)→近线性）、K_OF_N 阈值递推、**共享概率表**（`_probability_table`，同时供概率与重要度）、位掩码最小割集（路径上限+诚实截断标记）、**重要度内核**（`importance.py`：单遍自顶向下 reach + 差分数组累计跨层质量，O(#节点) 得到全部变量的共因子；每事件内建恒等式自检；未定义项如实上报）
- `src/native_safety/adapters/`：防御性 JSON 读取、契约词法概率输出
- `src/native_safety/evidence/`：run_<id>/ 证据包（manifest/inputs/results/reports/execution，SHA-256 工件哈希）
- `src/native_safety/cli/`：validate / analyze / capabilities，退出码 0/2/3/4/5；rate 模型额外输出 `rate_provenance[]`（每事件含 `interpretation`）、`probability_interpretation` 与强制非 per-flight-hour 警告；**`--importance {auto,on,off}`** 与 `importance_measures[]`（按 FV 降序）/`importance_status`/`importance_conventions`/`importance_exact`，报告含重要度表
- `verification/`：独立参考实现（真值表穷举 + Fraction，与生产内核零共享）、210 模型结构交叉对照脚本、**120 模型 rate 转换交叉对照脚本**（独立有理级数 oracle）、**120 模型重要度交叉对照脚本**（独立共因子 oracle + 真值表支持集判定，4072 项精确相等，附覆盖度统计）
- `tests/`：87 项测试（15 种子 + 12 结构拒绝 + 10 rate 拒绝 + 8 变形不变量 + rate 数值/边界不变量 + **22 项重要度：5 组手算锚点 + 跳层构型 + 变量高于根节点回归 + 未定义策略 + 支持集反例 + 吸收律 + 顺序无关 + 随机不变量 + CLI/报告集成** + 资源上限 + CLI/证据包集成 + 哈希稳定性 + rate 交叉验证）

## 实际执行并通过（2026-09-21）

```text
python -m pytest tests/ -q                       → 87 passed in 3.71s   (venv 3.13.12, pytest 9.1.1)
python verification/run_cross_check.py           → 210/210 passed       (结构，Fraction 精确相等)
python verification/run_rate_cross_check.py      → 120/120 passed       (rate，tolerance 1e-30，max diff 3.79e-41)
python verification/run_importance_cross_check.py→ 120/120 passed       (重要度，4072 项精确相等，无容差)
python run.py analyze …R01_rate_and… --evidence-dir … → succeeded, schema 0.2.0, p=1.997002664917588e-6, exit 0
python run.py analyze …M03…                      → status succeeded, p=0.044, exit 0, importance ranked A/C/B
python run.py analyze …M03… --importance off     → importance_measures null + 明确警告, exit 0
python run.py validate …N05…                     → unsupported, exit 3
python run.py validate <repairable:true / weibull> → RATE_UNSUPPORTED, exit 3
python run.py validate <v0.1.0 含 rate>           → VERSION, exit 2
python run.py validate <lambda_unit=1/s>          → RATE_UNITS, exit 2
```

性能实测（链式 OR 压力模型，i7，Python 3.13.12）：
n=400 → 0.13s；n=800 → 0.52s；n=1500 → 1.63s；n=3000 → 7.88s（BDD 节点=n，割集=n）
加算重要度：n=200 → 0.058s；n=800 → 1.23s；n=1500 → 5.41s（约为概率计算的 2.6 倍，绝对值与 1500 条记录恒等式校验全通过）

## 未执行 / 阻塞

- 第三方差分（SCRAM）：BLOCKED: GPL-3.0 商用合规裁决
- 业务 Gold Case：BLOCKED: 等待启动会确定专家与脱敏模型
- 真实 Medini 对照：不在 B 线范围（A 线职责；对照属联合验收）
- 持久化/FMEA/API/Web：未开始（30 天工作包序列；下一步为 B02 持久化与基线）
- 适航/工具鉴定材料：未开始，本版本不作此声明

## 已知限制

1. MCS 枚举为路径枚举+包含极小化；>200k 满足路径的模型触发截断（如实标记 incomplete）。超大割集族需要 Prime-Implicants/零抑制 BDD 等专用结构（后续）
2. 变量序为 DFS 发现序启发式，无动态重排序；病态交织结构可能触发节点上限（如实返回 resource_limited）
3. **失效率转换是全局唯一引入近似的步骤**：默认 40 位有效数字（`rate_precision_digits` 可在 [15,200] 调整），实测偏差 ~1e-41；其后全部精确有理数。仅覆盖恒定率 + 不可修复 + 显式任务时间
4. NOT/非相干、动态语义：显式 unsupported（能力矩阵见 CAPABILITY_MATRIX.md）
5. **重要度仅在相干模型内有定义**（与本引擎模型边界一致）；`Q=0` 或 `q−ᵢ=0` 时 FV/RAW/RRW 数学上无定义，如实输出 `null` + reason，绝不填占位数字；RAW/RRW 为比值形式（口径已在 `importance_conventions` 中随结果发布，待外部裁决确认）
6. 重要度在基本事件数 > 1000 时 auto 模式跳过（避免结果信封过大），跳过原因写入输出与警告；`--importance on` 可强制
7. 证据包身份字段为占位（local-cli, no approval authority）；正式审批身份待认证层
8. 单写者模型；无并发控制（B02 引入）
9. 测试随机结构模型规模 ≤8 事件（参考实现 2^n 穷举上限 20 事件的契约约束）；更大规模由链式/结构化压力测试覆盖（重要度恒等式已在 n=1500 压力模型上逐记录校验）。rate 交叉验证模型 ≤5 事件（同为 2^n 穷举约束）

## 与开工提示词「最小交付链」的对照

| 要求 | 状态 |
| --- | --- |
| 1. 自主 JSON 读取 + 结构/语义校验 | ✅ |
| 2. CLI 生产内核：顶事件概率/假设/版本/语义哈希/运行状态 | ✅（engine_stats 附变量序、节点数、限额） |
| 3. 小规模模型经验证的最小割集（未实现则明确排除） | ✅（已实现并验证；截断诚实标记） |
| 4. 可读报告 + 结构化证据清单 | ✅（report.md + manifest.json 五件套） |
| 5. 独立参考、种子、负例、变形验证；无 Medini/A 线/LLM/网络重跑 | ✅（全部本地标准库执行） |

首 72 小时完成定义（无 UI）：读取 JSON、校验 AND/OR 图、独立计算概率、结构化输出、最小范围报告、测试通过、重复事件例题通过、未知门拒绝 —— **全部满足**。

## 诚实声明

本版本是研究样机 + 方法可用候选。计算成功 ≠ 验证通过 ≠ 工程审查通过 ≠ 批准用途。所有输出 `approval_state = not_granted_by_this_result`。
