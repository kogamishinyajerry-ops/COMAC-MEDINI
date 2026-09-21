# OFFLINE_SETUP — 离线安装与运行

## 运行需求

- Python ≥ 3.10（在 3.11.8 与 3.13.12 实测通过）
- 无第三方依赖：生产链路只用标准库（json, re, fractions, decimal, hashlib, argparse, dataclasses, pathlib, **sqlite3**）
- 无网络、无 Medini、无 A 线服务、无 LLM —— 以上全部缺失时功能完整

## 快速开始

```bash
# 校验模型
python run.py validate <model.json>

# 求解（顶事件概率 + 最小割集 + 重要度 + 结构化结果）
python run.py analyze <model.json> --evidence-dir evidence --run-id <id>

# 重要度开关（默认 auto：≤1000 事件自动计算，超出则跳过并在输出中声明）
python run.py analyze <model.json> --importance off    # 不计算
python run.py analyze <model.json> --importance on     # 强制计算

# 能力矩阵（诚实状态）
python run.py capabilities
```

## 本地库（基线 / 运行记录 / 评审）

单文件 SQLite（schema v2：运行/基线/评审 + FMEA 行/关联/候选共八表），无服务、无端口。首次使用会自动建表（含迁移；v1 库自动**增量**升级到 v2，幂等可重跑）。

```bash
# 求解并记录（run_id 幂等：同内容重跑为空操作，不同内容拒绝覆盖）
python run.py analyze <model.json> --db store.sqlite --run-id <id> --actor <name>

python run.py store status  store.sqlite          # 计数、stale 数、待办评审、浮点列审计
python run.py store runs    store.sqlite --model <model_id>
python run.py store show    store.sqlite <run_id>  # 完整行 + 载荷 + 重要度
python run.py store important store.sqlite <run_id> --by fussell_vesely --top 10
python run.py store baseline store.sqlite <model_id> [--all]
python run.py store verify  store.sqlite           # 完整性自检（空 problems 即干净）
python run.py store export  store.sqlite --out dump.json   # 迁移/备份往返

# 变更闭环：提案 → 具名人类批准 → 应用（Agent 只能提案）
python run.py review propose store.sqlite <new_model.json> --review-id REV-1 \
       --expected-baseline-hash <current> --reviewer agent
python run.py review impact  store.sqlite REV-1            # 只读：这份提案会改什么（diff 取自库内行）
python run.py review decide  store.sqlite REV-1 --approve --reviewer <human>
python run.py review apply   store.sqlite REV-1 --reviewer <human>   # 旧 run 自动标 stale

# 对象级 patch：编辑指令作用于库内当前基线（无需整份模型文件）
python run.py review patch-preview store.sqlite --model-id <model_id> --patch patch.json  # 只读预览
python run.py review patch store.sqlite --model-id <model_id> --patch patch.json \
       --review-id P1 --expected-baseline-hash <current> --reviewer agent
python run.py review decide store.sqlite P1 --approve --reviewer <human>   # 之后同普通提案
# patch.json 是操作数组，如：
# [{"op":"set_event_probability","event":"A","probability":"0.25"},
#  {"op":"add_event","event":{"id":"D","p":"0.05"}},
#  {"op":"set_gate","gate":{"id":"G2","kind":"AND","inputs":["A","D"]}}]
# rate 事件用 set_event_rate（完整重跑转换闸门，p 自动重派生）：
# [{"op":"set_event_rate","event":"P1","rate":{"model":"constant_failure_rate",
#   "lambda":"5e-6","lambda_unit":"1/h","mission_time":"2000","mission_time_unit":"h",
#   "repairable":false,"source":"derating per vendor rev B"}}]

# 回退到某个旧基线（提案从基线表自建，仍需两步人类批准；无 --assume-yes）
python run.py store  baseline store.sqlite <model_id> --all            # 列出持有过的基线
python run.py review revert  store.sqlite --model-id <model_id> \
       --target-baseline-hash <old> --review-id RV-1 --reviewer agent
python run.py review decide  store.sqlite RV-1 --approve --reviewer <human>
python run.py review apply   store.sqlite RV-1 --reviewer <human>      # 旧 run 的 stale 双向翻转

# 显式换基线（不走评审、但同样要求具名人类与预期哈希）
python run.py store adopt-baseline store.sqlite <new_model.json> \
       --expected-baseline-hash <current> --reviewer <human>
```

## FMEA 基础表与追溯

FMEA 行与基本事件**多对多**关联；机器推断的行必须写 `inference_note`，且**人工批准后**才入正式表。已批准内容不可就地改写——修订产生**新版本**。

