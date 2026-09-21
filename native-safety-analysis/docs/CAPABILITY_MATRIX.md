# CAPABILITY_MATRIX — 能力矩阵（诚实状态）

状态定义：**implemented** = 代码存在；**verified** = 已有独立证据（测试/交叉验证实际执行通过）；**piloted** = 真实业务试用过。三者互不混用。未实现的能力显式拒绝，不占位成功。

| capability_id | 实现状态 | 独立验证状态 | 真实试用状态 | 证据 | 限制 |
| --- | --- | --- | --- | --- | --- |
| validate_static_fta | implemented | verified（15 种子 + 12 结构拒绝路径 + 10 rate 拒绝路径） | 未执行 | tests/test_seed_cases.py, tests/test_rate_model.py | 契约 0.1.0/0.2.0 子集；≤20,000 事件 / ≤100,000 门 |
| analyze_static_fta_probability (AND/OR) | implemented | verified（种子精确分数 + 200 随机交叉 + 独立 oracle） | 未执行 | verification/run_cross_check.py | 固定概率、独立性须显式声明 |
| analyze_static_fta_probability (K_OF_N) | implemented | verified（M05 + 随机交叉含嵌套表决） | 未执行 | tests/test_seed_cases.py | 输入引用互异；k∈[1,n] |
| minimal_cut_sets | implemented | verified（全部正例 + 无超集变形测试） | 未执行 | tests/test_metamorphic.py | 路径上限 200k；截断必标 incomplete |
| repeated_event_shared_subgraph | implemented | verified（M03/M07/M09/M10 + 0.044≠0.0494 锚点） | 未执行 | tests/test_seed_cases.py::test_m03_repeated_event_anchor | DAG 语义；同名即同变量 |
| semantic_model_hash | implemented | verified（稳定性+敏感性测试） | 未执行 | tests/test_limits_and_cli.py | canonical-v1；词法差异归一；含 rate 来源 |
| evidence_bundle | implemented | verified（CLI 集成测试检查五件套+manifest 哈希） | 未执行 | tests/test_limits_and_cli.py | 单机目录形态；无审批身份 |
| cli_validate/analyze/capabilities | implemented | verified（退出码 0/2/3/4 实测） | 未执行 | tests/test_limits_and_cli.py | — |
| exponential_rate_to_probability (q=−expm1(−λt)) | implemented | **verified**（libm 对照 ≤1e-15 相对；有理级数 oracle 差 <1e-38；120 随机交叉最大差 3.79e-41；单调/边界不变量） | 未执行 | tests/test_rate_model.py, verification/run_rate_cross_check.py | 恒定率、**不可修复**、显式单位 + 任务时间；q 为**任务失效概率**，非 per-flight-hour |
| importance_measures (Birnbaum / FV / RAW / RRW) | implemented | **verified**（5 组手算锚点精确命中；120 随机模型 4072 项**精确相等**对照（无容差）；支持集双路判定 509/509；每事件共因子恒等式内建校验；60+ 模型序关系不变量） | 未执行 | tests/test_importance.py, verification/run_importance_cross_check.py | 仅相干模型；RAW/RRW 为**比值形式**；`Q=0`/`q−ᵢ=0` 如实返回 null + reason；>1000 事件时 auto 模式跳过并在输出中声明 |
| NOT / non-coherent logic | 显式拒绝 | verified（拒绝路径） | — | SEMANTICS.md | 相干模型边界 |
| dynamic_gates (PAND/PDP/SPARE) | 显式拒绝 | verified（N05） | — | tests/test_seed_cases.py | UNSUPPORTED_GATE |
| repair / dormancy / CCF models | 显式拒绝 | verified（repairable/dormant/latent/inspection 各拒绝路径） | — | tests/test_rate_model.py | RATE_UNSUPPORTED，不静默退化 |
| **local_baseline_run_store** | implemented | **verified**（52 次 run 用 2ⁿ oracle 重推导库内数值，1362 项**精确相等无容差**；53 份基线规范形式独立重哈希；4501 值 SQLite `typeof()` 扫描零 REAL；往返 dump==restore；**9/9 突变被检出**） | 未执行 | tests/test_store.py, verification/run_store_verification.py | 单写者 + 乐观并发；基线不可就地改写；无结构化 patch 语言；无回退命令；存储层不授予批准权 |
| FMEA / requirements traceability | 未实现 | — | — | docs/NEXT_STEPS.md | 30 天工作包 B05 |
| web workbench | 未实现 | — | — | docs/NEXT_STEPS.md | 第二阶段 |
| Open-PSA / ReqIF import | 未实现 | — | — | docs/NEXT_STEPS.md | 互操作须专项验证 |
| PostgreSQL 迁移 | 未实现 | — | — | docs/NEXT_STEPS.md | 一期已备 `store export` 往返，迁移脚本待团队版 |

## 使用边界声明

本版本为**研究样机 + 方法可用候选**：
- 可声明：对契约 0.1.0/0.2.0 范围内的相干静态 FTA 模型独立完成确定性精确计算（含恒定失效率→任务概率的一次受控舍入转换），给出精确的 Birnbaum / Fussell–Vesely / RAW / RRW 重要度，并在本地库中按语义哈希锚定基线、保留旧版证据、把变更走"提案→批准→应用"闭环
- 可声明：维护一张与基本事件**多对多关联**的 FMEA 基础表，按 `fmea-canonical-v1` 精确重哈希，区分人工/推断/导入来源，并把推断行的确认要求与人工批准绑定到具体基线与内容
- 不可声明：替代 Medini、DO-330/TQL 任何等级、适航认可、正式安全结论；亦不可把 q 当作 per-flight-hour 指标；重要度数值本身不构成任何设计变更批准
- FMEA 表**只是失效模式与追溯的载体**，不含严重度/发生度/探测度/RPN，也不产生 FMECA/FMEDA 结论
- 存储层记录的是**工程变更的决定**，不是安全结论的批准
- `approval_state` 在所有输出中恒为 `not_granted_by_this_result`
