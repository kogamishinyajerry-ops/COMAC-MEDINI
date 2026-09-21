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
| **local_baseline_run_store** | implemented | **verified**（52 次 run 用 2ⁿ oracle 重推导库内数值，1362 项**精确相等无容差**；55 份基线规范形式独立重哈希；4615 值 SQLite `typeof()` 扫描零 REAL；往返 dump==restore；**10/10 突变被检出**；**9 类拒绝码实测**含 revert 守卫） | 未执行 | tests/test_store.py, tests/test_change_mgmt.py, verification/run_store_verification.py | 单写者 + 乐观并发；基线不可就地改写；存储层不授予批准权 |
| change_mgmt_impact_and_revert | implemented | **verified**（impact diff 两侧均取自库内行、词法噪音免疫、order_only 标注；revert 从基线表自建提案、目标重哈希锚定、仍走两步人类批准；stale 双向重派生；applied review 锚定基线重哈希检查；**REVERT_TARGET 守卫实测**） | 未执行 | tests/test_change_mgmt.py, verification/run_store_verification.py | revert 只能回到**本库持有过**的基线；无 `--assume-yes`；认证层未接入（`--reviewer` 字符串 + 保留标识判断） |
| object_level_patch_language | implemented | **verified**（47 项专项测试 + 状态机契约：九种操作按序应用、纯函数、**与等价全模型提案同哈希**（收敛，含 rate 编辑）、rate p 不可直设、**`set_event_rate` 完整重跑转换闸门且 q 对照独立 oracle（1e-30）**、被引用不可删、跨操作环与不可达环被拒、非法记 invalid、`1/3` 精确保留） | 未执行 | tests/test_patch.py, verification/run_store_verification.py | `model-patch-v1`：作用于库内当前基线的规范形式，产出普通提案；`set_event_rate` 仅编辑既有 rate 事件（不提供普通→rate 转换）；`add_event` 仅普通概率（**新增** rate 事件待需要）；无移动/重命名 |
| FMEA / requirements traceability | implemented | **verified**（15 当前行 + 2 被取代行**全部由原始列独立重建规范形式并重哈希**；34 条关联逐条解析（26 命中 / 8 悬空如实报告）；多对多双向证明（8 事件被 >1 行引用、12 行引用 >1 事件）；20 个候选全生命周期审计；115 个存储值 `typeof()` 扫描零 REAL；8 类拒绝码实测；**20/20 突变被检出**） | 未执行 | tests/test_fmea.py, verification/run_fmea_verification.py | 单表 + 关联表 + 候选表；**不含**严重度/发生度/探测度/RPN（FMECA 不在范围）；推断行（`source=inference`）必须写 `inference_note` 且**永远不能自动进入正式表**；关联悬空只报告与计数、**不判为完整性失败**；已批准内容不可就地改写，修订必产生新版本 |
| fmea_attention_from_importance | implemented | **verified**（关注项生成器幂等性/覆盖跳过/精确 `min-value`/未定义度量跳过/过期基线拒绝/带标记行批准后仍拒绝晋升，6 类拒绝路径实测；正式表零写入） | 未执行 | tests/test_fmea.py, verification/run_fmea_verification.py | 只产出**工作项**：四个描述字段带 `[UNCONFIRMED]`、组件/功能为 `*-UNASSIGNED`，**引擎不撰写 FMEA 内容**；带标记的行永远无法入表（`FMEA_PLACEHOLDER`）；仅支持单个模型当前基线上的 run |
| web workbench | 未实现 | — | — | docs/NEXT_STEPS.md | 第二阶段 |
| Open-PSA / ReqIF import | 未实现 | — | — | docs/NEXT_STEPS.md | 互操作须专项验证 |
| PostgreSQL 迁移 | 未实现 | — | — | docs/NEXT_STEPS.md | 一期已备 `store export` 往返，迁移脚本待团队版 |

## 使用边界声明

本版本为**研究样机 + 方法可用候选**：
- 可声明：对契约 0.1.0/0.2.0 范围内的相干静态 FTA 模型独立完成确定性精确计算（含恒定失效率→任务概率的一次受控舍入转换），给出精确的 Birnbaum / Fussell–Vesely / RAW / RRW 重要度，并在本地库中按语义哈希锚定基线、保留旧版证据、把变更走"提案→批准→应用"闭环
- 可声明：维护一张与基本事件**多对多关联**的 FMEA 基础表，按 `fmea-canonical-v1` 精确重哈希，区分人工/推断/导入来源，并把推断行的确认要求与人工批准绑定到具体基线与内容
- 可声明：对提案给出**结构化影响范围分析**（事件/门/假设/顶事件的增删改，两侧均取自库内行），提供**一等公民 revert**（从库内基线自建提案，仍走两步人类批准，stale 双向重派生）与**对象级 patch 语言**（九种编辑操作，作用于库内规范形式，与整份模型提案收敛于同一哈希；rate 编辑重跑转换闸门并对照独立 oracle）
- 可声明：按重要度排序**指出哪些事件值得做 FMEA**（给出精确度量值、排名与 run/基线来源），并生成带标记的**工作项草稿**
- 不可声明：由引擎**撰写** FMEA 内容（失效模式/原因/影响）——关注项的描述字段永远是占位符，带标记的行无法入表
- 不可声明：替代 Medini、DO-330/TQL 任何等级、适航认可、正式安全结论；亦不可把 q 当作 per-flight-hour 指标；重要度数值本身不构成任何设计变更批准
- FMEA 表**只是失效模式与追溯的载体**，不含严重度/发生度/探测度/RPN，也不产生 FMECA/FMEDA 结论
- 存储层记录的是**工程变更的决定**，不是安全结论的批准
- `approval_state` 在所有输出中恒为 `not_granted_by_this_result`