```bash
# 提案（agent 可提案；model_id 与 source 写在行 JSON 里）。推断行的 inference_note 不可省
python run.py fmea propose store.sqlite <fmea_row.json> \
       --candidate-id FREV-1 --expected-baseline-hash <current> --reviewer agent
# 修订既有行时必须指名目标版本：--expected-version <v>

# 具名人类批准 → 应用（应用后旧版本自动 superseded，且不会被就地改写）
python run.py fmea decide store.sqlite FREV-1 --approve --reviewer <human>
python run.py fmea apply  store.sqlite FREV-1 --reviewer <human>

python run.py fmea rows      store.sqlite --model <model_id> [--include-superseded]
python run.py fmea candidates store.sqlite [--state proposed] [--model <model_id>]
python run.py fmea show      store.sqlite <candidate_id>
python run.py fmea trace     store.sqlite --model <model_id> --event <event_id>
python run.py fmea trace     store.sqlite --model <model_id> --component <component_id>
python run.py fmea trace     store.sqlite --model <model_id> --dangling    # 失联关联（只报告）
```

> 注意：`source: "inference"` 而缺 `inference_note` 会被拒（`FMEA_SOURCE`，退出码 2）。悬空关联是**追溯缺口**，`store verify` 不将其判为损坏。

样例：`examples/F01_fmea_seal_leak.json`（人工行）、`examples/F02_fmea_inference_mode.json`（推断行）。

### 由重要度排序生成关注项（工作项，不是内容）

引擎会按重要度指出**哪些事件值得做 FMEA**，但它不知道失效模式/原因/影响，因此只生成**带标记的工作项**：

```bash
# 从某个 run 的已存重要度排名生成草稿（默认按 Fussell–Vesely，最多 10 条新草稿）
python run.py fmea propose-from-importance store.sqlite --run <run_id> \
       [--by fussell_vesely|birnbaum_importance|risk_achievement_worth|risk_reduction_worth|event_probability] \
       [--top N] [--min-value 0.1]

# 重复运行是空操作：已覆盖/已起草的事件会跳过并给出理由
# --min-value 按精确有理数比较（0.1 或 1/10 均可）；未定义的度量一律跳过
```

生成的草稿四个描述字段都以 `[UNCONFIRMED]` 开头，组件/功能为 `COMPONENT-UNASSIGNED` / `FUNCTION-UNASSIGNED`。
**带标记的行永远无法入表**：即使人类批准，`apply` 仍以 `FMEA_PLACEHOLDER` 拒绝（退出码 2）。人类的回答方式是**为同一 `fmea_id` 另行提出真实内容**，再走常规批准。

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | 模型非法（语义/解析错误，含单位/失效率参数非法、FMEA 输入/身份/来源/关联/patch 操作非法）；或存储/评审请求被拒（RUN_ID_CONFLICT / BASELINE_* / REVIEW_* / APPROVAL_AUTHORITY / FMEA_NOT_FOUND / FMEA_STATE / FMEA_REVISION_CONFLICT / FMEA_PLACEHOLDER / REVERT_TARGET） |
| 3 | 不支持的语义（如 PAND、非恒定失效率、可修复语义）；或库由不兼容的 schema 世代写入（STORE_SCHEMA_MISMATCH） |
| 4 | 资源上限（BDD 节点/割集路径截断） |
| 5 | 内部错误（含 `store verify` 发现的存储不一致），如实失败，不冒充成功 |

`analyze --db` 若计算成功但记录被拒：结果仍完整输出，`store.status = "refused"` 附带原因，退出码取该拒绝的码（2/3）。

## 测试与交叉验证

生产/测试环境需要 pytest（唯一的第三方依赖，仅测试用）：

```bash
python -m venv .venv
.venv/Scripts/python -m pip install pytest    # 联网准备环境执行
.venv/Scripts/python -m pytest tests/ -q      # 305 项，离线可跑

# 交叉验证不需要 pytest（纯标准库）：
python verification/run_cross_check.py              # 210 模型（结构），离线可跑
python verification/run_rate_cross_check.py         # 120 模型（λt→q 转换），离线可跑
python verification/run_importance_cross_check.py   # 120 模型（重要度），离线可跑
python verification/run_store_verification.py       # 存储（重推导 + 突变测试 + 往返 + revert/impact/patch+rate-edit 契约），离线可跑
python verification/run_fmea_verification.py        # FMEA（原始列独立重哈希 + 状态机 + 突变测试），离线可跑
```

内网离线迁移：将整个 `native-safety-analysis/` 目录拷入。pytest 可在准备环境 `pip download pytest -d wheels/` 后以 `pip install --no-index --find-links wheels/ pytest` 离线安装；生产与交叉验证链路完全不需要这一步。

## 证据包结构

```text
evidence/run_<id>/
  manifest.json              运行清单（版本、哈希、状态、耗时）
  inputs/model_input.json    原始输入的逐字节副本
  results/analysis_result.json  结构化结果信封
  reports/report.md          人类可读报告
  execution/engine_meta.json 引擎/解释器/命令行元数据
```
